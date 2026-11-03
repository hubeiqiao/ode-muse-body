"""Camera conversation: take_photo, queued flip, return_to_device_mode and companion prompts.

Owner decision (Joe, 2026-10-29 live run): the iPad is the AI's own device. In a camera
conversation the AI can talk, see the owner, flip_camera and take_photo; any other device
action goes through return_to_device_mode (back to a normal voice session).
Every frame here is synthetic. No device, network, model API or disk writes.
"""
import asyncio
import builtins
import hashlib
import json
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from hidagent import camera_conversation as cc
from hidagent import hid_lease
from hidagent.live_voice import VoiceBridge, ToolCall, VoiceControl, camera_context

from test_camera_landscape import (LANDSCAPE, Screen, camera_image, jpeg, templates, hid_lease_allowed,  # noqa: F401
                                   OwnerDetector)

SID = 'p' * 32
FIXTURES = Path(__file__).with_name('fixtures')
AT_DEFAULT = (1523, 548, 1633, 660)          # measured AssistiveTouch box (overlaps shutter lower-right)
DENIAL = re.compile(r"cannot (operate|control|take|open)|can't (operate|control|take|open)|unable to (operate|control)"
                    r"|not able to (operate|control)|cannot operate|only an AI|end the camera conversation first", re.I)


@pytest.fixture(autouse=True)
def flag(monkeypatch):
    monkeypatch.setenv('CAMERA_CONVERSATION', '1')
    yield
    cc.end_all_instances('test_cleanup')


def _fake_time():
    now = [1000.0]

    async def sleep(seconds):
        now[0] += max(seconds, 0.01)
    return now, (lambda: now[0]), sleep


class ShutterPhone:
    """Records precise taps; runs the pre-click guard like Phone.tap; optional screen change."""
    def __init__(self, screen, before_click=None, after_click=None, fail_after_click=False):
        self.screen, self.before_click, self.after_click = screen, before_click, after_click
        self.fail_after_click = fail_after_click
        self.taps, self.clicks = [], 0

    async def tap(self, x, y, aim=False, pre_click=None, **kw):
        hid_lease.check_hid('home')
        hid_lease.check_hid('move 3 4')
        if self.before_click is not None:
            self.screen.show(self.before_click)
        ok = pre_click() if pre_click else True
        self.taps.append((x, y, aim, ok))
        if not ok:
            raise RuntimeError('Learned target context changed during pointer movement; no click sent')
        hid_lease.check_hid('click')
        self.clicks += 1
        if self.fail_after_click:
            raise RuntimeError('ACK lost')
        if self.after_click is not None:
            self.screen.show(self.after_click)


def _detector(screen, phone):
    now, clock, sleep = _fake_time()
    return cc.LandscapeCameraDetector(phone, screen, clock=clock, sleep=sleep)


# ---------------------------------------------------------------- geometry / local checks

def test_shutter_aim_point_inside_disc_and_outside_assistive_touch():
    x, y = cc.SHUTTER_TAP_PX
    cx, cy, radius = cc.SHUTTER_DISC
    assert ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= radius - cc.SHUTTER_DISC_MARGIN
    l, t, r, b = cc.ASSISTIVE_TOUCH_PX
    assert (l, t, r, b) == AT_DEFAULT
    assert y <= t - cc.ASSISTIVE_TOUCH_MARGIN                       # upper-left part of the shutter
    tl, tt, tr, tb = cc._pixels(cc.LANDSCAPE_BOXES['thumbnail'], LANDSCAPE)
    assert not (tl <= x <= tr and tt <= y <= tb)
    assert abs(cc.SHUTTER_TAP[0] * 1646 - x) < 1e-6 and abs(cc.SHUTTER_TAP[1] * 1080 - y) < 1e-6
    assert cc.shutter_aim_safe(camera_image(seed=1)) is True
    # AssistiveTouch dragged over the aim point: the current frame refuses the tap.
    assert cc.shutter_aim_safe(camera_image(seed=1, assistive=(1500, 470, 1580, 550))) is False
    assert cc.shutter_aim_safe(camera_image(seed=1, assistive=(1490, 480, 1542, 535))) is False
    assert cc.shutter_aim_safe(camera_image(seed=1, assistive=(1540, 470, 1600, 530))) is False
    # Shutter not white (missing) at the aim point.
    assert cc.shutter_aim_safe(camera_image(seed=1, shutter=False)) is False


