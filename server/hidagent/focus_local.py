"""Current-run public search focus from local pixels, never from tap ACK alone.

Known query geometry or this run's independently located field bounds constrain
all reads. Unknown geometry and ambiguous native chrome fall back to the judge.
"""
import io
import math
import os
import re
import time
from statistics import median

from PIL import Image
from .app_query_text import LANDSCAPE_QUERY_BANDS, blinking_caret
from .local_text import LocalTextError, LocalTextReader

_MENU_WORD = re.compile(r'\b(?:AutoFill|Select All|Select|Paste)\b', re.I)


def _context(agent, app):
    history = [(i, s) for i, s in enumerate(agent.steps) if s.get('workflow') == 'app_search']
    taps = [(i, s) for i, s in history if s.get('phase') == 'focus' and 'act_s' in s
            and (s.get('action') == 'tap' or
                 (s.get('action') == 'key' and s.get('_search_keyboard_entry') == app
                  and (app, s.get('combo'), s.get('route_used')) in
                  (('X', 'cmd+f', 'keyboard_shortcut'), ('TikTok', 'space', 'fka_tab'))))]
    if not taps:
        return None, 'field_tap_not_acked'
    index, tap = taps[-1]
    identity = any(i <= index and 'act_s' in s and
                   (s.get('phase') == 'app_ready' or s.get('_search_foreground_verified') is True)
                   for i, s in history)
    if not identity:
        return None, 'foreground_not_verified_this_run'
    if tap.get('_learned_public_search', app) != app:
        return None, 'tap_app_mismatch'
    if any(s.get('action') != 'wait' for s in agent.steps[index+1:]):
        return None, 'input_context_changed'
    at = tap.get('t')
    updated = getattr(agent.screen, 'updated', 0)
    if not isinstance(at, (float, int)) or not isinstance(updated, (float, int)) or updated <= at:
        return None, 'frame_not_newer_than_tap_ack'
    return tap, None


def _field_box(agent, app, image, tap):
    target = agent.phone.target_info() if hasattr(agent, 'phone') else None
    if not target or target.get('device') != 'ipad':
        return None
    # Reuse independently measured App query geometry, never a generic header.
    if target.get('orientation') == 'landscape' and image.size == (1646, 1080):
        if app in LANDSCAPE_QUERY_BANDS:
            return LANDSCAPE_QUERY_BANDS[app]
    # A fresh model-located editable field is different from navigation. Bind
    # its bounds to the exact image geometry on which the tap was proposed.
    box = tap.get('_search_field_box', {})
    if tap.get('_search_field_size') != list(image.size):
        return None
    try:
        l, t, r, b = [box[k] for k in ('left', 'top', 'right', 'bottom')]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (l, t, r, b)):
            return None
        if not (0 <= l < r <= 1 and 0 <= t < b <= 1 and b-t <= .2):
            return None
        w, h = image.size
        return (round(l*w), round(t*h), round(r*w), round(b*h))
    except (KeyError, TypeError, ValueError):
        return None


def _menu_chrome(image, box):
    """Native glass capsule enclosing the word, measured on the saved frames.

    Fast OCR can merge the glass menu and a history row behind it. Require the
    separate upper rim and lower shadow around the word, not the word alone.
    Flat text, field outlines and query placeholders have neither enclosure.
    """
    l, t, r, b = box
    h = b-t
    if not 12 <= h <= 40 or r-l < 25:
        return False
    x0, x1 = round(l), round(r)
    y0, y1 = round(t), round(b)
    if x0 < 0 or x1 >= image.width or y0 < 35 or y1+35 >= image.height:
        return False
    def level(y):
        return median(sum(image.getpixel((x, y)))/3 for x in range(x0, x1))
    # Separate top/bottom rims are present on both saved light and dark
    # liquid-glass menus. Text pixels themselves cannot supply those rims.
    top = [level(y) for y in range(y0-30, y0-5)]
    bottom = [level(y) for y in range(y1+5, y1+25)]
    interior = median(level(y) for y in range(y1+3, y1+9))
    light = (min(top) > 170 and max(top)-min(top) >= 9
             and min(bottom) > 170 and max(bottom)-min(bottom) >= 8
             and interior >= min(bottom)+7)
    dark = (max(top) < 110 and max(bottom) < 110
            and max(top) >= interior+12 and max(bottom) >= interior+12
            and min(top) <= interior-5 and min(bottom) <= interior-5)
    return light or dark


