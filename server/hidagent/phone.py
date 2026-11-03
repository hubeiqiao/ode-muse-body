"""Touch-like gestures on the phone, built from relative mouse moves.

The pointer is relative, so the server tracks where it is (in mouse units) and moves
from there; every few gestures it slams it into the top-left corner (`home`) again to
cancel drift. Coordinates are fractions of the screen (0..1, origin top-left), so they
work for any frame size.
"""

import asyncio
import json
import os
import logging
import math
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

from . import clipboard, vision
from .device import Device, DeviceError, timeline_event, probe_recheck
from .hid_lease import check_hid, has_input_lease

log = logging.getLogger(__name__)

CALIBRATION_MOVE = 200  # mouse units moved diagonally for a manual (one point) calibration; stays on screen
CALIBRATION_STOPS = (150, 350)  # automatic calibration finds the pointer at these distances from the corner
# then where it lands for plain moves across the whole screen: the two stops above sit in the top-left
# third, so a line through them alone stretches small reading errors into misses of up to ~65 px near the
# bottom; with this grid the worst miss stayed within ~18 px (three calibrations on a real iPhone)
CALIBRATION_GRID = tuple((x, y) for y in (0.15, 0.5, 0.85) for x in (0.15, 0.5, 0.85))
REHOME_EVERY = 10       # gestures between re-homing
HOLD = 0.25             # seconds to rest before lifting, so iOS adds no momentum (the swipe stops where it ends)
TAP_GAP = 0.15          # seconds to stay still after a click, so iOS sees a finished tap, not the start of a drag
POINTER_WAIT = 2.0     # wait for a visible pointer change, not merely the next repeated JPEG
HOME_RECOVERY_WAIT = 2.0  # observe each acknowledged Home attempt before considering one retry
NUDGE = 12              # mouse units the pointer is wiggled by to find it on screen
AIM_TOLERANCE = 0.008   # screen fraction; a bigger miss is corrected before pressing
MAX_MISS = 0.06         # bigger misses (up to AIM_REJECT) are still corrected, but not learned from
HANDOFF_WAIT = 4.0      # seconds for Universal Clipboard to offer the Mac's new clipboard (1 s was too short)
AIM_REJECT = 2 * MAX_MISS  # a pointer "found" farther off is something else that moved (a clock hand): ignored
STALE_AFTER = 5.0       # seconds without a new frame before an action first checks that the phone still responds
PROBE_WAIT = 1.5        # seconds a pointer wiggle gets to show up on the mirrored screen
FROZEN_AFTER = 70.0     # no frame for this long: not even the status-bar clock changed, so the mirror is stuck
LONG_MOVE = 0.3         # screen fraction; after a longer move the pointer gets a moment to settle before a click
SETTLE_AFTER_LONG_MOVE = 0.15  # seconds


def pointer_observation_roi(label,bounds,margin,target,enabled):
    """See a whole pointer adjacent to a control, without changing click bounds."""
    if not enabled or label!='near' or target.get('device')!='ipad' or bounds is None:return bounds,margin
    w,h=target.get('width'),target.get('height')
    if (not isinstance(w,(int,float)) or not isinstance(h,(int,float)) or isinstance(w,bool) or isinstance(h,bool)
        or not math.isfinite(w) or not math.isfinite(h) or min(w,h)<=0):return bounds,margin
    if len(bounds)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in bounds):return bounds,margin
    l,t,r,b=bounds
    if l>=r or t>=b:return bounds,margin
    # Pointer diameter scales with the mirrored frame width. The extra band
    # is observation-only; original center/aim/pre_click are unchanged.
    px=.035;py=px*w/h
    expanded=(max(0,l-px),max(0,t-py),min(1,r+px),min(1,b+py))
    return expanded,min(margin,expanded[1])


def ascii_typing_segments(text: str, limit: int):
    """Bounded printable ASCII, preserving spaces despite firmware line.trim()."""
    if not 8<=limit<=32:raise ValueError('IPAD_TYPE_CHUNK_CHARS must be0 or8..32')
    if any(not 32<=ord(c)<=126 for c in text):raise ValueError('Bounded typing accepts one printable ASCII line')
    offset=0
    while offset<len(text):
        if text[offset]==' ':
            # The firmware trims both ends of a payload, so a chunk may never start
            # with a space either ("Full Screen Apps" at 11 used to arrive as "Full ScreenApps").
            yield 'key space',1
            offset+=1
            continue
        part=text[offset:offset+limit].rstrip(' ')
        if part:
            yield 'type '+part,len(part)
            offset+=len(part)
        else:
            # A trailing/all-space payload would be trimmed by the firmware.
            yield 'key space',1
            offset+=1


@contextmanager
def _timed(metrics, key):
    if metrics is None:
        yield
        return
    started=time.perf_counter()
    try:yield
    finally:metrics[key]=round(metrics.get(key,0)+time.perf_counter()-started,4)


_action_timing_parent=ContextVar('hid_action_timing_parent',default=None)


class PhoneUnavailable(RuntimeError):
    """The phone cannot be acted on right now (mirror frozen, phone locked, mouse ignored): say why, do nothing."""


class _PointerUnconfirmed(PhoneUnavailable):
    """Recoverable aim visibility failures; all safety refusals stay separate."""


def experimental_pointer_pace(target, milliseconds):
    """Only two opt-in cadences, never a default for another device/geometry."""
    return (os.environ.get('HID_POINTER_FAST_EXPERIMENT')=='1'
            and milliseconds in (20,30) and isinstance(target,dict)
            and target.get('device')=='ipad' and target.get('orientation')=='portrait'
            and bool(target.get('calibration_key')))


def control_interior_contains(box,point,info):
    """Strict two-pixel inset of an independently verified actual UI control."""
    try:
        w,h=info['width'],info['height'];x,y=point
        return (w>0 and h>0 and box['left']+2/w<x<box['right']-2/w
                and box['top']+2/h<y<box['bottom']-2/h)
    except (KeyError,TypeError,ValueError):return False

def validate_control_box(box,point,info):
    if (info.get('device')!='ipad' or info.get('orientation')!='portrait'
        or not isinstance(box,dict) or set(box)!={'left','top','right','bottom'}
        or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in box.values())
        or not 0<box['right']-box['left']<=.4 or not 0<box['bottom']-box['top']<=.2
        or not control_interior_contains(box,point,info)):
        raise PhoneUnavailable('Invalid independently verified control interior; no input sent')
    return dict(box)

