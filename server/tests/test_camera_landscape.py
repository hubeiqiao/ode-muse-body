"""iPad LANDSCAPE 1646x1080 Camera detector (camera conversation).

Measurements: outputs/current-goal-20261028/evidence/camera-measure-20261029.
Every frame here is synthetic (drawn shapes + noise). Templates are crops of the
synthetic frame written to tmp_path; the optional real-template smoke test only
reads the masked control-strip crops from the evidence folder and is skipped
when that folder is absent. No device, network, model API or server.
"""
import asyncio
import io
import os
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from hidagent import camera_conversation as cc
from hidagent import hid_lease
from hidagent.live_voice import VoiceBridge, ToolCall

SID = 'e' * 32
LANDSCAPE = (1646, 1080)
EVIDENCE = Path(os.environ.get('CAMERA_EVIDENCE_TEMPLATES',
                               'runtime/local-evidence/'
                               'outputs/current-goal-20261028/evidence/camera-measure-20261029/templates'))
DISCS = {'flash': 106.5, 'live': 207.5, 'timer': 309.5, 'flip': 411.5}  # measured centres, x~1545, r~39.5


def camera_image(seed=0, disc_gray=60, shift=(0, 0), preview=True, column_black=True, shutter=True,
                 photo_mode=True, thumb=(200, 140, 60), assistive=None):
    """Synthetic Camera UI: noise preview x<1543, black column, translucent discs, white shutter."""
    w, h = LANDSCAPE
    img = Image.new('RGB', LANDSCAPE, (0, 0, 0))
    if preview:
        noise = Image.effect_noise((1543, h), 50 + seed).convert('RGB')
        img.paste(noise, (0, 0))
    draw = ImageDraw.Draw(img)
    dx, dy = shift
    for name, cy in DISCS.items():
        cy += dy
        draw.ellipse((1545 + dx - 39, cy - 39, 1545 + dx + 39, cy + 39), fill=(disc_gray,) * 3)
        # a glyph inside each disc so discs are not all identical
        if name == 'flip':
            draw.arc((1545 + dx - 16, cy - 16, 1545 + dx + 16, cy + 16), 200, 340, fill=(240,) * 3, width=3)
            draw.arc((1545 + dx - 16, cy - 16, 1545 + dx + 16, cy + 16), 20, 160, fill=(240,) * 3, width=3)
        elif name == 'timer':
            draw.ellipse((1545 + dx - 14, cy - 14, 1545 + dx + 14, cy + 14), outline=(240,) * 3, width=3)
        elif name == 'live':
            draw.ellipse((1545 + dx - 18, cy - 18, 1545 + dx + 18, cy + 18), outline=(230, 220, 0), width=4)
        else:
            draw.polygon([(1545 + dx, cy - 20), (1535 + dx, cy + 4), (1555 + dx, cy - 4), (1545 + dx, cy + 20)], fill=(240,) * 3)
    if shutter:
        draw.ellipse((1545 + dx - 57, 481 + dy, 1545 + dx + 57, 596 + dy), fill=(255,) * 3)
    draw.ellipse((1506, 628, 1585, 711), fill=thumb)                 # thumbnail
    yellow, white = (255, 214, 10), (230,) * 3
    draw.rectangle((1530, 794, 1600, 815), fill=white if photo_mode else yellow)   # VIDEO label
    draw.rounded_rectangle((1505, 843, 1610, 904), 25, fill=(50, 50, 40))  # PHOTO pill
    draw.rectangle((1512, 867, 1576, 881), fill=yellow if photo_mode else white)   # PHOTO label (selected = yellow)
    if assistive is not None:                                        # AssistiveTouch floating button
        draw.rounded_rectangle(assistive, 22, fill=(70, 70, 70))
        cx, cy = (assistive[0] + assistive[2]) // 2, (assistive[1] + assistive[3]) // 2
        draw.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=(235,) * 3)
    draw.ellipse((1626, 13, 1632, 19), fill=(0, 200, 0))             # camera-in-use dot
    if not column_black:
        draw.rectangle((1600, 600, 1646, 1000), fill=(180, 180, 180))
    return img


