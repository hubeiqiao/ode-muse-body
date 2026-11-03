"""Exact public-query observation after this run's verified empty-field input.
Local OCR is not accessibility or focus. No typing or submission happens here.
Only a known actual form + current acknowledged input + observed text change
can use this fast path; all other cases fall back to the existing visual judge.
"""
import io,json,time,os
from pathlib import Path
from statistics import median
from PIL import Image,ImageChops,ImageStat
from .app_controls import public_search_control,FILES
from .local_text import LocalTextError,LocalTextReader


def _local_miss(agent, goal, reason, *, results=False, **details):
    agent.trace.write(kind='app-search-local-results-miss' if results else 'app-search-local-query-miss',
                      app=goal.get('app'), reason=reason, **details)
    return None

async def verify_local_query(agent,goal,frame):
    reader=getattr(agent,'query_text_reader',None)
    if reader is None or goal['app'] not in ('Bilibili','Instagram'):return _local_miss(agent,goal,'portrait_reader_or_app')
    target=agent.phone.target_info() if hasattr(agent,'phone') else None
    if not public_search_control(frame,goal['app'],target,control='public_query_text'):return _local_miss(agent,goal,'portrait_query_anchors')
    matches=[(i,s) for i,s in enumerate(agent.steps) if s.get('workflow')=='app_search' and s.get('phase')=='query' and 'act_s' in s]
    if len(matches)!=1:return _local_miss(agent,goal,'input_context')
    index,step=matches[0]
    if not step.get('_search_was_empty_verified') or step.get('text')!=goal['query']:return _local_miss(agent,goal,'input_context')
    before=getattr(agent,'shots',{}).get(index)
    if not before:return _local_miss(agent,goal,'missing_before_frame')
    started=time.perf_counter()
    try:
        spec=json.loads(FILES[(goal['app'],'public_query_text')].read_text())
        a=Image.open(io.BytesIO(before)).convert('RGB');b=Image.open(io.BytesIO(frame)).convert('RGB')
        if a.size!=b.size or list(b.size)!=spec['reference_size']:return _local_miss(agent,goal,'portrait_geometry')
        box=tuple(spec['query_text_box']);old=a.crop(box);current=b.crop(box)
        if sum(ImageStat.Stat(ImageChops.difference(old,current)).mean)/3<2:return _local_miss(agent,goal,'unchanged_query_region')
        crop=current.resize((current.width*3,current.height*3));buffer=io.BytesIO();crop.save(buffer,format='JPEG',quality=98)
        observed=await reader.read(buffer.getvalue(),mode='accurate')
        if observed.get('mode')!='accurate':return _local_miss(agent,goal,'ocr_mode')
        rows=observed['rows']
        exact=len(rows)==1 and rows[0].get('confidence',0)>=.99 and rows[0].get('text','').strip()==goal['query']
        agent.trace.write(frame,kind='app-search-local-query',source='local_vision_ocr',mode='accurate',exact=exact,read_s=observed.get('read_s'),seconds=round(time.perf_counter()-started,3),scope='Current-run empty-field input ACK plus known public form and changed actual text ROI; OCR is not focus proof.')
        return {'seconds':time.perf_counter()-started} if exact else _local_miss(agent,goal,'literal_mismatch')
    except (LocalTextError,OSError,ValueError,KeyError,TypeError) as exc:return _local_miss(agent,goal,'ocr_error',error=type(exc).__name__)


# Landscape is independently opt-in. Portrait approval rules stay unchanged.


def _landscape_image(agent, frame):
    if os.environ.get('LANDSCAPE_QUERY_OCR', '0') != '1':
        return None
    phone = getattr(agent, 'phone', None)
    target = phone.target_info() if phone else None
    if not target or (target.get('device'), target.get('orientation')) != ('ipad', 'landscape'):
        return None
    try:
        image = Image.open(io.BytesIO(frame)).convert('RGB')
        return image if image.size == (1646, 1080) else None
    except (OSError, ValueError, TypeError):
        return None


