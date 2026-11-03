"""Bounded local mirror observations. No provider, device or network clients.

OCR labels propose controls, not focus or delivery. Every input requires an
independent current-screen guard. Unknown layouts fail closed.
"""
import asyncio
import io
import math
import re
import time

from PIL import Image, ImageChops, ImageDraw, ImageStat
from .inventory_ocr import InventoryOCR


def picture(frame):
    with Image.open(io.BytesIO(frame)) as im:
        if im.width * im.height > 4_000_000:
            raise RuntimeError('Mirror geometry exceeds demo limit')
        return im.convert('RGB')


def frame_diff(before, after, box=None):
    a, b = picture(before), picture(after)
    if a.size != b.size:
        raise RuntimeError('Mirror geometry changed')
    if box:
        bounds = tuple(round(v * (a.width if i % 2 == 0 else a.height)) for i, v in enumerate(box))
        a, b = a.crop(bounds), b.crop(bounds)
    return sum(ImageStat.Stat(ImageChops.difference(a.resize((96, 64)), b.resize((96, 64)))).mean) / 3


def same_control_context(before, after, target):
    """Same local control and chrome after movement; pointer mask stays 11 px."""
    try:
        a, b = picture(before), picture(after)
        if a.size != b.size: return False
        x, y = round(target['x']*a.width), round(target['y']*a.height)
        for im in (a, b):
            ImageDraw.Draw(im).ellipse((x-11, y-11, x+11, y+11), fill='black')
        box = target['box']
        bounds = (max(0,round(box['left']*a.width)-8), max(0,round(box['top']*a.height)-8),
                  min(a.width,round(box['right']*a.width)+8), min(a.height,round(box['bottom']*a.height)+8))
        regions = (bounds, (0, 0, a.width, round(a.height*.12)))
        return all(sum(ImageStat.Stat(ImageChops.difference(a.crop(r),b.crop(r))).mean)/3 <= 2 for r in regions)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def detect_shutter(frame):
    """Unique filled white circle on black control edge, not a guessed point."""
    im = picture(frame)
    landscape = im.width > im.height
    small = im.resize((400, round(400 * im.height / im.width)))
    w, h = small.size
    xs = range(round(w * .88), w) if landscape else range(round(w * .3), round(w * .7))
    ys = range(round(h * .2), round(h * .8)) if landscape else range(round(h * .85), h)
    pixels = small.load()
    white = {(x, y) for y in ys for x in xs if min(pixels[x, y]) > 225}
    found = []
    while white:
        seed = white.pop(); todo = [seed]; part = [seed]
        while todo:
            x, y = todo.pop()
            for p in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if p in white:
                    white.remove(p); todo.append(p); part.append(p)
        l, r = min(x for x, _ in part), max(x for x, _ in part)+1
        t, b = min(y for _, y in part), max(y for _, y in part)+1
        dw, dh = r-l, b-t
        if not (12 <= dw <= w*.13 and .55*dw <= dh <= 1.15*dw):
            continue
        radius=dw/2; cx=(l+r)/2; cy=t+radius
        if l < 4 or t < 4 or r+4 >= w or cy+radius+4 >= h:
            continue
        # The lower-right disc can be covered by AssistiveTouch. Require the
        # complete upper semicircle's white core and black exterior instead of
        # treating that known overlap as absence of the shutter.
        angles=[math.pi+i*math.pi/8 for i in range(9)]
        core=[pixels[round(cx+radius*.65*math.cos(a)),round(cy+radius*.65*math.sin(a))] for a in angles]
        rim=[pixels[round(cx+(radius+4)*math.cos(a)),round(cy+(radius+4)*math.sin(a))] for a in angles]
        if any(min(p)<215 for p in core) or any(max(p)>100 for p in rim):
            continue
        box = dict(left=l/w, top=t/h, right=r/w, bottom=(cy+radius)/h)
        found.append({'shutter': (cx/w, cy/h), 'box': box})
    return found[0] if len(found) == 1 else None


def public_text(text):
    """Drop the signed-out greeting every public Amazon page carries ("Hello, sign in /
    Account & Lists") so only a real login wall or challenge reads as one."""
    return re.sub(r'hello,?\s*sign in', '', text, flags=re.I)


# Landscape 1646x1080 shutter measured 2026-10-31: centre (1544.5, 540), radius ~48 px.
FIXED_SHUTTER_PX = (1544.5, 540, 48)


