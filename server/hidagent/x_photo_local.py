"""Read-only guards for the measured 1646x1080 native X photo layout.

UI-only templates come from the owner's 2026-11-02 frames. No photo, avatar,
caption or feed content is a template. Unknown layouts return None so the
existing Jev observer retains its original confidence thresholds.
"""
from functools import lru_cache
from pathlib import Path
import io
import re

from PIL import Image, ImageChops, ImageStat

from .ipad_demo_vision import picture


PATCHES = {
    'cancel': (168, 101, 257, 135), 'audience': (455, 169, 635, 216),
    'reply': (164, 876, 388, 912), 'media': (156, 952, 189, 984),
    'post': (1383, 93, 1475, 141), 'placeholder': (456, 235, 712, 271),
    'remove': (1210, 294, 1249, 334), 'photos': (530, 151, 626, 187),
    'sidebar': (205, 228, 375, 276), 'library': (829, 151, 1189, 194),
    'confirm': (1421, 137, 1490, 206), 'badge': (684, 568, 702, 589),
}
CAPTION_BOX = dict(left=450/1646, top=230/1080, right=1265/1646, bottom=278/1080)


@lru_cache(maxsize=len(PATCHES))
def template(name):
    import os
    directory = Path(os.environ.get('X_PHOTO_TEMPLATE_DIR', Path(__file__).parents[2] / 'runtime/ipad/x-photo-templates'))
    path = directory / (name + '.png')
    if not path.is_file():
        return None
    with Image.open(path) as im:
        return im.convert('RGB')


def matches(im, *names):
    for name in names:
        actual, expected = im.crop(PATCHES[name]), template(name)
        if expected is None:
            return False
        # Same strict RGB rule as registered local app controls. Dimming,
        # moved/resized controls and different glyphs are not positive proof.
        if actual.size != expected.size or sum(ImageStat.Stat(ImageChops.difference(actual, expected)).mean)/3 >= 10:
            return False
    return True


def caption_matches(actual, expected):
    """Positional native I/l ambiguity in uppercase initialisms, nothing else.

    Apple Vision and Tesseract both read the fixture's 'AI' as 'Al'. Retain
    exact length, case, words and punctuation everywhere else; never use a
    substring or general fuzzy/edit-distance comparison for a caption.
    """
    actual, expected = ' '.join(actual.split()), ' '.join(expected.split())
    if actual == expected:
        return True
    initialisms = {i for match in re.finditer(r'\b[A-Z]{2,}\b', expected)
                   for i in range(match.start(), match.end()) if expected[i] == 'I'}
    return len(actual) == len(expected) and all(
        a == b or i in initialisms and a == 'l' and b == 'I'
        for i, (a, b) in enumerate(zip(actual, expected)))


async def check(local, frame, kind):
    """Positive state requires independent, undimmed UI anchors at both edges."""
    if local is None or kind not in ('foreground', 'composer', 'empty', 'picker', 'selected_picker',
                                     'unselected_picker', 'composer_image', 'draft'):
        return None
    im = picture(frame)
    if im.size != (1646, 1080):
        return None
    composer = matches(im, 'cancel', 'audience', 'reply', 'media')
    picker = matches(im, 'photos', 'sidebar', 'library')
    if not composer and not picker:
        return None
    if kind == 'foreground': return composer
    if kind == 'composer': return composer
    if kind == 'picker': return picker
    if kind in ('selected_picker', 'unselected_picker'):
        if not picker: return False
        # Fast full-frame OCR drops this translucent white count. Isolate and
        # contrast its white glyphs; works with the deployed fast-only worker.
        count = im.crop((950, 930, 1060, 961)).convert('L').point(lambda p: 0 if p > 235 else 255)
        out = io.BytesIO(); count.resize((count.width*2, count.height*2)).save(out, format='PNG')
        data = await local.ocr.read_layout(out.getvalue())
        summary = [r['text'].strip() for r in data['rows']]
        # The numeral must be exactly 1. OCR may misread the adjacent word
        # Photo; independent Photos chrome already establishes media identity.
        one = len(summary) == 1 and re.fullmatch(r'1\s+[A-Za-z]+', summary[0]) is not None
        selected = matches(im, 'badge', 'confirm') and one
        # Only the observed one-selected state has a local template. A
        # genuinely empty/other picker still needs the existing observer.
        return selected if kind == 'selected_picker' else False if selected else None
    if not composer: return False
    content = ImageStat.Stat(im.crop((455, 280, 1264, 850)))
    image = matches(im, 'remove', 'post') and sum(content.stddev)/3 > 20
    if kind == 'composer_image': return image
    if kind == 'empty':
        return matches(im, 'placeholder') and min(content.mean) > 250 and max(content.stddev) < 8
    if kind == 'draft':
        return {'caption_box': dict(CAPTION_BOX), 'source': 'local_x_photo_templates'} if image and not matches(im, 'placeholder') else False