def test_shutter_aim_safe_rejects_a_point_in_the_assistive_touch_box(monkeypatch):
    monkeypatch.setattr(cc, 'SHUTTER_TAP_PX', (1560, 570))         # inside the AT box
    assert cc.shutter_aim_safe(camera_image(seed=2)) is False
    monkeypatch.setattr(cc, 'SHUTTER_TAP_PX', (1500, 530))         # too close to the disc edge
    assert cc.shutter_aim_safe(camera_image(seed=2)) is False


def test_photo_mode_is_read_from_the_control_column():
    assert cc.photo_mode_selected(camera_image(seed=3)) is True
    assert cc.photo_mode_selected(camera_image(seed=3, photo_mode=False)) is False   # VIDEO selected
    assert cc.photo_mode_selected(Image.new('RGB', LANDSCAPE)) is False
    assert cc.photo_mode_selected(Image.new('RGB', (1080, 1646), (255, 214, 10))) is False


def test_thumbnail_stays_outside_the_preview_crop():
    l, t, r, b = cc._pixels(cc.LANDSCAPE_PREVIEW, LANDSCAPE)
    tl, tt, tr, tb = cc._pixels(cc.LANDSCAPE_BOXES['thumbnail'], LANDSCAPE)
    assert r < tl                                                   # crop ends before the thumbnail
    pl, pt, pr, pb = cc.THUMB_PROBE
    assert tl <= pl and pr <= tr and tt <= pt and pb <= tb
    assert pt >= cc.ASSISTIVE_TOUCH_PX[3]                           # probe below the AssistiveTouch box
    centre, radius = (1545.5, 669.5), 39.5                          # the thumbnail is a disc
    for x, y in ((pl, pt), (pr, pt), (pl, pb), (pr, pb)):
        assert ((x - centre[0]) ** 2 + (y - centre[1]) ** 2) ** 0.5 < radius   # no live-preview corners


# ---------------------------------------------------------------- detector.take_photo

def test_take_photo_one_precise_tap_then_photo_taken(templates):
    screen = Screen(jpeg(camera_image(seed=10)))
    phone = ShutterPhone(screen, after_click=jpeg(camera_image(seed=11, thumb=(40, 90, 200))))
    det = _detector(screen, phone)
    result = asyncio.run(det.take_photo())
    assert result['status'] == 'photo_taken'
    assert len(phone.taps) == 1 and phone.clicks == 1
    x, y, aim, ok = phone.taps[0]
    assert (round(x * 1646), round(y * 1080), aim, ok) == (cc.SHUTTER_TAP_PX[0], cc.SHUTTER_TAP_PX[1], True, True)


def test_take_photo_uncertain_when_thumbnail_unchanged_and_never_retries(templates):
    screen = Screen(jpeg(camera_image(seed=12)))
    phone = ShutterPhone(screen, after_click=jpeg(camera_image(seed=13)))   # same thumbnail
    det = _detector(screen, phone)
    result = asyncio.run(det.take_photo())
    assert result == {'status': 'photo_uncertain', 'reason': 'thumbnail_unchanged'}
    assert phone.clicks == 1


def test_take_photo_uncertain_when_camera_left_after_tap(templates):
    screen = Screen(jpeg(camera_image(seed=14)))
    away = Image.effect_noise(LANDSCAPE, 70).convert('RGB')        # Photos / another app
    phone = ShutterPhone(screen, after_click=jpeg(away))
    det = _detector(screen, phone)
    result = asyncio.run(det.take_photo())
    assert result == {'status': 'photo_uncertain', 'reason': 'camera_not_verified'}
    assert phone.clicks == 1


def test_take_photo_uncertain_when_the_click_ack_is_lost(templates):
    screen = Screen(jpeg(camera_image(seed=15)))
    phone = ShutterPhone(screen, fail_after_click=True)
    result = asyncio.run(_detector(screen, phone).take_photo())
    assert result == {'status': 'photo_uncertain', 'reason': 'tap_uncertain'}
    assert phone.clicks == 1


