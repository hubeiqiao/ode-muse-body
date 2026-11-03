"""Recovery must observe the result; an acknowledged Home key is not success."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from hidagent import home_icons, phone as module
from hidagent.phone import PhoneUnavailable
from test_home_recovery import phone
from test_input_latency import api


def observed_phone(tmp_path, monkeypatch, outcomes):
    p = phone(tmp_path)
    p.target_info = lambda: dict(device='ipad', orientation='landscape', width=1646, height=1080)
    monkeypatch.setattr(module, 'HOME_RECOVERY_WAIT', .001, raising=False)
    monkeypatch.setattr(home_icons, 'home_grid_witness', lambda frame, *args: frame == b'HOME')
    monkeypatch.setattr(home_icons, 'home_reference_available', lambda *args: True)

    async def fresh(*args):
        home_count = sum(c.args[0] == 'key cmd+h' for c in p._send.await_args_list)
        p.screen.frame = outcomes[min(home_count-1, len(outcomes)-1)]
        return p.screen.frame

    p.screen.fresh = AsyncMock(side_effect=fresh)
    return p


def test_home_first_attempt_fails_second_reaches_home(tmp_path, monkeypatch):
    p = observed_phone(tmp_path, monkeypatch, [b'VIDEO', b'HOME'])
    result = asyncio.run(p.recover_home())
    assert result == dict(ok=True, home_reached=True, attempts=2, retries=1)
    assert [c.args[0] for c in p._send.await_args_list] == ['key esc', 'key esc'] + ['key cmd+h']*2


def test_home_never_reached_is_bounded_and_reported(tmp_path, monkeypatch):
    p = observed_phone(tmp_path, monkeypatch, [b'VIDEO'])
    result = asyncio.run(p.recover_home())
    assert result == dict(ok=False, home_reached=False, attempts=2, retries=1)
    assert p._send.await_count == 4
    with pytest.raises(PhoneUnavailable, match='already attempted'):
        asyncio.run(p.recover_home())
    assert p._send.await_count == 4


def test_already_home_is_a_verified_noop_even_after_recovery(tmp_path, monkeypatch):
    p = observed_phone(tmp_path, monkeypatch, [b'HOME'])
    assert asyncio.run(p.recover_home()) == dict(ok=True, home_reached=True, attempts=1, retries=0)
    assert asyncio.run(p.recover_home()) == dict(ok=True, home_reached=True, attempts=0, retries=0)
    assert p._send.await_count == 3


def test_no_fresh_home_frame_cannot_report_success(tmp_path, monkeypatch):
    p = observed_phone(tmp_path, monkeypatch, [b'HOME'])
    p.screen.fresh = AsyncMock(return_value=None)
    assert asyncio.run(p.recover_home()) == dict(ok=False, home_reached=False, attempts=2, retries=1)
    assert p._send.await_count == 4


def test_lost_ack_or_cancel_never_retries(tmp_path, monkeypatch):
    for failure in (RuntimeError('lost ACK'), asyncio.CancelledError()):
        p = observed_phone(tmp_path, monkeypatch, [b'HOME'])
        p._send.side_effect = failure
        with pytest.raises(type(failure)):
            asyncio.run(p.recover_home())
        assert p._send.await_count == 1
        p.screen.fresh.assert_not_awaited()


def test_board_change_after_ack_never_retries(tmp_path, monkeypatch):
    p = observed_phone(tmp_path, monkeypatch, [b'VIDEO'])
    async def changed(*args):
        p.device.links += 1
        return b'VIDEO'
    p.screen.fresh.side_effect = changed
    with pytest.raises(PhoneUnavailable, match='binding changed'):
        asyncio.run(p.recover_home())
    assert p._send.await_count == 3


def test_landscape_recovery_uses_actual_local_grid_detector(tmp_path, monkeypatch):
    import json
    from test_home_icons import make, encode
    picture, path = make(tmp_path)
    data = json.loads(path.read_text())
    data.update(reference_size=[1646, 1080], orientation='landscape')
    path.write_text(json.dumps(data))
    home = encode(picture.resize((1646, 1080)))
    monkeypatch.setenv('APP_ICON_LAYOUT', str(path))
    monkeypatch.setattr(module, 'HOME_RECOVERY_WAIT', .001, raising=False)
    from PIL import Image
    p = phone(tmp_path, frame=encode(Image.new('RGB', (1646, 1080), 'white')))
    p.target_info = lambda: dict(device='ipad', orientation='landscape', width=1646, height=1080)
    async def fresh(*args):
        home_count = sum(c.args[0] == 'key cmd+h' for c in p._send.await_args_list)
        p.screen.frame = b'VIDEO' if home_count == 1 else home
        return p.screen.frame
    p.screen.fresh = AsyncMock(side_effect=fresh)
    assert asyncio.run(p.recover_home()) == dict(ok=True, home_reached=True, attempts=2, retries=1)


@pytest.mark.parametrize('wrong', ['orientation', 'geometry', 'overlay', 'no_layout'])
def test_local_home_witness_rejects_wrong_geometry_and_overlays(tmp_path, monkeypatch, wrong):
    import json
    from test_home_icons import make, encode
    picture, path = make(tmp_path)
    data = json.loads(path.read_text())
    data.update(reference_size=[1646, 1080], orientation='landscape')
    picture = picture.resize((1646, 1080))
    if wrong == 'orientation':
        data['orientation'] = 'portrait'
    elif wrong == 'geometry':
        data['reference_size'] = [710, 1080]
    elif wrong == 'overlay':
        picture.paste('black', (0, 0, 1646, 1080))
    else:
        path = tmp_path/'missing.json'
    if wrong != 'no_layout':
        path.write_text(json.dumps(data))
    monkeypatch.setenv('APP_ICON_LAYOUT', str(path))
    assert not home_icons.home_grid_witness(encode(picture), {'device': 'ipad', 'orientation': 'landscape'}, None)


@pytest.mark.parametrize('outcomes,reached', [([b'VIDEO', b'HOME'], True), ([b'VIDEO'], False)])
def test_recovery_api_reports_verified_result(api, tmp_path, monkeypatch, outcomes, reached):
    m, _ = api
    p = observed_phone(tmp_path, monkeypatch, outcomes)
    monkeypatch.setattr(m, 'phone', p)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app),
                                     base_url='http://127.0.0.1') as client:
            response = await client.post('/api/recover-home')
        assert response.status_code == 200
        assert response.json() == dict(ok=reached, home_reached=reached, attempts=2, retries=1)
    asyncio.run(run())