def jpeg(img, quality=92):
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=quality)
    return out.getvalue()


@pytest.fixture
def templates(tmp_path, monkeypatch):
    """Write synthetic templates with the real evidence file names and crops."""
    rear, front = camera_image(seed=1, disc_gray=12), camera_image(seed=2, disc_gray=60)
    rear.crop((1543, 0, 1646, 1080)).save(tmp_path / 'state0-static-column.png')
    rear.crop((1543, 366, 1588, 457)).save(tmp_path / 'state0-flip-righthalf.png')
    rear.crop((1543, 60, 1588, 457)).save(tmp_path / 'state0-rear-presumed-upper4-righthalf.png')
    front.crop((1543, 366, 1588, 457)).save(tmp_path / 'state1-front-presumed-flip-righthalf.png')
    front.crop((1543, 60, 1588, 457)).save(tmp_path / 'state1-front-presumed-upper4-righthalf.png')
    monkeypatch.setenv('CAMERA_TEMPLATE_DIR', str(tmp_path))
    cc.reset_template_cache()
    yield tmp_path
    cc.reset_template_cache()


@pytest.fixture(autouse=True)
def flag(monkeypatch):
    monkeypatch.setenv('CAMERA_CONVERSATION', '1')
    yield
    cc.end_all_instances('test_cleanup')


class Screen:
    def __init__(self, frame=None):
        self.frame, self.updated, self.live = frame, time.time(), True

    def mirror_health(self):
        return {'state': 'session_connected'}

    def show(self, frame, at=None):
        self.frame, self.updated = frame, (time.time() if at is None else at)


# ---------------------------------------------------------------- geometry

def test_landscape_preview_excludes_controls_thumbnail_and_top_band():
    box = cc.preview_region(LANDSCAPE, 'landscape')
    l, t, r, b = cc._pixels(box, LANDSCAPE)
    assert (l, b) == (0, 1080)
    assert r <= 1490          # left edge of the shutter disc / thumbnail (x~1490, 1506)
    assert r >= 1470          # but keep nearly all of the preview
    assert t >= 126           # status bar (0-33) + one/two-line banner (~126 px)
    chrome = cc._pixels(cc.chrome_region(LANDSCAPE, 'landscape'), LANDSCAPE)
    assert chrome == (1543, 0, 1646, 1080)


@pytest.mark.parametrize('size,orientation', [((1080, 1646), 'portrait'), ((1000, 700), 'landscape'),
                                              ((1646, 1080), 'portrait')])
def test_other_layouts_fail_closed(size, orientation):
    with pytest.raises(cc.CameraTemplatesPending):
        cc.preview_region(size, orientation)
    with pytest.raises(cc.CameraTemplatesPending):
        cc.chrome_region(size, orientation)


def test_flip_target_is_measured_centre_and_safe():
    x, y = cc.FLIP_TAP
    assert abs(x - 0.93864) < 1e-4 and abs(y - 0.38102) < 1e-4
    assert cc.flip_target_safe(cc.LANDSCAPE_BOXES['flip'], cc.flip_forbidden_boxes(), LANDSCAPE)


# ---------------------------------------------------------------- Camera UI

def test_verify_camera_ui_matches_both_states_and_small_shift(templates):
    assert cc.verify_camera_ui(jpeg(camera_image(seed=5, disc_gray=12))) is True
    assert cc.verify_camera_ui(jpeg(camera_image(seed=6, disc_gray=60))) is True
    assert cc.verify_camera_ui(jpeg(camera_image(seed=7, disc_gray=30, shift=(1, 2)))) is True


def test_verify_camera_ui_rejects_non_camera_frames(templates):
    noise = Image.effect_noise(LANDSCAPE, 70).convert('RGB')
    assert cc.verify_camera_ui(jpeg(noise)) is False
    assert cc.verify_camera_ui(jpeg(camera_image(column_black=False))) is False
    assert cc.verify_camera_ui(jpeg(camera_image(shutter=False))) is False
    dark_app = Image.new('RGB', LANDSCAPE, (0, 0, 0))
    assert cc.verify_camera_ui(jpeg(dark_app)) is False
    portrait = camera_image().rotate(90, expand=True)
    assert cc.verify_camera_ui(jpeg(portrait)) is False
    assert cc.verify_camera_ui(b'not an image') is False
    assert cc.verify_camera_ui(None) is False


