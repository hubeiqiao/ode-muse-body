import asyncio
import io

import pytest
from PIL import Image

from hidagent.phone import Phone
from hidagent.profiles import TargetProfiles


def frame(width, height):
    output = io.BytesIO()
    Image.new('RGB', (width, height)).save(output, 'JPEG')
    return output.getvalue()


@pytest.mark.parametrize('size,device,orientation', [
    ((710, 1080), 'ipad', 'portrait'), ((1646, 1080), 'ipad', 'landscape'),
    ((390, 844), 'iphone', 'portrait'), ((844, 390), 'iphone', 'landscape'),
    ((750, 1334), 'iphone', 'portrait'), ((1536, 2048), 'ipad', 'portrait'),
])
def test_auto_detects_both_device_families_and_orientations(tmp_path, size, device, orientation):
    info = TargetProfiles(tmp_path / 'target.json').describe(frame(*size))
    assert info['device'] == device and info['orientation'] == orientation
    assert (info['width'], info['height']) == size
    assert info['aspect'] == size[0] / size[1]


def test_unknown_screen_stays_unresolved_and_manual_mode_persists(tmp_path):
    path = tmp_path / 'target.json'
    profiles = TargetProfiles(path)
    assert profiles.describe(None)['calibration_key'] is None
    assert profiles.describe(None)['device'] is None
    profiles.select('iphone')
    assert TargetProfiles(path).describe(frame(710, 1080))['device'] == 'iphone'
    with pytest.raises(ValueError):
        profiles.select('../invalid')


def test_resolution_changes_keep_the_same_aspect_profile(tmp_path):
    profiles = TargetProfiles(tmp_path / 'target.json')
    assert profiles.describe(frame(710, 1080))['calibration_key'] == profiles.describe(frame(1420, 2160))['calibration_key']


@pytest.mark.parametrize('size,orientation', [
    ((2266, 1488), 'landscape'), ((1488, 2266), 'portrait'),
    ((1645, 1080), 'landscape'), ((1646, 1080), 'landscape'),
    ((709, 1080), 'portrait'), ((710, 1080), 'portrait'),
])
def test_native_mini_frames_keep_existing_calibration_key(tmp_path, size, orientation):
    info = TargetProfiles(tmp_path / 'target.json').describe(frame(*size))
    assert info['calibration_key'] == f'ipad-{orientation}-66'
    assert (info['width'], info['height']) == size


class Device:
    links = 1

    def __init__(self):
        self.sent = []

    async def send(self, command):
        self.sent.append(command)


class Screen:
    live = False

    def __init__(self, width, height):
        self.frame = frame(width, height)


def calibrate(phone, x, y):
    assert asyncio.run(phone.start_calibration()) is False
    phone.finish_calibration(x, y)


def test_calibration_isolated_by_family_and_orientation_and_restored(tmp_path):
    profiles, screen, device = TargetProfiles(tmp_path / 'target.json'), Screen(710, 1080), Device()
    phone = Phone(device, tmp_path / 'calibration.json', screen, profiles)
    assert not phone.calibrated
    calibrate(phone, .2, .1)
    ipad_span = dict(phone.span)
    screen.frame = frame(390, 844)
    assert not phone.calibrated
    calibrate(phone, .25, .2)
    iphone_span = dict(phone.span)
    assert iphone_span != ipad_span
    screen.frame = frame(844, 390)
    assert not phone.calibrated
    screen.frame = frame(710, 1080)
    assert phone.calibrated and phone.span == ipad_span
    screen.frame = frame(390, 844)
    assert phone.calibrated and phone.span == iphone_span
    assert len(list(tmp_path.glob('calibration-*.json'))) == 2


def test_rotation_during_manual_calibration_is_rejected(tmp_path):
    profiles, screen = TargetProfiles(tmp_path / 'target.json'), Screen(710, 1080)
    phone = Phone(Device(), tmp_path / 'calibration.json', screen, profiles)
    asyncio.run(phone.start_calibration())
    screen.frame = frame(1080, 710)
    with pytest.raises(RuntimeError, match='orientation changed'):
        phone.finish_calibration(.2, .1)
    assert not phone.calibrated
    assert not list(tmp_path.glob('calibration-*.json'))