def fixed_shutter(frame):
    """Shutter at its measured landscape spot, judged only on the half that lies on the
    black control column. A bright scene can touch the left half (a white wall merges
    with the disc), so the generic detector misses it; the column side never changes."""
    im = picture(frame)
    if im.size != (1646, 1080):
        return None
    cx, cy, radius = FIXED_SHUTTER_PX
    px = im.load()
    # Top to right only: AssistiveTouch covers the lower-right quarter.
    angles = [math.pi*(-.5+i/8) for i in range(5)]
    core = [px[round(cx+radius*.65*math.cos(a)), round(cy+radius*.65*math.sin(a))] for a in angles]
    rim = [px[round(cx+(radius+8)*math.cos(a)), round(cy+(radius+8)*math.sin(a))] for a in angles]
    if any(min(p) < 215 for p in core) or any(max(p) > 100 for p in rim):
        return None
    return {'shutter': (cx/im.width, cy/im.height),
            'box': dict(left=(cx-radius)/im.width, top=(cy-radius)/im.height,
                        right=(cx+radius)/im.width, bottom=(cy+radius)/im.height)}


def blocked_by_login(kind, text):
    """A challenge always blocks. Amazon results always show 'Hello, sign in' in the header,
    so for products only an actual login form (password or email field) counts."""
    if re.search(r'\b(captcha|verify you are human)\b', text, re.I):
        return True
    if kind == 'products':
        return bool(re.search(r'\b(password|email or mobile phone number)\b', text, re.I))
    return bool(re.search(r'\b(sign in|log in)\b', text, re.I))


def product_rows(text):
    """Group visible OCR blocks around prices. Missing values remain unknown."""
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    rows = []
    for i, line in enumerate(lines):
        price = re.fullmatch(r'\$\d[\d,]*(?:\.\d{2})?', line)
        if not price or i == 0:
            continue
        name = lines[i-1]
        if name.startswith('$') or re.search(r'out of 5|stars|sponsored|delivery', name, re.I):
            continue
        rating = None
        for following in lines[i+1:i+3]:
            m = re.search(r'([0-5](?:\.\d)?)\s+(?:out of 5|stars)', following, re.I)
            if m:
                rating = m[1]; break
        rows.append(dict(name=name, price=price[0], rating=rating))
    return rows[:3]


def product_cards(rows):
    """Amazon results are cards side by side, so group by column: each price row takes
    the rating and name lines just above it in the same column. Amazon prints cents as
    superscript, which OCR reads as "$49995"; with no separator the last two digits are
    the cents. Missing values remain unknown."""
    cards = []
    for price in rows:
        m = re.fullmatch(r'\$\s?(\d[\d,]*)(?:[.,](\d{2}))?', price['text'].strip())
        if not m:
            continue
        whole, cents = m[1].replace(',', ''), m[2]
        if cents is None and len(whole) >= 3:
            whole, cents = whole[:-2], whole[-2:]
        above = sorted((r for r in rows if r is not price
                        and abs(r['box']['left']-price['box']['left']) < .03
                        and 0 < price['box']['top']-r['box']['top'] < .14),
                       key=lambda r: r['box']['top'])
        rating, name = None, []
        for r in above:
            t = r['text'].strip()
            if rating is None and (g := re.match(r'([0-5](?:\.\d)?)\s*[*★☆]', t)):
                rating = g[1]
            elif not re.search(r'sponsored|delivery|bought in|\$', t, re.I):
                name.append(t)
        if name:
            cards.append(dict(name=' '.join(name), price='$'+whole+('.'+cents if cents else ''),
                              rating=rating, price_ocr=price['text'].strip()))
    return cards[:3]


class NativeLayoutOCR:
    """Apple Vision OCR (LOCAL_OCR_BIN worker, same one the app uses) in the read_layout shape.

    It keeps faint placeholders such as Spotlight's "Search" that Tesseract drops, reads
    Chinese, and takes ~0.2 s. Fast mode reports a flat 0.5 confidence."""
    min_confidence = .5

    def __init__(self, reader):
        self.reader = reader

    async def read_layout(self, image):
        data = await self.reader.read(image, mode='fast')
        rows = sorted(({'text': r['text'], 'confidence': r.get('confidence', 0), 'box': dict(r['box'])}
                       for r in data.get('rows', []) if str(r.get('text', '')).strip()),
                      key=lambda r: (r['box']['top'], r['box']['left']))
        return {'source': 'local_vision_ocr', 'is_accessibility_tree': False, 'rows': rows,
                'text': '\n'.join(r['text'] for r in rows), 'read_s': data.get('read_s')}