@pytest.mark.parametrize('frame,reason', [
    (lambda: Image.effect_noise(LANDSCAPE, 60).convert('RGB'), 'camera_not_verified'),
    (lambda: camera_image(seed=16, photo_mode=False), 'photo_mode_not_verified'),
])
def test_take_photo_refused_before_any_hid(templates, frame, reason):
    screen = Screen(jpeg(frame()))
    phone = ShutterPhone(screen)
    det = _detector(screen, phone)
    with pytest.raises(cc.CameraTemplatesPending) as error:
        asyncio.run(det.take_photo())
    assert error.value.reason == reason
    assert phone.taps == [] and phone.clicks == 0


def test_take_photo_pre_click_guard_sends_no_click(templates):
    screen = Screen(jpeg(camera_image(seed=17)))
    phone = ShutterPhone(screen, before_click=jpeg(Image.effect_noise(LANDSCAPE, 61).convert('RGB')))
    result = asyncio.run(_detector(screen, phone).take_photo())
    assert result == {'status': 'photo_not_taken', 'reason': 'no_click_sent'}
    assert phone.taps[0][3] is False and phone.clicks == 0


def test_take_photo_stop_before_click_sends_no_click(templates):
    screen = Screen(jpeg(camera_image(seed=18)))
    phone = ShutterPhone(screen)
    result = asyncio.run(_detector(screen, phone).take_photo(alive=lambda: False))
    assert result['status'] == 'photo_not_taken' and phone.clicks == 0


def test_take_photo_never_writes_files(templates, monkeypatch):
    screen = Screen(jpeg(camera_image(seed=19)))
    phone = ShutterPhone(screen, after_click=jpeg(camera_image(seed=20, thumb=(10, 200, 30))))
    det = _detector(screen, phone)
    det.ready()  # templates loaded before the guard
    cc.load_templates()
    real_open = builtins.open

    def guarded(file, mode='r', *a, **k):
        if any(c in mode for c in 'wax+'):
            raise AssertionError('take_photo must not write: %r' % (file,))
        return real_open(file, mode, *a, **k)
    monkeypatch.setattr(builtins, 'open', guarded)
    monkeypatch.setattr(Image.Image, 'save', lambda *a, **k: (_ for _ in ()).throw(AssertionError('save')))
    assert asyncio.run(det.take_photo())['status'] == 'photo_taken'


def test_stub_detector_take_photo_fails_closed():
    with pytest.raises(cc.CameraTemplatesPending):
        asyncio.run(cc.StubDetector().take_photo())


# ---------------------------------------------------------------- conversation / bridge

class PhotoDetector(OwnerDetector):
    """OwnerDetector plus a scripted take_photo that sends real whitelisted HID."""
    def __init__(self, outcome=None):
        super().__init__()
        self.outcome = outcome or {'status': 'photo_taken'}
        self.photo_calls = []

    async def take_photo(self, alive=None):
        if isinstance(self.outcome, Exception):
            self.photo_calls.append(('raised', alive() if alive else None))  # refused before any HID
            raise self.outcome
        self.photo_calls.append((hid_lease_allowed('click'), hid_lease_allowed('key enter'), alive() if alive else None))
        return dict(self.outcome)


def _bridge(det, clock=None):
    from test_camera_conversation import FakeScreen, synthetic
    screen = FakeScreen()
    kwargs = {'clock': clock} if clock else {}
    cam = cc.CameraConversation(screen, detector=det, **kwargs)
    state = {'device': True, 'screen': True, 'calibrated': True, 'target': {'device': 'ipad'},
             'models': {'planner': 'p'}, 'agent': {'running': False, 'task': '', 'steps': []}}
    stops = []

    async def start(goal):
        state['agent']['task'] = goal

    async def stop():
        stops.append(1)
    bridge = VoiceBridge(lambda: state, start, stop)
    bridge.camera = cam
    gen = cam.begin(SID)
    bridge.sessions[SID] = {'expires': time.monotonic() + 600, 'calls': {}, 'paused': False,
                            'cancelled_ids': set(), 'camera': {'allowed': True, 'generation': gen, 'max_s': 540}}
    cam.flip_settle_s = 0
    screen.show(synthetic(seed=1), at=time.time() - 0.2)
    return cam, screen, bridge, gen, state


def _photo(bridge, cid='ph', utterance='You can take a picture of me now.'):
    return asyncio.run(bridge.call(ToolCall(session=SID, id=cid, name='take_photo', args={}, utterance=utterance)))