def test_missing_templates_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv('CAMERA_TEMPLATE_DIR', raising=False)
    cc.reset_template_cache()
    frame = jpeg(camera_image())
    assert cc.verify_camera_ui(frame) is False
    assert cc.recognised_camera_ui(frame) is False
    monkeypatch.setenv('CAMERA_TEMPLATE_DIR', str(tmp_path / 'absent'))
    cc.reset_template_cache()
    assert cc.verify_camera_ui(frame) is False
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(frame))
    assert det.ready() == (False, 'camera_templates_missing')
    cc.reset_template_cache()


def test_recognised_camera_ui_activates_frame_privacy_rule_b(templates, monkeypatch):
    from hidagent.decision import DecisionUnavailable
    from hidagent.frame_privacy import check_cloud_frame
    monkeypatch.setenv('WITHHOLD_CAMERA_UI', '1')
    camera = jpeg(camera_image(seed=8))
    assert cc.recognised_camera_ui(camera) is True
    assert not cc.active_any()
    with pytest.raises(DecisionUnavailable, match='Camera UI visible'):
        check_cloud_frame(camera)
    check_cloud_frame(jpeg(Image.effect_noise(LANDSCAPE, 40).convert('RGB')))


def test_detector_ready_only_for_landscape_with_templates(templates):
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(jpeg(camera_image())))
    assert det.ready() == (True, 'ready')
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(jpeg(Image.new('RGB', (1080, 1646)))))
    assert det.ready() == (False, 'camera_layout_unsupported')
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(None))
    assert det.ready() == (False, 'camera_layout_unsupported')


# ---------------------------------------------------------------- banner

def _banner(img, bright=True):
    img = img.copy()
    draw = ImageDraw.Draw(img)
    fill = (245, 245, 245) if bright else (40, 40, 44)
    draw.rounded_rectangle((520, 14, 1130, 180), 28, fill=fill)
    return img


def test_banner_first_frame_is_uncertain_then_stable_scene_clear():
    clock = [100.0]
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(), clock=lambda: clock[0])
    base = camera_image(seed=10)
    assert det.banner_present(jpeg(base), 'landscape') is True       # no history: uncertain
    for _ in range(3):
        clock[0] += 1
        assert det.banner_present(jpeg(base), 'landscape') is False


@pytest.mark.parametrize('bright', [True, False])
def test_sudden_top_centre_rounded_panel_is_a_banner_and_held(bright):
    clock = [100.0]
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(), clock=lambda: clock[0])
    base = camera_image(seed=11)
    det.banner_present(jpeg(base), 'landscape')
    clock[0] += 1
    assert det.banner_present(jpeg(base), 'landscape') is False
    clock[0] += 1
    assert det.banner_present(jpeg(_banner(base, bright)), 'landscape') is True
    clock[0] += 1
    assert det.banner_present(jpeg(_banner(base, bright)), 'landscape') is True   # unchanged, still held
    clock[0] += cc.BANNER_HOLD_S + 1
    # A persistent banner still differs from the pre-banner reference: keep refusing.
    assert det.banner_present(jpeg(_banner(base, bright)), 'landscape') is True
    clock[0] += 1
    assert det.banner_present(jpeg(base), 'landscape') is True   # dismissal is itself a jump: held again
    clock[0] += cc.BANNER_HOLD_S + 1
    assert det.banner_present(jpeg(base), 'landscape') is False


def test_global_scene_change_is_not_a_banner():
    clock = [100.0]
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen(), clock=lambda: clock[0])
    base = camera_image(seed=12)
    det.banner_present(jpeg(base), 'landscape')
    clock[0] += 1
    assert det.banner_present(jpeg(base), 'landscape') is False
    clock[0] += 1
    brighter = base.point(lambda v: min(255, v + 70))      # lights on: band AND body change together
    assert det.banner_present(jpeg(brighter), 'landscape') is False


