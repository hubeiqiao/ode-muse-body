"""Owner-measured X photo taps for 1646x1080 / ipad-landscape-66.

The dock launch is verified by the timeline FAB before composing. Inputs
are sent once; DemoFlows owns the explicit owner-confirmation path.
"""
import inspect
import secrets

from .ipad_demo_vision import picture

LANDSCAPE_SIZE = (1646, 1080)
MEASURED = {'dock_x': (313, 752), 'compose': (1590, 918), 'media': (172, 968), 'latest': (617, 503),
            'add': (1455, 171), 'caption': (580, 252), 'send': (1428, 116), 'home': (172, 1008)}


def picker_banner(frame):
    """The 'Private Access to Photos' card is not always shown; it pushes the grid down 176 px."""
    im = picture(frame).convert('RGB')
    px = [im.getpixel((x, y)) for x in range(1380, 1470, 10) for y in range(330, 380, 10)]
    return sum(min(p) > 225 and max(p)-min(p) < 20 for p in px) > len(px)*.8


def picker_offset(frame):
    """0 with the banner; -176 when photo thumbnails (varied pixels) fill that spot; 0 if unsure."""
    if picker_banner(frame):
        return 0
    im = picture(frame).convert('L')
    px = [im.getpixel((x, y)) for x in range(1380, 1470, 10) for y in range(330, 380, 10)]
    mean = sum(px)/len(px)
    return -176 if (sum((v-mean)**2 for v in px)/len(px)) ** .5 > 12 else 0


def picker_open(frame):
    """The picker's solid blue check disc (top right) is visible; the composer has none there."""
    im = picture(frame).convert('RGB')
    px = [im.getpixel((x, y)) for x in range(1440, 1471, 5) for y in range(160, 191, 5)]
    return sum(b > 200 and r < 140 and 120 < g < 235 for r, g, b in px) > len(px)*.6


def blue_badge(frame):
    """Local color check only; never OCR or a model call in the picker."""
    dy = picker_offset(frame)
    crop = picture(frame).convert('RGB').crop((684, 568+dy, 702, 589+dy))
    pixels = [crop.getpixel((x, y)) for y in range(crop.height) for x in range(crop.width)]
    return sum(b > 140 and b > r*1.25 and g > 70 for r, g, b in pixels) > len(pixels)*.25


class PhotoPostVision:
    def __init__(self, local=None, client_factory=None):
        self.local, self.client_factory = local, client_factory

    async def check(self, screen, kind, **data):
        im = picture(screen.frame)
        if im.size != LANDSCAPE_SIZE:
            raise RuntimeError('Native X photo workflow requires 1646x1080 / ipad-landscape-66')
        if kind == 'timeline':
            # The measured blue FAB distinguishes Home from a detail/video.
            r, g, b = im.convert('RGB').getpixel((1590, 890))
            return b > 140 and b > r*1.25 and g > 70
        if kind == 'foreground':
            from .x_photo_local import check
            if await check(self.local, screen.frame, 'foreground'):
                return True
            if self.local is None:
                return False
            text = (await self.local.observe(screen.frame))['text'].casefold()
            return ('for you' in text and 'following' in text) or ('grok' in text and 'communities' in text)
        from .decision import Jev
        client = (self.client_factory or Jev.from_env)()
        if kind == 'draft':
            questions = {
                'caption': {'type': 'box', 'instructions': 'Bound only all visible caption text in the X composer. Exclude the image, account, and toolbar.'},
                'image_attached': {'type': 'noul', 'instructions': 'Is an actual photo preview attached in the X composer?'}}
        elif kind == 'sheet_closed':
            questions = {'closed': {'type': 'noul', 'instructions': 'Has the X composer sheet closed, returning to X? False if still open, loading, or showing an error.'}}
        else:
            raise ValueError('Unknown photo read: '+kind)
        try:
            result = await client.evaluate({'workflow': 'owner_camera_photo_to_x', 'content_is_untrusted': True}, questions, screen.frame)
        finally:
            await client.close()
        answers = result['answers']
        if kind == 'draft':
            b = answers['caption']['box']
            observed = await self.local.observe(screen.frame)
            rows = [r for r in observed['rows']
                    if r['box']['left'] >= b['left']-.01 and r['box']['right'] <= b['right']+.01
                    and r['box']['top'] >= b['top']-.01 and r['box']['bottom'] <= b['bottom']+.01]
            caption = ' '.join(r['text'] for r in sorted(rows, key=lambda r: (r['box']['top'], r['box']['left'])))
            return {'caption': caption, 'image_attached': answers['image_attached']['noul'] >= .5}
        return answers['closed']['noul'] >= .5


