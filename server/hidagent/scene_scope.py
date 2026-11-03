"""Bound stale-verdict checks to UI outside sampled motion.

Recent same-task waits, entry observations and post-Return settle frames can
establish motion. A boxed tap without history may sample the receiving stream
briefly, while retaining target, chrome and excessive-motion guards.
Missing evidence and keyboard input actions retain the original gate.
"""
import asyncio
import io
import math
import time

from PIL import Image, ImageChops, ImageStat


GRID = (32, 24)
MAX_MOTION = .65
MAX_AGE = 20
MARGIN = .02


def _image(frame):
    return Image.open(io.BytesIO(frame)).convert('L')


def _cells(image):
    # Eight pixels per cell retain local text/edge changes without letting the
    # unchanged remainder of a full image dilute a changed control.
    image = image.resize((GRID[0]*8, GRID[1]*8))
    return [image.crop((x*8, y*8, (x+1)*8, (y+1)*8))
            for y in range(GRID[1]) for x in range(GRID[0])]


def _delta(a, b):
    # Same grayscale mean cutoff as jev_agent.scene_changed; no model changes.
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0] > 2


def _box(action, box):
    if box is None:
        raw = action.get('_control_box') or action.get('_icon_hitbox')
        if isinstance(raw, (tuple, list)) and len(raw) == 4:
            raw = dict(zip(('left', 'top', 'right', 'bottom'), raw))
        box = raw
    if not isinstance(box, dict):
        return None
    coords = [box.get(k) for k in ('left', 'top', 'right', 'bottom')]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in coords):
        return None
    l, t, r, b = coords
    x, y = action.get('x'), action.get('y')
    if (not l < r or not t < b or type(x) not in (int, float)
            or type(y) not in (int, float) or not l <= x/1000 <= r or not t <= y/1000 <= b):
        return None
    return max(0, l-MARGIN), max(0, t-MARGIN), min(1, r+MARGIN), min(1, b+MARGIN)


def _settle_step(step, workflow):
    if 'act_s' not in step or step.get('workflow') != workflow:
        return False
    if workflow == 'app_search':
        return step.get('phase') in ('app_ready', 'scene_changed') and step.get('action') == 'wait'
    return (workflow == 'keyboard' and step.get('phase') == 'launch'
            and step.get('action') == 'key' and step.get('combo') == 'enter')


def _motion_record(agent, workflow):
    return getattr(agent, '_search_motion' if workflow == 'app_search' else '_launch_motion', None)


def start_launch_observation(agent, goal, *, workflow='keyboard', current_entry=False):
    """Sample already arriving frames during post-Return or search-wait settle.

    No input, extra wait round, screen source or model call is introduced. The
    short-lived sampler is bound to this task's steps and exact launch receipt.
    Search reuse may also sample while read-only foreground checks run, but
    those frames qualify only after an app_ready wait with no intervening input.
    """
    old=_motion_record(agent, workflow)
    if old and old.get('task'):old['task'].cancel()
    record={'steps':agent.steps,'index':len(agent.steps),'goal':dict(goal),'samples':[],
            'entry_started':time.time() if current_entry and workflow=='app_search' else None,
            'deadline':time.monotonic()+4}
    setattr(agent, '_search_motion' if workflow == 'app_search' else '_launch_motion', record)
    async def collect():
        while time.monotonic()<record['deadline'] and agent.steps is record['steps']:
            index=record['index']
            if len(agent.steps)>index+1:return
            since=record['entry_started']
            if len(agent.steps)==index+1:
                step=agent.steps[index]
                if not _settle_step(step, workflow):return
                if since is not None and step.get('phase')!='app_ready':return
                if since is None:since=step.get('t',float('inf'))
            if since is not None:
                at=getattr(agent.screen,'updated',0)
                frame=agent.screen.frame
                samples=record['samples']
                if (frame and type(at) in (int,float) and at>=since
                        and (not samples or at>samples[-1][0] and frame!=samples[-1][1])):
                    samples.append((at,frame))
                    del samples[:-8]
            await asyncio.sleep(.02)
    record['task']=asyncio.create_task(collect())


def finish_launch_observation(agent, goal, *, workflow='keyboard'):
    """Freeze pre-verdict history before a model can see the current frame."""
    record=_motion_record(agent, workflow)
    if not record:return
    record['task'].cancel()
    record['frozen']=bool(record['steps'] is agent.steps and record['goal']==goal
                          and len(agent.steps)==record['index']+1)
    if record['frozen']:
        step=agent.steps[record['index']]
        at=getattr(agent.screen,'updated',0)
        samples=record['samples']
        since=record.get('entry_started')
        if since is None:since=step.get('t',float('inf'))
        if (type(at) in (int,float) and at>=since and agent.screen.frame
                and (not samples or at>samples[-1][0] and agent.screen.frame!=samples[-1][1])):
            samples.append((at,agent.screen.frame))
            del samples[:-8]