def test_banner_unknown_layout_or_bad_frame_is_uncertain():
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen())
    assert det.banner_present(jpeg(Image.new('RGB', (1080, 1646))), 'portrait') is True
    assert det.banner_present(b'xx', 'landscape') is True
    assert cc.banner_present(jpeg(camera_image()), 'landscape') is True   # module stub keeps failing closed


# ---------------------------------------------------------------- front confirmation

class OwnerDetector:
    """Real-detector front semantics (unknown), everything else permissive."""
    def __init__(self):
        self.calls = []

    def ready(self):
        return True, 'ready'

    def reset(self):
        self.calls.append('reset')

    def preview_region(self, size, orientation):
        return cc.preview_region(size, orientation)

    def chrome_region(self, size, orientation):
        return cc.chrome_region(size, orientation)

    def chrome_fingerprint(self, image, box):
        return cc.region_fingerprint(image, box)

    def verify_camera_ui(self, frame):
        return True

    def verify_camera_front(self, frame):
        return None

    def banner_present(self, frame, orientation):
        return False

    def known_overlay(self, frame):
        return None

    async def launch_camera(self):
        self.calls.append('launch')

    async def flip_to_front(self):
        self.calls.append(('flip', hid_lease_allowed('click'), hid_lease_allowed('key space')))

    async def leave_camera(self):
        self.calls.append('leave')


def hid_lease_allowed(command):
    try:
        hid_lease.check_hid(command)
        return True
    except RuntimeError:
        return False


def test_module_verify_camera_front_cannot_prove_front():
    assert cc.verify_camera_front(jpeg(camera_image())) is None
    det = cc.LandscapeCameraDetector(phone=None, screen=Screen())
    assert det.verify_camera_front(jpeg(camera_image())) is None


def _camera_bridge(det):
    screen = Screen(jpeg(camera_image(seed=20)))
    cam = cc.CameraConversation(screen, detector=det)
    state = {'device': True, 'screen': True, 'calibrated': True, 'target': {'device': 'ipad'},
             'models': {'planner': 'p'}, 'agent': {'running': False, 'task': '', 'steps': []}}

    async def noop(*a):
        return None
    bridge = VoiceBridge(lambda: state, noop, noop)
    bridge.camera = cam
    gen = cam.begin(SID)
    bridge.sessions[SID] = {'expires': time.monotonic() + 600, 'calls': {}, 'paused': False,
                            'cancelled_ids': set(), 'camera': {'allowed': True, 'generation': gen, 'max_s': 540}}
    cam.flip_settle_s = 0
    return cam, screen, bridge, gen


def test_unknown_front_streams_at_once_without_owner_confirmation():
    # Owner decision 2026-10-29 (a): no spoken front confirmation. Camera UI verified (G5)
    # and front unknown -> streaming immediately; Gemini judges the first frames itself.
    det = OwnerDetector()
    cam, screen, bridge, gen = _camera_bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    assert det.calls == ['reset', 'launch']           # no automatic flip click
    assert cam.sessions[SID]['state'] == 'streaming'
    screen.show(jpeg(camera_image(seed=21)))
    r = cam.frame(SID, gen)
    assert r.status == 200 and r.body[:2] == b'\xff\xd8'


def test_prepare_without_verified_camera_ui_never_streams():
    det = OwnerDetector()
    det.verify_camera_ui = lambda frame: False
    cam, screen, bridge, gen = _camera_bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    assert cam.sessions[SID]['state'] == 'ended' and cam.sessions[SID]['reason'] == 'camera_not_verified'


def test_confirm_front_camera_is_a_noop_compatible_tool():
    det = OwnerDetector()
    cam, screen, bridge, gen = _camera_bridge(det)
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='c0', name='confirm_front_camera', args={})))
    assert r['status'] == 'not_ready' and cam.sessions[SID]['state'] == 'launching'
    asyncio.run(cam.prepare(SID, gen))
    baseline = cam.sessions[SID]['hid_baseline']
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='c1', name='confirm_front_camera', args={},
                                         utterance='yes, it shows me')))
    assert r['status'] == 'camera_streaming' and r['terminal'] is True
    assert cam.sessions[SID]['state'] == 'streaming' and cam.sessions[SID]['hid_baseline'] == baseline