class Phone:
    def __init__(self, device: Device, calibration_file: Path, screen=None, profiles=None):
        self.device, self._file, self.screen = device, calibration_file, screen
        self.profiles, self._base_file, self._profile_key = profiles, calibration_file, None
        self._calibration_key = None
        self._profile_pace = None
        # mouse units needed to cross the full screen width / height
        saved = json.loads(calibration_file.read_text()) if profiles is None and calibration_file.exists() else None
        self.span = {"w": saved["w"], "h": saved["h"]} if saved else None
        self.axes = dict((saved or {}).get("axes", {"x": 1, "y": 1}))
        self._typing_tuned_link = None
        self._pointer_tuned_link = None
        self._verified_home_probe_hint=None
        self._converged_position_hint = None
        self._home_local_hint = None
        self._point_learning_tx=ContextVar('point_learning_transaction',default=None)
        self._verified_control_tx=ContextVar('verified_control_transaction',default=None)
        self._pos = None  # pointer position in mouse units; None = unknown
        self._last_move = 0.0  # length of the latest move, as a fraction of the screen
        self._moves = 0
        self._link = 0  # device.links when the pointer was last homed
        # where the pointer really landed (seen on screen) for the screen fraction we aimed at, per axis;
        # a straight-line fit through them corrects every later move, so taps get better as the agent works
        self._seen = {axis: deque(map(tuple, (saved or {}).get("seen", {}).get(axis, [])), maxlen=24)
                      for axis in ("x", "y")}

    @property
    def calibrated(self) -> bool:
        self._sync_profile()
        return self.span is not None

    def target_info(self) -> dict | None:
        return self.profiles.describe(self.screen.frame if self.screen else None) if self.profiles else None

    def _pointer_ms(self):
        raw=os.environ.get('HID_POINTER_MS')
        fast_override=False
        configured=os.environ.get('HID_POINTER_PROFILE_MS')
        if configured and self.profiles is not None:
            overrides=json.loads(configured)
            if not isinstance(overrides,dict):raise ValueError('Pointer profile pacing must be an object')
            key=(self.target_info() or {}).get('calibration_key')
            fast_override=key in overrides
            raw=overrides.get(key,raw)
        if raw is None:return None
        ms=int(raw)
        if not 40<=ms<=100 and not (fast_override and experimental_pointer_pace(self.target_info(),ms)):
            raise ValueError('Pointer pacing requires 40..100ms, or an explicit isolated iPad experiment')
        return ms

    def _sync_profile(self) -> bool:
        if self.profiles is None:
            return False
        key = self.target_info()["calibration_key"]
        pace=self._pointer_ms()
        if key == self._profile_key and pace == self._profile_pace:
            return False
        self._verified_home_probe_hint=None
        self._profile_key = key
        if pace != self._profile_pace:self._calibration_key=None
        self._profile_pace = pace
        suffix = "-br" if os.environ.get("IPHONE_POINTER_ANCHOR") == "bottom-right" and self.target_info()["device"] == "iphone" else ""
        if pace in (20,30):suffix+=f"-ms{pace}"
        self._file = self._base_file.with_name(f"calibration-{key}{suffix}.json") if key else self._base_file
        saved = json.loads(self._file.read_text()) if key and self._file.exists() else None
        if saved and pace is not None and saved.get('motion_ms',40)!=pace:
            saved=None  # changing report cadence changes iPad pointer acceleration
        self.span = {"w": saved["w"], "h": saved["h"]} if saved else None
        self.axes = dict((saved or {}).get("axes", {"x": 1, "y": 1}))
        self._seen = {axis: deque(map(tuple, (saved or {}).get("seen", {}).get(axis, [])), maxlen=24)
                      for axis in ("x", "y")}
        self._pos, self._moves = None, 0
        return True

    async def start_calibration(self) -> bool:
        self._verified_home_probe_hint=None
        self._sync_profile()
        key=self._profile_key
        span=dict(self.span) if self.span else None
        axes=dict(self.axes)
        seen={axis:list(samples) for axis,samples in self._seen.items()}
        fast=(self._pointer_ms() in (20,30) and (self.target_info() or {}).get('device')=='ipad')
        try:
            delivery=await self._fast_calibration_counters() if fast else None
            return await self._calibrate(delivery_before=delivery) if fast else await self._calibrate()
        except BaseException:
            # A cancelled or failed calibration must not leave its provisional
            # fit active. Fast counter failures also invalidate tune/tickets.
            self._calibration_key=None  # a failed/stopped run cannot leave a manual-finish ticket
            if fast:
                self._pointer_tuned_link=None

            self._pos=None
            if self._profile_key==key and (self.profiles is None or self.target_info()['calibration_key']==key):
                self.span,self.axes=span,axes
                for axis,samples in seen.items():
                    self._seen[axis].clear();self._seen[axis].extend(samples)
            else:
                self._sync_profile()
            raise

    async def _fast_calibration_counters(self):
        link=getattr(self.device,'links',None)
        reply=await self.device.send('stats')
        try:
            if not isinstance(reply,str) or not reply.startswith('ok '):raise ValueError()
            values=dict(part.split('=',1) for part in reply.split()[1:] if '=' in part)
            keys=('sent','retried','dropped','ms','mouse_sub')
            if any(not values[k].isascii() or not values[k].isdigit() for k in keys):raise ValueError()
            counters={k:int(values[k]) for k in keys}
            if counters['ms']!=self._pointer_ms() or not counters['mouse_sub']:raise ValueError()
            if link!=getattr(self.device,'links',None):raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise PhoneUnavailable('Fast calibration needs stable readable mouse delivery counters; no mapping saved') from None
        return {'link':link,**counters}

    async def _calibrate(self, *, delivery_before=None) -> bool:
        """Find the pointer on screen at two known distances from the corner and fit the mapping from both.

        Two points give the scale and the offset, which one point cannot (iOS pointer motion is not exactly
        proportional). Returns True when it calibrated itself; otherwise the user clicks the pointer
        (finish_calibration).
        """
        self._sync_profile()
        self._calibration_key = self._profile_key
        previous_span = dict(self.span) if self.span else None
        previous_seen = {axis:list(samples) for axis,samples in self._seen.items()}
        original_axes = dict(self.axes)
        if self.profiles is not None and self._profile_key is None:
            raise RuntimeError("start screen mirroring before calibrating")
        self._pos = None
        self.span = None
        seen = await self._measure_stops()
        if len(seen) == 2:
            # Some iPad orientation/input combinations reverse an axis. Learn its sign
            # from observed motion, then home and measure again in screen coordinates.
            direction = -1 if self._home_fraction() else 1
            reversed_axes = [axis for axis, i in (("x", 1), ("y", 2)) if direction * (seen[1][i] - seen[0][i]) < 0]
            if reversed_axes:
                for axis in reversed_axes:
                    self.axes[axis] *= -1
                log.info("calibration: reversed axes %s; measuring again", reversed_axes)
                seen = await self._measure_stops()
        if len(seen) < 2:
            if delivery_before is not None:
                raise PhoneUnavailable('Fast calibration needs a verified automatic grid; no manual fallback or mapping saved')
            self.axes = original_axes
            log.info("calibration: pointer not found on screen, waiting for a click")
            await self._send("home")
            direction = -1 if self._home_fraction() else 1
            await self._move_xy(CALIBRATION_MOVE * direction, CALIBRATION_MOVE * direction)
            return False
        (u1, x1, y1), (u2, x2, y2) = seen
        anchor = self._home_fraction()
        direction = -1 if anchor else 1
        if direction * (x2 - x1) <= 0.01 or direction * (y2 - y1) <= 0.01:
            self.axes = original_axes
            raise RuntimeError("pointer calibration did not show consistent forward motion")
        self.span = {"w": round(direction * (u2 - u1) / (x2 - x1)), "h": round(direction * (u2 - u1) / (y2 - y1))}
        for samples in self._seen.values():
            samples.clear()
        for units, x, y in seen:  # where each stop landed, so moves are corrected for the offset too
            self._seen["x"].append((anchor + direction * units / self.span["w"], x))
            self._seen["y"].append((anchor + direction * units / self.span["h"], y))
        accepted_grid = []
        for gx, gy in CALIBRATION_GRID:
            await self._send("home")
            await self._move_xy(round((gx - anchor) * self.span["w"]), round((gy - anchor) * self.span["h"]))
            found = await self._find_pointer()
            if found and max(abs(found[0] - gx), abs(found[1] - gy)) <= 2 * MAX_MISS:
                self._seen["x"].append((gx, found[0]))
                self._seen["y"].append((gy, found[1]))
                accepted_grid.append((gx,gy))
        if (len(accepted_grid)<5 or len({x for x,y in accepted_grid})<2
                or len({y for x,y in accepted_grid})<2):
            # Two nearby initial stops cannot prove a whole-screen mapping.
            # Keep the prior orientation's file and mapping, never save a sparse fit.
            self.span, self.axes = previous_span, original_axes
            for axis,samples in previous_seen.items():
                self._seen[axis].clear(); self._seen[axis].extend(samples)
            self._pos = None
            raise RuntimeError("Too few reliable calibration grid points; previous calibration preserved")
        if delivery_before is not None:
            after=await self._fast_calibration_counters()
            if (after['link']!=delivery_before['link'] or after['sent']<=delivery_before['sent']
                    or any(after[k]!=delivery_before[k] for k in ('retried','dropped','ms','mouse_sub'))):
                raise PhoneUnavailable('Fast calibration delivery had retries, drops or a changed controller; previous mapping preserved')
        self._pos = None  # the grid moved the pointer behind _move_to's back
        self._save()
        log.info("calibration: pointer at %s, span %s", [(round(x, 3), round(y, 3)) for _, x, y in seen], self.span)
        return True

    async def _measure_stops(self) -> list:
        await self._send("home")
        seen = []
        direction = -1 if self._home_fraction() else 1
        for units in CALIBRATION_STOPS:
            step = units - (seen[-1][0] if seen else 0)
            await self._move_xy(step * direction, step * direction)
            found = await self._find_pointer() if self.screen is not None and self.screen.live else None
            if not found:
                break
            log.info("calibration sample: anchor=%s axes=%s units=%s pointer=%s",self._home_fraction(),self.axes,units,found)
            seen.append((units, *found))
        return seen

    async def _move_xy(self, dx: int, dy: int, point_move: bool = True) -> None:
        # iPad acceleration depends on the per-axis report delta. Keep point and
        # calibration moves on the same single-axis pace, regardless of slope.
        metrics=getattr(self,'_active_point_timing',None)
        target=self.target_info() if metrics is not None else {}
        axis_separate=bool(self._home_fraction() or os.environ.get('POINTER_AXIS_SEPARATE')=='1')
        bounded=(point_move and axis_separate and metrics is not None and self.span
                 and (target or {}).get('device')=='ipad' and target.get('orientation')=='portrait')
        limit=int(os.environ.get('IPAD_POINT_CHUNK_UNITS','0')) if bounded else 0
        if limit and (not 40<=limit<=200 or limit%10):
            raise ValueError('IPAD_POINT_CHUNK_UNITS must be 0 or a multiple of 10 from40 to200')
        if limit:
            link=self.device.links
            binding=tuple(target.get(k) for k in ('device','orientation','calibration_key','width','height'))
            async def segment(x,y):
                count=max(1,math.ceil(max(abs(x),abs(y))/limit))
                px=py=0
                for i in range(1,count+1):
                    current=self.target_info() or {}
                    if (self.device.links!=link or not getattr(self.device,'connected',True)
                            or tuple(current.get(k) for k in ('device','orientation','calibration_key','width','height'))!=binding):
                        self._pos=None
                        raise PhoneUnavailable('Point binding changed; remaining movement discarded')
                    nx=int(math.copysign(min(abs(x),i*limit),x)) if x else 0
                    ny=int(math.copysign(min(abs(y),i*limit),y)) if y else 0
                    check_hid('move')
                    metrics['point_chunks_dispatched']=metrics.get('point_chunks_dispatched',0)+1
                    try:
                        await self._send(f'move {nx-px} {ny-py}')
                    except (Exception,asyncio.CancelledError):
                        self._pos=None
                        metrics['point_chunk_delivery_uncertain']=True
                        raise
                    metrics['point_chunks_acknowledged']=metrics.get('point_chunks_acknowledged',0)+1
                    px,py=nx,ny
                    await asyncio.sleep(0)  # Stop can revoke before the next packet.
            # Preserve calibrated single-axis order and the firmware10-unit
            # reports. Only bound each synchronous command, not its report pace.
            if (self._home_fraction() or os.environ.get('POINTER_AXIS_SEPARATE')=='1') and dx and dy:
                await segment(dx,0);await segment(0,dy)
            else:await segment(dx,dy)
        elif point_move and (self._home_fraction() or os.environ.get("POINTER_AXIS_SEPARATE") == "1") and dx and dy:
            await self._send(f"move {dx} 0")
            await self._send(f"move 0 {dy}")
        else:
            # Only iPhone gesture segments opt in. Calibration/point moves keep
            # their original report cadence and mapping.
            limit = int(os.environ.get('HID_GESTURE_CHUNK', '0')) if not point_move else 0
            count = max(1, math.ceil(max(abs(dx), abs(dy))/limit)) if limit > 0 else 1
            px = py = 0
            for i in range(1, count+1):
                x, y = round(dx*i/count), round(dy*i/count)
                await self._send(f"move {x-px} {y-py}")
                px, py = x, y
                await asyncio.sleep(0)


    async def _find_pointer(self, known_span: bool = False) -> tuple[float, float] | None:
        """Wiggle the pointer and find it anywhere on screen (the span may still be unknown)."""
        await self.screen.settled(quiet=0.2, least=0.3, most=.6 if known_span else 1.5)
        before, sent = self.screen.frame, time.time()
        await self._send_pointer_probe(f"move {NUDGE} 0")
        shift=NUDGE/self.span["w"] if known_span and self.span else .025
        hint=self._eligible_home_probe_hint() if known_span else None
        if hint is not None and getattr(self,'_active_point_timing',None) is not None:
            self._active_point_timing['reacquire_home_hint_used']=True
        # Hint selects an observation ROI only. It never sets _pos or sends a
        # cached-coordinate move. Same nudge, deadline, compactness checks and
        # global fallback; the final aim/pre-click guard still decides.
        found = await self._locate_after_nudge(before,sent,hint or (0.5,0.5),shift,anywhere=hint is None,**({"timing_prefix":"reacquire"} if known_span else {}))
        await self._send(f"move -{NUDGE} 0")
        return found if found and 0.03 < found[0] < 0.97 and 0.03 < found[1] < 0.97 else None

    async def _locate_after_nudge(self, before, sent, near, shift, anywhere=False, timing_prefix="aim", bounds=None, local_only=False):
        metrics=getattr(self,'_active_point_timing',None)
        started=time.monotonic()
        deadline = started + POINTER_WAIT
        margin = .025 if (self.target_info() or {}).get("device") == "ipad" else .05
        probe={'near':list(near),'shift':shift,'bounds':list(bounds) if bounds is not None else None,
               'anywhere':anywhere,'fresh_frames':0,'outcome':'started'}
        self.last_pointer_probe=probe
        # This explicitly scoped native X account toolbar has magnetized hover
        # transitions. Require two consistent NEW frame candidates before
        # learning/correcting toward it; same original shape and aim thresholds.
        confirm_twice=((self.target_info() or {}).get('device')=='ipad' and bounds is not None and tuple(bounds)==(0,.025,1,.11))
        candidate=None;consistent=0
        try:
            while before and time.monotonic() < deadline:
                with _timed(metrics,timing_prefix+'_frame_wait_s'):after = await self.screen.fresh(sent,min(.35,max(0,deadline-time.monotonic())))
                sent = time.time()
                if after:
                    probe['fresh_frames']+=1
                    with _timed(metrics,timing_prefix+'_cv_s'):
                        # Keep only numeric rejection diagnostics for the latest
                        # pair. No pixels/text, no extra probe or looser matcher.
                        latest={}
                        def detect(label,center,**kwargs):
                            detail={}
                            observed_bounds,observed_margin=pointer_observation_roi(label,bounds,margin,self.target_info() or {},os.environ.get('HID_POINT_EDGE_PROBE','0')=='1')
                            observation={"bounds":observed_bounds,"ignore_caret":(self.target_info() or {}).get('device')=='ipad'} if bounds is not None else {}
                            # Preserve the original broad compact-change search:
                            # Home pointers cross textured icons and need not be
                            # uniform disks. Finding a miss enables _aim's bounded
                            # correction; it never bypasses its rejection gate.
                            value=vision.pointer(before,after,center,shift,top_margin=observed_margin,
                                diagnostics=detail,**kwargs,**observation)
                            if (value is None and (self.target_info() or {}).get('device')=='ipad'
                                    and (self.target_info() or {}).get('orientation')=='landscape'):
                                # Low-contrast disks on animated pages retain
                                # signed translation proof as a fallback. Its
                                # small near ROI supplements the broad search;
                                # the global fallback still covers the frame.
                                compact=dict(detail)
                                fallback=dict(kwargs)
                                if label=='near':fallback['reach']=(.04,.04)
                                value=vision.pointer(before,after,center,shift,top_margin=observed_margin,
                                    diagnostics=detail,**fallback,**observation,faint_motion=True)
                                detail['compact_search']=compact
                            if observed_bounds!=bounds:detail['observation_bounds']=list(observed_bounds)
                            # Unknown Home position needs a compact fully
                            # observed round-pointer change. Tiny transition
                            # specks and whole hovered icons are not a position
                            # estimate for a long move. Keep waiting within the
                            # same existing probe deadline, then use the original
                            # calibrated corner if no candidate is trustworthy.
                            if value and timing_prefix=='reacquire':
                                span=detail.get('change_span',[])
                                if len(span)!=2 or detail.get('changed_pixels',0)<80 or max(span)>40:
                                    detail['outcome']='ambiguous_home_candidate';value=None
                            if (value and label=='global' and not anywhere
                                    and (self._pos is not None or ((self.target_info() or {}).get('device')=='ipad'
                                         and os.environ.get('HID_POINT_LOCAL_FIRST','0')=='1'))
                                    and max(abs(value[i]-near[i]) for i in (0,1))>AIM_REJECT):
                                detail['outcome']='unrelated_far_global_candidate';value=None
                            latest[label]=detail
                            probe['cv_checks']=probe.get('cv_checks',0)+1
                            return value
                        found=None if anywhere else detect('near',near,**({'reach':(.04,.04),'confirm_motion':True} if local_only else {}))
                        if not found and not local_only:
                            found=detect('global',(0.5,0.5),reach=(0.5,0.5))
                        probe['cv_latest']=latest
                    if found:
                        if confirm_twice:
                            consistent=consistent+1 if candidate is not None and max(abs(found[i]-candidate[i]) for i in (0,1))<=.003 else 1
                            candidate=found;probe['candidate_confirmation_frames']=consistent
                            if consistent<2:
                                await asyncio.sleep(.04)
                                continue
                        probe['outcome']='found'
                        return found
                    elif confirm_twice:candidate=None;consistent=0
                await asyncio.sleep(.04)
            probe['outcome']='no_fresh_frame' if not probe['fresh_frames'] else 'pointer_not_confirmed'
            return None
        except asyncio.CancelledError:
            probe['outcome']='stopped'
            raise
        except Exception:
            probe['outcome']='failed'
            raise
        finally:
            probe['seconds']=round(time.monotonic()-started,4)
            if metrics is not None:
                metrics[timing_prefix+'_probe_fresh_frames']=metrics.get(timing_prefix+'_probe_fresh_frames',0)+probe['fresh_frames']
                metrics[timing_prefix+'_last_probe_outcome']=probe['outcome']
                metrics[timing_prefix+'_cv_checks']=probe.get('cv_checks',0)
                metrics[timing_prefix+'_cv_latest']=probe.get('cv_latest',{})

    def finish_calibration(self, x: float, y: float) -> None:
        """x, y: where the pointer ended up after start_calibration() (one point, picked by the user)."""
        self._verified_home_probe_hint=None
        self._sync_profile()
        if self.profiles is not None and self._calibration_key != self._profile_key:
            raise RuntimeError("device or orientation changed; start calibration again")
        anchor = self._home_fraction()
        self.span = {"w": round(CALIBRATION_MOVE / abs(x - anchor)), "h": round(CALIBRATION_MOVE / abs(y - anchor))}
        for samples in self._seen.values():
            samples.clear()
        self._save()

    def _save(self) -> None:
        """Calibration plus the observed landings, so the correction survives a restart."""
        if self.profiles is not None and self.target_info()["calibration_key"] != self._profile_key:
            self._sync_profile()
            raise RuntimeError("device or orientation changed; calibrate this screen first")
        pace=self._pointer_ms()
        if self.profiles is not None and pace!=self._profile_pace:
            self._sync_profile()
            raise RuntimeError('pointer cadence changed; calibrate this cadence first')
        self._file.write_text(json.dumps({**self.span, "axes": self.axes,
            **({'motion_ms':pace} if pace is not None else {}),
            "seen": {k: list(v) for k, v in self._seen.items()}}))

    def _home_probe_binding(self):
        target=self.target_info() or {}
        if (target.get('device')!='ipad' or target.get('orientation')!='portrait'
                or not target.get('calibration_key') or not target.get('width') or not target.get('height')
                or not self.span or not self.screen or not self.screen.live
                or not getattr(self.device,'connected',False)):
            return None
        return (target['device'],target['orientation'],target['calibration_key'],target['width'],target['height'],
            self.device.links,self._profile_key,self._calibration_key,self.span['w'],self.span['h'],self.axes['x'],self.axes['y'])

    def _remember_verified_home_pointer(self,check):
        # Called only after HybridAgent's exact Home target+fresh pointer proof
        # have passed immediately before click. Revoked tasks cannot retain hints.
        if os.environ.get('IPAD_HOME_PROBE_HINT','0')!='1':return
        check_hid('move')
        binding=self._home_probe_binding()
        point=check.get('observed') if isinstance(check,dict) else None
        error=check.get('error',1) if isinstance(check,dict) else 1
        if (binding is None or check is not getattr(self,'last_pointer_check',None)
                or isinstance(error,bool) or not isinstance(error,(int,float)) or not 0<=error<=.02 or not isinstance(point,(list,tuple)) or len(point)!=2
                or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not .05<v<.95 for v in point)):
            return
        self._verified_home_probe_hint={'point':tuple(point),'binding':binding,'at':time.monotonic()}

    def _eligible_home_probe_hint(self):
        if os.environ.get('IPAD_HOME_PROBE_HINT','0')!='1':return None
        hint=getattr(self,'_verified_home_probe_hint',None)
        if (not hint or self._home_probe_binding()!=hint['binding'] or not 0<=time.monotonic()-hint['at']<=120):
            self._verified_home_probe_hint=None
            return None
        return hint['point']

    def forget_position(self, *, keep_home_hint=False) -> None:
        """Discard commanded position; only system Home can retain an observation hint."""
        if not keep_home_hint:
            self._home_local_hint = self._converged_position_hint = None
        elif self._pos is not None:
            hint = self._converged_position_hint
            self._home_local_hint = (hint if self._local_hint_eligible(hint)
                                     and hint['command_position'] == self._pos else None)
        self._pos = None
        if not keep_home_hint:self._verified_home_probe_hint=None

    def _local_hint_eligible(self, hint):
        info = self.target_info() or {}
        return (os.environ.get('POINTER_HOME_LOCAL_REACQUIRE', '0') == '1'
                and hint is not None and info.get('device') == 'ipad'
                and info.get('orientation') in ('portrait', 'landscape')
                and bool(info.get('calibration_key'))
                and bool(info.get('width')) and bool(info.get('height'))
                and self.screen is not None and self.screen.live
                and getattr(self.device, 'connected', False)
                and hint['binding'] == self._learning_binding()
                and 0 <= time.monotonic()-hint['at'] <= 120)

    def _remember_converged_position(self, point):
        # A hint is not a commanded position. Home/launch must prove it anew.
        self._converged_position_hint = None
        if (os.environ.get('POINTER_HOME_LOCAL_REACQUIRE', '0') != '1'
                or self._pos is None or not all(.05 < v < .95 for v in point)):
            return
        hint = {'point': tuple(point), 'command_position': self._pos,
                'binding': self._learning_binding(), 'at': time.monotonic()}
        if self._local_hint_eligible(hint):
            self._converged_position_hint = hint

    async def _reacquire_home_local(self, hint):
        """One bounded local motion proof. Never trust an old app coordinate."""
        metrics = getattr(self, '_active_point_timing', None)
        def bound():
            check_hid('move')
            if not self._local_hint_eligible(hint):
                raise PhoneUnavailable('Pointer binding expired during Home local probe; no click sent')
        bound()
        await self.screen.settled(quiet=.2, least=.3, most=.6)
        bound()
        before, sent = self.screen.frame, time.time()
        await self._send_pointer_probe(f'move {NUDGE} 0')
        # Cancellation/uncertain delivery propagates without further input.
        found = await self._locate_after_nudge(before, sent, hint['point'],
            NUDGE/self.span['w'], timing_prefix='home_local', local_only=True)
        bound()
        await self._send(f'move {-NUDGE} 0')
        bound()
        if found and max(abs(found[i]-hint['point'][i]) for i in (0, 1)) <= .02:
            # Use the existing calibrated coordinate conversion, unchanged.
            self._pos = (self._dx(found[0]), self._dy(found[1]))
            self._moves = 0
            if metrics is not None:metrics['position_source'] = 'home_local_observation'
        elif metrics is not None:
            metrics['home_local_outcome'] = 'not_confirmed_use_existing_path'

    def _home_fraction(self) -> int:
        # Repeated motion beyond the top edge opens iPad Notification Center.
        # Anchor at bottom-right instead; iPhone keeps its original top-left anchor.
        return int(self.profiles is not None and (self.target_info()["device"] == "ipad" or
                   (self.target_info()["device"] == "iphone" and os.environ.get("IPHONE_POINTER_ANCHOR") == "bottom-right")))

    def _home_position(self) -> tuple:
        anchor = self._home_fraction()
        return (anchor * self.span["w"], anchor * self.span["h"]) if self.span else (0, 0)

    async def _send(self, command: str) -> None:
        """Map screen directions to HID axes; old iPhone calibrations default to +1."""
        check_hid(command)
        fields = command.split()
        pace=self._pointer_ms()
        if pace is not None and fields[0] in ('home','move','press','click','scroll'):
            setting=(self.device.links,pace)
            if self._pointer_tuned_link!=setting:
                await self.device.send(f'tune 10 {pace}')
                self._pointer_tuned_link=setting
        if fields[0] == "home":
            direction = -1 if self._home_fraction() else 1
            signs = {k: direction * v for k, v in self.axes.items()}
            command = "home" if signs == {"x": 1, "y": 1} else f"home {signs['x']} {signs['y']}"
        elif fields[0] == "move":
            command = f"move {int(fields[1]) * self.axes['x']} {int(fields[2]) * self.axes['y']}"
        check_hid(command)
        await self.device.send(command)

    async def _send_pointer_probe(self, command, *, skipped=None):
        """Record probe delivery without changing its commands, pacing or queue gate.

        Unknown delivery is conservatively true, with its uncertainty in the trace,
        so a lost ACK can never be mistaken for proof of zero pointer input.
        """
        delivery = 'unknown'
        try:
            await self._send(command)
            delivery = 'not_sent' if skipped is not None and skipped() else 'acknowledged'
        except DeviceError as exc:
            delivery = exc.delivery
            raise
        finally:
            sent = delivery != 'not_sent'
            metrics = getattr(self, '_active_point_timing', None)
            if metrics is not None and sent:
                metrics['pointer_probe_sent'] = True
            timeline_event('pointer_probe', pointer_probe_sent=sent, delivery=delivery)

    async def ensure_keyboard_live(self) -> None:
        """Passive readiness only: a static, connected mirror permits keyboard input."""
        readiness = getattr(self.device, 'readiness', None)
        controller = readiness() if callable(readiness) else {}
        mirror_health = getattr(self.screen, 'mirror_health', None)
        mirror = mirror_health() if callable(mirror_health) else {}
        state = mirror.get('state', 'mirror_diagnostics_unavailable')
        stable = bool(getattr(self.device, 'connected', False)
                      and controller.get('connected') and controller.get('stable'))
        pings = 0
        # Stability means two OK acks in the last 15 s, so a quiet link (long model waits) ages out of it.
        # A ping refreshes that evidence without touching the pointer.
        while (not stable and pings < 2 and callable(readiness) and controller.get('connected')
               and controller.get('reason') == 'waiting_for_stable_acknowledgements'):
            pings += 1
            try:
                await self.device.send('ping')
            except DeviceError:
                break
            controller = readiness()
            stable = bool(getattr(self.device, 'connected', False)
                          and controller.get('connected') and controller.get('stable'))
        timeline_event('ensure_live', mode='keyboard', mirror_state=state,
                       controller_stable=stable, probed=False, pointer_probe_sent=False, pings=pings)
        if state not in ('session_connected', 'session_connected_no_recent_frame'):
            name = getattr(self.screen, 'name', 'iPad Agent')
            raise PhoneUnavailable(f'Keyboard input refused: mirror state {state}; re-select {name} in Screen Mirroring; no input sent')
        if not stable:
            reason = controller.get('reason', 'controller_readiness_unavailable')
            raise PhoneUnavailable(f'Keyboard input refused: controller is not connected and stable ({reason}); no input sent')

    @contextmanager
    def _keyboard_action(self, kind, metrics=None):
        metrics = {} if metrics is None else metrics
        metrics.update(pointer_probe_sent=False, outcome='started')
        token = self._start_action_timing(kind, metrics)
        started = time.perf_counter()
        try:
            yield metrics
            if metrics['outcome'] == 'started':
                metrics['outcome'] = 'ok'
        except BaseException as exc:
            metrics['outcome'] = 'stopped' if isinstance(exc, asyncio.CancelledError) else 'failed'
            raise
        finally:
            metrics['total_s'] = round(time.perf_counter()-started, 4)
            self._finish_action_timing(metrics, token)

    async def ensure_live(self, force_probe: bool = False) -> None:
        """Before acting on a screen that has been still for a while, wiggle the pointer and wait for the mirror to
        show it, so a frozen mirror or a mouse the phone ignores is reported instead of tapped into blindly.
        (A locked phone usually shows up earlier, as the board's "phone usb asleep" error.)"""
        updated = getattr(self.screen, 'updated', None)
        health = getattr(self.screen, '_receiver_health', None) or {}
        age = time.time() - updated if updated is not None else None
        probed = age is not None and (force_probe or age >= STALE_AFTER)
        timeline_event('ensure_live', frame_age_s=age, probed=probed, pointer_probe_sent=False,
                       reason='no_screen' if age is None else 'forced' if force_probe else 'stale' if probed else 'fresh',
                       source_epoch=health.get('source_epoch'),
                       source_session_connected=health.get('peer_connected'),
                       receiver_stream_connected=getattr(self.screen, '_sock', None) is not None,
                       heartbeat_age_s=time.monotonic()-self.screen._receiver_health_at if health else None)
        if self.screen is None or updated is None:
            return
        if not force_probe and time.time() - self.screen.updated < STALE_AFTER:
            return
        near_right = self._pos is not None and self._pos[0] > 0.9 * self.span["w"]
        nudge = -NUDGE if near_right else NUDGE  # iOS stops the pointer at the edge, and the move back would not
        sent = time.time()
        skipped = False
        def recheck():
            nonlocal skipped
            skipped = time.time() - self.screen.updated < STALE_AFTER
            timeline_event('probe_recheck', frame_age_s=time.time()-self.screen.updated,
                           probed=not skipped, reason='fresh_at_device_lock' if skipped else 'still_stale')
            return skipped
        if os.environ.get('INPUT_PROBE_RECHECK', '0') == '1' and not force_probe:
            with probe_recheck(recheck):
                await self._send_pointer_probe(f"move {nudge} 0", skipped=lambda: skipped)
            if skipped:
                return
        else:
            await self._send_pointer_probe(f"move {nudge} 0")
        timeline_event('probe_fresh_start', after_frame_time=sent, timeout_s=PROBE_WAIT)
        moved = None
        try:
            moved = await self.screen.fresh(sent, PROBE_WAIT)  # before moving back: a move undone at once may never show
        finally:
            timeline_event('probe_fresh_end', accepted=bool(moved),
                           accepted_frame_time=self.screen.updated if moved else None)
        await self._send(f"move {-nudge} 0")
        if moved:
            return
        name = getattr(self.screen, "name", "iPhone Agent")
        if not self.screen.updated:  # no frame ever: nothing is stuck, mirroring was just never started
            raise PhoneUnavailable(f"the phone is not mirrored: choose Control Center › Screen Mirroring › {name}")
        if time.time() - self.screen.updated > FROZEN_AFTER:
            if has_input_lease() or os.environ.get('MIRROR_AUTO_RESTART','1')!='1':
                log.warning('mirror unavailable: frame_age=%.3f device_connected=%s target=%s receiver_left_unchanged=true',time.time()-self.screen.updated,getattr(getattr(self,'device',None),'connected',None),self.target_info() if getattr(self,'profiles',None) is not None else None)
                raise PhoneUnavailable("No fresh mirrored frame after the pointer probe; receiver left unchanged. Inspect AirPlay and decoder diagnostics before recovery.")
            self.screen.restart()
            raise PhoneUnavailable("the mirrored screen stopped updating, so the AirPlay receiver was restarted: "
                                   "unlock the phone if it is locked, then choose Control Center › Screen Mirroring › "
                                   f"{name} again")
        raise PhoneUnavailable("the pointer did not move on the mirrored screen: unlock the phone if it is locked; "
                               "if it is unlocked, it is ignoring the board's mouse, so unplug the board's adapter "
                               "from the phone and plug it back in")

    def _start_action_timing(self,kind,metrics):
        # Content-free action events, bounded and in-memory. A caller's
        # prepare-only point survives the later tap overwriting last_point.
        if not hasattr(self,'action_timings'):
            self.action_timings=deque(maxlen=128);self._action_timing_sequence=0
        self._action_timing_sequence+=1
        parent=_action_timing_parent.get()
        metrics.update(action_id=self._action_timing_sequence,
            parent_action_id=parent[1] if parent and parent[0] is self else None,
            kind=kind,started_monotonic_s=time.perf_counter())
        return _action_timing_parent.set((self,metrics['action_id']))

    def _finish_action_timing(self,metrics,token):
        try:
            metrics['finished_monotonic_s']=time.perf_counter()
            self.action_timings.append(deepcopy(metrics))
        finally:_action_timing_parent.reset(token)

    async def point(self, x: float, y: float, aim: bool = False, aim_tolerance: float = AIM_TOLERANCE, reacquire: bool = False, aim_region=None) -> None:
        metrics={'target':[x,y],'aim_requested':aim,'outcome':'started','pointer_probe_sent':False}
        self.last_point_timing=metrics
        timing_token=self._start_action_timing('point',metrics)
        previous=getattr(self,'_active_point_timing',None)
        self._active_point_timing=metrics
        started=time.perf_counter()
        try:
            self._sync_profile()
            if not self.calibrated:raise RuntimeError('pointer not calibrated')
            calibrated_binding = self._calibrated_tap_binding()
            with _timed(metrics,'live_check_s'):await self.ensure_live()
            if ((self.target_info() or {}).get('device')=='ipad'
                    and self._link==getattr(self.device,'links',self._link)):
                # Free point only; held drag segments remain unchanged.
                with _timed(metrics,'free_release_s'):await self._send('release')
            with _timed(metrics,'position_move_s'):
                if reacquire:await self._move_to(self._dx(x),self._dy(y),reacquire=True)
                else:await self._move_to(self._dx(x),self._dy(y))
            if aim and self.screen is not None:
                try:
                    with _timed(metrics,'aim_s'):await self._aim(x,y,tolerance=aim_tolerance,**({"bounds":aim_region} if aim_region is not None else {}))
                except PhoneUnavailable as exc:
                    # A single global Home probe can mistake a small animated
                    # widget for the pointer. Only the verified Home-icon caller
                    # can recover that observed-position proposal, once, before
                    # any click. Re-establish the original calibrated corner
                    # and require the unchanged aim and final icon guards again.
                    if not (reacquire and metrics.get('position_source')=='observed_pointer'
                            and (self.target_info() or {}).get('device')=='ipad'
                            and 'pointer is too far' in str(exc)):
                        raise
                    if self._sync_profile():raise PhoneUnavailable('Geometry changed during Home recovery; no click sent')
                    self._pos=None
                    metrics['home_reacquire_recovery']='rejected_global_candidate_use_corner_once'
                    with _timed(metrics,'recovery_position_s'):await self._move_to(self._dx(x),self._dy(y),reacquire=False)
                    with _timed(metrics,'recovery_aim_s'):await self._aim(x,y,tolerance=aim_tolerance,**({"bounds":aim_region} if aim_region is not None else {}))
            metrics['outcome']='ok'
        except BaseException as exc:
            if isinstance(exc, _PointerUnconfirmed):exc.binding = calibrated_binding
            metrics['outcome']='stopped' if isinstance(exc,asyncio.CancelledError) else 'failed'
            raise
        finally:
            metrics['total_s']=round(time.perf_counter()-started,4)
            self._active_point_timing=previous
            self._finish_action_timing(metrics,timing_token)

    async def _escape_pointer_probe(self,x,y,nudge,bounds,link,binding):
        """Measure two visible outside positions when a hover hides the pointer.

        Observation only: retain the original detector, ROI, aim tolerance and
        final click guard. Never learn this extrapolated position as calibration.
        """
        metrics=getattr(self,'_active_point_timing',None)
        escape=max(36,math.ceil(.045*self.span['w']))*(1 if nudge>0 else -1)
        if abs(escape)>100 or not .01<x+(escape+nudge)/self.span['w']<.99:return None
        def bound():
            check_hid('move')
            if (getattr(self.device,'links',None)!=link or tuple((self.target_info() or {}).get(k) for k in ('device','orientation','calibration_key','width','height'))!=binding):
                raise PhoneUnavailable('Pointer binding changed during escape probe; no click sent')
        moved=0;cancelled=False;uncertain=False
        if metrics is not None:
            metrics['aim_escape_attempts']=metrics.get('aim_escape_attempts',0)+1
            metrics['aim_escape_units']=abs(escape)
        try:
            bound();sent=time.time();await self._send_pointer_probe(f'move {escape} 0');moved+=escape
            outside=await self.screen.fresh(sent,PROBE_WAIT);bound()
            if not outside:return None
            sent=time.time();await self._send_pointer_probe(f'move {nudge} 0');moved+=nudge
            found=await self._locate_after_nudge(outside,sent,(x+escape/self.span['w'],y),nudge/self.span['w'],**({'bounds':bounds} if bounds is not None else {}))
            bound()
            if not found:return None
            return found[0]-escape/self.span['w'],found[1]
        except BaseException as exc:
            self._pos=None
            cancelled=isinstance(exc,asyncio.CancelledError)
            uncertain=isinstance(exc,DeviceError)
            raise
        finally:
            # A cancelled lease/link or unknown send must not dispatch an
            # inverse into a new task/device. Its next task must reacquire.
            same=(getattr(self.device,'links',None)==link and tuple((self.target_info() or {}).get(k) for k in ('device','orientation','calibration_key','width','height'))==binding)
            if moved and not cancelled and not uncertain and same:
                try:
                    bound();await self._send(f'move {-moved} 0')
                except BaseException:
                    self._pos=None
                    raise

    async def _aim(self, x: float, y: float, _attempt: int = 0, tolerance: float = AIM_TOLERANCE, bounds=None, _previous_correction=None) -> None:
        """Closed loop: find the real pointer on screen, fix a miss, and learn the error for later moves."""
        metrics=getattr(self,'_active_point_timing',None)
        learning_tx=self._point_learning_tx.get()
        if learning_tx is not None and 'legacy_seen' in learning_tx and (not learning_tx['active'] or learning_tx['owner'] is not asyncio.current_task() or learning_tx['legacy_binding']!=self._learning_binding()):
            raise PhoneUnavailable('Legacy learning scope expired before probe')
        control=getattr(self,'_verified_control_tx',None)
        control=control.get() if control is not None else None
        if control is not None and (not control['active'] or control['owner'] is not asyncio.current_task() or control['target']!=(x,y) or control['binding']!=self._learning_binding()):
            raise PhoneUnavailable('Verified control binding changed; no click sent')
        learning_v2=(os.environ.get('HID_POINT_LEARNING_V2','0')=='1' and (self.target_info() or {}).get('device')=='ipad')
        info = self.target_info() or {}
        edge_correction = (os.environ.get('POINTER_EDGE_CORRECTION', '0') == '1'
                           and info.get('device') == 'ipad' and info.get('orientation') == 'landscape'
                           and (x <= .1 or x >= .8 or y <= .1 or y >= .85))
        edge_binding = self._learning_binding() if edge_correction else None
        def check_edge_binding():
            if edge_correction and edge_binding != self._learning_binding():
                self._pos = None
                raise PhoneUnavailable('Pointer binding changed during edge probe; no click sent')
        # A gain estimate belongs only to this recursive aim and its binding.
        # It must never survive a link, geometry, span or axis change.
        if edge_correction and _previous_correction is not None and _previous_correction['binding'] != self._learning_binding():
            self._pos = None
            raise PhoneUnavailable('Pointer binding changed during edge correction; no click sent')
        # Capture the ACTUAL acknowledged command coordinate before probing.
        # _fix(target) may change during recursion and is not what was sent.
        command_position=self._pos
        command_link=getattr(self.device,'links',None)
        command_binding=tuple((self.target_info() or {}).get(k) for k in ('device','orientation','calibration_key','width','height'))
        if metrics is not None:metrics['aim_attempts']=metrics.get('aim_attempts',0)+1
        nudge = NUDGE if x < 0.9 else -NUDGE  # stay clear of the screen edge
        with _timed(metrics,'aim_settle_s'):await self.screen.settled(quiet=0.15, least=0.1, most=0.6)  # pointer at rest
        check_edge_binding()
        shift = nudge / self.span["w"]
        probe_before=getattr(self,'last_pointer_probe',None)
        reobserve=(info.get('device')=='ipad' and info.get('orientation')=='landscape')
        observation_binding=self._learning_binding() if reobserve else None
        def check_observation_binding():
            check_edge_binding()
            if reobserve and observation_binding!=self._learning_binding():
                self._pos=None
                raise PhoneUnavailable('Pointer binding changed during re-observation; no click sent')
        for observation in range(2):
            check_observation_binding()
            before, sent = self.screen.frame, time.time()
            with _timed(metrics,'aim_probe_send_s'):await self._send_pointer_probe(f"move {nudge} 0")
            with _timed(metrics,'aim_locate_s'):found = await self._locate_after_nudge(before, sent, (x, y), shift,**({"bounds":bounds} if bounds is not None else {}))
            check_observation_binding()
            restored_at=time.time()
            with _timed(metrics,'aim_probe_restore_s'):await self._send(f"move {-nudge} 0")
            check_observation_binding()
            probe=getattr(self,'last_pointer_probe',None)
            already_reobserved=metrics.get('aim_reobservations',0) if metrics is not None else _attempt
            if (found or not reobserve or observation or already_reobserved
                    or probe is probe_before or not probe
                    or probe.get('outcome') not in ('pointer_not_confirmed','no_fresh_frame')):
                break
            # settled() can return with the pre-move picture on a change-only
            # AirPlay stream. Rechecking that same baseline cannot fix it.
            # Restore first, require a new frame, then take ONE new probe pair
            # with exactly the original detector, ROI and acceptance guards.
            with _timed(metrics,'aim_reobserve_wait_s'):
                restored=await self.screen.fresh(restored_at,PROBE_WAIT)
            check_observation_binding()
            if not restored:break
            with _timed(metrics,'aim_settle_s'):
                await self.screen.settled(quiet=.15,least=.15,most=.6)
            if metrics is not None:metrics['aim_reobservations']=1
        escape_used=False
        probe=getattr(self,'last_pointer_probe',None)
        can_escape=(metrics is not None and metrics.get('aim_escape_attempts',0)==0) or (metrics is None and _attempt==0)
        if (not found and can_escape and (self.target_info() or {}).get('device')=='ipad'
                and (self.target_info() or {}).get('orientation')=='portrait'
                and probe is not probe_before and probe and probe.get('fresh_frames',0)>0
                and probe.get('outcome')=='pointer_not_confirmed' and probe.get('cv_latest')
                and all(v.get('outcome')=='too_few_changes' for v in probe['cv_latest'].values())):
            found=await self._escape_pointer_probe(x,y,nudge,bounds,command_link,command_binding)
            escape_used=found is not None
            if metrics is not None:metrics['aim_escape_found']=escape_used
        if not found:
            log.debug("aim (%.3f, %.3f): pointer not found (%s)", x, y,
                      "no pointer-sized change appeared before the deadline")
            self._pos = None
            probe=getattr(self,'last_pointer_probe',None)
            if probe is not probe_before and probe and probe.get('outcome')=='no_fresh_frame':
                # Static apps may hide the pointer and emit no AirPlay change.
                # tap() still requires the original live, calibrated binding.
                raise _PointerUnconfirmed("no fresh mirror frame after pointer probe; no click sent")
            raise _PointerUnconfirmed("pointer could not be visually confirmed; no click sent")
        fx, fy = found
        edge_round = None
        if edge_correction and metrics is not None:
            edge_round = {'attempt': _attempt, 'observed': [fx, fy], 'error': [fx-x, fy-y]}
            metrics.setdefault('aim_edge_rounds', []).append(edge_round)
        self.last_pointer_check={'target':[x,y],'observed':[fx,fy],'attempt':_attempt,'error':max(abs(fx-x),abs(fy-y)),'probe_restored_at':time.time()}
        log.debug("aim (%.3f, %.3f): pointer at (%.3f, %.3f)", x, y, fx, fy)
        if max(abs(fx - x), abs(fy - y)) > AIM_REJECT:  # something else moved; correcting toward it would mis-tap
            log.info("aim (%.3f, %.3f): rejecting pointer at (%.3f, %.3f), too far off", x, y, fx, fy)
            self._pos = None
            raise PhoneUnavailable("pointer is too far from the target; recalibrate before retrying")
        if control is None and not learning_v2 and not escape_used and max(abs(fx - x), abs(fy - y)) <= MAX_MISS:  # legacy learning for opt-out
            tx=self._point_learning_tx.get()
            staged=tx is not None and 'legacy_seen' in tx
            if staged and (not tx['active'] or tx['owner'] is not asyncio.current_task() or tx['legacy_binding']!=self._learning_binding()):
                raise PhoneUnavailable('Legacy learning task/binding changed; no sample committed')
            self._seen["x"].append((self._fix(x, "x"), fx))
            self._seen["y"].append((self._fix(y, "y"), fy))
            if staged:tx['legacy_dirty']=True
            else:self._save()
        if control is not None:
            if control['binding']!=self._learning_binding():raise PhoneUnavailable('Verified control binding changed during observation')
            if not escape_used and control_interior_contains(control['box'],(fx,fy),self.target_info()):
                self.last_pointer_check.update(control_hit_confirmed=True,control_box=control['box'])
                if metrics is not None:metrics['aim_acceptance']='verified_control_interior';metrics['learning_suppressed']='control_interior_is_not_center_calibration'
                return
        if control is None and abs(fx - x) <= tolerance and abs(fy - y) <= tolerance:
            if learning_v2 and not escape_used and command_position is not None:
                check_hid('move')
                if (getattr(self.device,'links',None)!=command_link or tuple((self.target_info() or {}).get(k) for k in ('device','orientation','calibration_key','width','height'))!=command_binding):
                    self._pos=None
                    raise PhoneUnavailable('Pointer binding changed before learning; no click sent')
                command=(command_position[0]/self.span['w'],command_position[1]/self.span['h'])
                if all(math.isfinite(v) and 0<=v<=1 for v in command):
                    tx=self._point_learning_tx.get()
                    if tx is None:
                        self._seen['x'].append((command[0],fx));self._seen['y'].append((command[1],fy))
                        self._save()
                        if metrics is not None:metrics['learning_source']='converged_actual_command'
                    elif tx['active'] and tx['owner'] is asyncio.current_task():
                        tx['pending']=(command,(fx,fy),self._learning_binding())
                        if metrics is not None:metrics['learning_source']='staged_converged_actual_command'
                # Strict live pointer proof refreshes drift confidence. Periodic
                # corner reset still applies to runs of UNVERIFIED moves.
                self._moves=0
            if not escape_used:
                self._remember_converged_position((fx, fy))
            return
        if _attempt>=2:
            self._pos=None
            raise _PointerUnconfirmed('Pointer correction did not converge; no click sent')
        dx, dy = round((x - fx) * self.span["w"]), round((y - fy) * self.span["h"])  # fix this miss right away
        next_correction = None
        if edge_correction:
            correction = [dx, dy]
            gains = [1., 1.]
            if _previous_correction is not None:
                for i, (target, observed, span) in enumerate(((x, fx, self.span['w']), (y, fy, self.span['h']))):
                    previous = _previous_correction['observed'][i]
                    units = _previous_correction['units'][i]
                    # Only a measured sign reversal OUTSIDE the unchanged hit
                    # tolerance supports reducing this axis's next correction.
                    # Reject implausible gains and sub-tolerance noisy samples.
                    if (units and (target-previous)*(target-observed) < 0
                            and min(abs(target-previous), abs(target-observed)) > tolerance):
                        gain = (observed-previous)/(units/span)
                        if math.isfinite(gain) and 1.25 <= gain <= 3:
                            gains[i] = gain
                            correction[i] = round((target-observed)*span/gain)
            dx, dy = correction
            if edge_round is not None:
                edge_round.update(gain=gains, correction_units=correction)
            next_correction = {'observed': (fx, fy), 'units': (dx, dy), 'binding': self._learning_binding()}
        with _timed(metrics,'aim_correction_move_s'):await self._move_xy(dx, dy)
        self._pos = (self._pos[0] + dx, self._pos[1] + dy)
        # Verify the corrected landing, rather than trusting an unobserved final move.
        if (self.target_info() or {}).get('device')=='ipad':
            await self._aim(x,y,_attempt+1,tolerance=tolerance,**({"bounds":bounds} if bounds is not None else {}),
                            **({'_previous_correction': next_correction} if edge_correction else {}))

    def _learning_binding(self):
        info=self.target_info() or {}
        return (getattr(self.device,'links',None),str(self._file),
                tuple(info.get(k) for k in ('device','orientation','calibration_key','width','height')),
                tuple((self.span or {}).get(k) for k in ('w','h')),
                tuple(self.axes.get(k) for k in ('x','y')))

    def _commit_pointer_learning(self,tx):
        if not tx['active'] or tx['owner'] is not asyncio.current_task():return False
        if 'legacy_seen' in tx:
            if not tx['legacy_dirty']:return False
            check_hid('move')
            if tx['legacy_binding']!=self._learning_binding():raise PhoneUnavailable('Legacy learning binding changed before commit')
            self._save();tx['legacy_dirty']=False
            return True
        sample=tx['pending']
        if sample is None:return False
        command,observed,binding=sample
        check_hid('move')
        if self._learning_binding()!=binding:
            raise PhoneUnavailable('Pointer binding changed before guarded learning commit')
        previous={k:deque(v,maxlen=v.maxlen) for k,v in self._seen.items()}
        try:
            for i,axis in enumerate(('x','y')):self._seen[axis].append((command[i],observed[i]))
            self._save()
        except BaseException:
            self._seen=previous
            raise
        tx['pending']=None
        return True

    def _fix(self, t: float, axis: str) -> float:
        """The fraction to aim at so the pointer lands on t, from the line through the observed landings."""
        pts = self._seen[axis]
        if len(pts) < 2:
            return t
        n = len(pts)
        mc, mf = sum(c for c, _ in pts) / n, sum(f for _, f in pts) / n
        var = sum((c - mc) ** 2 for c, _ in pts)
        a = sum((c - mc) * (f - mf) for c, f in pts) / var if var > 1e-4 else 1.0  # landed = a * aimed + b
        a = a if 0.85 < a < 1.15 else 1.0
        b = mf - a * mc
        return (t - b) / a if abs(b) < 0.1 else t

    async def _move_to(self, tx: int, ty: int, rehome: bool = True, reacquire: bool = False) -> None:
        metrics=getattr(self,'_active_point_timing',None)
        if self._sync_profile():
            raise RuntimeError("device or orientation changed; retry after checking calibration")
        if self._link != self.device.links:  # new link: a dropped gesture may have left the button down
            with _timed(metrics,'position_release_s'):await self._send('release')
            self._pos, self._link = None, self.device.links
        hint = self._home_local_hint
        self._home_local_hint = None  # One attempt, never replay a pending hint.
        if self._pos is None and self._local_hint_eligible(hint):
            with _timed(metrics, 'position_home_local_s'):
                await self._reacquire_home_local(hint)
        if (self._pos is None and reacquire and os.environ.get('IPAD_VISUAL_REACQUIRE')=='1'
                and (self.target_info() or {}).get('device')=='ipad'
                and self.screen is not None and self.screen.live):
            # Only a locally verified Home-icon caller opts in. Reuse the
            # existing observed pointer probe; never invent a cached position.
            with _timed(metrics,'position_reacquire_s'):found=await self._find_pointer(known_span=True)
            if self._sync_profile():raise PhoneUnavailable('Geometry changed during pointer reacquisition; no click sent')
            if found and all(.05<v<.95 for v in found):
                self._pos=(self._dx(found[0]),self._dy(found[1]));self._moves=0
                if metrics is not None:metrics['position_source']='observed_pointer'
            elif metrics is not None:metrics['reacquire_outcome']='not_observed_use_corner'
        if self._pos is None or (rehome and self._moves >= REHOME_EVERY):
            if metrics is not None:metrics['rehome_reason']='unknown_position' if self._pos is None else 'periodic_drift_reset'
            with _timed(metrics,'position_rehome_s'):await self._send('home')
            self._pos, self._moves = self._home_position(), 0
        x, y = self._pos
        self._pos = None  # unknown until the move succeeds
        self._last_move = max(abs(tx - x) / self.span["w"], abs(ty - y) / self.span["h"])
        with _timed(metrics,'delta_move_s'):await self._move_xy(tx - x, ty - y, point_move=rehome)
        self._pos, self._moves = (tx, ty), self._moves + 1

    async def release_input(self) -> None:
        """Stop without moving/re-homing the pointer; next gesture re-establishes position."""
        self._pos = None
        self._home_local_hint = self._converged_position_hint = None
        self._verified_home_probe_hint=None
        await self._send("release")

    async def reset(self) -> None:
        """Let go of the button and re-home the pointer, whatever state a gesture left."""
        self._pos = None
        self._home_local_hint = self._converged_position_hint = None
        self._verified_home_probe_hint=None
        await self._send("release")
        await self._send("home")
        self._pos, self._moves = self._home_position(), 0

    def _calibrated_tap_binding(self):
        return (self._learning_binding(), self._profile_key, self._profile_pace, self._home_fraction())

    def _check_calibrated_tap(self, binding):
        """Passive checks only. A reconnect or geometry change never earns a retry."""
        if asyncio.current_task().cancelling():raise asyncio.CancelledError()
        check_hid('move')
        if self._sync_profile() or binding != self._calibrated_tap_binding():
            raise PhoneUnavailable('Calibrated tap binding changed; no click sent')
        target = self.target_info() or {}
        if (self.profiles is None or not self.calibrated or target.get('device') != 'ipad'
                or target.get('orientation') not in ('portrait', 'landscape')
                or not target.get('calibration_key') or target['calibration_key'] != self._profile_key
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v <= 0
                       for v in (target.get('width'), target.get('height'),
                                 (self.span or {}).get('w'), (self.span or {}).get('h')))
                or any(self.axes.get(k) not in (-1, 1) for k in ('x', 'y'))):
            raise PhoneUnavailable('Calibrated tap requires a matching iPad calibration; no click sent')
        readiness = getattr(self.device, 'readiness', None)
        state = readiness() if callable(readiness) else {}
        if (not getattr(self.device, 'connected', False) or not state.get('connected')
                or not state.get('stable') or state.get('link_generation') != self.device.links):
            raise PhoneUnavailable('Calibrated tap requires a stable board link; no click sent')
        health = getattr(self.screen, 'mirror_health', None)
        if (not self.screen or not self.screen.live or not self.screen.frame
                or callable(health) and health().get('state') not in
                ('session_connected', 'session_connected_no_recent_frame', 'legacy_stream')):
            raise PhoneUnavailable('Calibrated tap requires a live mirror; no click sent')

    async def _calibrated_tap_move(self, x, y, binding, metrics):
        """Fresh calibration anchor, then the unchanged span/learned-offset mapping."""
        self._check_calibrated_tap(binding)
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (x, y)):
            raise PhoneUnavailable('Calibrated tap target outside screen; no click sent')
        self._pos = None
        self._home_local_hint = self._converged_position_hint = None
        self._verified_home_probe_hint = None
        with _timed(metrics, 'fallback_rehome_s'):await self._send('home')
        metrics['rehome_used'] = True
        self._check_calibrated_tap(binding)
        self._pos, self._moves = self._home_position(), 0
        with _timed(metrics, 'fallback_move_s'):
            await self._move_to(self._dx(x), self._dy(y))
        self._check_calibrated_tap(binding)

    async def tap(self, x: float, y: float, aim: bool = False, aim_tolerance: float | None = None, pre_click=None, reacquire: bool = False, aim_region=None, verified_control_box=None) -> None:
        """Preserve the exact input sequence; record non-overlapping outer stages."""
        inherited_learning=self._point_learning_tx.get()
        if inherited_learning is not None and 'legacy_seen' in inherited_learning:
            raise PhoneUnavailable('Nested or inherited legacy learning tap is not authorized')
        control=None;control_token=None
        if verified_control_box is not None:
            if not aim or pre_click is None:raise PhoneUnavailable('Verified control requires precise observation and an independent context guard')
            box=validate_control_box(verified_control_box,(x,y),self.target_info() or {})
            control={'owner':asyncio.current_task(),'active':True,'box':box,'target':(x,y),'binding':self._learning_binding()}
            control_token=self._verified_control_tx.set(control)
        metrics={'target':[x,y],'outcome':'started','pointer_probe_sent':False}
        self.last_tap_timing=metrics
        timing_token=self._start_action_timing('tap',metrics)
        point_before=getattr(self,'last_point_timing',None)
        deferred=(os.environ.get('HID_POINT_LEARNING_TRANSACTION','0')=='1'
                  and os.environ.get('HID_POINT_LEARNING_V2','0')=='1'
                  and (self.target_info() or {}).get('device')=='ipad')
        legacy_deferred=(aim and os.environ.get('HID_POINT_LEARNING_V2','0')!='1'
                         and (self.target_info() or {}).get('device')=='ipad')
        tx={'owner':asyncio.current_task(),'active':True,'pending':None} if deferred or legacy_deferred else None
        if legacy_deferred:
            tx.update(legacy_seen={k:deque(v,maxlen=v.maxlen) for k,v in self._seen.items()},
                      legacy_seen_owner=self._seen,legacy_binding=self._learning_binding(),legacy_dirty=False)
        learning_token=self._point_learning_tx.set(tx) if tx is not None else None
        fallback_binding = None
        started=time.perf_counter()
        try:
            with _timed(metrics,'point_s'):
                kwargs={'reacquire':True} if reacquire else {}
                if aim_region is not None:kwargs['aim_region']=aim_region
                try:
                    if aim_tolerance is None:await self.point(x,y,aim,**kwargs)
                    else:await self.point(x,y,aim,aim_tolerance=aim_tolerance,**kwargs)
                except _PointerUnconfirmed as exc:
                    if (not aim or os.environ.get('POINTER_CALIBRATED_FALLBACK', '1') == '0'
                            or self.profiles is None or getattr(exc, 'binding', None) is None):raise
                    fallback_binding = exc.binding
                    # Failed correction samples cannot alter the verified mapping.
                    if legacy_deferred:
                        self._seen = tx['legacy_seen']
                        tx['legacy_dirty'] = False
                    if tx is not None:tx['pending'] = None
                    metrics.update(aim='calibrated_unconfirmed', rehome_used=False)
                    await self._calibrated_tap_move(x, y, fallback_binding, metrics)
            if aim and self.screen is not None and (self.target_info() or {}).get("device") == "ipad":
                # _aim restores its probe and may apply a final correction. BLE ACK
                # precedes UIKit cursor animation; wait AFTER these movements, not
                # only before probing, so click cannot hit the previous position.
                with _timed(metrics,'post_aim_settle_s'):await self.screen.settled(quiet=.2, least=.25, most=.8)
            if self._last_move > LONG_MOVE:  # let iOS finish moving the pointer, or the click can land on the way
                with _timed(metrics,'long_move_settle_s'):await asyncio.sleep(SETTLE_AFTER_LONG_MOVE)
            with _timed(metrics,'pre_click_check_s'):
                if fallback_binding is not None:self._check_calibrated_tap(fallback_binding)
                if legacy_deferred and tx['legacy_binding']!=self._learning_binding():raise PhoneUnavailable('Pointer binding changed before guarded click')
                if control is not None and control['binding']!=self._learning_binding():raise PhoneUnavailable('Verified control binding changed before click')
                if pre_click is not None and not pre_click():
                    raise PhoneUnavailable('Learned target context changed during pointer movement; no click sent')
            if legacy_deferred and tx['legacy_binding']!=self._learning_binding():raise PhoneUnavailable('Pointer binding changed in context guard')
            if control is not None and control['binding']!=self._learning_binding():raise PhoneUnavailable('Verified control binding changed in context guard')
            if fallback_binding is not None:
                self._check_calibrated_tap(fallback_binding)
                before_click, clicked_at = self.screen.frame, time.time()
            with _timed(metrics,'click_s'):await self._send('click')
            with _timed(metrics,'tap_gap_s'):await asyncio.sleep(TAP_GAP)
            if tx is not None:metrics['learning_sample_committed']=self._commit_pointer_learning(tx)
            if fallback_binding is not None:
                metrics.update(screen_changed=False, screen_observed=False)
                with _timed(metrics, 'fallback_observe_s'):
                    after = await self.screen.fresh(clicked_at, POINTER_WAIT)
                metrics['screen_observed'] = after is not None
                metrics['screen_changed'] = after is not None and vision.changed(before_click, after, ignore=((x, y),))
            metrics['outcome']='ok'
        except BaseException as exc:
            if legacy_deferred and self._seen is tx['legacy_seen_owner']:
                self._seen=tx['legacy_seen']
            if tx is not None or control is not None or fallback_binding is not None:self._pos=None  # rejected/uncertain context cannot seed the next move
            metrics['outcome']='stopped' if isinstance(exc,asyncio.CancelledError) else 'failed'
            raise
        finally:
            if fallback_binding is not None:
                log.info('calibrated tap fallback: target=(%.3f, %.3f) rehome_used=%s screen_changed=%s outcome=%s',
                         x, y, str(metrics.get('rehome_used', False)).lower(),
                         str(metrics.get('screen_changed', 'unknown')).lower(), metrics['outcome'])
            if control is not None:
                control['active']=False;self._verified_control_tx.reset(control_token)
            if tx is not None:
                tx['active']=False;tx['pending']=None
                self._point_learning_tx.reset(learning_token)
            if getattr(self,'last_point_timing',None) is not point_before:
                metrics['point']=self.last_point_timing.copy()
                metrics['pointer_probe_sent']=self.last_point_timing.get('pointer_probe_sent',False)
            metrics['total_s']=round(time.perf_counter()-started,4)
            self._finish_action_timing(metrics,timing_token)

    async def long_press(self, x: float, y: float, seconds: float = .8) -> None:
        if not .6<=seconds<=1.2:raise PhoneUnavailable('Long press outside bounded duration')
        await self.point(x,y,aim=True)
        if self.screen is not None and self.target_info().get('device')=='ipad':
            await self.screen.settled(quiet=.2,least=.25,most=.8)
        try:
            await self._send('press')
            await asyncio.sleep(seconds)
        finally:
            try:await self._send('release')
            except DeviceError:
                if not asyncio.current_task().cancelling():raise

    async def swipe(self, x1: float, y1: float, x2: float, y2: float, hold: bool = True) -> None:
        await self.drag([(x1, y1), (x2, y2)], hold)

    async def drag(self, points: list[tuple[float, float]], hold: bool = True) -> None:
        """Press at the first point, move through the rest, release: one continuous stroke.

        hold=True rests at the end first, so lists and pickers stop exactly there instead of flinging on.
        System gestures (home, back) need the flick, so they pass hold=False.
        """
        await self.point(*points[0])
        await self._send("press")
        try:
            for x, y in points[1:]:
                await self._move_to(self._dx(x), self._dy(y), rehome=False)  # never home mid-drag
            if hold:
                await asyncio.sleep(HOLD)
        finally:
            try:
                await self._send("release")
            except DeviceError:
                # Cancellation closes the old TCP link. Firmware releases input
                # when it reconnects; preserve cancellation rather than retrying.
                if not asyncio.current_task().cancelling():
                    raise

    async def scroll(self, x: float, y: float, lines: int) -> None:
        await self.point(x, y)
        await self._send(f"scroll {lines} 0")

    async def type(self, text: str, *, keyboard_ms: int | None = None) -> None:
        if keyboard_ms is not None and (type(keyboard_ms) is not int or not 20 <= keyboard_ms <= 100):
            raise ValueError('keyboard_ms must be an integer from20 to100')
        metrics = self.last_typing_timing = {'chars': len(text), 'chunks': 0, 'chunk_lengths': [], 'config_resend': False}
        with self._keyboard_action('type', metrics):
            try:
                if keyboard_ms is None:await self._type(text)
                else:await self._type(text,keyboard_ms=keyboard_ms)
            except DeviceError as error:
                # Readiness or typing-speed config can fail before any text.
                # Only an actual payload write/paste with a lost ACK is ambiguous.
                error.typing_may_have_happened = bool(getattr(error,"typing_may_have_happened",False) or (error.delivery != "not_sent" and error.command_name in (None, "type", "key")))
                raise
            finally:
                metrics = getattr(self, 'last_typing_timing', {})
                timeline_event('typing', chars=len(text), chunks=metrics.get('chunks', 0),
                               chunk_lengths=metrics.get('chunk_lengths', []),
                               keyboard_ms=metrics.get('keyboard_ms'), initial_gap=metrics.get('initial_gap_s', 0),
                               config_resend=metrics.get('config_resend', False),
                               typing_send_s=metrics.get('typing_send_s'), outcome=metrics.get('outcome', 'incomplete'),
                               pointer_probe_sent=False)

    async def _type(self, text: str, *, keyboard_ms: int | None = None) -> None:
        """ASCII is typed key by key; anything else (Chinese, emoji) is pasted through Universal Clipboard."""
        metrics = self.last_typing_timing
        started = time.perf_counter()
        await self.ensure_keyboard_live()
        metrics['live_check_s'] = round(time.perf_counter()-started,3)
        if text.isascii():
            key_ms = keyboard_ms if keyboard_ms is not None else os.environ.get("HID_KEY_MS")
            if key_ms is not None:
                delay = int(key_ms)
                if not 20 <= delay <= 100:
                    raise ValueError("HID_KEY_MS must be between 20 and 100")
                token = (self.device.links, delay)
                if self._typing_tuned_link != token:
                    metrics['config_resend'] = True
                    await self._send(f"typing {delay}")
                    self._typing_tuned_link = token
            metrics['keyboard_ms']=int(key_ms) if key_ms is not None else None
            await self._type_ascii(text,metrics)
            return
        if not clipboard.supported():
            raise RuntimeError("non-ASCII text is pasted through Universal Clipboard, so the server must run on a Mac")
        # The text stays on the Mac's clipboard: iOS may first ask "Allow Paste?", and putting the user's own
        # clipboard back before that is answered would paste it (it could be a password) instead.
        started = time.perf_counter()
        clipboard.write(text)
        metrics['clipboard_write_s'] = round(time.perf_counter()-started,3)
        wait = max(0, min(8, float(os.environ.get('CLIPBOARD_HANDOFF_WAIT', HANDOFF_WAIT))))
        metrics['handoff_wait_s'] = wait
        await asyncio.sleep(wait)
        started = time.perf_counter()
        await self._send("key cmd+v")
        metrics['paste_key_s'] = round(time.perf_counter()-started,3)

    async def _type_ascii(self,text,metrics):
        target=self.target_info() or {}
        scoped=target.get('device')=='ipad' and target.get('orientation')=='portrait'
        limit=int(os.environ.get('IPAD_TYPE_CHUNK_CHARS','0')) if scoped else 0
        # Materialize/validate before the first payload, never discover bad
        # control characters or an invalid limit after a prefix was delivered.
        segments=list(ascii_typing_segments(text,limit)) if limit else [('type '+text,len(text))]
        # Experimental first-character transition boundary. A local search UI
        # can react to its first character; a longer delay is not delivery proof.
        # Default zero, only existing bounded iPad portrait typing may opt in.
        initial_ms=int(os.environ.get('IPAD_TYPE_INITIAL_GAP_MS','0')) if scoped and limit else 0
        if initial_ms and not 100<=initial_ms<=1000:
            raise ValueError('IPAD_TYPE_INITIAL_GAP_MS must be0 or100..1000')
        initial_gap=initial_ms/1000 if len(text)>1 else 0
        if initial_gap:
            segments=list(ascii_typing_segments(text[:1],limit))+list(ascii_typing_segments(text[1:],limit))
        metrics['initial_gap_s']=initial_gap
        binding=tuple(target.get(k) for k in ('device','orientation','calibration_key','width','height'))
        link=self.device.links
        metrics.update(payload_attempted_chars=0,acknowledged_chars=0,unconfirmed_chars=0,
                       typing_may_have_happened=False,chunked=bool(limit))
        started=time.perf_counter()
        try:
            for index,(command,count) in enumerate(segments):
                check_hid(command)
                if limit:
                    current=self.target_info() or {}
                    if (self.device.links!=link or not getattr(self.device,'connected',True)
                            or tuple(current.get(k) for k in ('device','orientation','calibration_key','width','height'))!=binding):
                        raise DeviceError('Typing binding changed; remainder discarded',delivery='not_sent',command_name='type')
                metrics['chunks'] += 1
                metrics['chunk_lengths'].append(count)
                metrics['payload_attempted_chars']+=count
                metrics['unconfirmed_chars']=count
                try:
                    await self._send(command)
                except DeviceError as exc:
                    if exc.delivery=='not_sent':metrics['unconfirmed_chars']=0
                    raise
                metrics['acknowledged_chars']+=count
                metrics['unconfirmed_chars']=0
                metrics['typing_may_have_happened']=bool(metrics['acknowledged_chars'])
                if limit:
                    # Suffix is still subject to lease/device/geometry checks
                    # on the next iteration; cancellation never resends a prefix.
                    await asyncio.sleep(initial_gap if index==0 else 0)
            metrics['outcome']='acknowledged'
        except (Exception,asyncio.CancelledError) as exc:
            metrics['typing_may_have_happened']=bool(metrics['acknowledged_chars'] or metrics['unconfirmed_chars'])
            metrics['outcome']='stopped' if isinstance(exc,asyncio.CancelledError) else 'failed'
            if isinstance(exc,DeviceError):exc.typing_may_have_happened=metrics['typing_may_have_happened']
            raise
        finally:metrics['typing_send_s']=round(time.perf_counter()-started,4)

    async def key(self, combo: str) -> None:
        with self._keyboard_action('key'):
            await self.ensure_keyboard_live()
            aliases = {"return": "enter", "escape": "esc", "command": "cmd", "control": "ctrl",
                       "option": "alt", "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right"}
            fields = [aliases.get(part.lower(), part.lower()) if len(part) > 1 else part for part in combo.split("+")]
            if (tuple(part.lower() for part in fields)==('cmd','h')
                    and (self.target_info() or {}).get('device')=='ipad'):
                # A system Home transition may relocate/clamp the OS pointer.
                # Do not carry an app's commanded position into the Home grid.
                # The verified Home-icon caller already has bounded reacquisition.
                self.forget_position(keep_home_hint=True)
            await self._send(f"key {'+'.join(fields)}")

    async def _observe_home(self, since, link):
        """Bounded, local-only observation; never infer Home from an input ACK."""
        from .home_icons import home_grid_witness
        deadline=time.monotonic()+HOME_RECOVERY_WAIT
        while time.monotonic()<deadline:
            frame=await self.screen.fresh(since,min(.35,max(0,deadline-time.monotonic())))
            if getattr(self.device,'links',None)!=link:
                raise PhoneUnavailable('Home recovery board binding changed; no retry sent')
            # Use the received frame's timestamp, not time after CV, so an
            # arriving animation frame is not skipped while inspecting pixels.
            since=getattr(self.screen,'updated',time.time())
            if frame and home_grid_witness(frame,self.target_info(),None):return True
            await asyncio.sleep(.04)
        return False

    async def recover_home(self) -> dict:
        """Verify Home locally after at most two acknowledged keyboard attempts.

        Without an orientation-matched reference, preserve the bounded legacy
        recovery and report Home as unknown. A lost ACK or stop propagates
        without replay; unchanged failed pictures remain guarded across calls.
        """
        with self._keyboard_action('recover_home') as metrics:
            await self.ensure_keyboard_live()
            import hashlib
            from .home_icons import home_grid_witness,home_reference_available
            target=self.target_info() or {}
            frame=getattr(self.screen,'frame',None)
            if target.get('device')!='ipad' or not getattr(self.device,'connected',False) or not frame:
                raise PhoneUnavailable('iPad Home recovery needs its connected board and an existing observed iPad frame')
            def result(reached,attempts):
                metrics.update(home_reached=reached,attempts=attempts,retries=max(0,attempts-1),
                               outcome='ok' if reached is not False else 'home_not_verified')
                return dict(ok=reached is not False,home_reached=reached,attempts=attempts,retries=max(0,attempts-1))
            if home_grid_witness(frame,target,None):return result(True,0)
            verifiable=home_reference_available(frame,target)
            digest=hashlib.sha256(frame).hexdigest()
            if digest==getattr(self,'_home_recovery_frame',None):
                raise PhoneUnavailable('Home recovery already attempted for this unchanged picture; inspect receiver diagnostics')
            self._home_recovery_frame=digest  # consume before delivery, never loop on a lost ACK
            self.forget_position()
            link=getattr(self.device,'links',None)
            # Match go_home(): one Escape clears a filled Spotlight query,
            # the second dismisses the panel. Consume the receipt guard first.
            for _ in range(2):
                if getattr(self.device,'links',None)!=link:
                    raise PhoneUnavailable('Home recovery board binding changed; no retry sent')
                await self.key('esc')
            for attempt in range(1,3):
                if getattr(self.device,'links',None)!=link:
                    raise PhoneUnavailable('Home recovery board binding changed; no retry sent')
                sent=time.time()  # Home may arrive before the keyboard ACK
                await self.key('cmd+h')
                if not verifiable:return result(None,1)
                if await self._observe_home(sent,link):return result(True,attempt)
                # Consume the observed failure too, before any second delivery.
                self._home_recovery_frame=hashlib.sha256(self.screen.frame).hexdigest()
            return result(False,2)

    async def go_home(self) -> None:
        if self.profiles is not None and self.target_info()["device"] in ("ipad", "iphone"):
            if self.target_info()["device"] == "ipad":
                # Filled Spotlight consumes the first Escape to clear text;
                # the second closes its panel. Both are idempotent on Home.
                # Keep key() ACK handling and existing Home invalidation.
                await self.key("esc")
                await self.key("esc")
            await self.key("cmd+h")
            return
        await self.swipe(0.5, 0.995, 0.5, 0.6, hold=False)  # resting here would open the app switcher

    async def go_back(self) -> None:
        if (self.target_info() or {}).get('device') == 'ipad':
            raise PhoneUnavailable('iPad back requires a verified control in the active app window; a full-display edge swipe can operate the other pane')
        await self.swipe(0.005, 0.45, 0.6, 0.45, hold=False)

    def _dx(self, x: float) -> int:
        return round(min(max(self._fix(x, "x"), 0), 1) * self.span["w"])

    def _dy(self, y: float) -> int:
        return round(min(max(self._fix(y, "y"), 0), 1) * self.span["h"])