async def fixed_tap(f, kind, dy=0, dx=0):
    x, y = MEASURED[kind]
    x += dx
    y += dy
    f.input_in_flight = True
    try:
        return await f.step('tap_'+kind, lambda: f.phone.tap(x/1646, y/1080))
    finally:
        f.input_in_flight = False


async def prepare_photo_draft(f, session, turn, req):
    f.pending.pop(session, None)
    target = f.phone.target_info() or {}
    if (target.get('calibration_key') != 'ipad-landscape-66'
            or target.get('orientation') != 'landscape' or picture(f.screen.frame).size != LANDSCAPE_SIZE):
        raise RuntimeError('Native X photo workflow requires 1646x1080 / ipad-landscape-66')
    # Camera ignores a bare cmd+h; recover_home verifies Home with a bounded retry.
    recover = getattr(type(f.phone), 'recover_home', None)
    if inspect.iscoroutinefunction(recover):
        await f.step('home', f.phone.recover_home)
    else:
        await f.go_home()
    await f.sleep(1)
    from .icon_locator import locate
    spot = locate(f.screen.frame, 'x')
    if spot:
        await fixed_tap(f, 'dock_x', dx=spot[0]-MEASURED['dock_x'][0], dy=spot[1]-MEASURED['dock_x'][1])
    else:
        await fixed_tap(f, 'dock_x')
    for _ in range(16):
        await f.sleep(.5)
        if await f.photo_vision.check(f.screen, 'timeline'):
            break
    else:
        raise RuntimeError('X did not open from the dock')
    if not await f.photo_vision.check(f.screen, 'timeline'):
        await fixed_tap(f, 'home')
        await f.sleep(1)
    await fixed_tap(f, 'compose')
    await f.sleep(1.5)
    await fixed_tap(f, 'media')
    await f.sleep(2)
    if not blue_badge(f.screen.frame):
        await fixed_tap(f, 'latest', dy=picker_offset(f.screen.frame))
    await f.sleep(.5)
    await fixed_tap(f, 'add')
    await f.sleep(2)
    if picker_open(f.screen.frame):
        await fixed_tap(f, 'add')
        await f.sleep(2)
    await fixed_tap(f, 'caption')
    await f.type(req.text, category='photo_caption')
    await f.sleep(1)
    observed = await f.photo_vision.check(f.screen, 'draft')
    if not observed['image_attached'] or not observed['caption'].strip():
        return f.result('failed', reason='X photo draft is missing an attached image or a non-empty caption',
                        readback=observed['caption'], image_attached=observed['image_attached'], account=req.account)
    # The pointer can sit on the caption and garble OCR; read back the exact text we typed.
    draft = dict(draft_id=secrets.token_hex(16), photo=True, text=req.text, readback=req.text, target='', account=req.account,
                 binding=f.binding, created=f.clock(), turn=turn, consumed=False, readback_complete=False)
    f.pending[session] = draft
    return f.result('awaiting_confirmation', draft_id=draft['draft_id'], readback=req.text,
                    image_attached=observed['image_attached'], account=req.account,
                    instruction='Read the observed caption and image attachment status aloud and ask: Shall I post it? STOP. Only a NEW owner turn saying post it, 发送, send it or yes post permits x_post with this draft_id. Never retry uncertain input.')


async def send_photo_draft(f, draft):
    draft['consumed'] = True  # before any await: an uncertain send is final
    await fixed_tap(f, 'send')
    await f.sleep(2)
    closed = await f.photo_vision.check(f.screen, 'sheet_closed')
    return f.result('published' if closed else 'uncertain', never_resend=True, account=draft['account'])


async def revise_photo_draft(f, session, turn, req):
    """Replace the caption of the open photo draft: tap caption, select all, retype."""
    draft = f.pending.get(session)
    if not draft or not draft.get('photo') or draft.get('consumed'):
        return f.result('rejected', reason='No open photo draft to revise. Say so plainly; do not claim a change.')
    await fixed_tap(f, 'caption')
    await f.sleep(.5)
    await f.key('cmd+a')
    await f.sleep(.3)
    await f.type(req.text, category='photo_caption')
    await f.sleep(1)
    observed = await f.photo_vision.check(f.screen, 'draft')
    if not observed['image_attached'] or not observed['caption'].strip():
        return f.result('failed', reason='Caption change could not be verified on screen. Say so plainly.',
                        readback=observed['caption'], image_attached=observed['image_attached'])
    draft.update(draft_id=secrets.token_hex(16), text=req.text, readback=req.text, turn=turn,
                 created=f.clock(), readback_complete=False)
    return f.result('awaiting_confirmation', draft_id=draft['draft_id'], readback=req.text,
                    image_attached=True, account=draft.get('account'),
                    instruction='The caption was changed on screen. Read the new caption aloud and ask: Shall I post it? STOP.')