def _launch_previous(agent, before, now, workflow):
    if workflow not in ('keyboard', 'app_search'):return None
    record=_motion_record(agent, workflow)
    if not record or not record.get('frozen') or record['steps'] is not agent.steps:return None
    index=record['index']
    if len(agent.steps)!=index+1:return None
    step=agent.steps[index]
    if not _settle_step(step, workflow):return None
    since=record.get('entry_started')
    if since is not None and step.get('phase')!='app_ready':return None
    if since is None:since=step.get('t',float('inf'))
    samples=record['samples']
    # The verdict frame must be the last settle observation. Never learn an
    # exclusion from any frame received during or after model evaluation.
    if len(samples)<3 or samples[-1][1]!=before:return None
    samples=samples[-3:]
    if (any(not 0<=now-at<=MAX_AGE or at<since for at,_ in samples)
            or not samples[0][0]<samples[1][0]<samples[2][0]
            or len({frame for _,frame in samples})!=3):return None
    return [frame for _,frame in samples[:2]]


def _previous(agent, before, now, workflow):
    steps = getattr(agent, 'steps', [])
    shots = getattr(agent, 'shots', {})
    launch=_launch_previous(agent,before,now,workflow)
    if launch is not None:return launch
    if len(steps) < 2:
        return None
    samples = []
    times = []
    # No crossing input, navigation, a workflow handoff, or an unrelated wait.
    for index in range(len(steps)-2, len(steps)):
        step = steps[index]
        if (step.get('action') != 'wait'
                or (workflow is not None and step.get('workflow') != workflow)
                or (step.get('workflow'), step.get('phase')) not in
                (('keyboard', 'changed_scene'), ('app_search', 'scene_changed'))):
            return None
        at = step.get('t')
        if type(at) not in (int, float) or not 0 <= now-at <= MAX_AGE:
            return None
        shot = shots.get(index)
        if not shot:
            return None
        samples.append(shot)
        times.append(at)
    if (times[0] >= times[1] or steps[-2].get('workflow') != steps[-1].get('workflow')
            or len(set([*samples, before])) != 3):
        return None
    return samples


def _receiving_motion(agent):
    """Read three fresh frames within 0.5 s without changing the screen source.

    Screen's receiver runs on its own thread. Poll its receipts at the same
    cadence as launch observation; never relabel the initial frame as fresh or
    save this post-verdict evidence for a later action.
    """
    screen = getattr(agent, 'screen', None)
    if screen is None:
        return None
    since = time.time()
    last = getattr(screen, 'updated', 0)
    if type(last) not in (int, float) or not math.isfinite(last):
        return None
    last = max(last, since)
    deadline = time.monotonic() + .5
    samples = []
    while time.monotonic() < deadline:
        at = getattr(screen, 'updated', 0)
        frame = screen.frame
        if type(at) in (int, float) and last < at <= time.time() and frame:
            samples.append(frame)
            last = at
            if len(samples) == 3:
                return samples if len(set(samples)) == 3 else None
        time.sleep(min(.02, max(0, deadline-time.monotonic())))
    return None


def verdict_scene_changed(agent, before, after, action, compare, *, box=None, foreground=False, workflow=None):
    """True means wait, False means the existing verdict remains usable.

    A point needs its actual verdict box plus margin. Keyboard and content
    verdicts do not inherit foreground-only exclusions. Every unmasked cell,
    including stable navigation chrome, must remain unchanged.
    """
    try:
        changed = compare(before, after)
        kind = action.get('action')
        region = _box(action, box) if kind == 'tap' else None
        if not ((kind == 'tap' and region) or (kind == 'done' and foreground)):
            return changed
        if action.get('_verified_icon_entry') and before == after:
            return False
        current = [_image(frame) for frame in (before, after)]
        if current[0].size != current[1].size:
            return True
        if region:
            l, t, r, b = region
            w, h = current[0].size
            crop = (math.floor(l*w), math.floor(t*h), math.ceil(r*w), math.ceil(b*h))
            if _delta(current[0].crop(crop), current[1].crop(crop)):
                return True
        samples = _previous(agent, before, time.time(), workflow)
        live = samples is None and region is not None
        if live:
            samples = _receiving_motion(agent)
        if samples is None:
            return changed
        images = [_image(frame) for frame in samples] + current
        if len({im.size for im in images}) != 1:
            return True
        if live and any(_delta(current[0].crop(crop), image.crop(crop))
                        for image in images[:3]):
            return True
        cells = [_cells(im) for im in images]
        # Always retain the outer chrome bands, even if they animated before
        # the verdict. Floating/interior stable chrome remains in unmasked cells.
        chrome_rows = GRID[1] // 8
        # Union the two sampled pairs so intermittent changes are covered too.
        # History ends at the verdict frame; fresh evidence is the first three
        # received frames. Never learn motion from the subsequent comparison.
        moving = {i for i in range(len(cells[0]))
                  if chrome_rows <= i // GRID[0] < GRID[1]-chrome_rows
                  and any(_delta(a[i], b[i]) for a, b in zip(cells[:2], cells[1:3]))}
        if not moving or len(moving)/len(cells[0]) > MAX_MOTION:
            return changed
        if region:
            for i in moving:
                x, y = i % GRID[0], i // GRID[0]
                if x/GRID[0] < r and (x+1)/GRID[0] > l and y/GRID[1] < b and (y+1)/GRID[1] > t:
                    return True
        if live:
            # Check every received frame, including transient chrome/overlays,
            # against the verdict. Only sampled off-target motion is exempt.
            return any(_delta(cells[3][i], frame[i])
                       for frame in cells[:3]+cells[4:]
                       for i in range(len(cells[0])) if i not in moving)
        return any(_delta(cells[2][i], cells[3][i]) for i in range(len(cells[0])) if i not in moving)
    except (OSError, ValueError, TypeError, KeyError):
        return True