def test_flip_while_streaming_accepts_late_transcription_one_click_then_first_frame_at_once():
    det = OwnerDetector()
    cam, screen, bridge, gen = _camera_bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    screen.show(jpeg(camera_image(seed=24)))
    assert cam.frame(SID, gen).status == 200
    # Live may emit the call before its input transcription.
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='f1', name='flip_camera', args={})))
    assert r['status'] == 'camera_flipped'
    assert det.calls[-1] == ('flip', True, False)     # click allowed inside the flip window, keys refused
    assert cam.sessions[SID]['state'] == 'streaming'
    # The flip's own click is re-baselined (no hid_activity) and the first new frame goes out at once.
    screen.show(jpeg(camera_image(seed=25)))
    r = cam.frame(SID, gen)
    assert r.status == 200, r
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='f2', name='flip_camera', args={}, utterance='again')))
    assert r['status'] == 'rejected' and r['reason'] == 'flip_already_used'
    assert sum(isinstance(c, tuple) for c in det.calls) == 1


def test_flip_asked_before_camera_is_streaming_is_queued_not_clicked():
    # Live 2026-10-29: "please flip the camera" while Camera was launching was rejected.
    # It is now queued (no HID yet) and done once right after the Camera UI is verified.
    det = OwnerDetector()
    cam, screen, bridge, gen = _camera_bridge(det)
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='f', name='flip_camera', args={}, utterance='flip')))
    assert r['status'] == 'flip_queued' and r['terminal'] is True
    assert not any(isinstance(c, tuple) for c in det.calls)
    cam.sessions[SID]['state'] = 'flipping'
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='g', name='flip_camera', args={}, utterance='flip')))
    assert r['status'] == 'rejected' and r['reason'] == 'flip_already_used'


def test_camera_tools_declared_and_prompt_explains_first_frames(monkeypatch):
    from hidagent.live_voice import camera_context, CAMERA_TOOL_NAMES
    system, tools = camera_context({'target': {'device': 'ipad'}})
    names = [d['name'] for d in tools[0]['functionDeclarations']]
    assert names == ['end_camera_conversation', 'stop_phone', 'flip_camera', 'take_photo', 'return_to_device_mode']
    assert 'flip_camera' in CAMERA_TOOL_NAMES and 'confirm_front_camera' in CAMERA_TOOL_NAMES  # compat no-op
    assert 'confirm_front_camera' not in system
    assert 'first frames' in system and 'flip_camera' in system and 'agrees' in system


# ---------------------------------------------------------------- HID wire formats

def _wire_phone(sent, frame_size=LANDSCAPE):
    """A real Phone whose wire layer records commands after the real check_hid."""
    from hidagent.phone import Phone

    class Device:
        links, connected = 1, True
    phone = Phone.__new__(Phone)
    phone.device = Device()
    phone.screen = None
    phone.profiles = None
    phone._typing_tuned_link = None
    phone._pos = None
    phone._verified_home_probe_hint = None

    async def ensure_live(force_probe=False):
        return None

    async def _send(command):
        hid_lease.check_hid(command)
        sent.append(command)
    phone.ensure_keyboard_live = ensure_live
    phone._send = _send
    phone.target_info = lambda: {'device': 'ipad', 'orientation': 'landscape', 'width': frame_size[0],
                                 'height': frame_size[1], 'calibration_key': 'ipad-landscape-66'}
    return phone


class LaunchScreen(Screen):
    """Spotlight (static) until 'key enter' is on the wire, then a live Camera preview."""
    def __init__(self, sent, frames):
        super().__init__(jpeg(Image.effect_noise(LANDSCAPE, 30).convert('RGB')))
        self.sent, self.frames, self.reads = sent, frames, 0

    @property
    def frame(self):
        if 'key enter' not in self.sent:
            return self._frame
        self.reads += 1
        return self.frames[self.reads % len(self.frames)]

    @frame.setter
    def frame(self, value):
        self._frame = value


def _fake_time():
    now = [1000.0]

    async def sleep(seconds):
        now[0] += max(seconds, 0.01)
    return now, (lambda: now[0]), sleep


