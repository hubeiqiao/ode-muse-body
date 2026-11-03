"""Run the AirPlay receiver on its own: `python -m hidagent.receiver`.

It runs UxPlay (starting it again whenever it exits) and serves the phone's screen as JPEG frames on a local TCP
port; the server follows that stream (MIRROR_STREAM=127.0.0.1:8891), so restarting the server leaves the phone's
Screen Mirroring session alone. UxPlay's pid is recorded so the server can still restart a frozen receiver.
"""

import logging
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from .screen import Screen
from .mirror_stream import record, HEARTBEAT_S

log = logging.getLogger(__name__)


def serve(screen, port: int) -> None:
    """Send each server that connects the current frame at once, then every new one.

    A still phone sends no frames, so a server restarted in the meantime would otherwise wait for the screen to
    change (GStreamer's tcpserversink only passes on frames made after a client connects).
    """
    with socket.create_server(("127.0.0.1", port)) as srv:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_feed, args=(screen, conn), daemon=True).start()


def _feed(screen, conn: socket.socket) -> None:
    sent = 0.0
    frame_floor = -1.0
    last_status, last_heartbeat = None, 0.0
    with conn:
        conn.settimeout(2)  # a dead consumer cannot pin an unbounded sender thread
        try:
            while True:
                updated, frame = screen.updated, screen.frame
                health = screen.receiver_health() if hasattr(screen,'receiver_health') else {
                    'version':1,'process_running':True,'peer_connected':bool(getattr(screen,'live',True)),
                    'source_epoch':str(id(screen)),
                    'source_frame_age_s':max(0,time.time()-updated) if updated else None}
                # Once disconnected, a cached JPEG cannot seed the next session.
                if not health['process_running'] or health['peer_connected'] is False:
                    frame_floor=max(frame_floor,updated)
                ready=(frame is not None and updated>frame_floor and health['process_running']
                       and health['peer_connected'] is not False)
                health={**health,'frame_ready':ready}
                signature=(health['source_epoch'],health['process_running'],health['peer_connected'],ready,
                           health.get('source_offline_reason'))
                now=time.monotonic()
                if signature!=last_status or now-last_heartbeat>=HEARTBEAT_S:
                    conn.sendall(record(health));last_status,last_heartbeat=signature,now
                if ready and updated>sent:
                    conn.sendall(frame);sent=updated
                time.sleep(.02)
        except OSError:
            pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    name = os.environ.get("AIRPLAY_NAME", "iPhone Agent")
    port = int(os.environ.get("MIRROR_PORT", 8891))
    data = Path(os.environ.get("HIDAGENT_DATA", Path.home() / ".hidagent"))
    data.mkdir(parents=True, exist_ok=True)
    screen = Screen(name, pidfile=data / "uxplay.pid",
                    restart_on_source_offline=os.environ.get('UXPLAY_ZOMBIE_RESTART', '1') == '1')
    def terminate(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    try:
        screen.start()
        log.info("AirPlay receiver '%s': frames on 127.0.0.1:%d", name, port)
        serve(screen, port)
    finally:
        screen.stop()
        if screen._proc:
            try:screen._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.error('Video process did not exit after graceful termination; no forced kill')


if __name__ == "__main__":
    main()
