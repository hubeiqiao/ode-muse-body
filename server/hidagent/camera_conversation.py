"""Owner-started camera conversation: the ONLY path for device frames to Gemini Live.

Camera-preview streaming is available only inside an explicitly started
camera conversation; the operator must decide whether to enable this feature. Frames are cropped
to the camera preview, downscaled and JPEG-encoded in memory, never written to
disk, never traced, never logged. Stop ends the session synchronously. Meta,
Jev and Haiku never receive camera frames (frame_privacy.check_camera_frame).

Capability flag: CAMERA_CONVERSATION=1 (default "0" = feature absent).

Supported layout: iPad LANDSCAPE 1646x1080 only (calibration ipad-landscape-66),
with matching local calibration. Every other size or
orientation, portrait included, is an unknown layout and FAILS CLOSED.

Local Camera recognition (no model, no new dependency, Pillow only):
  * the static black control column x>=1543 is black in two control-free bands;
  * the white shutter disc and the upper four control discs (flash, Live, timer,
    flip) match the saved masked control-strip crops by normalised correlation.
  The crops are read at runtime from CAMERA_TEMPLATE_DIR (the evidence
  templates/ folder); they are never copied into the repository. Without that
  directory every check returns False and the feature reports not ready.

Front camera: the controls cannot prove which camera is active (one before/after
flip pair only, confounded by preview brightness). verify_camera_front therefore
returns None ("unknown"). This implementation has no spoken
front confirmation. Once the Camera UI is verified locally (G5) and the other gates
pass, streaming starts at once and G6 allows "unknown"; Gemini looks at the first
frames and, if the owner is not visible, says so and offers to flip. Only on an
owner utterance (flip_camera) is ONE precise tap on the measured flip centre sent
(only when flip_target_safe), once per session. No face detector is used.
The first passing frame after streaming starts (and after the flip) is sent at
once; the RESUME_PASSES hysteresis applies only to resuming after a pause.
A flip the owner asks for while the Camera is still launching is queued and done
once, right after the Camera UI is verified and before streaming starts.

take_photo: on an operator
utterance only, while streaming/paused, ONE precise tap on the upper-left part of the
shutter disc (clear of the AssistiveTouch button), only with the Camera UI, PHOTO mode
and a white, uncovered aim point verified locally on the current frame. Success =
the Camera-roll thumbnail changes (in-memory pixel diff of a region outside the
preview crop) while the Camera stays verified; otherwise photo_uncertain, never a
retry. Photos/thumbnail are never opened; thumbnail pixels are never saved or sent.
Public setup and limitations: docs/SETUP.md.
"""
import asyncio
import hashlib
import io
import logging
import os
import secrets
import time
import weakref
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

from . import hid_lease

log = logging.getLogger(__name__)

FLAG = 'CAMERA_CONVERSATION'
TEMPLATE_DIR_ENV = 'CAMERA_TEMPLATE_DIR'
MAX_SESSION_S = 540          # below the ~10 min Live connection lifetime
MAX_FRAME_AGE_S = 3.0         # scaled AirPlay can deliver irregular, on-change frames
PAUSE_TIMEOUT_S = 15.0       # allow the 8 s banner hold and resume hysteresis before fallback
FRAME_INTERVAL_MS = 1000
LONG_SIDE = 768
JPEG_QUALITY = 70
RESUME_PASSES = 2
FLIP_SETTLE_S = 1.5
BLACK_STDDEV = 4.0
FROZEN_AFTER_S = 2.0         # frozen = byte-identical preview for >= 2 s (static scenes differ ~0.2 gray)
CHROME_MAX_DISTANCE = 6      # hamming bits of the 64-bit control-column hash
STREAMING_STATES = ('streaming', 'paused')
LAUNCH_STATES = ('launching', 'camera_check', 'front_check')
# HID the workflows may send, in the exact wire format Phone puts on the board:
#   Phone.key('cmd+space') -> 'key cmd+space'; Phone.key('enter'/'return') -> 'key enter';
#   Phone.type('Camera')   -> 'type Camera' (6 chars: below the 8-char minimum chunk, never split);
#   Phone.ensure_live      -> 'move N 0' / 'move -N 0' liveness nudge before key/type;
#   HID_KEY_MS (optional)  -> 'typing N' speed config before ASCII typing.
#   Phone.tap(aim=True)    -> 'home[ sx sy]', 'move dx dy', 'click' ('release' is always allowed).
# Bare names admit arguments; entries containing a space must match exactly.
LAUNCH_WHITELIST = ('key cmd+space', 'type Camera', 'key enter', 'key esc', 'key cmd+h', 'home', 'click', 'move', 'typing')
# Camera icon on Home page 1 (landscape); the dock's recent-apps slots move, this does not.
CAMERA_HOME_ICON = (720 / 1646, 350 / 1080)
FLIP_WHITELIST = ('home', 'move', 'click')
PHOTO_WHITELIST = FLIP_WHITELIST             # the same precise Phone.tap path as the flip
LEAVE_WHITELIST = ('key cmd+h', 'move')

