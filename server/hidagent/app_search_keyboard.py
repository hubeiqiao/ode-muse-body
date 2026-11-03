"""Opt-in, evidence-bounded public Search entry, before the pointer route.

Evidence: ipad-keyboard-route-20261030 and kbd-route-device-3-20261030.
The 20261031 program benchmark did not validate X Command-F and TikTok's
first three Tab stops missed Search. Both now require explicit app opt-in.
Neither profile implies focus, query delivery or successful search results.
"""
import os
import math

from .decision import DecisionUnavailable

# Ordered candidates. Add another shortcut/FKA candidate only with evidence;
# exhaustion hands control back to the existing pointer workflow.
ROUTES = {
    'X': (('keyboard_shortcut', 'cmd+f'),),
    'TikTok': (('fka_tab', 3),),
}


class KeyboardRouteFallback(Exception):
    pass


def route_state(agent, goal):
    state = getattr(agent, '_app_search_keyboard', None)
    if state and state['steps'] is agent.steps and state['goal'] == goal:
        return state
    return None


def configuration():
    enabled = os.environ.get('APP_SEARCH_KEYBOARD_ROUTES', '0') == '1'
    apps = os.environ.get('APP_SEARCH_KEYBOARD_APPS')
    allowed = {a.strip().casefold() for a in (apps or '').split(',')}
    return dict(enabled=enabled, apps=[app for app in ROUTES
                if enabled and app.casefold() in allowed])


def eligible(agent, goal):
    if goal['app'] not in configuration()['apps']:
        return False
    phone = getattr(agent, 'phone', None)
    if phone is None or (phone.target_info() or {}).get('device') != 'ipad':
        return False
    return True


def active(agent, goal):
    state = route_state(agent, goal)
    return bool(state and not state['fallback'])


def fallback(agent, goal, reason):
    state = route_state(agent, goal)
    if state is None:
        state = agent._app_search_keyboard = dict(
            steps=agent.steps, goal=dict(goal), route='keyboard_launch', parameter=None)
    state['fallback'] = True
    agent.trace.write(kind='app-search-route', app=goal['app'], route_used='pointer',
                      previous_route=state['route'], reason=reason, pointer_probe_sent=None)


def launch_binding(phone):
    target = phone.target_info() or {}
    return [target.get(k) for k in ('device', 'orientation', 'calibration_key', 'width', 'height')]


async def pointer_launch_actions(agent, goal, frame):
    """Recover only the observed absent Spotlight shortcut, never sent text."""
    if (frame != agent.screen.frame or not agent.screen.live or not agent.phone.calibrated
            or (agent.phone.target_info() or {}).get('device') != 'ipad'
            or any(s.get('action') == 'type' or s.get('phase') in ('launch', 'launch_tap', 'icon_launch', 'submit')
                   or (s.get('action') in ('key', 'tap') and 'act_s' not in s) for s in agent.steps)):
        raise DecisionUnavailable('Keyboard launch input or receipt uncertain; no pointer fallback or resend')
    binding = launch_binding(agent.phone)
    result = await agent.client.evaluate(
        {'policy': 'public_search_home_fallback', 'app': goal['app'],
         'purpose': 'verify Home and locate app after absent Spotlight shortcut'},
        {'ready': {'type': 'noul', 'instructions':
            f'Is this CURRENT unlocked iPad HOME ICON GRID with an actual installed {goal["app"]} app icon visible? '
            'Require Home itself, not an app timeline, Explore, app switcher, loading screen or content behind Spotlight. '
            'System Spotlight must be CLOSED. A shortcut ACK or requested app is not evidence.'},
         'blocked': {'type': 'noul', 'instructions':
            'Is ANY system Search, other window, lock screen, permission, login, modal or private editor covering Home?'},
         'target': {'type': 'box', 'instructions':
            f'Locate strictly INSIDE the actual installed {goal["app"]} app icon on the CURRENT HOME GRID or dock. '
            'Never a notification, widget, App Store suggestion, folder or another app.'}}, frame)
    agent.trace.write(frame, kind='app-search-launch-fallback-check', **result)
    answers = result.get('answers', {})
    target = answers.get('target', {})
    box = target.get('box', {})
    values = [box.get(k) for k in ('left', 'top', 'right', 'bottom')]
    if (not result.get('approved', True) or answers.get('ready', {}).get('noul', 0) < .99
            or answers.get('blocked', {}).get('noul', 1) > .01 or target.get('confidence', 0) < .9
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
            or not (values[0] < values[2] and values[1] < values[3])
            or frame != agent.screen.frame or binding != launch_binding(agent.phone)):
        raise DecisionUnavailable('Home app icon fallback not independently verified; no click or text sent')
    fallback(agent, goal, 'search_open_not_reached_before_text')
    return [dict(action='tap', workflow='keyboard', phase='icon_launch', route_used='pointer',
                 x=round((values[0]+values[2])*500), y=round((values[1]+values[3])*500), precise=True,
                 _pointer_launch_fallback=goal['app'], _pointer_launch_binding=binding,
                 _control_box=box, _shot=frame, think_s=result['seconds'])]


