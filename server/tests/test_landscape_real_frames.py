from synthetic_assets import ASSETS
"""Saved Nov 1 frames and synthetic faint pointers; strictly offline."""
import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from hidagent import home_icons
from test_home_recovery import phone

FIXTURES = Path(__file__).parent/'fixtures/landscape_apps_20261101'
LAYOUT = ASSETS / 'home/layout.json'
TARGET = dict(device='ipad', orientation='landscape', width=1646, height=1080,
              calibration_key='ipad-landscape-66')


@pytest.mark.parametrize('index', [1, 2, 3])
def test_saved_landscape_home_is_witnessed(monkeypatch, index):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    frame = (FIXTURES/f'landscape-home-{index}.jpg').read_bytes()
    assert home_icons.home_grid_witness(frame, TARGET, None)
    assert home_icons.locate_home_icon(frame, 'YouTube', TARGET) is None


@pytest.mark.parametrize('name', ['before', 'after-nudge12', 'after-move60-40'])
def test_saved_app_is_never_home(monkeypatch, name):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    frame = (FIXTURES/f'joespeaking-white-{name}.jpg').read_bytes()
    assert not home_icons.home_grid_witness(frame, TARGET, None)


@pytest.mark.parametrize('name', ['a01-yt-post.jpg', 'a04-ig-post.jpg', 'a06-x-post.jpg'])
def test_other_saved_apps_are_never_home(monkeypatch, name):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    picture=Image.open(FIXTURES.parent/'speed_retest2'/name).convert('RGB')
    # Also reject the content at the registered aspect ratio, so this test
    # cannot pass merely because the source capture has portrait geometry.
    assert not home_icons.home_grid_witness(encode(picture.resize((1646,1080))), TARGET, None)


@pytest.mark.parametrize('missing', ['file', 'orientation', 'templates'])
def test_missing_home_reference_is_bounded_keyboard_recovery_unverified(tmp_path, monkeypatch, missing):
    import json
    from test_home_icons import make, encode
    picture, path = make(tmp_path)
    if missing == 'file':
        path = tmp_path/'missing.json'
    elif missing == 'templates':
        data = json.loads(path.read_text())
        data['reference_size'] = [1646, 1080]
        path.write_text(json.dumps(data))
        (tmp_path/'0.png').unlink()
    monkeypatch.setenv('APP_ICON_LAYOUT', str(path))
    p = phone(tmp_path, frame=encode(picture.resize((1646, 1080))))
    p.target_info = lambda: dict(TARGET)
    result = asyncio.run(p.recover_home())
    assert result == dict(ok=True, home_reached=None, attempts=1, retries=0)
    assert [c.args[0] for c in p._send.await_args_list] == ['key esc', 'key esc', 'key cmd+h']
    p.screen.fresh.assert_not_awaited()


def encode(picture):
    output = io.BytesIO()
    picture.save(output, format='JPEG', quality=95)
    return output.getvalue()


def animated_pair(start=(958, 48), delta=(18, 0), radius=10, ink=160):
    frames = []
    for name, center in zip(('before', 'after-nudge12'),
                            (start, None if start is None else (start[0]+delta[0], start[1]+delta[1]))):
        picture = Image.open(FIXTURES/f'joespeaking-white-{name}.jpg').convert('RGB')
        if center:
            x, y = center
            overlay=picture.copy()
            ImageDraw.Draw(overlay).ellipse((x-radius, y-radius, x+radius, y+radius), fill=(ink,)*3)
            picture=Image.blend(picture,overlay,.25)
        frames.append(encode(picture))
    return frames


def locate(tmp_path, monkeypatch, frames, shift=18/1646, near=(958/1646, 48/1080), **kwargs):
    from hidagent import phone as module
    monkeypatch.setattr(module, 'POINTER_WAIT', .05)
    monkeypatch.delenv('POINTER_TWO_BLOB_CONFIRM', raising=False)
    p = phone(tmp_path, frame=frames[0])
    p.target_info = lambda: dict(TARGET)
    p.screen.fresh = AsyncMock(return_value=frames[1])
    found = asyncio.run(p._locate_after_nudge(frames[0], 0, near, shift, **kwargs))
    return found, p


@pytest.mark.parametrize('dx', [12, 18, -12, -18])
def test_faint_pointer_on_animated_app_is_confirmed(tmp_path, monkeypatch, dx):
    found, p = locate(tmp_path, monkeypatch, animated_pair(delta=(dx, 0)), shift=dx/1646)
    assert found is not None, p.last_pointer_probe
    assert found == pytest.approx((958/1646, 48/1080), abs=2/1646)
    p._send.assert_not_awaited()


@pytest.mark.parametrize('dx,dy', [(-18, 0), (45, 0), (18, 8), (0, 0)])
def test_faint_nonmatching_motion_is_not_a_pointer(tmp_path, monkeypatch, dx, dy):
    found, _ = locate(tmp_path, monkeypatch, animated_pair(delta=(dx, dy)))
    assert found is None


