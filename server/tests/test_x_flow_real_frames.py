"""Real 2026-11-02 iPad frames through the production OCR and vision paths.

Apple Vision tests require macOS. Set LOCAL_OCR_BIN to the deployed worker or
compile the repository worker automatically. No OCR rows/detectors are mocked.
"""
import asyncio
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image, ImageEnhance, ImageDraw, ImageFont

from hidagent.ipad_demo_vision import DemoVision, NativeLayoutOCR
from hidagent.local_text import LocalTextReader
from hidagent.x_photo_flow import PhotoPostVision


FRAMES = Path(__file__).parent / 'fixtures' / 'x_flow_20261102'


@pytest.fixture(scope='session')
def native_ocr_binary(tmp_path_factory):
    if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')): pytest.skip('Private device frames excluded from public snapshot')
    if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')):
        pytest.skip('Private device frames excluded from public snapshot')
    if sys.platform != 'darwin':
        pytest.skip('Real Apple Vision OCR requires macOS')
    configured = os.environ.get('LOCAL_OCR_BIN')
    if configured:
        assert Path(configured).is_file(), 'Configured native OCR worker is missing'
        return configured
    compiler = shutil.which('swiftc')
    if not compiler:
        pytest.skip('swiftc or LOCAL_OCR_BIN required for real Apple Vision OCR')
    binary = tmp_path_factory.mktemp('native-ocr') / 'vision-ocr'
    source = Path(__file__).resolve().parents[2] / 'tools' / 'native-probe' / 'vision_ocr.swift'
    subprocess.run([compiler, str(source), '-o', str(binary)], check=True, capture_output=True)
    return str(binary)


def test_real_spotlight_x_top_hit(native_ocr_binary, tmp_path):
    if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')): pytest.skip('Private device frames excluded from public snapshot')
    async def scenario():
        if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')): pytest.skip('Private device frames excluded from public snapshot')
        reader = LocalTextReader([native_ocr_binary, '--serve'])
        vision = DemoVision(NativeLayoutOCR(reader))
        screen = SimpleNamespace(frame=(FRAMES / 'spotlight-x-tophit-row.jpg').read_bytes())
        try:
            assert await vision.check(screen, 'spotlight')
            assert await vision.check(screen, 'top_hit', text='X')
            # Exercise the exact require() path which refused on the device.
            # Only the physical input boundary is fake; the detector is real.
            from test_ipad_demo_flows import make
            flow, phone, _, _, _ = make(tmp_path)
            flow.screen.frame = screen.frame
            flow.vision = vision
            flow.binding = flow.current_binding()
            assert await flow.require('top_hit', text='X')
            for method in (phone.key, phone.type, phone.tap, phone.go_home):
                method.assert_not_awaited()
        finally:
            await reader.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('actual,expected,accepted', [
    ('AI on an iPad.', 'AI on an iPad.', True),
    ('Al on an iPad.', 'AI on an iPad.', True),
    (' Al   on an iPad. ', 'AI on an iPad.', True),
    ('Al on an iPad. Extra.', 'AI on an iPad.', False),
    ('Al on an iPad', 'AI on an iPad.', False),
    ('Al on an iPhone.', 'AI on an iPad.', False),
    ('Al on an iPad.', 'AL on an iPad.', False),
    ('llike the photo.', 'Ilike the photo.', False),
    ('A1 on an iPad.', 'AI on an iPad.', False),
    ('ai on an iPad.', 'AI on an iPad.', False),
])
def test_caption_readback_only_allows_positional_initialism_i_l(actual, expected, accepted):
    from hidagent.x_photo_local import caption_matches
    assert caption_matches(actual, expected) is accepted


@pytest.mark.parametrize('change', ['missing_first_label', 'xchat_first', 'no_top_hit', 'suggestions_only'])
def test_real_spotlight_refuses_other_hits(native_ocr_binary, change):
    if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')): pytest.skip('Private device frames excluded from public snapshot')
    """Edit actual pixels, then rerun native OCR, never inject OCR rows."""
    async def scenario():
        if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')): pytest.skip('Private device frames excluded from public snapshot')
        reader = LocalTextReader([native_ocr_binary, '--serve'])
        vision = DemoVision(NativeLayoutOCR(reader))
        with Image.open(FRAMES / 'spotlight-x-tophit-row.jpg') as source:
            im = source.convert('RGB')
        if change in ('missing_first_label', 'suggestions_only'):
            # Leave the Suggestions X, later Quantumult X and Home/dock Xs.
            im.paste(im.getpixel((497, 418)), (450, 385, 545, 420))
        if change == 'xchat_first':
            # Exchange first/second icon+label pixels without changing headers.
            first, second = im.crop((440, 280, 550, 420)), im.crop((570, 280, 680, 420))
            im.paste(second, (440, 280)); im.paste(first, (570, 280))
        if change == 'no_top_hit':
            im.paste(im.getpixel((415, 240)), (415, 220, 500, 252))
        if change == 'suggestions_only':
            im.paste(im.getpixel((497, 418)), (419, 280, 840, 420))
        out = io.BytesIO(); im.save(out, format='PNG')
        try:
            assert not await vision.check(SimpleNamespace(frame=out.getvalue()), 'top_hit', text='X')
        finally:
            await reader.close()
    asyncio.run(scenario())


CAPTION = 'Selfie from my AI on an old iPad. Orange hoodie, warm light.'


def test_real_picker_blue_badge():
    if not FRAMES.is_dir() or not any(FRAMES.glob('*.jpg')): pytest.skip('Private device frames excluded from public snapshot')
    from hidagent.x_photo_flow import blue_badge
    frame = (FRAMES / 'x2.jpg').read_bytes()
    assert blue_badge(frame)
    im = Image.open(io.BytesIO(frame)).convert('RGB')
    ImageDraw.Draw(im).rectangle((684, 568, 702, 589), fill='gray')
    out = io.BytesIO(); im.save(out, 'PNG')
    assert not blue_badge(out.getvalue())
