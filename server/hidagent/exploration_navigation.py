"""Task-local public URL route using the existing leased executor."""
import io
import logging
import math
from asyncio import sleep
from time import monotonic

from PIL import Image

from .decision import probability
from .exploration_round import RoundRejected, public_url
from .frame_privacy import check_cloud_frame, known_excluded_overlay
from .jev_agent import scene_changed


log = logging.getLogger(__name__)


def known_url_input(state):
    """Relax only the exact allowlisted address input in this task-local route."""
    if state.get('policy') != 'exploration_navigation':
        return False
    try:
        url = public_url(state.get('public_url'))
    except RoundRejected:
        return False
    expected = {'url_focus': {'action': 'key', 'combo': 'cmd+t'},
                'url_type': {'action': 'type', 'text': url},
                'url_submit': {'action': 'key', 'combo': 'enter'}}
    return state.get('phase') in expected and state.get('candidate_action') == expected[state['phase']]


class ExplorationNavigation:
    def __init__(self, agent, plan, fallback):
        self.agent, self.plan, self.fallback = agent, plan, fallback
        self.url = public_url(plan['public_search_url']) if plan.get('public_search_url') and not plan.get('app') else None
        self.started = False
        self.launching_safari = False
        self.wait_counts = {}
        self.last_url_observation = None
        self.pending_scroll = None
        self.scroll_count = 0
        self.alternate_scroll = False
        self.article_box = None

    async def check(self, phase, questions, candidate=None):
        a = self.agent
        if phase in ('url_focus', 'url_type', 'url_submit', 'url_ready'):
            # Pace before capturing each live frame, across phase transitions too.
            if self.last_url_observation is not None:
                await sleep(max(0, 2.0 - (monotonic() - self.last_url_observation)))
            self.last_url_observation = monotonic()
        frame = a.screen.frame
        check_cloud_frame(frame)
        result = await a.client.evaluate({'policy': 'exploration_navigation', 'phase': phase,
            'public_url': self.url, 'candidate_action': candidate}, questions, frame)
        answers = result.get('answers', {})
        log.info('Exploration judge phase=%s ready=%s blocked=%s approved=%s system_blocker=%s passage=%s', phase,
                 answers.get('ready', {}).get('noul'), answers.get('blocked', {}).get('noul'),
                 result.get('approved'), answers.get('system_blocker', {}).get('noul'), answers.get('passage', {}).get('noul'))
        a.trace.write(frame, kind='exploration-navigation-check', phase=phase, **result)
        if scene_changed(frame, a.screen.frame):
            return None
        return result

    @staticmethod
    def yes(result, key):
        value = (result or {}).get('answers', {}).get(key, {}).get('noul')
        return probability(value) and value >= .99

    @staticmethod
    def question(text):
        return {'type': 'noul', 'instructions': text}

    def emit(self, action, phase):
        return [{**action, 'workflow': 'bounded_scroll' if phase == 'passage_scroll' else 'exploration_url', 'phase': phase,
                 '_shot': self.agent.screen.frame}]

    def wait(self, phase):
        # Combine alternating Safari failure labels so they cannot evade the cap.
        key = 'safari' if phase.startswith('safari_') else phase
        self.wait_counts[key] = self.wait_counts.get(key, 0) + 1
        limit = 5 if phase in ('url_focus', 'url_type', 'url_submit', 'url_ready') else 3
        if self.wait_counts[key] >= limit:
            raise RoundRejected(f'Exploration {phase} did not become ready after {limit} observations')
        return self.emit({'action': 'wait', 'thought': 'Observe again: '+phase}, phase)

    async def launch_safari(self):
        from .shortcut_workflow import next_shortcut_actions
        actions = await next_shortcut_actions(self.agent, {'app': 'Safari', 'text': None})
        if any(s['action'] == 'done' for s in actions):
            self.launching_safari = False
            # A launcher receipt is not permission to type a URL. Reobserve
            # Safari foreground and address focus through the URL route.
            return self.wait('url_focus')
        if all(s['action'] in ('wait', 'replan') for s in actions):
            return self.wait('url_focus')
        return actions

    def article_frame(self, frame):
        # Compare article pixels only: toolbar focus/hover is not scroll progress.
        with Image.open(io.BytesIO(frame)) as picture:
            w, h = picture.size
            left, top, right, bottom = self.article_box
            out = io.BytesIO()
            picture.crop((int(left*w), int(top*h), int(right*w), int(bottom*h))).save(out, format='PNG')
            return out.getvalue()

    async def scroll_passage(self):
        a = self.agent
        escaped = any(s.get('phase') == 'passage_escape' and 'act_s' in s for s in a.steps)
        if not escaped:
            action = {'action': 'key', 'combo': 'esc'}
            safe = await self.check('passage_escape', {'ready': self.question(
                'Is Safari the active PUBLIC article window, where Escape only dismisses browser address/keyboard '
                'focus or harmless page selection? A blue Full Keyboard Access address focus ring is acceptable. '
                'Reject private content, login, permissions and system overlays.')}, action)
            if not self.yes(safe, 'ready') or (safe or {}).get('approved') is False:
                return self.wait('passage_escape')
            return self.emit(action, 'passage_escape')
        if not self.alternate_scroll:
            if self.scroll_count >= 3:
                raise RoundRejected('No supporting visible article passage after three page scrolls')
            surface = await self.check('passage_surface', {'surface': {'type': 'box', 'instructions':
                'Locate the readable scrollable ARTICLE BODY in the active public Safari window using its actual '
                'geometry. Exclude address bar, toolbar, sidebars, banners, Dock, overlays and other windows. '
                'Return full-image normalized fractions; low confidence if ambiguous.'}})
            answer = (surface or {}).get('answers', {}).get('surface', {})
            box = answer.get('box', {})
            values = [box.get(k) for k in ('left', 'top', 'right', 'bottom')]
            confidence = answer.get('confidence')
            if (not probability(confidence) or confidence < .9 or
                    not all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in values)):
                return self.wait('passage_surface')
            left, top, right, bottom = values
            if right <= left or bottom <= top or (right-left)*(bottom-top) < .08:
                return self.wait('passage_surface')
            self.article_box = tuple(values)
            action = {'action': 'scroll', 'x': round(1000*(left+right)/2),
                      'y': round(1000*(top+bottom)/2), 'lines': -6}
            condition = ('Is the proposed pointer location clearly inside the active public Safari ARTICLE BODY, '
                         'so a pointer wheel scroll moves article content? Address-bar keyboard focus is acceptable '
                         'for POINTER scrolling after Escape. Reject toolbar, banners, other panes, overlays and private content.')
        else:
            action = {'action': 'key', 'combo': 'space'}
            condition = ('Is Safari the active public article reading window with keyboard focus off the address bar '
                         'and page content focused so Space ONLY scrolls down? Reject any Full Keyboard Access focus '
                         'ring on toolbar/controls, editable fields, buttons, links, dialogs or uncertain keyboard focus.')
        safe = await self.check('passage_scroll', {'ready': self.question(condition)}, action)
        if not self.yes(safe, 'ready') or (safe or {}).get('approved') is False:
            if self.alternate_scroll:
                raise RoundRejected('Article did not move after pointer scroll; keyboard alternate has unsafe focus')
            return self.wait('passage_scroll')
        self.pending_scroll = (self.article_frame(a.screen.frame), self.alternate_scroll)
        if not self.alternate_scroll:
            self.scroll_count += 1
        return self.emit(action, 'passage_scroll')

    async def next(self):
        a = self.agent
        if self.pending_scroll is not None:
            # The executor has delivered one scroll and observed; allow AirPlay
            # to catch up before deciding that a known receipt had no effect.
            await sleep(1.0)
            before, alternate = self.pending_scroll
            self.pending_scroll = None
            moved = scene_changed(before, self.article_frame(a.screen.frame))
            a.trace.write(a.screen.frame, kind='exploration-scroll-result', moved=moved,
                          method='keyboard' if alternate else 'pointer')
            if not moved and alternate:
                raise RoundRejected('Article did not move after pointer and keyboard scroll methods')
            self.alternate_scroll = not moved
            if not moved:
                return await self.scroll_passage()
        if not self.started:
            # Recognised notification chrome can be dismissed without uploading
            # an excluded frame. Other privacy exclusions remain fail-closed.
            excluded = known_excluded_overlay(a.screen.frame)
            if excluded == 'notification_center_known_layout':
                return self.emit({'action': 'key', 'combo': 'esc'}, 'dismiss_overlay')
            result = await self.check('start', {
                'overlay': self.question('Is a dismissible SYSTEM overlay (Control Center, Screen Mirroring panel, '
                    'system Spotlight/Search, or notification center/banner) covering this unlocked device? '
                    'Do not read notification text. Exclude lock/passcode, login, permissions, app dialogs and account editors.'),
                'clear': self.question('Is this unlocked device free of system overlays, lock/passcode, login, '
                    'permissions and blocking dialogs, so public read-only navigation may begin?')})
            if self.yes(result, 'overlay'):
                return self.emit({'action': 'key', 'combo': 'esc'}, 'dismiss_overlay')
            if not self.yes(result, 'clear'):
                return self.wait('system_overlay_clear')
            self.started = True
        if not self.url:
            return await self.fallback()
        if self.launching_safari:
            # Spotlight is expected during launch; let its existing privacy,
            # layout and per-action checks govern until app-ready is proven.
            return await self.launch_safari()
        phases = {s.get('phase') for s in a.steps
                  if s.get('workflow') == 'exploration_url' and s.get('action') in ('key', 'type') and 'act_s' in s}
        if 'url_focus' not in phases:
            # A fresh tab starts with an empty focused address field; cmd+L can
            # leave focus held by page content in a reused tab (ipad_demo_flows).
            action, phase = {'action': 'key', 'combo': 'cmd+t'}, 'url_focus'
            condition = ('Is Safari the ACTIVE FOREMOST app/window, ready for the harmless cmd+T new-tab '
                         'shortcut? Use actual window geometry. Page content and fundraising/cookie banners '
                         'do not prevent focusing its address bar.')
        elif 'url_type' not in phases:
            action, phase = {'action': 'type', 'text': self.url}, 'url_type'
            condition = ('Is the actual Safari address field visibly EDITABLE and focused, with its old address fully selected '
                         'or the field empty, so typing the supplied public URL replaces the entire address? '
                         'Require caret/selection/edit chrome; a shortcut receipt or Favorites alone is not focus proof.')
        elif 'url_submit' not in phases:
            action, phase = {'action': 'key', 'combo': 'enter'}, 'url_submit'
            condition = ('Is the actual Safari editable address field focused and ready for Enter after we typed public_url? '
                         'Visible matching URL text supports readiness, but clipped or uncertain exact text alone '
                         'must not block submitting this public URL. Reject an unrelated field or foreground app.')
        else:
            action, phase = {'action': 'done'}, 'url_ready'
            condition = ('Is a readable PUBLIC Safari article relevant to '+self.plan['topic']+' actually loaded, '
                         'with visible title and factual passage and browser source chrome? Reject loading, errors, '
                         'Favorites, address editing, search suggestions, blocking modals and private content. '
                         'A dismissible fundraising/cookie banner is acceptable if the title, factual passage and '
                         'actual source chrome remain visible. Do not click Donate. A URL receipt is not proof.')
        questions = {
            'ready': self.question(condition),
            'system_blocker': self.question('Is an explicit SYSTEM overlay, login dialog or permission modal '
                'covering Safari and preventing this exact navigation action? Answer yes only for visible system '
                'overlay/login/permission UI. Ordinary page content, fundraising/cookie banners and search '
                'suggestions under the address bar do not count.')}
        if phase == 'url_ready':
            questions['passage'] = self.question(
                'Is a readable factual passage visible on the proposed public article, supporting a surprising discovery? '
                'ANY fact in the same article/topic family is acceptable; do not insist on the pre-planned angle '+
                self.plan['topic']+'. Require actual visible text, not prior knowledge, the proposal, a heading alone '
                'or hidden content. Reusing an article or duplicate discovery is NOT a reason to scroll: accept another '
                'visible fact, or leave topic diversity to the next round. This is passage-seeking only; final source verification is separate.')
        if phase == 'url_focus':
            questions['safari'] = self.question('Is Safari the ACTIVE FOREMOST app/window? Use actual window geometry.')
        result = await self.check(phase, questions, action)
        answers = (result or {}).get('answers', {})
        ready = answers.get('ready', {}).get('noul')
        blocker = answers.get('system_blocker', {}).get('noul')
        if phase == 'url_focus' and result is not None:
            safari = answers.get('safari', {}).get('noul')
            if (probability(safari) and safari <= .2 and
                    probability(blocker) and blocker < .8):
                # Rejection here applies to cmd+T in the wrong foreground app,
                # not to launching Safari. The existing launcher independently
                # checks layout, Spotlight target and each launch action.
                self.launching_safari = True
                return await self.launch_safari()
            if not probability(safari) or safari < .8:
                return self.wait(phase)
        # Navigation readiness is not source evidence; the separate reader and
        # independent pixel verifier retain their strict .99/.01 evidence gate.
        if ((result or {}).get('approved') is False or
                (probability(blocker) and blocker >= .8)):
            return self.wait(phase)
        if probability(ready) and ready >= .8:
            passage = answers.get('passage', {}).get('noul')
            if phase == 'url_ready' and not (probability(passage) and passage >= .8):
                return await self.scroll_passage()
            return self.emit(action, phase)
        return self.wait(phase)
