import asyncio
from types import SimpleNamespace

from hidagent import phone as phone_module
from hidagent.phone import Phone

TRUE_SPAN = (460, 1000)  # mouse units across the real screen


def landing(units):
    """iOS pointer motion that is not proportional: short of the target low on the screen, past it high up."""
    x, y = units[0] / TRUE_SPAN[0], units[1] / TRUE_SPAN[1]
    return 0.96 * x + 0.045, 0.93 * y + 0.03


def seen_with_noise():
    """Where the pointer is found on screen: the landing, read with a few pixels of error (alternating sign)."""
    readings = 0

    def find(units):
        nonlocal readings
        readings += 1
        x, y = landing(units)
        e = 0.006 if readings % 2 else -0.006
        return x + e, y + e
    return find


class Device:
    links = 0

    def __init__(self):
        self.pos = [0, 0]

    async def send(self, line):
        cmd, *args = line.split()
        if cmd == "home":
            self.pos = [0, 0]
        elif cmd == "move":
            self.pos = [self.pos[0] + int(args[0]), self.pos[1] + int(args[1])]
        return "ok"


def test_grid_calibration_corrects_moves_across_the_whole_screen(tmp_path, monkeypatch):
    device = Device()
    phone = Phone(device, tmp_path / "calibration.json", screen=SimpleNamespace(live=True))
    find = seen_with_noise()
    monkeypatch.setattr(phone, "_find_pointer", lambda: asyncio.sleep(0, find(device.pos)))

    assert asyncio.run(phone.start_calibration())
    for target in [(0.2, 0.1), (0.5, 0.5), (0.85, 0.9), (0.3, 0.97)]:
        x, y = landing((phone._dx(target[0]), phone._dy(target[1])))
        assert abs(x - target[0]) < 0.015 and abs(y - target[1]) < 0.015, (target, (x, y))  # ~15 px
    assert len(phone._seen["y"]) == 2 + len(phone_module.CALIBRATION_GRID)
    assert phone._pos is None  # the next gesture homes first


def test_reversed_hid_axes_are_detected_rehomed_and_persisted(tmp_path, monkeypatch):
    import json
    class ReversedDevice(Device):
        def __init__(self):
            super().__init__()
            self.commands = []
        async def send(self, line):
            self.commands.append(line)
            cmd, *args = line.split()
            if cmd == 'home':
                signs = list(map(int, args)) if args else [1, 1]
                self.pos = [0 if signs[i] < 0 else TRUE_SPAN[i] for i in range(2)]
            elif cmd == 'move':
                self.pos = [max(0, min(TRUE_SPAN[i], self.pos[i] - int(args[i]))) for i in range(2)]
    device = ReversedDevice()
    path = tmp_path / 'calibration.json'
    phone = Phone(device, path, screen=SimpleNamespace(live=True))
    monkeypatch.setattr(phone, '_find_pointer', lambda: asyncio.sleep(0, tuple(device.pos[i] / TRUE_SPAN[i] for i in range(2))))
    assert asyncio.run(phone.start_calibration())
    assert phone.axes == {'x': -1, 'y': -1}
    assert 'home -1 -1' in device.commands
    assert phone.span == {'w': TRUE_SPAN[0], 'h': TRUE_SPAN[1]}
    asyncio.run(phone.point(.4, .6))
    assert abs(device.pos[0] / TRUE_SPAN[0] - .4) < .01
    assert abs(device.pos[1] / TRUE_SPAN[1] - .6) < .01
    assert json.loads(path.read_text())['axes'] == phone.axes
    assert Phone(Device(), path).axes == phone.axes