class DemoVision:
    def __init__(self, ocr=None, *, camera=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.ocr = ocr or InventoryOCR()
        self.min_confidence = getattr(self.ocr, 'min_confidence', .9)
        self.social_ocr = ocr or InventoryOCR(languages='eng+chi_sim')
        self.social_cached = None
        self.camera = camera
        self.clock, self.sleep = clock, sleep
        self.shutters = {}
        self.cached = None
        self.reply_box = None

    async def observe(self, frame):
        if self.cached and self.cached['frame'] == frame:
            return self.cached
        result = await self.ocr.read_layout(frame)
        self.cached = {**result, 'frame': frame}
        return self.cached

    async def observe_social(self, frame):
        if self.social_cached and self.social_cached['frame'] == frame:
            return self.social_cached
        result = await self.social_ocr.read_layout(frame)
        self.social_cached = {**result, 'frame':frame}
        return self.social_cached

    async def spotlight_x_label(self, frame, head, end):
        """Read the first icon's label in the measured landscape Spotlight layout.

        Native fast OCR omits isolated letters in a full frame, even when it
        reads adjacent XChat/Quantumult X labels. Isolating the label restores
        it. Anchor to the current Top Hit header, exclude the icon itself and
        stop before the next section. Never use a later X or the Home dock.
        """
        try:
            im = picture(frame)
        except (OSError, ValueError):
            return False
        if im.size != (1646, 1080) or not (.2 < head['left'] < .5 and .08 < head['top'] < .3):
            return False
        box = (head['left']+.015, head['top']+.147, head['left']+.08, head['top']+.18)
        if box[3] >= end:
            return False
        bounds = tuple(round(v*(im.width if i%2 == 0 else im.height)) for i, v in enumerate(box))
        out = io.BytesIO()
        im.crop(bounds).save(out, format='PNG')
        data = await self.ocr.read_layout(out.getvalue())
        # One entire label, not a substring of XChat or Quantumult X. Fast
        # Apple OCR reports 0.5 for every label; do not weaken global thresholds.
        return len(data['rows']) == 1 and data['rows'][0]['text'].strip().casefold() == 'x'

    async def wait_stable(self, screen, *, before, timeout=12, dwell=.8, region=(0, .12, 1, .94)):
        """Require changed content, three new observations and a quiet dwell.

        A repeated cached frame is never a completion signal. Byte changes from
        JPEG noise alone do not establish that an answer arrived.
        """
        deadline = self.clock()+timeout
        stamp = screen.updated
        last = before; changed = False; quiet_since = None; samples = 0
        while self.clock() < deadline:
            if not screen.live:
                raise RuntimeError('Mirror disconnected during stable-frame wait')
            await self.sleep(.2)
            if screen.updated <= stamp:
                continue
            current, stamp = screen.frame, screen.updated
            changed = changed or frame_diff(before, current, region) >= 2
            delta = frame_diff(last, current, region); last = current
            if changed and delta < 1:
                samples += 1
                if quiet_since is None: quiet_since = self.clock()
                if samples >= 3 and self.clock()-quiet_since >= dwell:
                    return current
            else:
                samples = 0; quiet_since = None
        raise RuntimeError('No changed, fresh, stable content before deadline')

    @staticmethod
    def text_has(text, needle):
        if re.match(r'https?://', needle):
            # Address bars clip the scheme and OCR confuses / l I | and = -; compare
            # the rest of the URL with those characters folded together.
            fold = lambda v: re.sub(r'[^a-z0-9+]', '', re.sub(r'[/|i]', 'l', v.casefold().replace('=', '-')))
            return fold(re.sub(r'^https?://', '', needle)) in fold(text)
        return ' '.join(needle.split()).casefold() in ' '.join(text.split()).casefold()

    async def target(self, screen, kind, **kw):
        if kind == 'social_profile_entry':
            from .ipad_social_yolo import profile_target
            data = await self.observe_social(screen.frame)
            return profile_target(data, kw['app'], kw.get('stage', 0))
        data = await self.observe(screen.frame)
        labels = {'grok_input': ('Ask anything', 'Ask Grok', 'Message Grok'),
                  'reply_button': ('Reply',), 'reply_input': ('Post your reply', 'Post your reply!'),
                  'post_button': ('Reply',), 'flip': ('Flip', 'Switch camera')}
        allowed = (kw['query'],) if kind == 'named_post' else labels.get(kind, ())
        rows = [r for r in data['rows'] if r['text'].strip().casefold() in
                tuple(s.casefold() for s in allowed) and r['confidence'] >= self.min_confidence]
        if kind == 'named_post':
            rows = [r for r in rows if .18 <= r['box']['top'] <= .85 and r['box']['left'] >= .25]
        if kind in ('grok_input', 'reply_input'):
            rows = [r for r in rows if r['box']['top'] >= .35]
        if kind == 'post_button':
            rows = [r for r in rows if r['box']['left'] > .5]
        if len(rows) != 1:
            raise RuntimeError('Unique local control not found: '+kind)
        box = rows[0]['box']
        if kind == 'reply_input': self.reply_box = dict(box)
        return {'x': (box['left']+box['right'])/2, 'y': (box['top']+box['bottom'])/2, 'box': box}

    async def check(self, screen, kind, **kw):
        frame = screen.frame
        if kind == 'camera':
            # A circle alone is never app identity. Use existing local Camera
            # chrome templates and PHOTO-mode verification, no vision model.
            if not self.camera or not self.camera.camera_still(frame):
                raise RuntimeError('Camera PHOTO chrome templates not verified; configure CAMERA_TEMPLATE_DIR')
            im = picture(frame)
            phone = self.camera.phone
            target = phone.target_info() or {}
            key = (phone.device.links, im.size, target.get('calibration_key'), target.get('orientation'))
            found = self.shutters.get(key)
            if found is None: found = detect_shutter(frame) or fixed_shutter(frame)
            if not found:
                raise RuntimeError('Unique unobstructed Camera shutter not found')
            from .camera_conversation import LANDSCAPE_SIZE, THUMB_PROBE, SHUTTER_TAP, SHUTTER_CLEAR_BOX
            if im.size != LANDSCAPE_SIZE:
                raise RuntimeError('Camera orientation profile not locally verified')
            # Existing measured safe patch avoids AssistiveTouch overlap.
            patch = im.crop(SHUTTER_CLEAR_BOX)
            if min(ImageStat.Stat(patch).mean) < 220:
                raise RuntimeError('Camera shutter aim patch is covered')
            # Only a currently verified clear PHOTO shutter can reuse the
            # cached detection. Link, calibration and geometry invalidate it.
            self.shutters.clear()
            self.shutters[key] = found
            return {**found, 'shutter': SHUTTER_TAP,
                    'thumbnail': tuple(v/(im.width if i%2 == 0 else im.height) for i, v in enumerate(THUMB_PROBE)),
                    'direction': None}
        if kind == 'capture':
            deadline = self.clock()+3; stamp = kw['stamp']
            while self.clock() < deadline:
                await self.sleep(.1)
                if not screen.live: return False
                if screen.updated <= stamp: continue
                stamp = screen.updated
                if self.camera and self.camera.camera_still(screen.frame) and frame_diff(kw['before'], screen.frame, kw['thumbnail']) >= 6:
                    return True
            return False
        if kind == 'direction':
            # The stock iPad Camera has no reliable front/rear indicator.
            # Never infer an absolute lens from brightness or a toggle ACK.
            return None
        if kind == 'focus':
            from .app_query_text import blinking_caret
            box = kw['box']; old = frame; stamp = screen.updated
            bounds = tuple(round(box[k] * (picture(frame).width if i%2 == 0 else picture(frame).height))
                           for i, k in enumerate(('left', 'top', 'right', 'bottom')))
            deadline = self.clock()+2
            while self.clock() < deadline:
                await self.sleep(.1)
                if screen.updated <= stamp: continue
                now = screen.frame; stamp = screen.updated
                if blinking_caret(picture(old).crop(bounds), picture(now).crop(bounds)):
                    return True
                old = now
            return False
        if kind in ('answer', 'products', 'post_read', 'post_results'):
            stable = await self.wait_stable(screen, before=kw['before'], timeout=30 if kind == 'answer' else 12,
                                            dwell=1.4 if kind == 'answer' else .8)
            data = await self.observe(stable)
            if blocked_by_login(kind, public_text(data['text'])):
                raise RuntimeError('Login or challenge visible; no credentials or further input')
            if kind == 'products':
                if not self.text_has(data['text'], 'amazon') or not self.text_has(data['text'], kw.get('query', '')):
                    raise RuntimeError('Amazon public results identity missing')
                items = product_cards(data.get('rows', [])) or product_rows(data['text'])
                if not items: raise RuntimeError('No locally readable product/price blocks')
                return {'items': items, 'text': data['text'], 'source': data['source']}
            if kind == 'answer':
                if (not re.search(r'\bGrok\b', data['text']) or self.text_has(data['text'], 'Stop generating')
                        or not kw.get('query') or not self.text_has(data['text'], kw['query'])):
                    raise RuntimeError('Grok answer identity or completion not established')
                old = await self.observe(kw['before'])
                previous = {s.strip() for s in old['text'].splitlines()}
                chrome = re.compile(r'^(?:Grok(?: Bot)?|Ask anything|Ask Grok|Message Grok|Copy|Regenerate|Good response|Bad response|Thinking[. ]*|Searching[. ]*)$', re.I)
                lines = [s.strip() for s in data['text'].splitlines() if s.strip()
                         and s.strip() not in previous and s.strip() != kw['query'].strip()
                         and not chrome.fullmatch(s.strip())]
                answer = '\n'.join(lines)
                if len(answer) < 8:
                    raise RuntimeError('No new answer beyond the prompt echo or existing history')
                return {'text': answer, 'frame': stable, 'source': data['source'],
                        'completion_basis': 'fresh_frame_stability_and_new_ocr_content'}
            if kind == 'post_results' and not all(self.text_has(data['text'], s) for s in ('Latest', 'People', kw['query'])):
                raise RuntimeError('Named X post search results not locally verified')
            return {'text': data['text'], 'frame': stable, 'source': data['source']}
        if kind in ('social_landing', 'social_profile', 'grok_saved', 'public_search'):
            from .ipad_social_yolo import landing, parse_profile, SECURITY
            if kind in ('social_landing', 'social_profile', 'public_search', 'grok_saved'):
                frame = await self.wait_stable(screen, before=kw['before'])
            data = (await self.observe_social(frame) if kind in ('social_landing', 'social_profile')
                    else await self.observe(frame))
            text = data['text']
            if kind == 'social_landing':
                state = landing(kw['app_key'], text)
                if state['status'] == 'clear': state['profile'] = parse_profile(kw['app_key'], text)
                return state
            if SECURITY.search(text):
                if kind == 'social_profile': return False
                raise RuntimeError('Login or security page; no further input')
            if kind == 'social_profile': return parse_profile(kw['app'], text)
            if kind == 'public_search':
                if not self.text_has(text, 'Google') or not self.text_has(text, kw['query']):
                    raise RuntimeError('Public search identity missing')
            else:
                if not self.text_has(text, 'Grok') or re.search(r'Stop generating|Thinking|正在思考', text, re.I):
                    raise RuntimeError('Existing Grok answer not locally established')
                chrome = re.compile(r'^(?:Grok(?: Bot)?|Ask anything|Ask Grok|Message Grok|Copy|Regenerate|Good response|Bad response)$', re.I)
                text = '\n'.join(s.strip() for s in text.splitlines() if s.strip() and not chrome.fullmatch(s.strip()))
                if len(text) < 8: raise RuntimeError('No readable existing answer')
            return {'text':text[:4000], 'source':data['source'], 'content_only':True}
        data = await self.observe(frame)
        text = data['text']
        if re.search(r'\b(sign in|log in|password|captcha)\b', public_text(text), re.I):
            return False
        if kind == 'launch':
            return self.text_has(text, kw['app'])
        if kind == 'top_hit':
            # Enter before results arrive opens nothing or the wrong thing, so the
            # row right under Spotlight's "Top Hit" header must be the target.
            # App hits are a large icon with the name ~15 % below the header; the bar
            # also completes to "<name> - Open" when Enter will launch it.
            rows = data['rows']
            heads = [r for r in rows if r['text'].strip().casefold() == 'top hit']
            want = kw['text'].casefold().replace(' ', '')
            squash = lambda v: re.sub(r'\s+', '', v.strip().casefold())
            if '/' in want:
                # URL hits: OCR confuses / l I | and = -, as in the address bar.
                fold = lambda v: re.sub(r'[^a-z0-9+]', '', re.sub(r'[/|i]', 'l', v.casefold().replace('=', '-')))
                want, squash = fold(want), fold
            completed = any(re.fullmatch(re.escape(want)+r'[-–—]open', squash(r['text']))
                            and r['box']['top'] < .2 for r in rows)
            if want == 'x':
                if completed: return True
                if len(heads) != 1: return False
                header = heads[0]['box']
                head = header['top']
                # Spotlight may show several icons under Top Hit. OCR can emit
                # separate labels or one combined line; Enter opens the FIRST
                # icon, never an X later in the row or in Suggestions/Apps.
                end = min((r['box']['top'] for r in rows
                           if r['text'].strip().casefold() in (
                               'suggestions', 'siri suggestions', 'apps', 'websites',
                               'search in app', 'show more') and r['box']['top'] > head),
                          default=head+.2)
                hits = [r for r in rows if head+.09 < r['box']['top'] < min(end, head+.2)
                        and header['left']-.01 <= r['box']['left'] < .76]
                if hits:
                    top = min(r['box']['top'] for r in hits)
                    first = min((r for r in hits if r['box']['top'] <= top+.03),
                                key=lambda r: r['box']['left'])
                    if first['box']['left'] <= header['left']+.09:
                        return bool(re.match(r'^X(?:\s|$)', first['text'].strip()))
                return await self.spotlight_x_label(frame, header, end)
            return completed or len(heads) == 1 and any(
                squash(r['text']) == want and 0 < r['box']['top'] - heads[0]['box']['top'] < .2 for r in rows)
        if kind == 'spotlight':
            # iPadOS 26 Spotlight is a translucent centred bar near the top. Evidence:
            # its "Search" placeholder (the caret often hides "Se", so OCR reads "arch"),
            # or its result section headers, since a selected old query is often
            # unreadable. Off-centre search fields (Settings sidebar) are not Spotlight.
            headers = ('top hit', 'websites', 'suggestions', 'siri suggestions', 'google suggestions',
                       'search in app', 'show more')
            return any(re.fullmatch(r'[|lI\[(]?\s*(?:Q\s*)?(?:S?e)?arch', r['text'].strip(), re.I)
                       and r['box']['top'] < .3 and r['box']['left'] > .2 and r['box']['right'] < .8
                       or r['text'].strip().casefold() in headers and r['box']['top'] < .3
                       and .2 < r['box']['left'] < .8
                       for r in data['rows'])
        if kind == 'literal':
            return self.text_has(text, kw['text'])
        if kind == 'post_url':
            urls = re.findall(r'https://(?:www\.)?x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]+', text)
            return urls[0] if len(urls) == 1 else False
        if kind in ('post_target', 'draft'):
            # The URL is independently read from Safari's selected address by
            # the workflow. Account proof must be in the self-account sidebar,
            # never a handle mentioned inside a public post.
            accounts = [r for r in data['rows'] if r['text'].strip() == kw['account']
                        and r['box']['right'] < .28 and r['box']['top'] > .6]
            if len(accounts) != 1 or not all(self.text_has(text, s) for s in ('Home', 'Explore', 'Profile')):
                return False
            if kind == 'post_target': return True
            if self.reply_box is None: return False
            b = self.reply_box
            lines = [r['text'] for r in data['rows'] if r['box']['top'] >= b['top']-.01
                     and r['box']['bottom'] <= min(.92, b['bottom']+.25)
                     and r['box']['left'] >= b['left']-.03 and r['text'] not in ('Reply', 'Everyone can reply')]
            actual = ' '.join(' '.join(lines).split())
            if actual != ' '.join(kw['text'].split()): return False
            return {'text': actual, 'target': kw['target'], 'account': kw['account']}
        if kind == 'published':
            # A toast alone is not exact-target delivery proof.
            try:
                data = await self.check(screen, 'post_read', before=kw['before'])
            except RuntimeError:
                return False
            return all(self.text_has(data['text'], s) for s in (kw['text'], kw['account'], kw['target'])) and not self.text_has(data['text'], 'Post your reply')
        raise RuntimeError('Unsupported local demo observation: '+kind)


def radio_blue(frame, label_box):
    """Blue pixel count in the radio circle left of a Settings option label (iPadOS 26 layout)."""
    im = picture(frame)
    y = round((label_box['top']+label_box['bottom'])*im.height/2)
    r = round(label_box['left']*im.width)-2
    # OCR sometimes folds the radio glyph into the label box, so also look 30 px inside it; text is never blue.
    region = im.crop((max(0, r-44), max(0, y-18), max(1, r+32), min(im.height, y+19)))
    return sum(1 for yy in range(region.height) for x in range(region.width)
               if (lambda c: c[2] > 150 and c[2]-c[0] > 70 and c[1] > 60)(region.getpixel((x, yy))))


def selected_checkmark(frame, label_box, others=()):
    """Full Screen Apps is selected only when its radio is a filled blue circle
    and every other mode's radio is not. Unknown layouts stay unready."""
    mine = radio_blue(frame, label_box)
    return mine >= 120 and all(radio_blue(frame, box) < 30 for box in others)