def annotate(agent, goal, actions):
    state = route_state(agent, goal)
    route = state['route'] if state and not state['fallback'] else 'pointer'
    for action in actions:
        existing_route=(action.get('execution_route') or
                        ('chrome_reddit_web' if action.get('workflow')=='reddit_web' else None))
        if action.get('workflow') == 'app_search' or existing_route:
            action['route_used'] = (existing_route if existing_route and existing_route != 'native_app'
                                    and not state else route)
            # This is a planned action. Pointer delivery is measured by _do,
            # including failed/uncertain probes, never inferred from the route.
            action['pointer_probe_sent'] = False if action['action'] in ('key', 'type', 'wait', 'done') else None
    return actions


async def entry_actions(agent, goal, history, judge, emit):
    state = route_state(agent, goal)
    if state and state.get('uncertain'):
        raise DecisionUnavailable('Keyboard search delivery uncertain; no fallback or resend')
    if state and state['fallback']:
        return None
    if any(s.get('phase') in ('focus', 'select', 'query', 'submit', 'clear_query') for s in history):
        return None
    if state is None:
        if not eligible(agent, goal):
            return None
        route, parameter = ROUTES[goal['app']][0]
        state = agent._app_search_keyboard = dict(
            steps=agent.steps, goal=dict(goal), route=route, parameter=parameter, fallback=False)
        agent.trace.write(kind='app-search-route', app=goal['app'], route_used=route,
                          pointer_probe_sent=False, reason='evidence_backed_entry_selected')
    entries = [s for s in history if s.get('_search_keyboard_entry') == goal['app']]
    if any('act_s' not in s for s in entries):
        raise DecisionUnavailable('Keyboard search receipt uncertain; no fallback or resend')
    questions = {'ready': {'type': 'noul', 'instructions':
        f'Is the SAME verified {goal["app"]} public foreground active and unobstructed, ready for public Search keyboard navigation? '
        'Reject another app, system Spotlight, private editor, modal or unknown window. A previous entry ACK alone is not proof.'}}
    if state['route'] == 'fka_tab' and entries:
        questions['search_control_focused'] = {'type': 'noul', 'instructions':
            f'Does the ACTUAL current FKA focus ring uniquely enclose {goal["app"]} public Search button/magnifier '
            'inside the actual app window? A group, static outline, text caret, account button, private composer or '
            'a requested/remembered Tab count is FALSE. Only an independently visible Search-button ring permits Space.'}
    answers = await judge(questions, 'verify keyboard public search entry')
    if answers.get('ready', {}).get('noul', 0) < .99:
        raise KeyboardRouteFallback('keyboard_entry_ownership_failed')
    if state['route'] == 'keyboard_shortcut':
        return emit(dict(action='key', combo=state['parameter'], _search_keyboard_entry=goal['app']), 'focus')
    if entries and answers.get('search_control_focused', {}).get('noul', 0) >= .99:
        return emit(dict(action='key', combo='space', _search_keyboard_entry=goal['app']), 'focus')
    if len(entries) >= state['parameter']:
        raise KeyboardRouteFallback('fka_search_ring_not_verified')
    return emit(dict(action='key', combo='tab', _search_keyboard_entry=goal['app'],
                     _search_tab_index=len(entries)), 'keyboard_tab')