# ---------------------------------------------------------------- measured geometry
LANDSCAPE_SIZE = (1646, 1080)
COLUMN_X = 1543                              # static black control column x>=1543
# Preview: x 0..1480 keeps clear of the controls that straddle x=1543 (shutter disc
# from x~1490, Camera-roll thumbnail x1506-1585: a private photo). y from 140:
# status bar 0-33 px plus a one/two-line notification banner (~10+80 pt at
# ~1.395 px/pt ~= 126 px) top-centre over the preview; 140 px leaves margin. A
# taller banner is still caught by banner_present (G7).
LANDSCAPE_PREVIEW = (0.0, 0.1297, 0.8992, 1.0)
LANDSCAPE_CHROME = (0.9375, 0.0, 1.0, 1.0)   # x 1543..1646, the static control column only
# Fractional boxes from p3-layout.json (frame 1646x1080).
LANDSCAPE_BOXES = {
    'flash': (0.9143, 0.062, 0.9623, 0.1352),
    'live_photo': (0.9143, 0.1556, 0.9623, 0.2287),
    'timer': (0.9143, 0.25, 0.9623, 0.3231),
    'flip': (0.9143, 0.3444, 0.9623, 0.4176),
    'shutter': (0.9052, 0.4454, 0.9721, 0.5519),
    'assistive_touch': (0.9253, 0.5074, 0.9921, 0.6111),
    'thumbnail': (0.9143, 0.5815, 0.9623, 0.6583),
    'mode_video': (0.9143, 0.7352, 0.9623, 0.7546),
    'mode_photo': (0.9143, 0.7806, 0.9623, 0.837),
}
FLIP_TAP = (1545 / 1646, 411.5 / 1080)       # measured flip centre; one precise /api/tap worked (14.6 s)
FLIP_TIMEOUT_S = 30.0
# Shutter (take_photo). Measured inner white disc (p3/p2 rightstrips): x~1498-1594,
# y~494-590 -> centre (1546, 542), r~48. AssistiveTouch overlaps its lower-right
# (x1523-1633, y548-660), so the aim point is the disc's upper-left part.
SHUTTER_DISC = (1546, 542, 48)               # centre x, centre y, radius (px)
SHUTTER_DISC_MARGIN = 10                     # aim point at least this far inside the disc edge
SHUTTER_TAP_PX = (1535, 512)                 # 32.8 px from the centre, 36 px above AssistiveTouch
SHUTTER_TAP = (SHUTTER_TAP_PX[0] / 1646, SHUTTER_TAP_PX[1] / 1080)
ASSISTIVE_TOUCH_PX = (1523, 548, 1633, 660)
ASSISTIVE_TOUCH_MARGIN = 24
SHUTTER_AIM_HALF = 8                         # +-8 px patch around the aim point must be plain white now
SHUTTER_CLEAR_BOX = (1520, 508, 1572, 546)   # upper disc, above AssistiveTouch: all white when uncovered
SHUTTER_AIM_WHITE_MIN = 220.0
SHUTTER_AIM_STDDEV_MAX = 20.0
# Mode wheel: the selected label is yellow. Read only inside the static column (x>=1543),
# so preview content cannot fake it. Measured: ~125-132 yellow px in the PHOTO band, 0 in VIDEO.
PHOTO_LABEL_BAND = (1543, 860, 1585, 890)
VIDEO_LABEL_BAND = (1543, 780, 1585, 830)
PHOTO_YELLOW_MIN = 40
VIDEO_YELLOW_MAX = 3
# Camera-roll thumbnail: a disc x1506-1585 y628-711 (a private photo). Only a box inside the
# disc and below the AssistiveTouch box is diffed (the bounding-box corners show the live
# preview: measured diff 20 there across the flip, ~1 inside), in memory, as a 16x8 gray
# thumbnail. Never saved or sent.
THUMB_PROBE = (1522, 664, 1569, 698)
THUMB_DIFF_MIN = 6.0                         # mean |delta| 0..255; mirror noise on a static image ~0.2
PHOTO_CONFIRM_S = 3.0
PHOTO_POLL_S = 0.1
PHOTO_TAP_TIMEOUT_S = 30.0
PHOTO_MIN_INTERVAL_S = 2.0
# Control-free black bands inside the column (absolute px, l t r b).
BLACK_BANDS = ((1600, 30, 1646, 470), (1603, 720, 1646, 780))
BLACK_MEAN_MAX = 10.0
BLACK_STDDEV_MAX = 12.0
SHUTTER_CORE = (1545, 500, 1575, 540)        # inside the white shutter disc, above AssistiveTouch
SHUTTER_CORE_MIN = 180.0
# Template crops (absolute px) and NCC thresholds. Measured cross-state NCC:
# upper4 rear/front 0.85, flip rear/front 0.80 (timer-vs-flip 0.78: shapes alike).
TEMPLATE_FILES = {
    'column': 'state0-static-column.png',
    'flip': ('state0-flip-righthalf.png', 'state1-front-presumed-flip-righthalf.png'),
    'upper4': ('state0-rear-presumed-upper4-righthalf.png', 'state1-front-presumed-upper4-righthalf.png'),
}
SHUTTER_CROP_IN_COLUMN = (0, 478, 57, 546)
ANCHORS = {'shutter': (1543, 478), 'upper4': (1543, 60), 'flip': (1543, 366)}
NCC_MIN = {'shutter': 0.85, 'upper4': 0.70, 'flip': 0.65}
# Launch (warm launch live 0.73-0.80 s after the Enter ACK; cold launch not measured).
SPOTLIGHT_SETTLE_S = 0.8
LAUNCH_TIMEOUT_S = 8.0
LIVE_WINDOW_S = 5.0
LIVE_MIN_NEW = 2             # received pictures, independent of preview motion / mirror FPS
POLL_S = 0.04
# Banner (G7): top-centre band over the preview.
BANNER_BAND = (362, 0, 1284, 200)
BANNER_BODY = (0, 300, 1480, 1080)
BANNER_JUMP = 10.0           # mean |delta| of the band thumbnail (0..255)
BANNER_HOLD_S = 8.0          # iOS temporary banners auto-dismiss after ~5 s


def enabled():
    return os.environ.get(FLAG, '0') == '1'


class CameraTemplatesPending(RuntimeError):
    """Fail-closed refusal from a template-dependent primitive; `reason` ends the session."""
    def __init__(self, message='camera templates pending', reason='camera_templates_pending'):
        super().__init__(message)
        self.reason = reason


# ----------------------------------------------------------------------------
# Local template matching (Pillow only, no model, nothing written)
# ----------------------------------------------------------------------------

_template_cache = {}


def reset_template_cache():
    _template_cache.clear()