def _downward_menu_anchor(image, box):
    """Actual narrowing native menu arrow, not the OCR word's center.

    A floating-window menu may sit ABOVE the query field. Its measured arrow
    tip must still land inside that field; never promote nearby menu text.
    """
    l, _, r, b = box
    cx = round((l+r)/2)
    if cx < 25 or cx+25 >= image.width or b+34 >= image.height:
        return None
    wide, streak = 0, 0
    for y in range(round(b)+7, round(b)+34):
        level = lambda x: sum(image.getpixel((x, y)))/3
        bg = median(level(cx+dx) for dx in (-24, -18, 18, 24))
        xs = [x for x in range(cx-12, cx+13) if level(x)-bg >= 12]
        if not xs or xs[-1]-xs[0]+1 != len(xs) or abs((xs[0]+xs[-1])/2-cx) > 6:
            wide, streak = 0, 0
            continue
        width = len(xs)
        wide, streak = max(wide, width), streak+1
        if wide >= 15 and streak >= 4 and width <= 6:
            return ((xs[0]+xs[-1])/2, y)
    return None


def edit_menu_signal(image, field, observed, region):
    """OCR menu words plus enclosing native pixels, at this field only."""
    if observed.get('mode') != 'fast':
        return None
    LocalTextReader._validate(observed)
    l, t, r, b = field
    x0, y0, x1, y1 = region
    for row in observed['rows']:
        text, box = row['text'], row['box']
        rl, rr = x0+box['left']*(x1-x0), x0+box['right']*(x1-x0)
        rt, rb = y0+box['top']*(y1-y0), y0+box['bottom']*(y1-y0)
        for word in _MENU_WORD.finditer(text):
            # Position proposal only. The capsule's pixels must independently
            # enclose it. No OCR confidence threshold acts as focus approval.
            wl = rl + (rr-rl)*word.start()/len(text)
            wr = rl + (rr-rl)*word.end()/len(text)
            word_box = (wl, rt, wr, rb)
            anchor = _downward_menu_anchor(image, word_box) if rt < t else ((wl+wr)/2, rt)
            if not anchor or not (l <= anchor[0] <= r and t <= anchor[1] <= b+120):
                continue
            if _menu_chrome(image, word_box):
                return {'signal': 'edit_menu', 'word': word.group(),
                        'anchor': [round(v, 1) for v in anchor]}
    return None


def _selected_text(crop):
    """Filled native blue selection plus two opposite insertion handles.

    A keyboard focus ring, blue button, or highlight without handles is a miss.
    All pixels, including both caps, must be inside the field crop.
    """
    pixels = crop.load()
    fill = [(x, y) for y in range(crop.height) for x in range(crop.width)
            if 100 <= pixels[x, y][0] <= 220 and pixels[x, y][2] >= 210
            and pixels[x, y][2]-pixels[x, y][0] >= 30
            and pixels[x, y][1]-pixels[x, y][0] >= 10]
    if not fill:
        return False
    l, t = min(x for x, _ in fill), min(y for _, y in fill)
    r, b = max(x for x, _ in fill)+1, max(y for _, y in fill)+1
    if not (20 <= r-l and 14 <= b-t <= 45 and len(fill) >= (r-l)*(b-t)*.6):
        return False
    def blue(x, y):
        red, green, blue = pixels[x, y]
        return red < 80 and blue >= 150 and blue-red >= 80 and green-red >= 30
    def handle(x, cap_top, cap_bottom):
        if not (7 <= x < crop.width-7 and 0 <= cap_top < cap_bottom <= crop.height):
            return False
        stem = max(sum(blue(xx, y) for y in range(t, b)) for xx in range(x-2, x+3))
        cap = sum(blue(xx, y) for xx in range(x-6, x+7) for y in range(cap_top, cap_bottom))
        return stem >= (b-t)*.8 and cap >= 20
    return handle(l, t-12, t-3) and handle(r-1, b+3, b+12)