def _header_region(image):
    # Generic top search band, below the status bar and above suggestions/body.
    # Full width avoids hiding an extra suffix at a hard-coded text-box edge.
    w, h = image.size
    return (0, round(h * .03), w, round(h * .12))


# Pixel geometry checked against saved retest9 native frames. Each band contains
# the complete field value, excludes textual submit controls and ends above
# autocomplete. Unknown apps cannot inherit another app's geometry.
LANDSCAPE_QUERY_BANDS = {
    'TikTok': (117, 40, 1480, 94),
    'YouTube': (0, 32, 1646, 130),
    'rednote': (78, 53, 1490, 117),
}


def _query_region(image, app=None):
    return LANDSCAPE_QUERY_BANDS.get(app) if app else _header_region(image)


def _normalized_query(text):
    return ' '.join(text.casefold().split())


def _landscape_query_matches(text, query):
    # Fixed per-position equivalence only. An i/l typing substitution can pass:
    # native Vision reads dotted i as l. Observed live typing failures dropped
    # or added characters, which still fail length/order/exact-other-char checks.
    confusables = str.maketrans({'l': 'i', '1': 'i', '|': 'i', '0': 'o'})
    actual, expected = _normalized_query(text), _normalized_query(query)
    return bool(expected) and len(actual) == len(expected) and actual.translate(confusables) == expected.translate(confusables)


