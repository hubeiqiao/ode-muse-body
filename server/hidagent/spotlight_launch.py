"""Experimental, local-only Spotlight launch transaction.

Intermediate frames stay in memory. Neither a model nor Trace receives them.
The detector is bounded to measured iPad portrait and 1646x1080 landscape
geometry, not a general Spotlight recognizer. Ambiguity blocks image upload.
"""
import asyncio
import io
import os
import time

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageStat

from .hid_lease import check_hid, has_input_lease

_now = time.monotonic
_wall = time.time
_sleep = asyncio.sleep
CLOSE_TIMEOUT = 4.0
POLL_SECONDS = .1


def capsule_edge_score(frame):
    """Scalar only, including the evidence folder's top-300px privacy crop.

    At reference width 710, take the lower quartile of 25 horizontal strip
    luminance changes across the floating capsule's top border. Text/results
    below that border are irrelevant. No OCR, templates or image export.
    """
    with Image.open(io.BytesIO(frame)) as original:
        w, h = original.size
        if w < 100 or h * 710 / w < 140:
            raise ValueError('Insufficient capsule region')
        image = original.convert('L').resize((710, round(h * 710 / w)))
    changes = []
    for x in range(110, 601, 20):
        inside = ImageStat.Stat(image.crop((x, 76, x+12, 78))).mean[0]
        outside = ImageStat.Stat(image.crop((x, 72, x+12, 74))).mean[0]
        changes.append(inside - outside)
    return sorted(changes)[len(changes)//4]


@dataclass(frozen=True)
class LandscapeLimits:
    """Conservative half-minimum open evidence margins; see labelled holdout test.

    These are Spotlight-only limits, independent of Home/pointer calibration.
    Absolute appearance can veto closed, but cannot establish open by itself.
    """
    band: float = 9.0
    temporal_shape: float = 1.5
    shape: float = 1.5
    glyph: float = 28.0


LANDSCAPE_LIMITS = LandscapeLimits()


def _landscape_image(frame):
    with Image.open(io.BytesIO(frame)) as original:
        if original.width != 1646 or original.height < 220:
            raise ValueError('Insufficient landscape capsule region')
        return original.crop((0, 0, 1646, 220)).convert('L')


def _mean(image, box):
    return ImageStat.Stat(image.crop(box)).mean[0]


def _quartile(values):
    return sorted(values)[len(values) // 4]


def _landscape_edges(image):
    # Exclude the query, status bar and focus rings near the screen/window edge.
    top = [_mean(image, (x, 114, x+12, 117)) - _mean(image, (x, 120, x+12, 123))
           for x in range(450, 1190, 20)]
    bottom = [_mean(image, (x, 201, x+12, 203)) - _mean(image, (x, 205, x+12, 207))
              for x in range(460, 1180, 20)]
    sides = [_mean(image, (380, 145, 385, 175)) - _mean(image, (400, 145, 405, 175)),
             _mean(image, (1265, 145, 1270, 175)) - _mean(image, (1240, 145, 1245, 175))]
    return top, bottom, sides


def _shape(edges):
    top, bottom, sides = edges
    return max(_quartile([abs(v) for v in top]), _quartile([abs(v) for v in bottom]),
               min(abs(v) for v in sides))


def landscape_capsule_check(frame, before=None, limits=LANDSCAPE_LIMITS):
    """Three-way, local scalar decision on a native top-220 band.

    Primary evidence is change from the pre-Command-Space frame INSIDE the
    rounded capsule, plus changes across its boundary. Shape and the right
    search control are secondary vetoes. No background-specific templates,
    OCR, resizing, text extraction or pixels below y=219. A reference is an
    observation, not proof that it was closed: a stationary capsule must veto
    closed too. Unsupported/short crops are never runtime upload permission.
    """
    image = _landscape_image(frame)
    edges = _landscape_edges(image)
    shape = _shape(edges)
    top, bottom, sides = edges
    top_score = _quartile([abs(v) for v in top])
    bottom_score = _quartile([abs(v) for v in bottom])
    glyph = max(_mean(image, (1195, 151, 1205, 169)),
                _mean(image, (1205, 151, 1215, 169))) - _mean(image, (1170, 151, 1185, 169))
    # A boundary at both ends or at top+bottom is a plausible capsule even
    # without a visible mic/clear control. Never declare that shape closed.
    capsule_shape = max(min(top_score, bottom_score), min(top_score, *(abs(v) for v in sides)))
    plausible = glyph >= 20 or capsule_shape >= .25
    delta = temporal_shape = localized_delta = None
    open_margin = -1.0
    closed_margin = min(20-glyph, .25-capsule_shape)
    if before is not None:
        reference = _landscape_image(before)
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).rounded_rectangle((389, 117, 1257, 210), radius=38, fill=255)
        diff = ImageChops.difference(reference, image)
        delta = ImageStat.Stat(diff, mask).mean[0]
        # Uniform scene/exposure changes are not localized to the capsule.
        localized_delta = max(0, delta - _mean(diff, (450, 80, 1190, 110)))
        old_edges = _landscape_edges(reference)
        temporal_shape = _shape([[a-b for a, b in zip(new, old)]
                                 for new, old in zip(edges, old_edges)])
        open_margin = min(localized_delta/limits.band, temporal_shape/limits.temporal_shape,
                          shape/limits.shape, glyph/limits.glyph) - 1
        closed_margin = min(closed_margin, 3-localized_delta, .75-temporal_shape)
    state = ('open' if before is not None and open_margin >= 0 else
             'closed' if not plausible and closed_margin > 0 else 'unknown')
    return {'state': state, 'band_delta': None if delta is None else round(delta, 4),
            'localized_delta': None if localized_delta is None else round(localized_delta, 4),
            'temporal_shape': None if temporal_shape is None else round(temporal_shape, 4),
            'shape_score': round(shape, 4), 'glyph_contrast': round(glyph, 4),
            'edge_score': round(top_score, 4), 'capsule_shape': round(capsule_shape, 4),
            'closed_margin': round(closed_margin, 4), 'open_margin': round(open_margin, 4)}


def local_check(frame, before=None):
    """Only full measured frames can authorize model/trace upload."""
    try:
        with Image.open(io.BytesIO(frame)) as image:
            w, h = image.size
        if (w, h) == (1646, 1080):
            return landscape_capsule_check(frame, before=before)
        if abs(w / h - 710 / 1080) > .003:
            return {'state': 'unknown', 'reason': 'unsupported_geometry'}
        score = capsule_edge_score(frame)
        magnitude = abs(score)
        state = 'open' if magnitude >= 12 else 'closed' if magnitude <= 4 else 'unknown'
        return {'state': state, 'edge_score': round(score, 4),
                'closed_margin': round(4-magnitude, 4), 'open_margin': round(magnitude-12, 4)}
    except (OSError, ValueError, TypeError, ZeroDivisionError):
        return {'state': 'unknown', 'reason': 'invalid_frame'}


def query_band_changed(before, after):
    """Visual text-change witness only, not OCR or app/result identity proof.

    Tile means preserve small replaced selections. Changes to video, results,
    app icon, mic and focus rings cannot satisfy this query-band witness.
    """
    try:
        a, b = _landscape_image(before), _landscape_image(after)
        diff = ImageChops.difference(a, b)
        return any(_mean(diff, (x, y, x+24, y+20)) > 4
                   for x in range(465, 1100, 24) for y in range(137, 177, 20))
    except (OSError, ValueError, TypeError):
        return False


def _run_state(agent, app):
    state = getattr(agent, '_spotlight_launch', None)
    return state if state and state['steps'] is agent.steps and (app is None or state['app'] == app) else None


def eligible(agent, goal):
    # An in-flight privacy guard survives a flag change. New attempts default off.
    if _run_state(agent, goal['app']):
        return True
    if os.environ.get('APP_LAUNCH_SPOTLIGHT_FIRST', '0') != '1' or goal.get('text') is not None:
        return False
    from .shortcut_workflow import APP_ALIASES
    aliases = APP_ALIASES.get(goal['app'], ())
    if not aliases or not aliases[0] or not all(32 <= ord(c) <= 126 for c in aliases[0]):
        return False
    phone = getattr(agent, 'phone', None)
    target = phone.target_info() if phone is not None else None
    return bool(target and target.get('device') == 'ipad'
                and (target.get('orientation') == 'portrait'
                     or (target.get('orientation') == 'landscape'
                         and (target.get('width'), target.get('height')) == (1646, 1080)))
                and not any(s.get('workflow') == 'keyboard' for s in agent.steps))


def _binding(agent):
    target = agent.phone.target_info() or {}
    return tuple(target.get(k) for k in ('device', 'orientation', 'calibration_key', 'width', 'height'))


def _valid(agent, state):
    check_hid('spotlight_first')
    screen, phone = agent.screen, agent.phone
    try:
        with Image.open(io.BytesIO(screen.frame)) as image:
            size = image.size
        return (has_input_lease() and screen.live and 0 <= _wall()-screen.updated < 2
                and phone.calibrated and phone.device.connected
                and _binding(agent) == state['binding'] and phone.device.links == state['link']
                and size == state['binding'][3:5]
                and (size == (1646, 1080) or abs(size[0]/size[1] - 710/1080) <= .003))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _blank(state):
    out = io.BytesIO()
    Image.new('RGB', state['size'], 'black').save(out, format='PNG')
    return out.getvalue()


def receipt(agent, app):
    state = _run_state(agent, app)
    return state['receipt'] if state else None


def fail_action(agent, app, reason):
    state = _run_state(agent, app)
    state['status'] = 'failed'
    r = state['receipt']
    r['reason'] = reason
    r['stages_s']['total_s'] = round(_now()-state['started'], 4)
    agent.trace.write(kind='spotlight-launch', **r)
    # The outer Agent stores action _shot in its evidence cache. Even failure
    # screenshots must therefore be generated pixels, never the current panel.
    return [{'action': 'fail', 'reason': reason, 'workflow': 'keyboard',
             'phase': 'spotlight_failed', 'think_s': 0, '_shot': _blank(state),
             'launch_method': 'spotlight_first', 'launch_receipt': r,
             'never_retry_typing': r['never_retry_typing']}]


def safe_frame(agent, app, frame):
    """Recheck every model/trace entry, including ready-check retries."""
    state = _run_state(agent, app)
    return bool(state and _valid(agent, state) and frame == agent.screen.frame
                and local_check(frame, before=state.get('closed_reference'))['state'] == 'closed')


def _difference(before, after):
    def gray(frame):
        with Image.open(io.BytesIO(frame)) as image:
            return image.convert('L').resize((32, 64))
    return ImageStat.Stat(ImageChops.difference(gray(before), gray(after))).mean[0]


async def prepare(agent, goal):
    """Adapt the transaction's fallback to the existing icon/keyboard caller.

    That caller's literal fallback branch supports only Home icons. For a
    non-Home fallback, re-enter its generic keyboard classifier with a distinct
    truthy status, preserving its safe_frame checks. No fabricated history or
    caller edits. An open/unknown legacy frame is blocked on the next turn;
    it can never reach the old model-based Spotlight inspection.
    """
    result = await _prepare(agent, goal)
    state = _run_state(agent, goal['app'])
    if not state or isinstance(result, list) or result not in ('fallback', 'fallback_keyboard'):
        return result
    if state.get('delegating_legacy'):
        return 'fallback_keyboard'
    from .home_icons import locate_home_icon
    if result == 'fallback' and locate_home_icon(agent.screen.frame, goal['app'], agent.phone.target_info()):
        return result
    from .shortcut_workflow import next_shortcut_actions
    state['status'] = 'fallback_keyboard'
    state['delegating_legacy'] = True
    try:
        actions = await next_shortcut_actions(agent, goal)
    finally:
        state['delegating_legacy'] = False
    if not safe_frame(agent, goal['app'], agent.screen.frame):
        return fail_action(agent, goal['app'], 'Spotlight fallback frame changed; no legacy evidence')
    if state['receipt']['never_retry_typing'] and any(
            a.get('action') == 'type' or
            (a.get('action') == 'key' and a.get('combo') in ('cmd+space', 'cmd+a', 'enter'))
            for a in actions):
        return fail_action(agent, goal['app'], 'Spotlight query already attempted; no second query or submit')
    return [dict(a, launch_method='spotlight_first', launch_receipt=state['receipt']) for a in actions]


async def _prepare(agent, goal):
    """Return None for legacy, ready/fallback for safe continuation, or actions.

    A single transaction uses Phone's existing lease/liveness-checked key/type
    operations. It never yields a private action screenshot to the outer loop.
    Attempt state is written BEFORE sending, so lost receipts cannot replay it.
    """
    app = goal['app']
    state = _run_state(agent, app)
    if state:
        if state['status'] in ('ready', 'fallback', 'fallback_keyboard') and safe_frame(agent, app, agent.screen.frame):
            return state['status']
        return fail_action(agent, app, 'Spotlight launch receipt uncertain or private/stale frame; no replay')
    from .foreground_reuse import reuse_foreground
    reuse = await reuse_foreground(agent, app) if goal.get('text') is None else None
    if reuse is not None:
        return [{'action': 'done', 'workflow': 'keyboard', 'phase': 'foreground_reused',
                 '_verified_foreground_reuse': True, 'think_s': round(reuse['seconds'], 3),
                 'answer': app+' foreground rechecked locally against prior verified App pixels; no App relaunch.',
                 '_shot': agent.screen.frame}]
    if not eligible(agent, goal):
        return None
    from .shortcut_workflow import APP_ALIASES
    frame = agent.screen.frame
    try:
        with Image.open(io.BytesIO(frame)) as image:
            size = image.size
    except (OSError, ValueError, TypeError):
        size = (710, 1080)
    state = dict(steps=agent.steps, app=app, started=_now(), status='pending',
                 binding=_binding(agent), link=getattr(agent.phone.device, 'links', None),
                 size=size, before=frame, closed_reference=None, escaped=False,
                 receipt={'launch_method': 'spotlight_first', 'stages_s': {}, 'local_checks': [],
                          'fallback_reason': None, 'never_retry_typing': False})
    agent._spotlight_launch = state
    r = state['receipt']

    def sample(stage):
        check = local_check(agent.screen.frame, before=state.get('closed_reference'))
        r['local_checks'].append({'stage': stage, 'seconds': round(_now()-state['started'], 4), **check})
        return check

    async def send(stage, operation):
        if not _valid(agent, state):
            raise ValueError('precondition_changed')
        started = _now()
        try:
            await operation()
        finally:
            r['stages_s'][stage+'_s'] = round(_now()-started, 4)

    async def wait_for(stage, desired, changed=False, timeout=CLOSE_TIMEOUT, query_before=None):
        started, last_updated, consecutive = _now(), agent.screen.updated, 0
        last_frame = None
        while _now()-started < timeout:
            await _sleep(POLL_SECONDS)
            if not _valid(agent, state):
                return False
            if agent.screen.updated <= last_updated:
                continue
            last_updated = agent.screen.updated
            check = sample(stage)
            current = agent.screen.frame
            delta = _difference(state['before'], current) if changed else 0
            if changed:
                r['local_checks'][-1]['frame_delta'] = round(delta, 4)
            query_changed = query_before is None or query_band_changed(query_before, current)
            if query_before is not None:
                r['local_checks'][-1]['query_changed'] = query_changed
            ok = check['state'] == desired and (not changed or delta > 2) and query_changed
            stable = last_frame is None or _difference(last_frame, current) <= 2
            consecutive = consecutive+1 if ok and stable else 1 if ok else 0
            last_frame = current
            if consecutive >= 2:
                r['stages_s'][stage+'_s'] = round(_now()-started, 4)
                return True
        r['stages_s'][stage+'_s'] = round(_now()-started, 4)
        return False

    async def escape(reason, allow_fallback):
        r['fallback_reason'] = reason
        if not state['escaped']:
            state['escaped'] = True  # Set before dispatch, including uncertain ACK.
            try:
                await send('esc', lambda: agent.phone.key('esc'))
            except Exception:
                return fail_action(agent, app, 'Spotlight escape receipt uncertain; no frame uploaded')
        # Do not even judge pixels from the 0.25 s translucent closing ghost.
        await _sleep(.4)
        if await wait_for('esc_close_wait', 'closed', timeout=1):
            state['closed_reference'] = agent.screen.frame
            if allow_fallback:
                state['status'] = 'fallback'
                r['stages_s']['total_s'] = round(_now()-state['started'], 4)
                agent.trace.write(kind='spotlight-launch', **r)
                return 'fallback'
        return fail_action(agent, app, 'Spotlight not safely closed; no legacy input or upload')

    if not _valid(agent, state):
        return fail_action(agent, app, 'Spotlight preconditions failed; no input or model call')
    preflight = sample('preflight')['state']
    if preflight != 'closed':
        return await escape('spotlight_preflight_'+preflight, True)
    state['closed_reference'] = frame
    try:
        await send('open', lambda: agent.phone.key('cmd+space'))
    except Exception:
        return await escape('spotlight_open_receipt_uncertain', False)
    if not await wait_for('open_wait', 'open', timeout=1):
        return await escape('spotlight_open_unverified', True)
    # No selection, clearing, retries, pointer clicks, or model checks here.
    query_before = agent.screen.frame
    r['never_retry_typing'] = True
    # VoiceBridge derives delivery flags from Agent steps. Mark the attempt
    # before dispatch, without a screenshot or typed text, so cancellation and
    # lost ACKs cannot masquerade as no input. Only a real ACK confirms it.
    agent._record(action='type_uncertain', workflow='keyboard', phase='spotlight_query',
                  launch_method='spotlight_first', thought='Canonical app query attempted once; receipt pending.')
    input_step = agent.steps[-1]
    try:
        await send('type', lambda: agent.phone.type(APP_ALIASES[app][0]))
    except Exception:
        return fail_action(agent, app, 'Spotlight type receipt uncertain; never retype or submit')
    input_step.update(action='type', act_s=r['stages_s']['type_s'],
                      thought='Canonical app query acknowledged once; no screenshot retained.')
    agent.trace.write(kind='spotlight-type-ack', launch_method='spotlight_first',
                      seconds=r['stages_s']['type_s'])
    if size == (1646, 1080) and not await wait_for(
            'query_change_wait', 'open', timeout=1, query_before=query_before):
        return await escape('spotlight_query_change_unverified', False)
    if not _valid(agent, state) or sample('before_enter')['state'] != 'open':
        return await escape('spotlight_panel_lost_after_type', False)
    try:
        await send('enter', lambda: agent.phone.key('enter'))
    except Exception:
        return await escape('spotlight_enter_receipt_uncertain', True)
    if not await wait_for('close_wait', 'closed', changed=True):
        return await escape('spotlight_close_timeout', True)
    state['closed_reference'] = agent.screen.frame
    state['status'] = 'ready'
    r['stages_s']['total_s'] = round(_now()-state['started'], 4)
    agent.trace.write(kind='spotlight-launch', **r)
    return 'ready'