def test_precise_tap_never_clicks_when_pointer_is_missing_or_far_away(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    import pytest
    from hidagent.phone import PhoneUnavailable
    for found in (None, (.8, .8)):
        device = Device()
        device.commands = []
        original_send = device.send
        async def send(command):
            device.commands.append(command)
            await original_send(command)
        device.send = send
        screen = SimpleNamespace(frame=b'frame', settled=AsyncMock(), fresh=AsyncMock(return_value=b'new'))
        phone = Phone(device, tmp_path / 'calibration.json', screen=screen)
        phone.span = {'w': 460, 'h': 1000}
        phone._move_to = AsyncMock()
        phone._pos = (0, 0)
        monkeypatch.setattr(phone_module.vision, 'pointer', lambda *args, **kwargs: found)
        with pytest.raises(PhoneUnavailable):
            asyncio.run(phone.tap(.2, .2, aim=True))
        assert 'click' not in device.commands


def test_pointer_probe_waits_through_repeated_frames_for_actual_motion(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    device = Device()
    screen = SimpleNamespace(frame=b'before', settled=AsyncMock(),
                             fresh=AsyncMock(side_effect=[b'before', b'before', b'moved']))
    phone = Phone(device, tmp_path / 'calibration.json', screen=screen)
    monkeypatch.setattr(phone_module.vision, 'pointer', lambda a, b, *args, **kwargs: (.4, .4) if a != b else None)
    assert asyncio.run(phone._find_pointer()) == (.4, .4)
    assert screen.fresh.await_count == 3
    assert device.pos == [0, 0]  # the probe was undone after the observation


def test_partial_visual_calibration_always_leaves_the_known_manual_stop(tmp_path, monkeypatch):
    device = Device()
    phone = Phone(device, tmp_path / 'c.json', screen=SimpleNamespace(live=True))
    readings = iter([(.3, .2), None])
    monkeypatch.setattr(phone, '_find_pointer', lambda: asyncio.sleep(0, next(readings)))
    assert not asyncio.run(phone.start_calibration())
    assert device.pos == [phone_module.CALIBRATION_MOVE] * 2


def test_failed_direction_probe_does_not_leave_axes_flipped(tmp_path, monkeypatch):
    device = Device()
    phone = Phone(device, tmp_path / 'c.json', screen=SimpleNamespace(live=True))
    readings = iter([(.8, .8), (.6, .6), None])
    monkeypatch.setattr(phone, '_find_pointer', lambda: asyncio.sleep(0, next(readings)))
    assert not asyncio.run(phone.start_calibration())
    assert phone.axes == {'x': 1, 'y': 1}
    assert device.pos == [phone_module.CALIBRATION_MOVE] * 2


def test_ipad_precise_click_waits_after_aim_before_press(tmp_path):
    from unittest.mock import AsyncMock
    order=[]
    async def point(*args): order.append('aim')
    async def settled(**kwargs): order.append('settled')
    async def send(command): order.append(command)
    phone=Phone(Device(),tmp_path/'c.json',screen=SimpleNamespace(settled=settled))
    phone.point=point
    phone._send=send
    phone.target_info=lambda:{'device':'ipad'}
    asyncio.run(phone.tap(.4,.55,aim=True))
    assert order==['aim','settled','click']
    order.clear()
    phone.target_info=lambda:{'device':'iphone'}
    asyncio.run(phone.tap(.4,.55,aim=True))
    assert order==['aim','click']


def test_sparse_grid_does_not_replace_previous_verified_mapping(tmp_path,monkeypatch):
    import pytest
    device=Device();path=tmp_path/'calibration.json'
    phone=Phone(device,path,screen=SimpleNamespace(live=True))
    phone.span={'w':460,'h':1000}
    phone._seen['x'].append((.5,.5));phone._seen['y'].append((.5,.5))
    phone._save();before=path.read_bytes();calls=0
    async def find():
        nonlocal calls
        calls+=1
        return landing(device.pos) if calls<=2 else None
    monkeypatch.setattr(phone,'_find_pointer',find)
    with pytest.raises(RuntimeError,match='previous calibration preserved'):
        asyncio.run(phone.start_calibration())
    assert phone.span=={'w':460,'h':1000} and path.read_bytes()==before
    assert list(phone._seen['x'])==[(.5,.5)] and phone._pos is None


def test_pointer_pacing_is_reapplied_after_board_reconnect_not_idle(monkeypatch,tmp_path):
    from unittest.mock import AsyncMock
    monkeypatch.setenv('HID_POINTER_MS','80')
    device=SimpleNamespace(links=1,send=AsyncMock())
    phone=Phone(device,tmp_path/'calibration.json')
    async def run():
        await phone._send('release')
        await phone._send('move 10 0')
        await phone._send('move 20 0')
        device.links=2
        await phone._send('move 5 0')
    asyncio.run(run())
    assert [c.args[0] for c in device.send.await_args_list]==[
        'release','tune 10 80','move 10 0','move 20 0','tune 10 80','move 5 0']


def test_calibration_file_records_motion_cadence(monkeypatch,tmp_path):
    import json
    monkeypatch.setenv('HID_POINTER_MS','80')
    phone=Phone(Device(),tmp_path/'calibration.json');phone.span={'w':934,'h':1435};phone._save()
    assert json.loads((tmp_path/'calibration.json').read_text())['motion_ms']==80


def test_cancelled_calibration_restores_mapping_without_new_motion(monkeypatch,tmp_path):
    phone=Phone(Device(),tmp_path/'c.json');phone.span={'w':934,'h':1435}
    phone._seen['x'].append((.5,.5));phone._save();before=(tmp_path/'c.json').read_bytes()
    async def run():
        entered=asyncio.Event()
        async def partial():
            phone.span={'w':1,'h':1};phone._seen['x'].clear();entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(phone,'_calibrate',partial)
        task=asyncio.create_task(phone.start_calibration());await entered.wait();task.cancel()
        result=await asyncio.gather(task,return_exceptions=True)
        assert isinstance(result[0],asyncio.CancelledError)
    asyncio.run(run())
    assert phone.span=={'w':934,'h':1435} and list(phone._seen['x'])==[(.5,.5)]
    assert (tmp_path/'c.json').read_bytes()==before and phone._pos is None


def test_ipad_corrected_pointer_is_reobserved_before_click(tmp_path):
 from unittest.mock import AsyncMock,Mock
 screen=SimpleNamespace(frame=b'frame',settled=AsyncMock())
 phone=Phone(SimpleNamespace(links=1),tmp_path/'c.json',screen=screen)
 phone.span={'w':1000,'h':1000};phone._pos=(500,500)
 phone.target_info=lambda:{'device':'ipad'}
 phone._send=AsyncMock();phone._move_xy=AsyncMock();phone._save=Mock()
 phone._locate_after_nudge=AsyncMock(side_effect=[(.53,.5),(.5,.5)])
 asyncio.run(phone._aim(.5,.5))
 assert phone._locate_after_nudge.await_count==2 and phone.last_pointer_check['error']==0

def test_ipad_pointer_that_never_converges_is_rejected(tmp_path):
 from unittest.mock import AsyncMock,Mock
 import pytest
 from hidagent.phone import PhoneUnavailable
 screen=SimpleNamespace(frame=b'frame',settled=AsyncMock())
 phone=Phone(SimpleNamespace(links=1),tmp_path/'c.json',screen=screen)
 phone.span={'w':1000,'h':1000};phone._pos=(500,500)
 phone.target_info=lambda:{'device':'ipad'}
 phone._send=AsyncMock();phone._move_xy=AsyncMock();phone._save=Mock()
 phone._locate_after_nudge=AsyncMock(return_value=(.53,.5))
 with pytest.raises(PhoneUnavailable,match='did not converge'):asyncio.run(phone._aim(.5,.5))
 assert phone._locate_after_nudge.await_count==3