def test_known_ipad_and_iphone_home_use_keyboard_without_pointer_gesture(tmp_path):
    from unittest.mock import AsyncMock
    profiles, screen, device = TargetProfiles(tmp_path / 'target.json'), Screen(710, 1080), Device()
    phone = Phone(device, tmp_path / 'calibration.json', screen, profiles)
    phone.ensure_keyboard_live = AsyncMock()
    asyncio.run(phone.go_home())
    # iPad closes a possibly open Spotlight capsule before Home.
    assert device.sent == ['key esc', 'key esc', 'key cmd+h']
    screen.frame = frame(390, 844)
    phone.swipe = AsyncMock()
    asyncio.run(phone.go_home())
    phone.swipe.assert_not_awaited()
    assert device.sent == ['key esc', 'key esc', 'key cmd+h', 'key cmd+h']


def test_last_real_geometry_survives_disconnect_and_restart(tmp_path):
    path = tmp_path / 'target.json'
    profiles = TargetProfiles(path)
    live = profiles.describe(frame(710, 1080))
    disconnected = profiles.describe(None)
    restarted = TargetProfiles(path).describe(None)
    assert not disconnected['live_frame'] and not restarted['live_frame']
    assert restarted['device'] == 'ipad'
    assert restarted['aspect'] == live['aspect']
    assert restarted['calibration_key'] == live['calibration_key']


def test_ipad_bottom_right_anchor_preserves_screen_coordinates(tmp_path):
    profiles, screen, device = TargetProfiles(tmp_path / 'target.json'), Screen(710, 1080), Device()
    phone = Phone(device, tmp_path / 'calibration.json', screen, profiles)
    phone._sync_profile()
    phone.axes = {'x': -1, 'y': -1}
    phone.span = {'w': 1000, 'h': 1500}
    asyncio.run(phone.point(.2, .3))
    assert device.sent == ['release', 'home', 'move 800 0', 'move 0 1050']
    assert phone._pos == (200, 450)
    assert phone._home_fraction() == 1


def test_iphone_bottom_right_has_isolated_calibration_and_axis_pacing(tmp_path,monkeypatch):
    import json
    monkeypatch.setenv('IPHONE_POINTER_ANCHOR','bottom-right')
    monkeypatch.setenv('POINTER_AXIS_SEPARATE','1')
    profiles,screen,device=TargetProfiles(tmp_path/'target.json'),Screen(498,1080),Device()
    old=tmp_path/'calibration-iphone-portrait-46.json'
    old.write_text(json.dumps({'w':600,'h':1300}))
    phone=Phone(device,tmp_path/'calibration.json',screen,profiles)
    assert not phone.calibrated
    assert phone._file.name=='calibration-iphone-portrait-46-br.json'
    phone.span={'w':1000,'h':1500}
    asyncio.run(phone.point(.2,.3))
    assert device.sent==['release','home -1 -1','move -800 0','move 0 -1050']
    assert json.loads(old.read_text())=={'w':600,'h':1300}


def test_old_direction_mapping_is_not_reused_at_different_motion_cadence(monkeypatch,tmp_path):
    import json
    monkeypatch.setenv('HID_POINTER_MS','80')
    profiles,screen=TargetProfiles(tmp_path/'target.json'),Screen(710,1080)
    key=profiles.describe(screen.frame)['calibration_key']
    path=tmp_path/f'calibration-{key}.json'
    path.write_text(json.dumps({'w':1085,'h':1645,'axes':{'x':1,'y':1}}))
    phone=Phone(Device(),tmp_path/'calibration.json',screen,profiles)
    assert not phone.calibrated
    data=json.loads(path.read_text());data['motion_ms']=80;path.write_text(json.dumps(data))
    phone._profile_key=None
    assert phone.calibrated


@pytest.mark.parametrize('family',['ipad','iphone',None])
def test_back_never_swipes_other_ipad_pane(tmp_path,family):
 from types import SimpleNamespace
 from unittest.mock import AsyncMock
 from hidagent.phone import PhoneUnavailable
 phone=Phone(SimpleNamespace(),tmp_path/'none.json')
 phone.target_info=lambda:{'device':family}
 phone.swipe=AsyncMock()
 if family=='ipad':
  with pytest.raises(PhoneUnavailable):asyncio.run(phone.go_back())
  phone.swipe.assert_not_awaited()
 else:
  asyncio.run(phone.go_back())
  phone.swipe.assert_awaited_once_with(.005,.45,.6,.45,hold=False)