def _mask_landscape_caret(crop, rows):
    """Remove one geometrically isolated tall bar, never inferred query letters.

    Work in original pixels. Require a narrow, uniform, high-contrast rectangle
    near the observed row end, extending above AND below neighboring glyphs.
    A normal l/I/1, punctuation, an interior stem, or ambiguous bars are misses.
    No query-dependent spelling repair or punctuation stripping happens here.
    """
    if len(rows) != 1 or not rows[0]['text'].strip():
        return None
    box = rows[0]['box']
    w, h = crop.size
    top, bottom = round(box['top'] * h), round(box['bottom'] * h)
    height = bottom - top
    right = round(box['right'] * w)
    if height < 8 or not (0 < box['left'] < box['right'] < .98):
        return None
    pixels = crop.load()

    def distance(a, b):
        return max(abs(x-y) for x, y in zip(a, b))

    def background(x, y):
        samples = [pixels[x+dx, y] for dx in (-9, -6, 6, 9)]
        return tuple(round(median(c)) for c in zip(*samples))

    found = set()
    for x in range(max(10, right-height), min(w-10, right+height*2)):
        start = None
        lo, hi = max(1, top-height), min(h-1, bottom+height)
        for y in range(lo, hi+1):
            ink = y < hi and distance(pixels[x, y], background(x, y)) >= 65
            if ink and start is None:
                start = y
            if ink or start is None:
                continue
            end, begin = y, start
            start = None
            if end-begin < 20:
                continue
            color = pixels[x, (begin+end)//2]
            if any(distance(pixels[x, yy], color) > 35 for yy in range(begin+1, end-1)):
                continue
            left, right_edge = x, x+1
            while left > 1 and all(distance(pixels[left-1, yy], color) <= 35 for yy in range(begin+1, end-1)):
                left -= 1
            while right_edge < w-1 and all(distance(pixels[right_edge, yy], color) <= 35 for yy in range(begin+1, end-1)):
                right_edge += 1
            if not 1 <= right_edge-left <= 4:
                continue
            # Estimate actual nearby glyph height independently of OCR boxes,
            # which may themselves include the caret. Do not erase interiors.
            bg = background(x, begin)
            neighbors = [(xx, yy) for xx in range(max(round(box['left']*w), left-100), left-4)
                         for yy in range(lo, hi)
                         if distance(pixels[xx, yy], bg) >= 65]
            if not neighbors:
                continue
            nt, nb = min(yy for _, yy in neighbors), max(yy for _, yy in neighbors)+1
            if not (nt-begin >= 3 and end-nb >= 2 and end-begin >= (nb-nt)*1.25):
                continue
            # Text to the right makes this an interior bar, not a row-end caret.
            if any(distance(pixels[xx, yy], bg) >= 65
                   for xx in range(right_edge+2, min(w-10, right+height))
                   for yy in range(nt, nb)):
                continue
            flank = [pixels[xx, yy] for xx in (left-3, right_edge+3)
                     for yy in (begin, begin+1, end-2, end-1)]
            fill = tuple(round(median(c)) for c in zip(*flank))
            if any(distance(p, fill) > 25 for p in flank) or distance(color, fill) < 65:
                continue
            found.add((left, begin, right_edge, end, fill))
    if not found:
        return None
    # JPEG chroma subsampling can split one bar into differently colored
    # adjacent columns. Merge only overlapping/touching runs of the same bar.
    runs = sorted(found)
    left, top, right, bottom, fill = runs[0]
    for l, t, r, b, color in runs[1:]:
        if l > right or abs(t-top) > 2 or abs(b-bottom) > 2:
            return None
        right, top, bottom = max(right, r), min(top, t), max(bottom, b)
    if right-left > 4:
        return None
    fill = tuple(round(median(c)) for c in zip(*(r[4] for r in runs)))
    masked = crop.copy()
    masked.paste(fill, (left, top, right, bottom))
    return masked


def _mask_colored_caret(crop, rows):
    """Find an isolated native colored insertion bar independently of OCR end.

    Fast Vision can truncate the last letter and exclude the bar from its box.
    Unlike a monochrome l/I, a saturated blue/red uniform rectangle on neutral
    UI does not need to extend beyond both glyph ascenders and descenders.
    Never erase a neighboring glyph, an interior bar, or multiple candidates.
    """
    if len(rows) != 1 or not rows[0]['text'].strip():
        return None
    w, h = crop.size
    pixels = crop.load()
    found = set()
    box = rows[0]['box']
    def distance(a, b):
        return max(abs(x-y) for x, y in zip(a, b))
    for x in range(max(4, round(box['left']*w)), w-4):
        start = None
        for y in range(h+1):
            p = pixels[x, y] if y < h else (0, 0, 0)
            colored = max(p)-min(p) >= 80 and max(p) >= 130
            if colored and start is None:
                start = y
            if colored or start is None:
                continue
            top, bottom = start, y
            start = None
            if not 18 <= bottom-top <= min(60, h-4) or top < 2 or bottom > h-2:
                continue
            color = pixels[x, (top+bottom)//2]
            if any(distance(pixels[x, yy], color) > 35 for yy in range(top+1, bottom-1)):
                continue
            left, right = x, x+1
            while left > 1 and all(distance(pixels[left-1, yy], color) <= 35 for yy in range(top+1, bottom-1)):
                left -= 1
            while right < w-1 and all(distance(pixels[right, yy], color) <= 35 for yy in range(top+1, bottom-1)):
                right += 1
            if not 1 <= right-left <= 4 or left < 3 or right+2 >= w:
                continue
            # Uniform neutral background on BOTH flanks proves isolation.
            # The row may end before the caret, but may not begin after it.
            flank = [pixels[xx, yy] for xx in (left-2, right+2) for yy in range(top, bottom)]
            fill = tuple(round(median(c)) for c in zip(*flank))
            if max(fill)-min(fill) > 45 or any(distance(p, fill) > 25 for p in flank):
                continue
            neighbors = [(xx, yy) for xx in range(max(0, left-100), left-3)
                         for yy in range(max(0, top-10), min(h, bottom+10))
                         if distance(pixels[xx, yy], fill) >= 65]
            if not neighbors:
                continue
            nt, nb = min(yy for _, yy in neighbors), max(yy for _, yy in neighbors)+1
            if not (bottom-top >= nb-nt and (nt-top >= 3 or bottom-nb >= 2)):
                continue
            # Any trailing real glyph makes it an interior bar. Do not use the
            # requested spelling to decide which pixels to erase.
            end = min(w-4, max(round(box['right']*w)+30, right+100))
            if any(distance(pixels[xx, yy], fill) >= 65
                   for xx in range(right+3, end) for yy in range(max(0, top-3), min(h, bottom+3))):
                continue
            found.add((left, top, right, bottom, fill))
    if len(found) != 1:
        return None
    left, top, right, bottom, fill = found.pop()
    masked = crop.copy()
    masked.paste(fill, (left, top, right, bottom))
    return masked


def _separate_caret_row(crop, rows, query):
    """One exact row plus one adjacent OCR bar, independently proven by pixels."""
    if len(rows) != 2:
        return None
    candidates = [r for r in rows if _landscape_query_matches(r['text'], query)]
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    extra = next(r for r in rows if r is not candidate)
    if extra['text'].strip() not in ('|', 'I', 'l'):
        return None
    a, b = candidate['box'], extra['box']
    width, height = crop.size
    if not (0 < (b['right'] - b['left']) * width <= 4
            and 0 <= (b['left'] - a['right']) * width <= 20
            and max(a['top'], b['top']) < min(a['bottom'], b['bottom'])):
        return None
    merged = {'text': candidate['text'] + extra['text'], 'box': {
        'left': a['left'], 'right': b['right'],
        'top': min(a['top'], b['top']), 'bottom': max(a['bottom'], b['bottom'])}}
    masked = _mask_landscape_caret(crop, [merged])
    changed = ImageChops.difference(crop, masked).getbbox() if masked is not None else None
    if changed is None:
        return None
    # The erased physical bar must occupy the extra OCR glyph, not merely
    # another nearby bar. No punctuation, suffix or suggestion is discarded.
    x, y = (changed[0] + changed[2]) / 2, (changed[1] + changed[3]) / 2
    if not (b['left'] * width - 2 <= x <= b['right'] * width + 2
            and b['top'] * height - 2 <= y <= b['bottom'] * height + 2):
        return None
    return candidate


async def observe_landscape_query(agent, goal, frame, *, results=False):
    """Read only the top crop. This proves text, never ownership or focus.

    Require one complete row and reject all split/extra text, even if that
    costs a hit on an adjacent Search button. Vision reports constant .5
    confidence here; validate its numeric shape but do not use it as a gate.
    """
    image = _landscape_image(agent, frame)
    if image is None:
        return _local_miss(agent, goal, 'unsupported_or_disabled', results=results)
    def miss(reason, **details):
        return _local_miss(agent, goal, reason, results=results, **details)
    reader = getattr(agent, 'query_text_reader', None) or getattr(agent, 'navigation_text_reader', None)
    owned = False
    if reader is None:
        command = os.environ.get('LOCAL_OCR_BIN')
        if not command:
            return miss('reader_unavailable')
        reader = LocalTextReader([command, '--serve'])
        owned = True
    started = time.perf_counter()
    region = _query_region(image, goal.get('app'))
    if region is None:
        if owned:
            await reader.close()
        return miss('unregistered_query_band')
    crop = image.crop(region)
    async def read_crop(pixels):
        buffer = io.BytesIO()
        pixels.resize((pixels.width * 2, pixels.height * 2)).save(buffer, format='PNG')
        result = await reader.read(buffer.getvalue(), mode='fast')
        LocalTextReader._validate(result)
        return result
    try:
        observed = await read_crop(crop)
        if observed.get('mode') != 'fast':
            return miss('ocr_mode')
        rows = observed['rows']
        caret_masked = False
        separate = _separate_caret_row(crop, rows, goal['query']) if not results else None
        if separate is not None:
            rows, caret_masked = [separate], True
        if not results and not (len(rows) == 1 and _landscape_query_matches(rows[0]['text'], goal['query'])):
            masked = _mask_landscape_caret(crop, rows)
            if masked is None:
                masked = _mask_colored_caret(crop, rows)
            if masked is not None:
                observed = await read_crop(masked)
                if observed.get('mode') != 'fast':
                    return miss('ocr_mode_after_mask')
                rows = observed['rows']
                caret_masked = True
        matches = [r for r in rows if _landscape_query_matches(r['text'], goal['query'])]
        if len(matches) != 1:
            return miss('literal_mismatch', observed=[r['text'] for r in rows], caret_masked=caret_masked)
        candidate = matches[0]
        box = candidate['box']
        top = (region[1] + box['top'] * crop.height) / image.height
        bottom = (region[1] + box['bottom'] * crop.height) / image.height
        if not (0 < box['left'] < box['right'] < .98 and .04 <= top < bottom <= .10):
            return miss('query_geometry')
        # A suffix may be split off as tiny punctuation or a wrapped line.
        # No nonempty second row in this narrow band can prove a full value.
        if any(r is not candidate and r['text'].strip() for r in rows):
            return miss('extra_text_rows', observed=[r['text'] for r in rows])
        return {'seconds': time.perf_counter() - started, 'caret_masked': caret_masked,
                'box': {**box, 'left': (region[0] + box['left'] * crop.width) / image.width,
                        'right': (region[0] + box['right'] * crop.width) / image.width,
                        'top': top, 'bottom': bottom}}
    except (LocalTextError, OSError, ValueError, KeyError, TypeError) as exc:
        return miss('ocr_error', error=type(exc).__name__)
    finally:
        if owned:
            await reader.close()


def _query_input(agent, goal, *, submitted=False):
    """Bind to this task's unique acknowledged input and no intervening action."""
    steps = agent.steps
    queries = [(i, s) for i, s in enumerate(steps)
               if s.get('workflow') == 'app_search' and s.get('phase') == 'query']
    if len(queries) != 1:
        return None
    index, typed = queries[0]
    if typed.get('action') != 'type' or 'act_s' not in typed or typed.get('text') != goal['query']:
        return None
    inputs = [s for s in steps if 'act_s' in s and s.get('action') not in ('wait', 'done')]
    position = next((i for i, s in enumerate(inputs) if s is typed), -1)
    selected = (position > 0 and inputs[position-1].get('workflow') == 'app_search'
                and inputs[position-1].get('phase') == 'select'
                and inputs[position-1].get('_search_focus_verified') is True)
    # Current flow: verified focus, then an acknowledged, uninterrupted
    # clear sequence (caret to end, delete to line start, backspaces), then
    # typing. Every clear key must be ACKed in order right before the text.
    clear = typed.get('_search_clear_sequence') if typed.get('_search_focus_verified') is True else None
    cleared = False
    if isinstance(clear, dict) and isinstance(clear.get('backspaces'), int):
        count = 2 + clear['backspaces'] if clear.get('caret_to_end') else 1 + clear['backspaces']
        before = inputs[position-count:position] if position >= count else []
        cleared = (len(before) == count and all(
            s.get('workflow') == 'app_search' and s.get('phase') == 'clear_query'
            and s.get('_search_focus_verified') is True and s.get('_search_clear_index') == i
            for i, s in enumerate(before)))
    if not (typed.get('_search_was_empty_verified') is True or selected or cleared):
        return None
    if not submitted:
        return (index, typed) if inputs and inputs[-1] is typed else None
    submits = [(i, s) for i, s in enumerate(steps)
               if s.get('workflow') == 'app_search' and s.get('phase') == 'submit']
    if len(submits) != 1:
        return None
    submit_index, submit = submits[0]
    if (submit_index <= index or submit.get('action') != 'key' or submit.get('combo') != 'enter'
            or 'act_s' not in submit or len(inputs) < 2 or inputs[-2] is not typed or inputs[-1] is not submit):
        return None
    return submit_index, submit


def _region_changed(before, image, region):
    try:
        old = Image.open(io.BytesIO(before)).convert('RGB')
        if old.size != image.size:
            return False
        return sum(ImageStat.Stat(ImageChops.difference(old.crop(region), image.crop(region))).mean) / 3 >= 2
    except (OSError, ValueError, TypeError):
        return False


async def verify_landscape_literal(agent, goal, frame):
    image = _landscape_image(agent, frame)
    if image is None:
        return _local_miss(agent, goal, 'unsupported_or_disabled')
    context = _query_input(agent, goal) if image is not None else None
    if context is None:
        return _local_miss(agent, goal, 'input_context')
    before = getattr(agent, 'shots', {}).get(context[0])
    region = _query_region(image, goal.get('app'))
    if not before or region is None or not _region_changed(before, image, region):
        return _local_miss(agent, goal, 'missing_before_or_unchanged_query_region')
    return await observe_landscape_query(agent, goal, frame)


def remember_landscape_results(agent, goal, frame):
    """Called only after the existing strict model results/modal approval."""
    if _landscape_image(agent, frame) is None or _query_input(agent, goal, submitted=True) is None:
        return
    # Keep object references and value snapshots. Clearing/reusing steps, even
    # for the same query, must not import a previous task's approval.
    agent._landscape_results_proof = {
        'steps': agent.steps, 'goal': dict(goal), 'frame': frame,
        'inputs': [(s, dict(s)) for s in agent.steps
                   if 'act_s' in s and s.get('action') not in ('wait', 'done')]}


async def reuse_landscape_results(agent, goal, frame):
    def miss(reason):
        return _local_miss(agent, goal, reason, results=True, phase='reuse')
    image = _landscape_image(agent, frame)
    if image is None:
        return miss('unsupported_or_disabled')
    proof = getattr(agent, '_landscape_results_proof', None)
    if not proof:
        return miss('no_prior_results_proof')  # Never skip the first model approval.
    agent._landscape_results_proof = None
    context = _query_input(agent, goal, submitted=True)
    inputs = [s for s in agent.steps if 'act_s' in s and s.get('action') not in ('wait', 'done')]
    if (context is None or proof['steps'] is not agent.steps or proof['goal'] != goal
            or len(inputs) != len(proof['inputs'])
            or any(s is not old or s != snapshot for s, (old, snapshot) in zip(inputs, proof['inputs']))):
        return miss('reuse_input_context')
    editor = getattr(agent, 'shots', {}).get(context[0])
    if not editor or not _region_changed(editor, image, (0, round(image.height * .15), image.width, image.height)):
        return miss('missing_editor_or_unchanged_body')
    # A body diff alone could be a modal, loading page, or unrelated results.
    # Reuse only an unchanged previously approved full page, including chrome.
    try:
        approved = Image.open(io.BytesIO(proof['frame'])).convert('RGB')
        if approved.size != image.size or ImageChops.difference(approved, image).getbbox():
            return miss('approved_results_changed')
    except (OSError, ValueError, TypeError):
        return miss('invalid_approved_results')
    local = await observe_landscape_query(agent, goal, frame)
    if local is None:
        return miss('reuse_literal_mismatch')
    if agent.screen.frame != frame:
        return miss('frame_changed_during_check')
    agent._landscape_results_proof = proof
    agent.trace.write(kind='app-search-local-results-reuse', source='local_vision_ocr',
                      scope='Same-task prior model approval, unchanged approved page, exact query, and body changed from editor.')
    return local


async def _landscape_loaded_results(agent, app, frame):
    """Tabs exist during loading too. Require readable result-body text.

    This is a conservative positive signal, not a skeleton classifier. Unknown,
    empty, placeholder-only and explicit no-results pages keep the model path.
    """
    regions = {'TikTok': (415, 620, 1230, 1050),
               'YouTube': (610, 150, 1646, 800),
               'rednote': (0, 785, 1646, 925)}
    reader = getattr(agent, 'query_text_reader', None) or getattr(agent, 'navigation_text_reader', None)
    owned = False
    if reader is None:
        command = os.environ.get('LOCAL_OCR_BIN')
        if not command:
            return False
        reader = LocalTextReader([command, '--serve'])
        owned = True
    try:
        image = Image.open(io.BytesIO(frame)).convert('RGB')
        crop = image.crop(regions[app])
        buffer = io.BytesIO()
        crop.save(buffer, format='PNG')
        observed = await reader.read(buffer.getvalue(), mode='fast')
        LocalTextReader._validate(observed)
        if observed.get('mode') != 'fast':
            return False
        texts = [r['text'].strip() for r in observed['rows'] if r['text'].strip()]
        # A modal over otherwise loaded results must not be called complete.
        import re
        if any(re.search(r'\b(log[ -]?in|sign[ -]?in|allow|permission|loading|try again)\b', t, re.I) for t in texts):
            return False
        if app == 'YouTube':
            return (any(len(t.split()) >= 5 for t in texts)
                    and any(re.search(r'\b(?:views?|ago)\b|[0-9]+[KM]\b', t, re.I) for t in texts))
        # Actual card captions, not a spinner, static query or filter labels.
        return sum(len(t) >= 12 and len(t.split()) >= 3 for t in texts) >= 2
    except (LocalTextError, OSError, ValueError, KeyError, TypeError):
        return False
    finally:
        if owned:
            await reader.close()


async def verify_landscape_results(agent, goal, frame):
    """Exact query + registered App results chrome + loaded body, same task."""
    if os.environ.get('LANDSCAPE_RESULTS_LOCAL', '0') != '1':
        return _local_miss(agent, goal, 'results_disabled', results=True)
    image = _landscape_image(agent, frame)
    if image is None:
        return _local_miss(agent, goal, 'unsupported_or_disabled', results=True)
    if agent.screen.frame != frame:
        return _local_miss(agent, goal, 'frame_changed_before_check', results=True)
    if _query_input(agent, goal, submitted=True) is None:
        return _local_miss(agent, goal, 'input_context', results=True)
    from .app_controls import landscape_results_control
    anchors = landscape_results_control(frame, goal['app'], agent.phone.target_info())
    if not anchors:
        return _local_miss(agent, goal, 'results_anchors', results=True)
    started = time.perf_counter()
    literal = await observe_landscape_query(agent, goal, frame, results=True)
    if literal is None:
        return None  # observe_landscape_query already recorded its exact reason.
    if not await _landscape_loaded_results(agent, goal['app'], frame):
        return _local_miss(agent, goal, 'results_body_not_loaded', results=True)
    if agent.screen.frame != frame:
        return _local_miss(agent, goal, 'frame_changed_during_check', results=True)
    agent.trace.write(kind='app-search-local-results', source='local_vision_ocr',
                      template=anchors['template'], scope='Exact query label, registered landscape results anchors and readable loaded result cards; same-task acknowledged submit.')
    return {'seconds': time.perf_counter() - started, 'template': anchors['template']}


def blinking_caret(before, current, rows=()):
    """Two field-only crops differ by one isolated insertion bar.

    Timestamp/ACK/run binding belongs to focus_local. Static glyphs/rings do
    not qualify. Reuse the existing caret geometry when OCR supplies a row;
    otherwise the on/off pixel difference independently locates the bar.
    """
    if before.size != current.size:
        return False
    difference = ImageChops.difference(before, current)
    channels = difference.split()
    change = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    mask = change.point(lambda p: 255 if p >= 65 else 0)
    box = mask.getbbox()
    if not box:
        return False
    l, t, r, b = box
    if not (1 <= r-l <= 4 and 18 <= b-t <= 60 and 3 <= l and r+3 < current.width
            and 2 <= t and b < current.height-2):
        return False
    if ImageStat.Stat(mask.crop(box)).mean[0] < 255*.85:
        return False
    for visible, hidden in ((current, before), (before, current)):
        # Existing OCR-independent pixel checks for known row-end carets.
        if rows and (_mask_colored_caret(visible, rows) is not None
                     or _mask_landscape_caret(visible, rows) is not None):
            return True
        pixels = visible.load()
        colors = [pixels[x, y] for x in range(l, r) for y in range(t+1, b-1)]
        ink = tuple(round(median(c)) for c in zip(*colors))
        flank = [pixels[x, y] for x in (l-3, r+3) for y in range(t, b)]
        fill = tuple(round(median(c)) for c in zip(*flank))
        distance = lambda a, z: max(abs(x-y) for x, y in zip(a, z))
        hidden_colors = [hidden.getpixel((x, y)) for x in range(l, r) for y in range(t+1, b-1)]
        if (all(distance(p, ink) <= 35 for p in colors)
                and all(distance(p, fill) <= 25 for p in flank+hidden_colors)
                and distance(ink, fill) >= 65):
            return True
    return False