def test_launch_sends_measured_wire_commands_inside_whitelist(templates, monkeypatch):
    monkeypatch.delenv('HID_KEY_MS', raising=False)
    sent = []
    frames = [jpeg(camera_image(seed=30 + i)) for i in range(4)]
    screen = LaunchScreen(sent, frames)
    now, clock, sleep = _fake_time()
    det = cc.LandscapeCameraDetector(_wire_phone(sent), screen, clock=clock, sleep=sleep)
    cam = cc.CameraConversation(screen, detector=det)
    gen = cam.begin(SID)
    asyncio.run(cam.prepare(SID, gen))
    # Home first; this uncalibrated wire phone cannot tap the icon, so Spotlight follows.
    assert sent == ['key esc', 'key cmd+h', 'key cmd+space', 'type Camera', 'key enter']
    assert cam.sessions[SID]['state'] == 'streaming'


def test_launch_whitelist_matches_phone_wire_format():
    # Phone.key('enter'/'return') puts 'key enter' on the wire, never 'key return'.
    assert 'key enter' in cc.LAUNCH_WHITELIST and 'key return' not in cc.LAUNCH_WHITELIST
    cam = cc.CameraConversation(Screen(), detector=OwnerDetector())
    cam.begin(SID)
    with cc.camera_hid_window(cc.LAUNCH_WHITELIST):
        for command in ('key cmd+space', 'type Camera', 'key enter', 'move 3 0', 'move -3 0', 'typing 30'):
            hid_lease.check_hid(command)
        for command in ('key cmd+a', 'type Camerax', 'key space', 'key enter extra'):
            with pytest.raises(RuntimeError):
                hid_lease.check_hid(command)
    with cc.camera_hid_window(cc.FLIP_WHITELIST):
        for command in ('home', 'home -1 -1', 'move 4 5', 'click'):
            hid_lease.check_hid(command)
        for command in ('key enter', 'press', 'type x', 'scroll 1 0'):
            with pytest.raises(RuntimeError):
                hid_lease.check_hid(command)
    with cc.camera_hid_window(cc.LEAVE_WHITELIST):
        hid_lease.check_hid('key cmd+h')
        with pytest.raises(RuntimeError):
            hid_lease.check_hid('key cmd+space')


def test_launch_without_live_preview_fails_closed(templates):
    sent = []
    frozen = jpeg(camera_image(seed=40))
    screen = LaunchScreen(sent, [frozen])                 # Camera UI, but byte-identical frames
    now, clock, sleep = _fake_time()
    det = cc.LandscapeCameraDetector(_wire_phone(sent), screen, clock=clock, sleep=sleep)
    cam = cc.CameraConversation(screen, detector=det)
    gen = cam.begin(SID)
    asyncio.run(cam.prepare(SID, gen))
    assert cam.sessions[SID]['state'] == 'ended'
    assert cam.sessions[SID]['reason'] == 'camera_not_live'


def test_launch_refuses_portrait_before_any_hid(templates):
    sent = []
    screen = Screen(jpeg(Image.new('RGB', (1080, 1646))))
    det = cc.LandscapeCameraDetector(_wire_phone(sent, (1080, 1646)), screen)
    cam = cc.CameraConversation(screen, detector=det)
    gen = cam.begin(SID)
    asyncio.run(cam.prepare(SID, gen))
    assert sent == []
    assert cam.sessions[SID]['state'] == 'ended'
    assert cam.sessions[SID]['reason'] == 'camera_layout_unsupported'


def test_leave_sends_one_cmd_h(templates):
    sent = []
    screen = Screen(jpeg(camera_image()))
    det = cc.LandscapeCameraDetector(_wire_phone(sent), screen)
    cam = cc.CameraConversation(screen, detector=det)
    gen = cam.begin(SID)
    cam.end(SID, 'owner_end')
    assert asyncio.run(cam.leave()) == {'camera_left_foreground': True}
    assert sent == ['key cmd+h']


