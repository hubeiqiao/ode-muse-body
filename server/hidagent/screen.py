"""The phone's screen, received over AirPlay mirroring by UxPlay, as JPEG frames."""

import asyncio
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from .mirror_stream import PREFIX, MAX_RECORD, HEARTBEAT_TIMEOUT_S, decode

log = logging.getLogger(__name__)

OFFLINE_FEEDBACK_S = 20
NTP_BURST_COUNT = 5
NTP_BURST_WINDOW_S = 10
ZOMBIE_RESTART_INTERVAL_S = 60
NTP_LOG_INTERVAL_S = 60
FEEDBACK_TIMEOUT = re.compile(r'(\d+) seconds since last client feedback request.*client may be offline')


class Screen:
    """The latest JPEG frame of the phone's screen, from UxPlay.

    By default the server runs UxPlay itself and reads frames from a pipe. With `stream` ("host:port") it follows
    a standalone receiver instead (`python -m hidagent.receiver`), so restarting the server does not end the
    phone's Screen Mirroring session. `pidfile` is where UxPlay's pid is recorded, so a server following a
    receiver can restart that receiver's UxPlay.
    """

    def __init__(self, name: str = "iPhone Agent", fps: int = 8, quality: int = 75,
                 stream: str | None = None, pidfile: Path | None = None,
                 restart_on_source_offline: bool = False):
        if stream and not stream.rpartition(":")[2].isdigit():
            raise ValueError(f"MIRROR_STREAM must be host:port, not {stream!r}")
        self.name, self.fps, self.quality = name, fps, quality
        self.stream, self.pidfile = stream, pidfile
        self.frame: bytes | None = None
        self.updated = 0.0
        self._proc = None
        self._sock = None
        self._clients_at, self._clients_n = 0.0, None
        self._stopping = False
        self._receiver_health = None
        self._receiver_health_at = 0.0
        self.restart_on_source_offline = restart_on_source_offline
        self._health_lock = threading.RLock()
        self._source_offline_reason = None
        self._ntp_burst_at, self._ntp_burst_count = None, 0
        self._ntp_log_at, self._ntp_suppressed = None, 0
        self._last_zombie_restart_at = None
        self._zombie_restarted_proc = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._follow if self.stream else self._supervise, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        if self._proc:
            self._proc.terminate()
        sock = self._sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # wakes the follower's recv(); close() alone does not on Linux
            except OSError:
                pass
            sock.close()  # and on Windows only close() does

    def restart(self) -> None:
        """Restart UxPlay (its supervisor starts a new one); the phone must choose it in Screen Mirroring again."""
        log.warning("AirPlay receiver: no new frames, restarting it")
        self.frame, self.updated = None, 0.0  # nothing since the restart: the next action must not restart it again
        if self.stream:
            if self._sock is None:  # the receiver is gone, so its pid may belong to an unrelated process by now
                log.warning("no AirPlay receiver is streaming at %s: start `python -m hidagent.receiver`", self.stream)
                return
            try:
                os.kill(int(Path(self.pidfile).read_text()), signal.SIGTERM)
            except (OSError, TypeError, ValueError) as e:
                log.warning("could not restart the standalone receiver: %r", e)
        elif self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _follow(self) -> None:
        """Read frames from the standalone receiver's TCP stream; reconnect whenever it goes away."""
        host, port = self.stream.rsplit(":", 1)
        waiting = False
        while not self._stopping:
            try:
                with socket.create_connection((host, int(port)), timeout=2) as sock:
                    sock.settimeout(1)
                    self._receiver_health, self._receiver_health_at = None, 0.0
                    self._sock, waiting = sock, False
                    log.info("following the AirPlay receiver at %s", self.stream)
                    def read():
                        while not self._stopping:
                            try:return sock.recv(1 << 16)
                            except socket.timeout:
                                if (self._receiver_health is not None and
                                        time.monotonic()-self._receiver_health_at>HEARTBEAT_TIMEOUT_S):
                                    raise TimeoutError('Local receiver heartbeat stopped')
                        return b''
                    self._consume(read, health_records=True)
            except (OSError,ValueError):
                if not waiting:
                    log.info("waiting for the AirPlay receiver at %s (python -m hidagent.receiver)", self.stream)
                    waiting = True
            finally:
                self._sock, self.frame = None, None
                self._receiver_health, self._receiver_health_at = None, 0.0
            time.sleep(0.5)

    def _supervise(self) -> None:
        """Keep UxPlay running: it exits on some disconnects, so restart it every time."""
        while not self._stopping:
            if os.name == "nt":
                self._run_with_socket()
            else:
                read_fd, write_fd = os.pipe()
                self._launch(f"fdsink fd={write_fd}", pass_fds=(write_fd,))
                os.close(write_fd)
                self._read(read_fd)  # returns when UxPlay closes the pipe
            code = self._proc.wait()
            self.frame = None
            if self._stopping:break
            log.warning("AirPlay receiver exited (code %s), restarting", code)
            time.sleep(1)

    def _launch(self, sink: str, **popen) -> None:
        display_size = os.environ.get("UXPLAY_DISPLAY_SIZE")
        display_args, scale = [], ""
        if display_size:
            match = re.fullmatch(r"([0-9]{3,4})x([0-9]{3,4})", display_size)
            if not match or not all(320 <= int(n) <= 7680 for n in match.groups()):
                raise ValueError("UXPLAY_DISPLAY_SIZE must be WxH with both dimensions in 320..7680")
            width, height = map(int, match.groups())
            raw_height = os.environ.get("MIRROR_FRAME_HEIGHT", "1080")
            if not re.fullmatch(r"[0-9]{1,10}", raw_height) or not 1 <= int(raw_height) <= 2147483647:
                raise ValueError("MIRROR_FRAME_HEIGHT must be a positive GStreamer integer (1..2147483647)")
            frame_height = int(raw_height)
            display_args = ["-s", display_size]
            scale = f"videoscale ! video/x-raw,height={frame_height},pixel-aspect-ratio=1/1 ! "
            # GStreamer rounds native mini frames to 1645/709 at height 1080.
            # One right border column retains the exact 1646/710 template sizes
            # without cropping/stretching content or constraining other aspects.
            # In particular, a 16:9 source must still change the calibration key.
            scaled_widths = ((2 * frame_height * w + h) // (2 * h)
                             for w, h in ((width, height), (height, width)))
            if all(w % 2 for w in scaled_widths):
                scale += "videobox right=-1 ! "
        sink = (f"videorate drop-only=true ! video/x-raw,framerate={self.fps}/1 ! "
                f"{scale}jpegenc quality={self.quality} ! {sink}")
        uxplay = os.environ.get("UXPLAY", "uxplay")  # e.g. C:/msys64/ucrt64/bin/uxplay.exe
        # UxPlay resets this many seconds AFTER client heartbeats stop. Heartbeats
        # continue on a still screen, so this is not an unchanged-frame timeout.
        reset = str(max(0, int(os.environ.get("UXPLAY_RESET_S", "15"))))
        with self._health_lock:
            self._clients_at, self._clients_n = 0.0, None
            self._ntp_burst_at, self._ntp_burst_count = None, 0
            self._ntp_log_at, self._ntp_suppressed = None, 0
            self._proc = subprocess.Popen(
                [uxplay, "-n", self.name, "-nh", "-a", "-reset", reset, *display_args, "-vs", sink],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1, **popen)
        if self.pidfile:
            Path(self.pidfile).write_text(str(self._proc.pid))
        log.info("AirPlay receiver '%s' started (pid %d)", self.name, self._proc.pid)
        self._log_thread = threading.Thread(target=self._read_uxplay_output, args=(self._proc,), daemon=True)
        self._log_thread.start()

    def _read_uxplay_output(self, proc) -> None:
        """Drain both UxPlay log streams even while its JPEG pipe is silent."""
        with proc.stdout:
            for line in proc.stdout:
                with self._health_lock:
                    if proc is not self._proc:
                        return  # Late output from a previous child cannot poison its replacement.
                    self._handle_uxplay_line(line.rstrip())
        with self._health_lock:
            if proc is self._proc:
                self._flush_ntp_summary()

    def _flush_ntp_summary(self) -> None:
        if self._ntp_suppressed:
            log.info('UxPlay: suppressed %d repeated raop_ntp No route to host errors', self._ntp_suppressed)
            self._ntp_suppressed = 0

    def _handle_uxplay_line(self, line: str) -> None:
        """Explicit AirPlay failure evidence overrides even an established TCP socket."""
        with self._health_lock:
            now = time.monotonic()
            feedback = FEEDBACK_TIMEOUT.search(line)
            if feedback and int(feedback[1]) >= OFFLINE_FEEDBACK_S:
                self._mark_source_offline('client_feedback_timeout', now)
            if 'raop_ntp error sending request' in line and 'No route to host' in line:
                if self._ntp_burst_at is None or now - self._ntp_burst_at > NTP_BURST_WINDOW_S:
                    self._ntp_burst_at, self._ntp_burst_count = now, 0
                self._ntp_burst_count += 1
                # A few transient route errors with recent video are not a dead session.
                no_frames = not self.updated or time.time() - self.updated >= NTP_BURST_WINDOW_S
                if self._ntp_burst_count >= NTP_BURST_COUNT and no_frames:
                    self._mark_source_offline('ntp_no_route', now)
                if self._ntp_log_at is None:
                    log.info('UxPlay: %s', line)
                    self._ntp_log_at = now
                else:
                    if now - self._ntp_log_at >= NTP_LOG_INTERVAL_S:
                        self._flush_ntp_summary()
                        self._ntp_log_at = now
                    self._ntp_suppressed += 1
                return
            log.info('UxPlay: %s', line)

    def _mark_source_offline(self, reason: str, now: float) -> None:
        if self._source_offline_reason is None:
            self._source_offline_reason = reason
            self.frame = None  # No cached picture may seed a replacement session.
            log.warning('AirPlay source offline (%s); re-select %s in Screen Mirroring', reason, self.name)
        proc = self._proc
        if (self.restart_on_source_offline and not self._stopping and proc is not None
                and proc is not self._zombie_restarted_proc and proc.poll() is None
                and (self._last_zombie_restart_at is None
                     or now - self._last_zombie_restart_at >= ZOMBIE_RESTART_INTERVAL_S)):
            self._last_zombie_restart_at, self._zombie_restarted_proc = now, proc
            log.info('Restarting offline AirPlay child (pid %s); supervisor will relaunch it', proc.pid)
            proc.terminate()  # Only our own child, never a pidfile or a device command.

    def _run_with_socket(self) -> None:
        """Windows cannot hand UxPlay a pipe, so its pipeline connects back to a local socket instead."""
        with socket.create_server(("127.0.0.1", 0)) as srv:
            srv.settimeout(0.5)
            self._launch(f"tcpclientsink host=127.0.0.1 port={srv.getsockname()[1]}")
            while self._proc.poll() is None:  # the pipeline reconnects whenever UxPlay rebuilds it
                try:
                    conn, _ = srv.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(None)
                    try:
                        self._consume(lambda c=conn: c.recv(1 << 16))
                    except OSError:
                        pass  # UxPlay went away mid-frame

    @property
    def live(self) -> bool:
        running = self._sock is not None if self.stream else self._proc is not None and self._proc.poll() is None
        if not running or self.frame is None:
            return False
        if not self.stream and self._source_offline_reason is not None:
            return False
        if self.stream and self._receiver_health is not None:
            health=self._receiver_health
            if (time.monotonic()-self._receiver_health_at>HEARTBEAT_TIMEOUT_S
                    or not health['process_running'] or not health['frame_ready']
                    or health['peer_connected'] is False):
                return False
            if health['peer_connected'] is None:
                age=health['source_frame_age_s']
                return age is not None and age<90 and time.time()-self.updated<90
            if health['peer_connected'] is True:
                return True  # confirmed session: an unchanged screen is allowed
        clients = None if self.stream else self._clients()  # a followed receiver's process is not ours
        if clients is not None:  # Is a device connected to UxPlay right now?
            return clients > 0
        return time.time() - self.updated < 90  # elsewhere: AirPlay sends no frames while the screen is still

    def receiver_health(self):
        """Receiver-owned process/session facts, never a local TCP proxy claim."""
        running=self._proc is not None and self._proc.poll() is None
        clients=self._clients() if running else 0
        with self._health_lock:
            return {'version':1,'process_running':running,
                    'peer_connected':False if self._source_offline_reason else None if clients is None else clients>0,
                    'source_offline_reason':self._source_offline_reason,
                    'source_epoch':str(getattr(self._proc,'pid','none')),
                    'source_frame_age_s':max(0,time.time()-self.updated) if self.updated else None}

    def mirror_health(self):
        """Public diagnostics; absence of frames alone cannot prove a decoder stall."""
        health=self._receiver_health
        age=max(0,time.time()-self.updated) if self.updated else None
        heartbeat_age=max(0,time.monotonic()-self._receiver_health_at) if health else None
        if not self.stream:
            health={**self.receiver_health(),'frame_ready':self.frame is not None}
        offline_reason=health.get('source_offline_reason') if health else None
        if self.stream and self._sock is None:state='receiver_disconnected'
        elif not health:state='legacy_stream' if self.live else 'legacy_stream_no_recent_frame'
        elif heartbeat_age is not None and heartbeat_age>HEARTBEAT_TIMEOUT_S:state='receiver_heartbeat_lost'
        elif offline_reason:state='source_offline'
        elif not health['process_running']:state='receiver_waiting'
        elif health['peer_connected'] is False:state='source_disconnected'
        elif not health['frame_ready'] or self.frame is None:state='waiting_for_first_frame'
        elif health['peer_connected'] is None:state='source_session_unknown'
        else:state='session_connected' if age is not None and age<5 else 'session_connected_no_recent_frame'
        return {'state':state,'health_protocol':1 if health else None,
                'source_session_connected':False if state in ('receiver_disconnected','receiver_heartbeat_lost','receiver_waiting','source_offline')
                                           else health['peer_connected'] if health else None,
                'source_offline_reason':offline_reason,
                'hint':f're-select {self.name} in Screen Mirroring'
                       if state=='source_offline' else None,
                'source_frame_age_s':health['source_frame_age_s'] if health else None,
                'heartbeat_age_s':round(heartbeat_age,3) if heartbeat_age is not None else None,
                'recovery':'waiting_for_source' if state in ('source_disconnected','source_offline') else
                           'local_stream_reconnect' if state in ('receiver_disconnected','receiver_heartbeat_lost') else 'none'}

    def _clients(self) -> int | None:
        """Established TCP connections of the UxPlay process (a mirroring phone keeps some open).

        Read with lsof on macOS or /proc on Linux, cached for a second.
        Return None when the platform cannot inspect the process connections.
        """
        if time.time() - self._clients_at < 1:
            return self._clients_n
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["/usr/sbin/lsof", "-nP", "-a", "-p", str(self._proc.pid),
                     "-iTCP", "-sTCP:ESTABLISHED", "-Fn"],
                    capture_output=True, text=True, timeout=2, check=False)
                n = sum(line.startswith("n") and "->" in line for line in result.stdout.splitlines())
                self._clients_at, self._clients_n = time.time(), n if result.returncode in (0, 1) else None
                return self._clients_n
            except (OSError, subprocess.TimeoutExpired):
                return None
        try:
            fds = os.listdir(f"/proc/{self._proc.pid}/fd")
            inodes = set()
            for fd in fds:
                link = os.readlink(f"/proc/{self._proc.pid}/fd/{fd}")
                if link.startswith("socket:["):
                    inodes.add(link[8:-1])
            n = 0
            for table in ("/proc/net/tcp", "/proc/net/tcp6"):
                with open(table) as f:
                    for row in f.readlines()[1:]:
                        cols = row.split()
                        n += cols[3] == "01" and cols[9] in inodes  # 01 = ESTABLISHED
        except OSError:
            n = None
        self._clients_at, self._clients_n = time.time(), n
        return n

    @property
    def aspect(self) -> float:
        """Frame width / height, read from the JPEG header (portrait iPhone as a fallback)."""
        f = self.frame or b""
        i = f.find(b"\xff\xc0")  # baseline start-of-frame: ... height(2) width(2)
        if i < 0 or len(f) < i + 9:
            return 9 / 19.5
        return int.from_bytes(f[i + 7:i + 9], "big") / int.from_bytes(f[i + 5:i + 7], "big")

    async def settled(self, quiet: float = 0.35, least: float = 0.25, most: float = 4) -> None:
        """Wait until the screen stops changing (AirPlay only sends frames on change)."""
        started = time.time()
        await asyncio.sleep(least)
        while time.time() - self.updated < quiet and time.time() - started < most:
            await asyncio.sleep(0.1)

    async def fresh(self, since: float, timeout: float = 0.8) -> bytes | None:
        """The first frame received after `since`, or None if none arrives in time."""
        deadline = time.time() + timeout
        while self.updated <= since:
            if time.time() > deadline:
                return None
            await asyncio.sleep(0.02)
        return self.frame

    def _read(self, fd: int) -> None:
        with os.fdopen(fd, "rb", buffering=0) as pipe:
            self._consume(lambda: pipe.read(1 << 16))

    def _consume(self, read, health_records=False) -> None:
        """Parse JPEGs, plus bounded health records only on the local follower."""
        buf = b""
        while chunk := read():
            buf += chunk
            while buf:
                start=buf.find(b'\xff\xd8')
                marker=buf.find(PREFIX) if health_records else -1
                if marker>=0 and (start<0 or marker<start):
                    end=buf.find(b'\n',marker)
                    if end<0:
                        if len(buf)-marker>MAX_RECORD:raise ValueError('Oversized mirror health')
                        buf=buf[marker:];break
                    if end-marker>MAX_RECORD:raise ValueError('Oversized mirror health')
                    health=decode(buf[marker+len(PREFIX):end])
                    old=self._receiver_health
                    if (old and old['source_epoch']!=health['source_epoch']) or not health['frame_ready'] or health['peer_connected'] is False:
                        self.frame=None  # never relabel a prior session as fresh
                    self._receiver_health,self._receiver_health_at=health,time.monotonic()
                    buf=buf[end+1:];continue
                if start<0:
                    # Preserve a possible fragmented health prefix or JPEG SOI.
                    buf=buf[-max(len(PREFIX)-1,1):];break
                end=buf.find(b'\xff\xd9',start)
                if end<0:
                    buf=buf[start:];break
                allowed=(not health_records or self._receiver_health is None or
                         (self._receiver_health['process_running'] and self._receiver_health['frame_ready']
                          and self._receiver_health['peer_connected'] is not False))
                if allowed:
                    with self._health_lock:
                        self.frame,self.updated=buf[start:end+2],time.time()
                        if not health_records:
                            self._source_offline_reason = None
                            self._ntp_burst_at,self._ntp_burst_count = None,0
                buf=buf[end+2:]
