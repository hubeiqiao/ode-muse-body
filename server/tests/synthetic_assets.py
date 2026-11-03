"""Synthetic matcher assets, generated in a disposable directory using PIL.

Control specs describe geometry only. No screenshots, logos, account pixels or
real device frames are distributed or reconstructed by this module.
"""
import hashlib
import json
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

_TEMP = tempfile.TemporaryDirectory(prefix='ipad-agent-test-assets-')
ASSETS = Path(_TEMP.name)
SPECS = Path(__file__).parent / 'fixtures/control-specs'


def patch(path, size):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(path.name.encode()).digest()
    im = Image.new('RGB', size, tuple(12 + v % 45 for v in digest[:3]))
    draw = ImageDraw.Draw(im)
    w, h = size
    for i in range(3):
        x = max(1, (w - 2) * (i + 1) // 4)
        draw.line((x, 1, x, max(1, h - 2)), fill=(230, 230, 230), width=max(1, w // 12))
    draw.line((1, h // 2, max(1, w - 2), h // 2), fill=(190, 190, 190), width=max(1, h // 10))
    im.save(path)


for spec in SPECS.rglob('*.json'):
    data = json.loads(spec.read_text())
    dest = ASSETS / spec.relative_to(SPECS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data))
    if 'patches' in data:
        for item in data['patches'].values():
            box = item['box']
            patch(dest.parent / item['file'], (round(box[2] - box[0]), round(box[3] - box[1])))
    if 'icons' in data:
        w, h = data['reference_size']
        for item in [*data['icons'].values(), data['home_guard']]:
            l, t, r, b = item['crop']
            patch(dest.parent / item['file'], (round(r*w) - round(l*w), round(b*h) - round(t*h)))