@pytest.mark.parametrize('after', ['after-nudge12', 'after-move60-40'])
def test_real_animation_alone_never_confirms_pointer(tmp_path, monkeypatch, after):
    frames = [(FIXTURES/f'joespeaking-white-{name}.jpg').read_bytes() for name in ('before', after)]
    found, _ = locate(tmp_path, monkeypatch, frames)
    assert found is None


def test_home_witness_ignores_dock_widget_badges_and_one_pointer(monkeypatch):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    picture = Image.open(FIXTURES/'landscape-home-2.jpg').convert('RGB')
    draw = ImageDraw.Draw(picture)
    # Completely replace the widget, clock, recent-app dock and AssistiveTouch.
    for box in [(0, 0, 1646, 50), (227, 60, 606, 438), (0, 910, 1646, 1080),
                (1520, 625, 1645, 735)]:
        draw.rectangle(box, fill='magenta')
    draw.ellipse((905, 530, 945, 570), fill='gray')
    assert home_icons.home_grid_witness(encode(picture), TARGET, None)
    # Conversely, a dock and wallpaper alone cannot witness an app as Home.
    draw.rectangle((227, 60, 1430, 840), fill='white')
    assert not home_icons.home_grid_witness(encode(picture), TARGET, None)


def test_witness_cannot_be_configured_as_a_click_layout(monkeypatch):
    path=LAYOUT.with_name('witness-landscape.json')
    frame=(FIXTURES/'landscape-home-1.jpg').read_bytes()
    monkeypatch.setenv('APP_ICON_LAYOUT', str(path))
    assert home_icons.home_grid_witness(frame, TARGET, None)
    assert not home_icons.has_home_icon('YouTube', frame, TARGET)
    assert home_icons.locate_home_icon(frame, 'YouTube', TARGET) is None


def test_saved_home_recovery_observes_first_key_result(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_ICON_LAYOUT', str(LAYOUT))
    p=phone(tmp_path, frame=(FIXTURES/'joespeaking-white-before.jpg').read_bytes())
    p.target_info=lambda:dict(TARGET)
    async def fresh(*args):
        p.screen.frame=(FIXTURES/'landscape-home-3.jpg').read_bytes()
        return p.screen.frame
    p.screen.fresh=AsyncMock(side_effect=fresh)
    assert asyncio.run(p.recover_home()) == dict(ok=True, home_reached=True, attempts=1, retries=0)
    assert asyncio.run(p.recover_home()) == dict(ok=True, home_reached=True, attempts=0, retries=0)
    assert [c.args[0] for c in p._send.await_args_list] == ['key esc', 'key esc', 'key cmd+h']


@pytest.mark.parametrize('visible,guard', [(True,True),(True,False),(False,True)])
def test_animated_app_tap_keeps_confirmation_and_final_guard(tmp_path, monkeypatch, visible, guard):
    from test_tap_reobservation import phone as tap_phone
    from hidagent.phone import PhoneUnavailable
    p,_=tap_phone(tmp_path,monkeypatch)
    frames=animated_pair(delta=(12,0)) if visible else [
        (FIXTURES/f'joespeaking-white-{name}.jpg').read_bytes() for name in ('before','after-nudge12')]
    p.screen.frame=frames[0]
    p._pos=(958,48)
    state={'nudged':False}
    async def send(command):
        if command=='move 12 0':state['nudged']=True
        if command=='move -12 0':state['nudged']=False
    async def fresh(*args):
        p.screen.frame=frames[int(state['nudged'])]
        return p.screen.frame
    p._send=AsyncMock(side_effect=send)
    p.screen.fresh=AsyncMock(side_effect=fresh)
    async def run():
        await p.tap(958/1646,48/1080,aim=True,pre_click=lambda:guard)
    if visible and guard:
        asyncio.run(run())
    else:
        with pytest.raises(PhoneUnavailable):asyncio.run(run())
    assert sum(c.args[0]=='click' for c in p._send.await_args_list)==int(visible and guard)
    if not visible:p._save.assert_not_called()


@pytest.mark.parametrize('kind', ['hidden_before','hidden_after','square','extra_motion','clipped'])
def test_faint_motion_requires_a_complete_single_round_pair(kind):
    from hidagent import vision
    pictures=[Image.new('RGB',(1646,1080),(247,)*3) for _ in range(2)]
    for i,(picture,x) in enumerate(zip(pictures,(958,976))):
        draw=ImageDraw.Draw(picture)
        if kind==('hidden_before' if i==0 else 'hidden_after'):continue
        if kind=='square':draw.rectangle((x-10,38,x+10,58),fill=(226,)*3)
        else:draw.ellipse((x-10,38,x+10,58),fill=(226,)*3)
    if kind=='extra_motion':ImageDraw.Draw(pictures[1]).ellipse((990,65,1010,85),fill=(226,)*3)
    bounds=(.58,.025,.62,.1) if kind=='clipped' else None
    d={}
    assert vision.pointer(*map(encode,pictures),near=(958/1646,48/1080),shift=18/1646,
                          reach=(.04,.04),top_margin=.025,faint_motion=True,
                          bounds=bounds,diagnostics=d) is None, d


# Private real-device frames are deliberately excluded from the public snapshot.
pytestmark = pytest.mark.skip(reason="Requires private real-device capture excluded from the open-source snapshot; synthetic matcher tests run separately")