def test_take_photo_accepts_empty_transcription():
    det = PhotoDetector()
    cam, screen, bridge, gen, _ = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    r = _photo(bridge, cid='empty-transcription', utterance='')
    assert r['status'] == 'photo_taken'
    assert len(det.photo_calls) == 1


def test_take_photo_only_while_streaming_or_paused():
    det = PhotoDetector()
    cam, screen, bridge, gen, _ = _bridge(det)
    r = _photo(bridge)
    assert r['status'] == 'rejected' and r['reason'] == 'photo_not_allowed_launching' and r['terminal'] is True
    assert det.photo_calls == []


def test_take_photo_success_one_window_then_frames_continue():
    from test_camera_conversation import synthetic
    det = PhotoDetector()
    cam, screen, bridge, gen, state = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    screen.show(synthetic(seed=2))
    assert cam.frame(SID, gen).status == 200
    r = _photo(bridge)
    assert r['status'] == 'photo_taken' and r['terminal'] is True
    assert 'retry' not in r['instruction'].lower() or 'never' in r['instruction'].lower()
    assert det.photo_calls == [(True, False, True)]    # click allowed in the window, keys refused, session alive
    assert cam.sessions[SID]['state'] == 'streaming'
    # The shutter click itself is re-baselined: the next new frame is served, not hid_activity.
    hid_lease.check_hid('release')
    screen.show(synthetic(seed=3))
    r = cam.frame(SID, gen)
    assert r.status == 200, r
    assert state['agent']['task'] == ''


def test_take_photo_hid_baseline_reset_after_real_hid():
    from test_camera_conversation import synthetic

    class Clicking(PhotoDetector):
        async def take_photo(self, alive=None):
            for command in ('home', 'move 5 5', 'click'):
                hid_lease.check_hid(command)
            with pytest.raises(RuntimeError):
                hid_lease.check_hid('key cmd+h')
            return {'status': 'photo_taken'}
    det = Clicking()
    cam, screen, bridge, gen, _ = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    baseline = cam.sessions[SID]['hid_baseline']
    assert _photo(bridge)['status'] == 'photo_taken'
    assert cam.sessions[SID]['hid_baseline'] != baseline
    screen.show(synthetic(seed=4))
    assert cam.frame(SID, gen).status == 200


def test_take_photo_rate_limited_to_one_every_two_seconds():
    now = [5000.0]
    det = PhotoDetector()
    cam, screen, bridge, gen, _ = _bridge(det, clock=lambda: now[0])
    asyncio.run(cam.prepare(SID, gen))
    assert _photo(bridge, cid='a')['status'] == 'photo_taken'
    now[0] += 1.0
    r = _photo(bridge, cid='b')
    assert r['status'] == 'rejected' and r['reason'] == 'photo_rate_limited'
    assert len(det.photo_calls) == 1
    now[0] += 1.1
    assert _photo(bridge, cid='c')['status'] == 'photo_taken'      # the owner can ask again
    assert len(det.photo_calls) == 2


@pytest.mark.parametrize('outcome', [{'status': 'photo_uncertain', 'reason': 'thumbnail_unchanged'},
                                     RuntimeError('ble ack lost')])
def test_take_photo_uncertain_is_reported_and_never_retried(outcome):
    det = PhotoDetector(outcome)
    cam, screen, bridge, gen, _ = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    r = _photo(bridge)
    assert r['status'] == 'photo_uncertain' and r['terminal'] is True
    assert 'not retry' in r['instruction'].lower() or 'never retry' in r['instruction'].lower()
    assert len(det.photo_calls) == 1
    assert cam.sessions[SID]['state'] in ('streaming', 'ended')


def test_take_photo_refusal_without_hid_keeps_session_and_baseline():
    det = PhotoDetector(cc.CameraTemplatesPending('mode', reason='photo_mode_not_verified'))
    cam, screen, bridge, gen, _ = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    baseline = cam.sessions[SID]['hid_baseline']
    r = _photo(bridge)
    assert r['status'] == 'rejected' and r['reason'] == 'photo_mode_not_verified'
    assert cam.sessions[SID]['state'] == 'streaming' and cam.sessions[SID]['hid_baseline'] == baseline