def _half(image):
    w, h = image.size
    return image.resize((max(1, w // 2), max(1, h // 2)), Image.BOX)


def _prepared(template):
    """Half-resolution zero-mean template vector and its norm."""
    small = _half(template)
    values = list(small.tobytes())
    mean = sum(values) / len(values)
    centred = [v - mean for v in values]
    norm = sum(v * v for v in centred) ** 0.5
    return {'size': small.size, 'full': template.size, 'vec': centred, 'norm': norm}


def load_templates():
    """Templates from CAMERA_TEMPLATE_DIR, cached by path+mtime. None when unavailable."""
    raw = os.environ.get(TEMPLATE_DIR_ENV)
    if not raw:
        return None
    root = Path(raw)
    names = [TEMPLATE_FILES['column'], *TEMPLATE_FILES['flip'], *TEMPLATE_FILES['upper4']]
    try:
        key = (str(root), tuple(os.stat(root / name).st_mtime_ns for name in names))
    except OSError:
        return None
    if key in _template_cache:
        return _template_cache[key]
    try:
        def gray(name):
            with Image.open(root / name) as picture:
                return picture.convert('L')
        column = gray(TEMPLATE_FILES['column'])
        loaded = {'shutter': [_prepared(column.crop(SHUTTER_CROP_IN_COLUMN))],
                  'flip': [_prepared(gray(name)) for name in TEMPLATE_FILES['flip']],
                  'upper4': [_prepared(gray(name)) for name in TEMPLATE_FILES['upper4']]}
    except (OSError, ValueError):
        return None
    _template_cache.clear()
    _template_cache[key] = loaded
    return loaded


def _ncc_best(gray, prepared, origin, search=1):
    """Best NCC of a half-res template around `origin` (+-search half-res px = +-2 px)."""
    tw, th = prepared['size']
    fw, fh = prepared['full']
    ox, oy = origin
    pad = 2 * search
    region = gray.crop((ox - pad, oy - pad, ox + fw + pad, oy + fh + pad))
    region = region.resize(((fw + 2 * pad) // 2, (fh + 2 * pad) // 2), Image.BOX)
    rw = region.size[0]
    data = region.tobytes()
    vec, norm = prepared['vec'], prepared['norm']
    if norm <= 1e-6:
        return 0.0
    n = tw * th
    best = -1.0
    for dy in range(0, 2 * search + 1):
        for dx in range(0, 2 * search + 1):
            window = []
            for row in range(th):
                start = (dy + row) * rw + dx
                window.extend(data[start:start + tw])
            if len(window) != n:
                continue
            mean = sum(window) / n
            var = sum(v * v for v in window) - n * mean * mean
            if var <= 1e-6:
                continue
            score = sum(a * b for a, b in zip(window, vec)) / ((var ** 0.5) * norm)
            best = max(best, score)
    return best


def _decode(frame):
    if frame is None:
        return None
    if isinstance(frame, Image.Image):
        return frame
    try:
        image = Image.open(io.BytesIO(frame))
        image.load()
        return image
    except (OSError, ValueError, TypeError):
        return None


def _column_black(gray):
    for band in BLACK_BANDS:
        stat = ImageStat.Stat(gray.crop(band))
        if stat.mean[0] > BLACK_MEAN_MAX or stat.stddev[0] > BLACK_STDDEV_MAX:
            return False
    return True


def _shutter_ok(gray, templates):
    if ImageStat.Stat(gray.crop(SHUTTER_CORE)).mean[0] < SHUTTER_CORE_MIN:
        return False
    return _ncc_best(gray, templates['shutter'][0], ANCHORS['shutter']) >= NCC_MIN['shutter']


def _anchor_ok(gray, templates, name):
    return max(_ncc_best(gray, t, ANCHORS[name]) for t in templates[name]) >= NCC_MIN[name]


def camera_ui_check(frame, templates=None, require_flip=True):
    """Landscape Camera UI: exact size, black column bands, shutter, upper discs, flip disc."""
    templates = templates or load_templates()
    if templates is None:
        return False
    image = _decode(frame)
    if image is None or image.size != LANDSCAPE_SIZE:
        return False
    gray = image.convert('L')
    if not _column_black(gray) or not _shutter_ok(gray, templates):
        return False
    if not require_flip:
        return True
    return _anchor_ok(gray, templates, 'upper4') and _anchor_ok(gray, templates, 'flip')


def verify_camera_ui(frame) -> bool:
    """G5: landscape Camera controls match the saved crops. False on any doubt."""
    try:
        # The visible iPad pointer often rests on the flip disc (Automatically Hide
        # Pointer is off), so the black column + shutter (NCC ~0.99) identify Camera.
        return camera_ui_check(frame, require_flip=False)
    except Exception as error:  # never let a recogniser error open a gate
        log.warning('camera ui check failed: %s', type(error).__name__)
        return False


def verify_camera_front(frame):
    """G6/F1: the controls cannot prove front vs rear (measured), so this is None
    ("unknown"); the session then needs the owner's one-time confirmation."""
    return None


def preview_region(frame_size, orientation):
    """G4/crop: fractional (l, t, r, b) preview box; landscape 1646x1080 only."""
    if tuple(frame_size) == LANDSCAPE_SIZE and orientation == 'landscape':
        return LANDSCAPE_PREVIEW
    raise CameraTemplatesPending('no measured camera layout for this size', reason='unknown_layout')


def chrome_region(frame_size, orientation):
    """G8: fractional control-column box; landscape 1646x1080 only."""
    if tuple(frame_size) == LANDSCAPE_SIZE and orientation == 'landscape':
        return LANDSCAPE_CHROME
    raise CameraTemplatesPending('no measured camera layout for this size', reason='unknown_layout')


def banner_present(frame, orientation) -> bool:
    """Stateless form cannot compare against earlier frames: uncertain -> True.
    LandscapeCameraDetector.banner_present keeps the per-session history."""
    return True


def recognised_camera_ui(frame) -> bool:
    """Deny-only recogniser used by frame_privacy for ANY cloud frame (rule b).

    Same local check as verify_camera_ui. Cheap exits first (size, black bands),
    so non-camera frames cost a decode and two ImageStat calls.
    """
    return verify_camera_ui(frame)


def flip_forbidden_boxes():
    """Every control the flip click must keep its margin from."""
    return [box for name, box in LANDSCAPE_BOXES.items() if name != 'flip']


def _yellow_count(rgb, band):
    count = 0
    crop = rgb.crop(band)
    pixels = crop.get_flattened_data() if hasattr(crop, 'get_flattened_data') else crop.getdata()
    for r, g, b in pixels:
        if r >= 190 and g >= 190 and (r + g) / 2 - b >= 60:
            count += 1
    return count


def _rgb(frame):
    image = _decode(frame)
    if image is None or image.size != LANDSCAPE_SIZE:
        return None
    return image.convert('RGB')


def photo_mode_selected(frame) -> bool:
    """PHOTO is the selected (yellow) mode label and VIDEO is not. False on any doubt."""
    rgb = _rgb(frame)
    if rgb is None:
        return False
    # Yellow preview content (a toy, a lamp) can bleed into the VIDEO band; compare the two.
    photo, video = _yellow_count(rgb, PHOTO_LABEL_BAND), _yellow_count(rgb, VIDEO_LABEL_BAND)
    return photo >= PHOTO_YELLOW_MIN and (video <= VIDEO_YELLOW_MAX or photo >= 4*video)


def shutter_aim_safe(frame) -> bool:
    """The shutter aim point is inside the measured disc, clear of AssistiveTouch and the
    thumbnail, and on THIS frame a plain white patch (nothing floating over it)."""
    x, y = SHUTTER_TAP_PX
    cx, cy, radius = SHUTTER_DISC
    if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 > radius - SHUTTER_DISC_MARGIN:
        return False
    l, t, r, b = ASSISTIVE_TOUCH_PX
    dx, dy = max(l - x, 0, x - r), max(t - y, 0, y - b)
    if (dx * dx + dy * dy) ** 0.5 < ASSISTIVE_TOUCH_MARGIN:
        return False
    tl, tt, tr, tb = _pixels(LANDSCAPE_BOXES['thumbnail'], LANDSCAPE_SIZE)
    if tl <= x <= tr and tt <= y <= tb:
        return False
    rgb = _rgb(frame)
    if rgb is None:
        return False
    gray = rgb.convert('L')
    h = SHUTTER_AIM_HALF
    for box in ((x - h, y - h, x + h + 1, y + h + 1), SHUTTER_CLEAR_BOX):
        stat = ImageStat.Stat(gray.crop(box))
        if stat.mean[0] < SHUTTER_AIM_WHITE_MIN or stat.stddev[0] > SHUTTER_AIM_STDDEV_MAX:
            return False
    return True


def _thumb_signature(rgb):
    return list(rgb.convert('L').crop(THUMB_PROBE).resize((16, 8), Image.BOX).tobytes())


def _signature_diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def camera_still_check(frame, templates=None):
    """Camera still in front while the pointer hovers the shutter: static column, upper
    discs, flip disc and PHOTO mode (the shutter itself is under the pointer)."""
    templates = templates or load_templates()
    rgb = _rgb(frame)
    if templates is None or rgb is None:
        return False
    gray = rgb.convert('L')
    return (_column_black(gray) and _anchor_ok(gray, templates, 'upper4')
            and _anchor_ok(gray, templates, 'flip') and photo_mode_selected(rgb))


async def launch_camera():
    raise CameraTemplatesPending('camera launch needs the landscape detector')


async def flip_to_front():
    raise CameraTemplatesPending('camera flip needs the landscape detector')


async def leave_camera():
    raise CameraTemplatesPending('camera exit needs the landscape detector')


class StubDetector:
    """No phone/templates bound: fails closed. Used when no detector is supplied."""
    def ready(self):
        return False, 'camera_templates_pending'

    def preview_region(self, frame_size, orientation):
        raise CameraTemplatesPending('camera preview template pending measurement')

    def chrome_region(self, frame_size, orientation):
        raise CameraTemplatesPending('camera control-strip template pending measurement')

    def chrome_fingerprint(self, image, box):
        return region_fingerprint(image, box)

    def verify_camera_ui(self, frame):
        return False

    def verify_camera_front(self, frame):
        return False

    def banner_present(self, frame, orientation):
        return True

    def known_overlay(self, frame):
        from .frame_privacy import known_excluded_overlay
        return known_excluded_overlay(frame)

    async def launch_camera(self):
        return await launch_camera()

    async def flip_to_front(self):
        return await flip_to_front()

    async def take_photo(self, alive=None):
        raise CameraTemplatesPending('camera photo needs the landscape detector')

    async def leave_camera(self):
        return await leave_camera()


class _BannerWatch:
    """Per-session top-centre band history (G7).

    A banner is a SUDDEN change localised to the top-centre band while the rest
    of the preview stays put (light and dark banners both count). It is held for
    BANNER_HOLD_S; after that it clears only when the band is back near the
    pre-banner reference or the whole scene has moved. No history -> uncertain.
    """
    def __init__(self, clock):
        self.clock = clock
        self.reset()

    def reset(self):
        self.prev = None
        self.pre_ref = None
        self.body_at_jump = None
        self.hold_until = None

    @staticmethod
    def _thumb(gray, box, size):
        return list(gray.crop(box).resize(size, Image.BOX).tobytes())

    @staticmethod
    def _diff(a, b):
        return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

    def check(self, frame, orientation):
        image = _decode(frame)
        if image is None or orientation != 'landscape' or image.size != LANDSCAPE_SIZE:
            return True
        gray = image.convert('L')
        band = self._thumb(gray, BANNER_BAND, (48, 10))
        body = self._thumb(gray, BANNER_BODY, (48, 24))
        now = self.clock()
        prev, self.prev = self.prev, (band, body)
        if prev is None:
            return True
        d_band, d_body = self._diff(band, prev[0]), self._diff(body, prev[1])
        if d_band >= BANNER_JUMP and d_band >= 3 * d_body + 6:
            if self.hold_until is None:
                self.pre_ref, self.body_at_jump = prev[0], prev[1]
            self.hold_until = now + BANNER_HOLD_S
            return True
        if self.hold_until is None:
            return False
        if now < self.hold_until:
            return True
        still = self._diff(band, self.pre_ref) >= BANNER_JUMP
        same_scene = self._diff(body, self.body_at_jump) < BANNER_JUMP / 2
        if still and same_scene:
            self.hold_until = now + BANNER_HOLD_S   # persistent banner
            return True
        self.hold_until = self.pre_ref = self.body_at_jump = None
        return False


class LandscapeCameraDetector:
    """iPad landscape 1646x1080 Camera: local recognition plus the measured HID steps."""
    def __init__(self, phone, screen, clock=time.monotonic, sleep=asyncio.sleep):
        self.phone, self.screen, self.clock, self.sleep = phone, screen, clock, sleep
        self.banner = _BannerWatch(clock)

    def reset(self):
        self.banner.reset()

    def ready(self):
        if load_templates() is None:
            return False, 'camera_templates_missing'
        image = _decode(getattr(self.screen, 'frame', None))
        if image is None or image.size != LANDSCAPE_SIZE:
            return False, 'camera_layout_unsupported'
        target = self.phone.target_info() if hasattr(self.phone, 'target_info') else None
        if target is not None and (target.get('device') != 'ipad' or target.get('orientation') != 'landscape'):
            return False, 'camera_layout_unsupported'
        return True, 'ready'

    def preview_region(self, frame_size, orientation):
        return preview_region(frame_size, orientation)

    def chrome_region(self, frame_size, orientation):
        return chrome_region(frame_size, orientation)

    def chrome_fingerprint(self, image, box):
        return region_fingerprint(image, box)

    def verify_camera_ui(self, frame):
        return verify_camera_ui(frame)

    def verify_camera_front(self, frame):
        return verify_camera_front(frame)

    def banner_present(self, frame, orientation):
        try:
            return self.banner.check(frame, orientation)
        except Exception as error:
            log.warning('banner check failed: %s', type(error).__name__)
            return True

    def known_overlay(self, frame):
        from .frame_privacy import known_excluded_overlay
        return known_excluded_overlay(frame)

    def _require_ready(self):
        ok, reason = self.ready()
        if not ok:
            raise CameraTemplatesPending('camera not ready: ' + reason, reason=reason)
        if self.phone is None:
            raise CameraTemplatesPending('no phone bound', reason='camera_templates_pending')

    async def _live(self):
        """Require advancing pictures on a connected, fresh mirror, without an FPS floor.

        Identical decoded images may have new receipt timestamps. A connected
        socket/heartbeat alone never proves liveness; a frozen receipt fails.
        Byte changes also witness updates for older screen implementations.
        """
        start = self.clock()
        last = hashlib.sha256(self.screen.frame or b'').digest()
        updated = self.screen.updated
        changes = 0
        while self.clock() - start < LIVE_WINDOW_S:
            await self.sleep(POLL_S)
            frame, received = self.screen.frame, self.screen.updated
            health = self.screen.mirror_health()
            if (not frame or not self.screen.live or health.get('state') != 'session_connected'
                    or not 0 <= time.time() - received < MAX_FRAME_AGE_S):
                continue
            digest = hashlib.sha256(frame).digest()
            if received > updated or digest != last:
                changes += 1
                last, updated = digest, received
                if changes >= LIVE_MIN_NEW:
                    log.info('camera liveness verified updates=%d elapsed_s=%.2f', changes, self.clock()-start)
                    return True
        log.info('camera liveness failed updates=%d elapsed_s=%.2f', changes, self.clock()-start)
        return False

    async def launch_camera(self):
        """Measured: cmd+space, type 'Camera' once, Enter; then local UI + liveness wait."""
        self._require_ready()
        if verify_camera_ui(self.screen.frame):
            log.info('camera already foreground; skipping Spotlight')
        else:
            # Spotlight cost ~12 s and often failed on the native mirror: go Home, tap the icon.
            if hasattr(self.phone, 'tap'):
                await self.phone.key('esc')
                await self.phone.key('cmd+h')
                await self.sleep(1.2)
                try:
                    from .icon_locator import locate
                    spot = locate(self.screen.frame, 'camera')
                    x, y = ((spot[0]/1646, spot[1]/1080) if spot else CAMERA_HOME_ICON)
                    await self.phone.tap(x, y, aim=True)
                    await self.sleep(1.5)
                except Exception as error:  # fall back to Spotlight below
                    log.info('camera icon tap failed: %s', type(error).__name__)
            if not verify_camera_ui(self.screen.frame):
                await self.phone.key('cmd+space')
                await self.sleep(SPOTLIGHT_SETTLE_S)
                await self.phone.type('Camera')
                await self.sleep(SPOTLIGHT_SETTLE_S)
                await self.phone.key('enter')
        deadline = self.clock() + LAUNCH_TIMEOUT_S
        while not verify_camera_ui(self.screen.frame):
            if self.clock() >= deadline:
                raise CameraTemplatesPending('Camera UI not recognised', reason='camera_not_verified')
            await self.sleep(0.1)
        if not await self._live():
            raise CameraTemplatesPending('Camera preview not live', reason='camera_not_live')

    async def flip_to_front(self):
        """ONE precise tap on the measured flip centre, only with a verified UI and safe target."""
        if self.phone is None or not verify_camera_ui(self.screen.frame):
            raise CameraTemplatesPending('Camera UI not verified before flip', reason='camera_not_verified')
        if not flip_target_safe(LANDSCAPE_BOXES['flip'], flip_forbidden_boxes(), LANDSCAPE_SIZE):
            raise CameraTemplatesPending('flip target too close to another control', reason='flip_target_unsafe')

        def still_camera():
            # The pointer hovers the flip disc while aiming, so only the column and shutter are rechecked.
            return camera_ui_check(self.screen.frame, require_flip=False)
        await asyncio.wait_for(self.phone.tap(FLIP_TAP[0], FLIP_TAP[1], aim=True, pre_click=still_camera),
                               FLIP_TIMEOUT_S)

    def camera_still(self, frame):
        try:
            return camera_still_check(frame)
        except Exception as error:
            log.warning('camera still check failed: %s', type(error).__name__)
            return False

    async def take_photo(self, alive=None):
        """ONE precise tap on the shutter's upper-left part; success = the thumbnail changes.

        Refusals before any HID raise CameraTemplatesPending. After the tap the result is
        photo_taken, photo_uncertain (never retried) or photo_not_taken (no click was sent).
        The thumbnail is only compared in memory; it is never opened, saved or sent.
        """
        if self.phone is None:
            raise CameraTemplatesPending('no phone bound', reason='camera_not_verified')
        frame = self.screen.frame
        if not verify_camera_ui(frame):
            raise CameraTemplatesPending('Camera UI not verified before photo', reason='camera_not_verified')
        rgb = _rgb(frame)
        if rgb is None or not photo_mode_selected(rgb):
            raise CameraTemplatesPending('PHOTO mode not verified', reason='photo_mode_not_verified')
        if not shutter_aim_safe(rgb):
            # Usually our own pointer resting on the shutter from a previous tap; tap anyway.
            log.info('shutter aim point not plain white; tapping the measured shutter anyway')
        before = _thumb_signature(rgb)
        guard = {'passed': False}

        def still_camera():
            ok = (alive is None or alive()) and self.camera_still(self.screen.frame)
            guard['passed'] = bool(ok)
            return bool(ok)
        try:
            await asyncio.wait_for(self.phone.tap(SHUTTER_TAP[0], SHUTTER_TAP[1], aim=True, pre_click=still_camera),
                                   PHOTO_TAP_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not guard['passed']:
                return {'status': 'photo_not_taken', 'reason': 'no_click_sent'}
            log.warning('camera photo tap uncertain: %s', type(error).__name__)
            return {'status': 'photo_uncertain', 'reason': 'tap_uncertain'}
        deadline = self.clock() + PHOTO_CONFIRM_S
        still = False
        while True:
            rgb = _rgb(self.screen.frame)
            still = rgb is not None and self.camera_still(rgb)
            if still and _signature_diff(_thumb_signature(rgb), before) >= THUMB_DIFF_MIN:
                return {'status': 'photo_taken'}
            if self.clock() >= deadline:
                break
            await self.sleep(PHOTO_POLL_S)
        return {'status': 'photo_uncertain', 'reason': 'thumbnail_unchanged' if still else 'camera_not_verified'}

    async def leave_camera(self):
        if self.phone is None:
            raise CameraTemplatesPending('no phone bound')
        await self.phone.key('cmd+h')


# ----------------------------------------------------------------------------
# Pure helpers (no I/O)
# ----------------------------------------------------------------------------

def orientation_of(size):
    w, h = size
    return 'landscape' if w > h else 'portrait'


def _pixels(box, size):
    w, h = size
    l, t, r, b = box
    if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
        raise ValueError('invalid fractional box')
    return int(l * w), int(t * h), int(r * w), int(b * h)


def region_fingerprint(image, box):
    """64-bit average hash of a region; detects Slide Over / overlays on the controls."""
    tile = image.crop(_pixels(box, image.size)).convert('L').resize((8, 8))
    values = list(tile.tobytes())
    mean = sum(values) / len(values)
    return ''.join('1' if v > mean else '0' for v in values)


def fingerprint_distance(a, b):
    if a is None or b is None or len(a) != len(b):
        return 1 << 30
    return sum(x != y for x, y in zip(a, b))


def flip_target_safe(target, forbidden, frame_size):
    """Flip click target centre must keep max(40px, 0.04*min(w,h)) from every no-go box."""
    w, h = frame_size
    margin = max(40.0, 0.04 * min(w, h))
    cx, cy = (target[0] + target[2]) / 2 * w, (target[1] + target[3]) / 2 * h
    for box in forbidden:
        l, t, r, b = box[0] * w, box[1] * h, box[2] * w, box[3] * h
        dx = max(l - cx, 0.0, cx - r)
        dy = max(t - cy, 0.0, cy - b)
        if (dx * dx + dy * dy) ** 0.5 < margin:
            return False
    return True


def encode_preview(image, box):
    """Crop to the preview box, long side <= 768, JPEG q70. Memory only."""
    crop = image.crop(_pixels(box, image.size))
    w, h = crop.size
    scale = min(1.0, LONG_SIDE / max(w, h))
    if scale < 1.0:
        crop = crop.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    out = io.BytesIO()
    crop.save(out, 'JPEG', quality=JPEG_QUALITY)
    return crop, out.getvalue()


# ----------------------------------------------------------------------------
# HID guard: while a camera session is active, only whitelisted workflow HID.
# ----------------------------------------------------------------------------

_INSTANCES = weakref.WeakSet()
_hid_window = ContextVar('camera_hid_window', default=None)


@contextmanager
def camera_hid_window(allowed):
    """Allow only the listed command prefixes (launch/flip/leave), task-local."""
    hid_lease.set_camera_block(_hid_blocked)
    token = _hid_window.set(tuple(allowed))
    try:
        yield
    finally:
        _hid_window.reset(token)


def _hid_blocked(command):
    allowed = _hid_window.get()
    if allowed is None:
        return active_any()
    text = command.strip()
    # Bare command names ('move', 'click') admit arguments; anything with a space must match exactly.
    return not any(text == item or (' ' not in item and text.startswith(item + ' ')) for item in allowed)


def active_any():
    return any(instance.active() for instance in list(_INSTANCES))


def end_all_instances(reason):
    for instance in list(_INSTANCES):
        instance.end_all(reason)


@dataclass
class FrameResult:
    status: int
    reason: str
    body: bytes | None = None
    resume_voice: bool = False
    instruction: str | None = None


class CameraConversation:
    """In-memory state machine and gates G1-G9 for owner-started camera sessions.

    idle -> launching -> camera_check -> front_check -> streaming <-> paused -> ended
    streaming/paused -(owner asks, once)-> flipping -> streaming (first frame at once)
    launching/camera_check/front_check -(owner asks)-> flip queued, done once before streaming
    streaming/paused -(owner asks, >= 2 s apart)-> photographing -> streaming (new HID baseline)
    """

    def __init__(self, screen, detector=None, clock=time.time):
        self.screen = screen
        self.detector = detector or StubDetector()
        self.clock = clock
        self.sessions = {}
        self.flip_settle_s = FLIP_SETTLE_S
        self.counts = {}
        _INSTANCES.add(self)
        hid_lease.set_camera_block(_hid_blocked)

    # ---------------------------------------------------------- lifecycle
    def ready(self):
        return self.detector.ready()

    def active(self):
        return any(rec['state'] != 'ended' for rec in self.sessions.values())

    def begin(self, sid, generation=None):
        self._prune()
        generation = generation or secrets.token_hex(8)
        self.sessions[sid] = {'state': 'launching', 'generation': generation, 'started_at': self.clock(),
                              'streaming_since': None, 'last_seen_updated': 0.0, 'last_sent_updated': 0.0,
                              'last_preview_digest': None, 'digest_since': 0.0, 'hid_baseline': None,
                              'chrome_refs': {}, 'flipped': False, 'flip_queued': False, 'first_pending': False,
                              'last_photo_at': None, 'photos': 0,
                              'ok_streak': 0, 'reason': None, 'paused_since': None, 'sent': 0, 'task': None}
        reset = getattr(self.detector, 'reset', None)
        if reset is not None:
            reset()  # per-session detector history (banner band)
        log.info('camera session begin')
        return generation

    def end(self, sid, reason):
        """Synchronous: callers rely on this flipping state before any await."""
        rec = self.sessions.get(sid)
        if not rec or rec['state'] == 'ended':
            return False
        rec['state'], rec['reason'], rec['generation'] = 'ended', reason, None
        rec['ended_at'] = self.clock()
        task, rec['task'] = rec.get('task'), None
        if task is not None and not task.done() and task is not _current_task():
            task.cancel()
        log.info('camera session end reason=%s frames_sent=%d', reason, rec['sent'])
        return True

    def end_all(self, reason):
        for sid in list(self.sessions):
            self.end(sid, reason)

    def status(self, sid):
        rec = self.sessions.get(sid)
        if not rec:
            return {'camera_state': 'none'}
        return {'camera_state': rec['state'], 'reason': rec['reason'], 'frames_sent': rec['sent']}

    def _prune(self):
        ended = [sid for sid, rec in self.sessions.items() if rec['state'] == 'ended']
        for sid in ended[:-32]:
            self.sessions.pop(sid, None)

    def _current(self, sid, generation):
        rec = self.sessions.get(sid)
        return rec if rec and rec['state'] != 'ended' and rec['generation'] == generation else None

    def mark_streaming(self, sid, generation):
        """After F3 verification (and after the flip): baseline HID and freshness so no
        earlier frame is ever sent; the first passing NEW frame is then sent at once."""
        rec = self._current(sid, generation)
        if rec is None:
            return False
        rec['state'], rec['reason'] = 'streaming', None
        rec['paused_since'] = None
        rec['streaming_since'] = self.clock()
        rec['hid_baseline'] = hid_lease.hid_count()
        rec['ok_streak'] = 0
        rec['first_pending'] = True
        rec['chrome_refs'] = {}          # G8 reference is re-taken from the first frame (flip changes the disc)
        rec['last_preview_digest'] = None
        self._prime_banner()
        log.info('camera session streaming')
        return True

    def _prime_banner(self):
        """Give the G7 banner watch its history from the current frame (never sent), so the
        first new frame is compared against it instead of being refused as 'no history'."""
        image = _decode(self.screen.frame)
        if image is None:
            return
        try:
            self.detector.banner_present(self.screen.frame, orientation_of(image.size))
        except Exception as error:
            log.warning('banner prime failed: %s', type(error).__name__)

    # ---------------------------------------------------------- launch workflow
    def start_prepare(self, sid, generation):
        rec = self._current(sid, generation)
        if rec is not None:
            rec['task'] = asyncio.get_running_loop().create_task(self.prepare(sid, generation))
        return rec

    async def prepare(self, sid, generation):
        """L*/F* orchestration. Primitives are stubs until measured; any failure ends the session.

        From begin() onward check_cloud_frame refuses every image, so no step can
        consult Jev/Haiku/Meta on a frame that may contain the owner's face.
        """
        try:
            with camera_hid_window(LAUNCH_WHITELIST):
                await self.detector.launch_camera()
            rec = self._current(sid, generation)
            if rec is None:
                return
            rec['state'] = 'camera_check'
            if not self.detector.verify_camera_ui(self.screen.frame):
                self.end(sid, 'camera_not_verified')
                return
            rec['state'] = 'front_check'
            front = self.detector.verify_camera_front(self.screen.frame)
            # None = controls cannot prove the side: stream at once; Gemini judges the
            # first frames and offers a flip (owner-gated) if the owner is not visible.
            # An owner flip asked for during the launch is done here, once, on the verified UI.
            queued = rec['flip_queued']
            if front is False or queued:
                rec['flipped'], rec['flip_queued'], rec['state'] = True, False, 'flipping'
                with camera_hid_window(FLIP_WHITELIST):
                    await self.detector.flip_to_front()
                await asyncio.sleep(self.flip_settle_s)
                if self._current(sid, generation) is None:
                    return
                if not self.detector.verify_camera_ui(self.screen.frame):
                    self.end(sid, 'camera_not_verified' if queued and front is not False else 'not_front')
                    return
                if front is False and not self.detector.verify_camera_front(self.screen.frame):
                    self.end(sid, 'not_front')  # never click a second time
                    return
            self.mark_streaming(sid, generation)
        except asyncio.CancelledError:
            self.end(sid, 'cancelled')
            raise
        except CameraTemplatesPending as error:
            self.end(sid, error.reason)
        except Exception as error:  # fail closed; reason only, never frame content
            log.warning('camera launch failed: %s', type(error).__name__)
            self.end(sid, 'camera_launch_failed')

    def _owner_rec(self, sid, generation):
        rec = self.sessions.get(sid)
        if rec is None or rec['state'] == 'ended':
            return None
        if generation is not None and generation != rec['generation']:
            return None
        return rec

    def confirm_front(self, sid, generation=None):
        """Compatibility no-op: the spoken front confirmation was dropped (2026-10-29).
        Never changes state, never sends HID; reports whether frames already stream."""
        rec = self._owner_rec(sid, generation)
        if rec is None:
            return {'status': 'camera_ended', 'terminal': True}
        if rec['state'] in STREAMING_STATES:
            return {'status': 'camera_streaming', 'terminal': True,
                    'instruction': 'No confirmation is needed: preview frames already stream.'}
        return {'status': 'not_ready', 'terminal': True, 'reason': 'camera_' + rec['state'],
                'instruction': 'The Camera is still opening. Frames start by themselves; do not ask the owner to confirm.'}

    async def flip(self, sid, generation=None):
        """Owner asked to flip: ONE whitelisted click per session, only while streaming/paused.
        Frames are refused during the flip; afterwards streaming restarts with a new HID baseline."""
        rec = self._owner_rec(sid, generation)
        if rec is None:
            return {'status': 'camera_ended', 'terminal': True}
        if rec['flipped'] or rec['flip_queued']:
            return {'status': 'rejected', 'terminal': True, 'reason': 'flip_already_used',
                    'instruction': 'The flip was already used once. End the camera conversation and start again.'}
        if rec['state'] in LAUNCH_STATES:
            # Owner asked while the Camera is still opening: remember it, flip once when verified.
            rec['flip_queued'] = True
            log.info('camera flip queued')
            return {'status': 'flip_queued', 'terminal': True,
                    'instruction': 'The Camera is still opening. The flip happens once, right after the Camera '
                                   'is verified. Tell the owner; do not call flip_camera again.'}
        if rec['state'] not in STREAMING_STATES:
            return {'status': 'rejected', 'terminal': True, 'reason': 'flip_not_allowed_' + rec['state'],
                    'instruction': 'The Camera is not open yet. Wait for the first frames, then ask the owner again.'}
        rec['flipped'] = True  # consumed before any HID: a lost ACK never leads to a second click
        rec['state'] = 'flipping'
        generation = rec['generation']
        try:
            with camera_hid_window(FLIP_WHITELIST):
                await self.detector.flip_to_front()
            await asyncio.sleep(self.flip_settle_s)
        except asyncio.CancelledError:
            self.end(sid, 'cancelled')
            raise
        except CameraTemplatesPending as error:
            self.end(sid, error.reason)
            return {'status': 'camera_ended', 'terminal': True, 'reason': error.reason}
        except Exception as error:
            log.warning('camera flip failed: %s', type(error).__name__)
            self.end(sid, 'flip_failed')
            return {'status': 'camera_ended', 'terminal': True, 'reason': 'flip_failed'}
        rec = self._current(sid, generation)
        if rec is None:
            return {'status': 'camera_ended', 'terminal': True}
        if not self.detector.verify_camera_ui(self.screen.frame):
            self.end(sid, 'camera_not_verified')
            return {'status': 'camera_ended', 'terminal': True, 'reason': 'camera_not_verified'}
        self.mark_streaming(sid, generation)
        return {'status': 'camera_flipped', 'terminal': True,
                'instruction': 'Flipped once. Look at the next frames and tell the owner what you see now.'}

    async def take_photo(self, sid, generation=None):
        """Owner asked for a photo: at most ONE shutter tap per call, >= 2 s apart, only while
        streaming/paused. Frames are refused during it; afterwards a new HID baseline."""
        rec = self._owner_rec(sid, generation)
        if rec is None:
            return {'status': 'camera_ended', 'terminal': True}
        if rec['state'] not in STREAMING_STATES:
            return {'status': 'rejected', 'terminal': True, 'reason': 'photo_not_allowed_' + rec['state'],
                    'instruction': 'The Camera is not ready for a photo yet. No photo was taken. '
                                   'Tell the owner; they can ask again once you see them.'}
        now = self.clock()
        if rec['last_photo_at'] is not None and now - rec['last_photo_at'] < PHOTO_MIN_INTERVAL_S:
            return {'status': 'rejected', 'terminal': True, 'reason': 'photo_rate_limited',
                    'instruction': 'A photo was just taken. Wait two seconds; the owner can ask again.'}
        take = getattr(self.detector, 'take_photo', None)
        if take is None:
            return {'status': 'rejected', 'terminal': True, 'reason': 'photo_unavailable'}
        rec['last_photo_at'] = now  # consumed before any HID: a lost ACK never leads to a second tap
        previous, rec['state'] = rec['state'], 'photographing'
        generation = rec['generation']
        before = hid_lease.hid_count()
        try:
            with camera_hid_window(PHOTO_WHITELIST):
                result = await take(alive=lambda: self._current(sid, generation) is not None)
        except asyncio.CancelledError:
            self.end(sid, 'cancelled')
            raise
        except CameraTemplatesPending as error:
            result = {'status': 'rejected', 'reason': error.reason}
        except Exception as error:
            log.warning('camera photo failed: %s', type(error).__name__)
            result = {'status': 'photo_uncertain', 'reason': 'photo_failed'}
        rec = self._current(sid, generation)
        if rec is None:
            return {'status': 'camera_ended', 'terminal': True, 'photo': result.get('status')}
        if hid_lease.hid_count() == before:
            rec['state'] = previous        # nothing reached the board: the old G9 baseline still holds
        else:
            still = getattr(self.detector, 'camera_still', self.detector.verify_camera_ui)
            if not still(self.screen.frame):
                self.end(sid, 'camera_not_verified')
                return {'status': 'camera_ended', 'terminal': True, 'reason': 'camera_not_verified',
                        'photo': result.get('status'),
                        'instruction': 'The Camera is no longer verified, so the camera conversation ended. '
                                       + PHOTO_INSTRUCTIONS.get(result.get('status'), '')}
            self.mark_streaming(sid, generation)  # the shutter tap is ours: new G9 baseline
        status = result.get('status')
        if status == 'photo_taken':
            rec['photos'] += 1
        log.info('camera photo result=%s', status)
        receipt = {'status': status, 'terminal': True}
        if result.get('reason'):
            receipt['reason'] = result['reason']
        receipt['instruction'] = PHOTO_INSTRUCTIONS.get(status) or PHOTO_REFUSALS.get(
            result.get('reason'), 'No photo was taken. Tell the owner; they can ask again.')
        return receipt

    async def leave(self):
        """One cmd+h to close Camera after an owner end. Stubbed: reports honestly."""
        try:
            with camera_hid_window(LEAVE_WHITELIST):
                await self.detector.leave_camera()
            return {'camera_left_foreground': True}
        except CameraTemplatesPending:
            return {'camera_left_foreground': False, 'camera_note': 'Camera may still be open on the iPad.'}
        except Exception as error:
            log.warning('camera leave failed: %s', type(error).__name__)
            return {'camera_left_foreground': False, 'camera_note': 'Camera may still be open on the iPad.'}

    # ---------------------------------------------------------- gates
    def _count(self, reason):
        self.counts[reason] = self.counts.get(reason, 0) + 1

    def _pause(self, rec, reason):
        if rec['paused_since'] is None:
            rec['paused_since'] = self.clock()
        rec['state'], rec['reason'], rec['ok_streak'] = 'paused', reason, 0
        self._count(reason)
        return FrameResult(409, reason)

    def _hard(self, sid, reason):
        self.end(sid, reason)
        self._count(reason)
        return FrameResult(409, reason)

    def frame(self, sid, generation):
        """Return one gated, cropped JPEG or a reason. G1 voice-ownership is checked by the bridge."""
        rec = self.sessions.get(sid)
        # G1 (camera side): a session this object began.
        if rec is None:
            return FrameResult(403, 'not_camera_session')
        # G2: current generation, streaming state, time limit.
        if rec['state'] == 'ended' or not generation or generation != rec['generation']:
            return FrameResult(410, 'ended')
        if self.clock() - rec['started_at'] > MAX_SESSION_S:
            self.end(sid, 'time_limit')
            return FrameResult(410, 'ended')
        if rec['state'] not in STREAMING_STATES:
            return FrameResult(409, 'not_streaming')
        if rec['paused_since'] is not None and self.clock()-rec['paused_since'] >= PAUSE_TIMEOUT_S:
            self.end(sid, rec['reason'] or 'camera_unavailable')
            return FrameResult(410, 'ended')
        # G9 first among device checks: any non-release HID since streaming began is fatal.
        if hid_lease.hid_count() != rec['hid_baseline']:
            return self._hard(sid, 'hid_activity')
        # G3: live mirror, connected session, fresh and NEW frame.
        frame, updated = self.screen.frame, self.screen.updated
        now = self.clock()
        if not frame or not getattr(self.screen, 'live', False):
            return self._pause(rec, 'stale')
        health = self.screen.mirror_health() if hasattr(self.screen, 'mirror_health') else {}
        if health.get('state') != 'session_connected':
            return self._pause(rec, 'stale')
        if updated <= rec['streaming_since'] or now - updated >= MAX_FRAME_AGE_S:
            return self._pause(rec, 'stale')
        if updated <= rec['last_seen_updated']:
            return FrameResult(204, 'no_new_frame')
        rec['last_seen_updated'] = updated
        try:
            image = Image.open(io.BytesIO(frame))
            image.load()
            image = image.convert('RGB')
        except (OSError, ValueError):
            return self._pause(rec, 'stale')
        # G4: exact known layout for this size/orientation.
        orientation = orientation_of(image.size)
        try:
            box = self.detector.preview_region(image.size, orientation)
            chrome_box = self.detector.chrome_region(image.size, orientation)
            _pixels(box, image.size)
        except (CameraTemplatesPending, ValueError, TypeError):
            return self._hard(sid, 'unknown_layout')
        # G5: Camera verified.
        if not self.detector.verify_camera_ui(frame):
            return self._pause(rec, 'camera_not_verified')
        # G6: unknown side (None) is allowed (current camera-mode policy); a side proven
        # to be the rear (False) or any unexpected value still ends the session.
        front = self.detector.verify_camera_front(frame)
        if front is not True and front is not None:
            return self._hard(sid, 'not_front')
        # G7: no known overlay, no banner.
        if self.detector.known_overlay(frame) or self.detector.banner_present(frame, orientation):
            return self._pause(rec, 'overlay')
        # G8: control strip unchanged versus the verified reference for this layout.
        key = image.size
        fingerprint = self.detector.chrome_fingerprint(image, chrome_box)
        reference = rec['chrome_refs'].get(key)
        if reference is None:
            rec['chrome_refs'][key] = fingerprint
        elif fingerprint_distance(reference, fingerprint) > CHROME_MAX_DISTANCE:
            return self._pause(rec, 'chrome_changed')
        # Content: crop first, then check black / frozen preview on the crop only.
        crop, body = encode_preview(image, box)
        if ImageStat.Stat(crop.convert('L')).stddev[0] < BLACK_STDDEV:
            return self._pause(rec, 'preview_black')
        digest = hashlib.sha256(crop.tobytes()).digest()
        if digest == rec['last_preview_digest']:
            # A static scene still differs by ~0.2 gray, so identical pixels mean a
            # repeated frame: never resend it; frozen only after FROZEN_AFTER_S.
            if now - rec['digest_since'] >= FROZEN_AFTER_S:
                return self._pause(rec, 'preview_frozen')
            return FrameResult(204, 'no_new_frame')
        rec['last_preview_digest'], rec['digest_since'] = digest, now
        if rec['state'] == 'paused':
            # Hysteresis only for resuming after a pause; the first frame of a
            # streaming start (or after the flip) goes out on its first pass.
            rec['ok_streak'] += 1
            if not rec['first_pending'] and rec['ok_streak'] < RESUME_PASSES:
                return FrameResult(409, 'resuming')
            rec['state'], rec['reason'] = 'streaming', None
            rec['paused_since'] = None
            log.info('camera session resumed')
        if rec['first_pending']:
            rec['first_pending'] = False
            log.info('camera first frame sent')
        rec['last_sent_updated'] = updated
        rec['sent'] += 1
        self._count('sent')
        return FrameResult(200, 'ok', body)


PHOTO_INSTRUCTIONS = {
    'photo_taken': 'One photo was taken and saved by the Camera app. Tell the owner. '
                   'Do not open, describe or promise to send the photo.',
    'photo_uncertain': 'The shutter was pressed once but the photo could not be confirmed. Tell the owner honestly '
                       'that you are not sure it was taken; never claim success and do not retry. They can ask again.',
    'photo_not_taken': 'No photo was taken (no tap was sent). Tell the owner; do not retry on your own. '
                       'They can ask again.',
}
PHOTO_REFUSALS = {
    'photo_mode_not_verified': 'No photo was taken: the Camera is not in PHOTO mode. Tell the owner; do not retry.',
    'shutter_target_unsafe': 'No photo was taken: something covers the shutter button (for example the '
                             'AssistiveTouch button). Tell the owner; do not retry.',
    'camera_not_verified': 'No photo was taken: the Camera controls are not verified right now. Tell the owner; '
                           'do not retry. They can ask again.',
}


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None