class TapPhone:
    def __init__(self, screen, change_to=None):
        self.taps, self.screen, self.change_to = [], screen, change_to

    async def tap(self, x, y, aim=False, pre_click=None, **kw):
        if self.change_to is not None:
            self.screen.show(self.change_to)
        ok = pre_click() if pre_click else True
        self.taps.append((x, y, aim, ok))
        if not ok:
            raise RuntimeError('no click sent')


def test_flip_is_one_precise_tap_on_measured_centre(templates):
    screen = Screen(jpeg(camera_image(seed=50)))
    phone = TapPhone(screen)
    det = cc.LandscapeCameraDetector(phone, screen)
    asyncio.run(det.flip_to_front())
    assert len(phone.taps) == 1
    x, y, aim, ok = phone.taps[0]
    assert (round(x, 5), round(y, 5), aim, ok) == (0.93864, 0.38102, True, True)


def test_flip_refused_without_verified_ui_or_safe_target(templates, monkeypatch):
    screen = Screen(jpeg(Image.effect_noise(LANDSCAPE, 60).convert('RGB')))
    phone = TapPhone(screen)
    det = cc.LandscapeCameraDetector(phone, screen)
    with pytest.raises(cc.CameraTemplatesPending):
        asyncio.run(det.flip_to_front())
    screen.show(jpeg(camera_image(seed=51)))
    monkeypatch.setattr(cc, 'flip_target_safe', lambda *a: False)
    with pytest.raises(cc.CameraTemplatesPending):
        asyncio.run(det.flip_to_front())
    assert phone.taps == []


def test_flip_pre_click_guard_blocks_when_camera_ui_disappears(templates):
    screen = Screen(jpeg(camera_image(seed=52)))
    phone = TapPhone(screen, change_to=jpeg(Image.effect_noise(LANDSCAPE, 61).convert('RGB')))
    det = cc.LandscapeCameraDetector(phone, screen)
    with pytest.raises(RuntimeError):
        asyncio.run(det.flip_to_front())
    assert phone.taps[0][3] is False


# ---------------------------------------------------------------- end-to-end gates with the real detector

def test_real_detector_first_frame_is_sent_at_once_and_resume_keeps_hysteresis(templates):
    clock = [time.time()]
    screen = Screen()
    screen.show(jpeg(camera_image(seed=60)), at=clock[0])
    det = cc.LandscapeCameraDetector(phone=None, screen=screen, clock=lambda: clock[0])
    cam = cc.CameraConversation(screen, detector=det, clock=lambda: clock[0])
    gen = cam.begin(SID)
    cam.sessions[SID]['state'] = 'camera_check'
    assert cam.mark_streaming(SID, gen)
    results = []
    for seed in range(61, 64):
        clock[0] += 1
        screen.show(jpeg(camera_image(seed=seed)), at=clock[0])
        results.append(cam.frame(SID, gen))
    # First passing frame goes out at once (banner history primed when streaming began).
    assert [r.reason for r in results] == ['ok', 'ok', 'ok']
    body = results[0].body
    with Image.open(io.BytesIO(body)) as out:
        assert max(out.size) <= 768
        assert abs(out.size[0] / out.size[1] - 1480 / 940) < 0.02
    # A transient pause after streaming started still needs two clean passes to resume.
    clock[0] += 1
    screen.show(jpeg(_banner(camera_image(seed=64))), at=clock[0])
    assert cam.frame(SID, gen).reason == 'overlay'
    clock[0] += 1
    screen.show(jpeg(camera_image(seed=69)), at=clock[0])
    assert cam.frame(SID, gen).reason == 'overlay'      # the dismissal is itself a jump: held
    clock[0] += cc.BANNER_HOLD_S + 1
    reasons = []
    for seed in range(65, 68):
        clock[0] += 1
        screen.show(jpeg(camera_image(seed=seed)), at=clock[0])
        reasons.append(cam.frame(SID, gen).reason)
    assert reasons == ['resuming', 'ok', 'ok'], reasons