def test_per_orientation_pacing_preserves_independent_calibration(tmp_path,monkeypatch):
 monkeypatch.setenv('HID_POINTER_MS','80')
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":40,"ipad-landscape-66":80}')
 profiles,screen,device=TargetProfiles(tmp_path/'target.json'),Screen(710,1080),Device()
 phone=Phone(device,tmp_path/'calibration.json',screen,profiles)
 assert phone._pointer_ms()==40
 screen.frame=frame(1646,1080)
 assert phone._pointer_ms()==80
 screen.frame=frame(390,844)
 assert phone._pointer_ms()==80


@pytest.mark.parametrize('ms',[20,30])
def test_fast_pacing_keeps_original_mapping_and_requires_independent_file(tmp_path,monkeypatch,ms):
 import json
 monkeypatch.setenv('HID_POINTER_MS','40')
 monkeypatch.setenv('HID_POINTER_FAST_EXPERIMENT','1')
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":40,"ipad-landscape-66":80}')
 profiles,screen=TargetProfiles(tmp_path/'target.json'),Screen(710,1080)
 old=tmp_path/'calibration-ipad-portrait-66.json'
 original=json.dumps({'w':968,'h':1424,'motion_ms':40});old.write_text(original)
 phone=Phone(Device(),tmp_path/'calibration.json',screen,profiles)
 assert phone.calibrated and phone.span['w']==968
 monkeypatch.setenv('HID_POINTER_PROFILE_MS',json.dumps({'ipad-portrait-66':ms,'ipad-landscape-66':80}))
 assert not phone.calibrated
 assert phone._file.name==f'calibration-ipad-portrait-66-ms{ms}.json'
 assert old.read_text()==original
 phone.span={'w':600,'h':900};phone._save()
 assert json.loads(phone._file.read_text())['motion_ms']==ms
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":40,"ipad-landscape-66":80}')
 assert phone.calibrated and phone.span['w']==968
 assert phone._file==old and old.read_text()==original
 screen.frame=frame(1646,1080)
 assert phone._pointer_ms()==80
 screen.frame=frame(390,844)
 assert phone._pointer_ms()==40


@pytest.mark.parametrize('flag,overrides,global_ms,size',[
 ('0','{"ipad-portrait-66":30}','40',(710,1080)),
 ('1','{}','30',(710,1080)),
 ('1','{"iphone-portrait-46":30}','40',(498,1080)),
 ('1','{"ipad-landscape-66":30}','40',(1646,1080)),
 ('1','{"ipad-portrait-66":25}','40',(710,1080)),
])
def test_fast_pacing_refuses_unscoped_or_unapproved_configuration(tmp_path,monkeypatch,flag,overrides,global_ms,size):
 monkeypatch.setenv('HID_POINTER_FAST_EXPERIMENT',flag)
 monkeypatch.setenv('HID_POINTER_PROFILE_MS',overrides)
 monkeypatch.setenv('HID_POINTER_MS',global_ms)
 phone=Phone(Device(),tmp_path/'calibration.json',Screen(*size),TargetProfiles(tmp_path/'target.json'))
 with pytest.raises(ValueError):phone._pointer_ms()


def test_cadence_change_invalidates_unfinished_manual_calibration(tmp_path,monkeypatch):
 import json
 monkeypatch.setenv('HID_POINTER_MS','40')
 monkeypatch.setenv('HID_POINTER_FAST_EXPERIMENT','1')
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":40}')
 phone=Phone(Device(),tmp_path/'calibration.json',Screen(710,1080),TargetProfiles(tmp_path/'target.json'))
 phone._sync_profile();phone._calibration_key=phone._profile_key
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":30}')
 with pytest.raises(RuntimeError,match='start calibration again'):phone.finish_calibration(.3,.3)
 assert not (tmp_path/'calibration-ipad-portrait-66-ms30.json').exists()


@pytest.mark.parametrize('method',['point','tap'])
def test_fast_cadence_without_its_mapping_sends_no_point_or_click(tmp_path,monkeypatch,method):
 import json
 monkeypatch.setenv('HID_POINTER_MS','40')
 monkeypatch.setenv('HID_POINTER_FAST_EXPERIMENT','1')
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":20}')
 old=tmp_path/'calibration-ipad-portrait-66.json'
 original=json.dumps({'w':968,'h':1424,'motion_ms':40});old.write_text(original)
 board=Device();phone=Phone(board,tmp_path/'calibration.json',Screen(710,1080),TargetProfiles(tmp_path/'target.json'))
 with pytest.raises(RuntimeError,match='not calibrated'):asyncio.run(getattr(phone,method)(.5,.5))
 assert board.sent==[] and old.read_text()==original