def test_stop_during_photo_marks_session_dead_for_the_click_guard():
    seen = []

    class Slow(PhotoDetector):
        async def take_photo(self, alive=None):
            seen.append(alive())
            self.started.set()
            await self.release.wait()
            seen.append(alive())                    # what the pre-click guard sees after Stop
            return {'status': 'photo_not_taken', 'reason': 'no_click_sent'}
    det = Slow()
    cam, screen, bridge, gen, _ = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))

    async def run():
        det.started, det.release = asyncio.Event(), asyncio.Event()
        task = asyncio.ensure_future(bridge.call(ToolCall(session=SID, id='s', name='take_photo', args={},
                                                          utterance='take a photo')))
        await det.started.wait()
        await bridge.interrupt(VoiceControl(session=SID, text='stop'))
        det.release.set()
        return await task
    r = asyncio.run(run())
    assert seen == [True, False]
    assert r['status'] == 'camera_ended'


# ---------------------------------------------------------------- queued flip

def _flip(bridge, cid, utterance='please flip the camera'):
    return asyncio.run(bridge.call(ToolCall(session=SID, id=cid, name='flip_camera', args={}, utterance=utterance)))


def test_flip_during_launch_is_queued_and_done_once_after_ui_verified():
    from test_camera_conversation import synthetic
    det = PhotoDetector()
    cam, screen, bridge, gen, _ = _bridge(det)
    r = _flip(bridge, 'f1', utterance='')
    assert r['status'] == 'flip_queued' and r['terminal'] is True and r['instruction']
    assert not any(isinstance(c, tuple) for c in det.calls), 'no HID while launching'
    r = _flip(bridge, 'f2', utterance='flip it again')
    assert r['status'] == 'rejected' and r['reason'] == 'flip_already_used'
    asyncio.run(cam.prepare(SID, gen))
    assert det.calls == ['reset', 'launch', ('flip', True, False)]
    assert cam.sessions[SID]['state'] == 'streaming'
    screen.show(synthetic(seed=5))
    assert cam.frame(SID, gen).status == 200
    r = _flip(bridge, 'f3', utterance='flip')
    assert r['reason'] == 'flip_already_used'
    assert sum(isinstance(c, tuple) for c in det.calls) == 1


def test_queued_flip_is_dropped_when_camera_ui_is_not_verified():
    det = PhotoDetector()
    det.verify_camera_ui = lambda frame: False
    cam, screen, bridge, gen, _ = _bridge(det)
    assert _flip(bridge, 'f1')['status'] == 'flip_queued'
    asyncio.run(cam.prepare(SID, gen))
    assert det.calls == ['reset', 'launch']
    assert cam.sessions[SID]['state'] == 'ended' and cam.sessions[SID]['reason'] == 'camera_not_verified'


# ---------------------------------------------------------------- return_to_device_mode

def _mint(monkeypatch, bridge, **kw):
    monkeypatch.setenv('GEMINI_LIVE_API_KEY', 'k')
    for name in ('SOURCE_NOTE_WORKFLOWS', 'DEVICE_MODE', 'AIRPLAY_NAME', 'CAMERA_LIVE_SETUP_OVERRIDES'):
        monkeypatch.delenv(name, raising=False)

    async def post(self, url, **kwargs):
        return httpx.Response(200, json={'name': 'auth_tokens/x'})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    return asyncio.run(bridge.mint(**kw))


def test_return_to_device_mode_ends_camera_and_grants_one_voice_mint(monkeypatch):
    from test_camera_conversation import synthetic
    det = PhotoDetector()
    cam, screen, bridge, gen, state = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    r = asyncio.run(bridge.call(ToolCall(session=SID, id='r1', name='return_to_device_mode', args={})))
    assert r['status'] == 'switch_to_voice' and r['terminal'] is True and r['instruction']
    assert cam.sessions[SID]['state'] == 'ended' and cam.sessions[SID]['reason'] == 'switch_to_voice'
    assert det.calls[-1] == 'leave', 'Camera leaves the foreground (cmd+h) so normal vision is not refused'
    assert state['agent']['task'] == '', 'the switch itself starts no device task'
    screen.show(synthetic(seed=6))
    f = bridge.camera_frame(SID, gen)
    assert f.status == 410 and f.reason == 'switch_to_voice' and f.body is None
    # The normal mint right after the switch passes the 3 s reconnect limit, once.
    bridge.last_mint = time.monotonic()
    normal = _mint(monkeypatch, bridge)
    assert 'camera' not in normal and 'camera' not in bridge.sessions[normal['session']]
    with pytest.raises(HTTPException) as e:
        _mint(monkeypatch, bridge)
    assert e.value.status_code == 429