def test_frozen_means_identical_for_two_seconds():
    from test_camera_conversation import FakeDetector, synthetic, FakeScreen
    now = [time.time()]
    screen = FakeScreen()
    cam = cc.CameraConversation(screen, detector=FakeDetector(), clock=lambda: now[0])
    gen = cam.begin(SID)
    screen.show(synthetic(seed=70), at=now[0] - .2)
    cam.mark_streaming(SID, gen)
    same = synthetic(seed=71)
    now[0] += .5
    screen.show(same, at=now[0])
    assert cam.frame(SID, gen).status == 200
    now[0] += 1.0
    screen.show(same, at=now[0])
    r = cam.frame(SID, gen)
    assert r.status == 204 and r.reason == 'no_new_frame'      # identical < 2 s: not resent, not frozen
    now[0] += 1.2
    screen.show(same, at=now[0])
    r = cam.frame(SID, gen)
    assert r.status == 409 and r.reason == 'preview_frozen'


# ---------------------------------------------------------------- real masked templates (optional)

@pytest.mark.skipif(not (EVIDENCE / 'state0-static-column.png').exists(), reason='evidence templates absent')
def test_real_masked_templates_match_composed_frame(monkeypatch):
    monkeypatch.setenv('CAMERA_TEMPLATE_DIR', str(EVIDENCE))
    cc.reset_template_cache()
    try:
        frame = Image.effect_noise(LANDSCAPE, 50).convert('RGB')
        frame.paste(Image.open(EVIDENCE / 'state0-static-column.png').convert('RGB'), (1543, 0))
        assert cc.verify_camera_ui(jpeg(frame)) is True
        frame.paste(Image.open(EVIDENCE / 'state1-front-presumed-upper4-righthalf.png').convert('RGB'), (1543, 60))
        assert cc.verify_camera_ui(jpeg(frame)) is True
        assert cc.verify_camera_ui(jpeg(Image.effect_noise(LANDSCAPE, 50).convert('RGB'))) is False
    finally:
        cc.reset_template_cache()


@pytest.mark.parametrize('same_pixels', [False, True])
def test_live_accepts_slow_irregular_received_frames(same_pixels):
    # A decoder can receive an identical image; byte changes are not a frame clock.
    screen = Screen(b'camera-0')
    now, clock, _ = _fake_time()
    updates = iter([1000.8, 1002.5, 1004.2])
    due = [next(updates)]
    count = [0]

    async def sleep(seconds):
        now[0] += seconds
        if now[0] >= due[0]:
            count[0] += 1
            screen.show(b'camera-0' if same_pixels else str(count[0]).encode())
            due[0] = next(updates, float('inf'))

    det = cc.LandscapeCameraDetector(None, screen, clock=clock, sleep=sleep)
    assert asyncio.run(det._live()) is True


@pytest.mark.parametrize('bad', ['frozen', 'stale', 'disconnected'])
def test_live_rejects_frozen_stale_and_disconnected_mirror(bad):
    screen = Screen(b'camera')
    if bad == 'stale':
        screen.updated = time.time() - 30
    if bad == 'disconnected':
        screen.mirror_health = lambda: {'state': 'source_disconnected'}
    now, clock, _ = _fake_time()

    async def sleep(seconds):
        now[0] += seconds
        if bad != 'frozen':
            screen.frame = str(now[0]).encode()

    det = cc.LandscapeCameraDetector(None, screen, clock=clock, sleep=sleep)
    assert asyncio.run(det._live()) is False


def test_already_open_camera_flip_does_not_relaunch(templates):
    sent = []
    screen = Screen(jpeg(camera_image(seed=90)))
    now, clock, _ = _fake_time()

    async def sleep(seconds):
        now[0] += seconds
        screen.updated = time.time()

    det = cc.LandscapeCameraDetector(_wire_phone(sent), screen, clock=clock, sleep=sleep)
    flips = []

    async def flip():
        assert cc.verify_camera_ui(screen.frame)
        flips.append('flip')

    det.flip_to_front = flip
    cam = cc.CameraConversation(screen, detector=det)
    cam.flip_settle_s = 0
    gen = cam.begin(SID)
    cam.sessions[SID]['flip_queued'] = True
    asyncio.run(cam.prepare(SID, gen))
    assert sent == [], 'Already foreground Camera must not receive Spotlight or typing'
    assert flips == ['flip']
    assert cam.status(SID)['camera_state'] == 'streaming'