def _pixel_focus(agent, tap, image, field):
    crop = image.crop(field)
    sample = getattr(agent, '_app_search_focus_sample', None)
    updated = agent.screen.updated
    if _selected_text(crop):
        return {'signal': 'selected_text'}
    if (sample and sample['tap'] is tap and sample['field'] == field
            and updated-sample['updated'] >= .4
            and blinking_caret(sample['crop'], crop, sample.get('rows', ()))):
        return {'signal': 'caret_blink', 'frame_interval_s': round(updated-sample['updated'], 4)}
    # Never compare a previous run/tap or two reads of the same stream frame.
    if (not sample or sample['tap'] is not tap or sample['field'] != field
            or updated-sample['updated'] >= .4):
        agent._app_search_focus_sample = {'tap': tap, 'field': field, 'updated': updated, 'crop': crop}
    return None


async def verify_local_focus(agent, goal, frame):
    started = time.perf_counter()
    def miss(reason):
        agent.trace.write(kind='app-search-local-focus-miss', app=goal['app'], reason=reason)
        return None
    tap, reason = _context(agent, goal['app'])
    if reason:
        return miss(reason)
    try:
        image = Image.open(io.BytesIO(frame)).convert('RGB')
        field = _field_box(agent, goal['app'], image, tap)
        if field is None:
            return miss('unregistered_query_geometry')
        l, t, r, b = field
        region = (max(0, l-120), max(0, t-120), min(image.width, r+120), min(image.height, b+120))
        proof = _pixel_focus(agent, tap, image, field)
        if proof is None:
            reader = getattr(agent, 'query_text_reader', None) or getattr(agent, 'navigation_text_reader', None)
            owned = False
            if reader is None:
                command = os.environ.get('LOCAL_OCR_BIN')
                if not command:
                    return miss('reader_unavailable')
                reader = LocalTextReader([command, '--serve'])
                owned = True
            try:
                crop = image.crop(region)
                buffer = io.BytesIO()
                crop.resize((crop.width*2, crop.height*2)).save(buffer, format='PNG')
                observed = await reader.read(buffer.getvalue(), mode='fast')
            finally:
                if owned:
                    await reader.close()
            if observed.get('mode') != 'fast':
                return miss('ocr_mode')
            proof = edit_menu_signal(image, field, observed, region)
            # Keep actual field-only OCR row geometry for the shared caret
            # helpers on the next fresh sample. Menu/history rows are excluded.
            sample = getattr(agent, '_app_search_focus_sample', None)
            if sample and sample['tap'] is tap and sample['updated'] == agent.screen.updated:
                rows = []
                x0, y0, x1, y1 = region
                for row in observed['rows']:
                    box = row['box']
                    rl, rr = x0+box['left']*(x1-x0), x0+box['right']*(x1-x0)
                    rt, rb = y0+box['top']*(y1-y0), y0+box['bottom']*(y1-y0)
                    if l <= rl < rr <= r and t <= rt < rb <= b:
                        rows.append({**row, 'box': {'left': (rl-l)/(r-l), 'right': (rr-l)/(r-l),
                            'top': (rt-t)/(b-t), 'bottom': (rb-t)/(b-t)}})
                sample['rows'] = rows
        if not proof:
            return miss('no_local_focus_signal')
        # Motion outside the bounded query/menu area is irrelevant. A changed
        # editor/menu while reading must fall back to the existing fresh judge.
        current = Image.open(io.BytesIO(agent.screen.frame)).convert('RGB')
        if current.size != image.size or current.crop(region).tobytes() != image.crop(region).tobytes():
            return miss('query_region_changed_during_check')
        seconds = time.perf_counter()-started
        agent.trace.write(frame, kind='app-search-local-focus', app=goal['app'],
                          field_box=list(field), seconds=round(seconds, 4), **proof)
        return {**proof, 'seconds': seconds}
    except (LocalTextError, OSError, ValueError, KeyError, TypeError):
        return miss('local_observation_error')