def test_voice_switch_grant_does_not_open_a_camera_mint(monkeypatch):
    det = PhotoDetector()
    cam, screen, bridge, gen, _ = _bridge(det)
    asyncio.run(cam.prepare(SID, gen))
    asyncio.run(bridge.call(ToolCall(session=SID, id='r1', name='return_to_device_mode', args={}, utterance='open notes')))
    bridge.last_mint = time.monotonic()
    with pytest.raises(HTTPException) as e:
        _mint(monkeypatch, bridge, camera=True)
    assert e.value.status_code == 429


def test_return_to_device_mode_is_not_a_normal_session_tool(monkeypatch):
    from test_camera_conversation import make, bridge_for
    cam, _ = make()
    b, state, _ = bridge_for(cam)
    normal = _mint(monkeypatch, b)
    names = [d['name'] for d in normal['setup']['tools'][0]['functionDeclarations']]
    assert 'return_to_device_mode' not in names and 'take_photo' in names
    r = asyncio.run(b.call(ToolCall(session=normal['session'], id='x', name='take_photo', args={}, utterance='photo')))
    assert r['status'] != 'switch_to_voice' and state['agent']['task'] == ''


# ---------------------------------------------------------------- prompts

def _memory(tmp_path):
    from hidagent.companion import CompanionMemory
    memory = CompanionMemory(tmp_path)
    memory.remember('ESP32QA I prefer ocean animals.')
    return memory


def test_camera_prompt_says_own_body_lists_tools_and_never_denies(monkeypatch, tmp_path):
    system, tools = camera_context({'target': {'device': 'ipad'}})
    names = [d['name'] for d in tools[0]['functionDeclarations']]
    assert names == ['end_camera_conversation', 'stop_phone', 'flip_camera', 'take_photo', 'return_to_device_mode']
    assert 'your own device' in system and 'body' in system
    for tool in ('flip_camera', 'take_photo', 'return_to_device_mode', 'end_camera_conversation'):
        assert tool in system
    assert not DENIAL.search(system), DENIAL.search(system)
    for d in tools[0]['functionDeclarations']:
        assert not DENIAL.search(d['description']), d['name']
    # With companion memory: same rules plus the persona; still no denial anywhere.
    from test_camera_conversation import make, bridge_for
    cam, _ = make()
    b, _, _ = bridge_for(cam)
    b.memory = _memory(tmp_path)
    result = _mint(monkeypatch, b, camera=True)
    text = result['setup']['systemInstruction']['parts'][0]['text']
    assert 'You are Ode' in text and 'take_photo' in text and 'return_to_device_mode' in text
    assert not DENIAL.search(text)
    assert 'phone_command' not in text


def test_normal_companion_text_never_denies_operating_the_ipad(monkeypatch, tmp_path):
    from test_camera_conversation import make, bridge_for
    cam, _ = make()
    b, _, _ = bridge_for(cam)
    b.memory = _memory(tmp_path)
    result = _mint(monkeypatch, b)
    text = result['setup']['systemInstruction']['parts'][0]['text']
    assert 'You are Ode' in text and 'your own device' in text
    assert not DENIAL.search(text), DENIAL.search(text)
    assert 'open_camera' in text and 'take_photo' in text
    assert 'start_camera_conversation' not in text


# Updated deliberately: Joe requested shared ownership text even with camera/memory disabled.
# Updated deliberately again (Joe 2026-10-30): the owner-chosen AI name (default Ode) is rendered into it.
# Updated for continuous normal-session iPad screen vision and the no-false-success rule (2026-11-02).
FLAG_OFF_FIXTURE_SHA256 = 'df477ebe861c7a2edfb7c872ce02a7258c29f2b092e224f1d14de3e402802474'


def test_flag_off_normal_fixture_is_unchanged():
    digest = hashlib.sha256((FIXTURES / 'voice_setup_normal_ipad.json').read_bytes()).hexdigest()
    assert digest == FLAG_OFF_FIXTURE_SHA256
