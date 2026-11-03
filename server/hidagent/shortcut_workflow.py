"""Bounded keyboard routes, with image checks before mutations and completion.

Only explicit app-open and new-Notes/literal goals use this route. General goals
and creative writing stay with the planner. All input still goes through Phone.
"""
import re
import os
import asyncio
import time
import io
from PIL import Image
from .app_cards import load_app_card
from .decision import DecisionUnavailable, probability
from .jev_agent import scene_changed
from .ui_guard import control_region_matches
from .scene_scope import verdict_scene_changed, start_launch_observation, finish_launch_observation

# Canonical ASCII Spotlight queries avoid requiring Universal Clipboard merely
# to open an app. Aliases also describe actual localized app labels for vision.
APP_ALIASES = {
    'Notes': ('Notes', '备忘录', '備忘錄'),
    'Calculator': ('Calculator', '计算器', '計算機'),
    'Settings': ('Settings', '设置', '設定', '設置'),
    'Safari': ('Safari', '浏览器', '瀏覽器'),
    'Maps': ('Maps', 'Apple Maps', '地图', '地圖', '苹果地图', '蘋果地圖'),
    'Weather': ('Weather', '天气', '天氣'),
    'Joe Speaking': ('Joe Speaking', 'JoeSpeaking'),
    'X': ('X', 'Twitter', '推特'),
    'Instagram': ('Instagram',),
    'TikTok': ('TikTok', 'Tik Tok'),
    'Douyin': ('Douyin', '抖音'),
    'Bilibili': ('Bilibili', '哔哩哔哩', 'B站'),
    'LinkedIn': ('LinkedIn', '领英'),
    'Reddit': ('Reddit',),
    'YouTube': ('YouTube',),
    'Threads': ('Threads',),
    'Google Maps': ('Google Maps', '谷歌地图'),
    'Discord': ('Discord',),
    'Muse': ('Muse',),
    'App Store': ('App Store',),
    'Chrome': ('Chrome', 'Google Chrome'),
    'Grok Bot': ('Grok Bot', 'Grok'),
    'ChatGPT': ('ChatGPT', 'Chat GPT'),
    'rednote': ('rednote', 'red note', 'XiaoHongShu', 'xiao hong shu', '小红书', '小紅書'),
}
APPS = tuple(APP_ALIASES)

# HID typing is ASCII only. These are queries, not localized display labels.
# A later candidate requires a fresh explicit no-installed-app observation.
SPOTLIGHT_QUERIES = {'Douyin': ('douyin', 'dou yin'), 'Bilibili': ('bilibili',)}


def spotlight_queries(app):
    return SPOTLIGHT_QUERIES.get(app, (app,))


def no_installed_app_question():
    return {'type': 'noul', 'instructions':
        'Does this CURRENT system Spotlight frame explicitly show NO installed APP row '
        'matching the requested app? Inspect the actual result section headings and icons. '
        'Siri Knowledge, Websites, documents, files, App Store download suggestions and '
        'Search in App are NOT installed apps. A matching app icon with Open qualifies '
        'as an installed app, so answer NO in that case. Unclear/cropped results mean NO. '
        'This absence check overrides a guessed positive based only on query text.'}


def installed_app_question(app, labels):
    return {'type': 'noul', 'instructions':
        f'On this CURRENT Spotlight screen, are BOTH the query {app!r} and the FIRST '
        f'installed APP result visibly present? Its visible app label can be ANY of {labels!r}, '
        'because iOS app labels are localized. The query may be at TOP or BOTTOM, or inside the centered floating iPad system Search panel. '
        'Check visible app identity and icon, not keyboard behavior or selection highlighting. '
        'A top capsule with app name followed by Open is valid with a matching app icon. '
        'Web pages, documents, Search in App actions, and unrelated local/staging variants do NOT qualify.'}


def first_spotlight_result_question(labels):
    return {'type': 'noul', 'instructions':
        f'Is the CURRENT Spotlight TOP HIT / very FIRST selectable result the installed app labelled one of {labels!r}, '
        'so a SINGLE Return will launch that app? Inspect the first row above ALL other results, its icon and section. '
        'An installed app lower in Apps does NOT qualify when Chrome/browser suggestions, Siri Knowledge, '
        'websites, files, or Search in App occur first. Missing/uncertain selection means NO. '
        'App presence elsewhere and the requested query alone are not proof of the Return target.'}


def foremost_app_question(app):
    return {'type': 'noul', 'instructions':
        f'Is {app!r} the ACTIVE FOREMOST app/window usable for the next keyboard/content operation, '
        'with NO different app window overlapping its main content and NO system Spotlight overlay? '
        'Do not count Safari/Notes content visible behind a floating Calculator or another app. '
        'A floating target window itself can qualify when nothing else covers it.'}


def shortcut_goal(task):
    # Only a complete app-open request is normalized. Never eat a trailing
    # instruction (post/delete/write), and never transliterate dictated text.
    aliases = {re.sub(r'\s+', '', alias).casefold(): name
               for name, names in APP_ALIASES.items() for alias in names}
    english = re.fullmatch(r'\s*(?:open|launch)\s+(.+?)(?:\s+app)?[.!。！]?\s*', task, re.I)
    chinese = re.fullmatch(r'\s*(?:请你|请|麻烦你|麻烦|你|帮我|你帮我)?\s*(?:打开|启动|进入)(?:一下)?\s*(.+?)(?:这个|这个应用|应用|APP)?[。！!]?\s*', task, re.I)
    match = english or chinese
    if match:
        name = aliases.get(re.sub(r'\s+', '', match[1]).casefold())
        if name:
            return {'app': name, 'text': None}
    note = re.fullmatch(r'\s*open notes and (?:create a new note with text|write|type) "([^"\r\n]{1,300})"[.!]?\s*', task, re.I)
    if note and all(32 <= ord(c) <= 126 for c in note[1]):
        return {'app': 'Notes', 'text': note[1]}
    return None


async def launch_layout_guard(agent, app):
    """Bind a launch to its calibrated geometry and consume Home recovery once.

    This uses the existing calibration-independent recovery, never a pointer or
    normal Home key on the new uncalibrated layout. State belongs to this task's
    steps list, shared by the Reddit -> Chrome handoff.
    """
    phone = getattr(agent, 'phone', None)
    target = phone.target_info() if phone is not None else None
    if not target or target.get('device') != 'ipad':
        return None
    with Image.open(io.BytesIO(agent.screen.frame)) as im:
        binding = (target.get('calibration_key'), target.get('width'), target.get('height'), im.size)
    state = getattr(agent, '_launch_layout_guard', None)
    if not state or state['steps'] is not agent.steps:
        state = dict(steps=agent.steps, binding=binding, recovered=False)
        agent._launch_layout_guard = state
    changed = binding != state['binding'] and getattr(phone, 'calibrated', True) is False
    if not changed and not state['recovered']:
        return None
    reason = 'external_display_layout: launch changed to uncalibrated geometry; native launch stopped.'
    first = not state['recovered']
    if first:
        state['recovered'] = True  # consume before awaiting, including a lost ACK
        state['recovery_app'] = app
        try:
            recovery=await phone.recover_home()
            # Legacy/unknown Home receipts permit the original later live-frame
            # checks. An explicit negative witness must not be treated as ACK
            # success merely because the method returned without an exception.
            if isinstance(recovery,dict) and recovery.get('ok') is False:
                state['recovery_failed']=True
        except asyncio.CancelledError:
            raise
        except Exception:
            state['recovery_failed'] = True
        agent.trace.write(kind='launch-layout-recovery', app=app, reason='external_display_layout',
                          before=state['binding'], after=binding, recovery_attempts=1,
                          recovery_failed=state.get('recovery_failed', False))
    # Reddit may continue only on a later, live, calibrated frame in the
    # original geometry. No native launch is retried after Home recovery.
    if (app in ('Reddit', 'Chrome') and state.get('recovery_app') == 'Reddit'
            and not state.get('recovery_failed') and not first
            and binding == state['binding'] and getattr(phone, 'calibrated', False)
            and getattr(agent.screen, 'live', False)):
        return None
    return [dict(action='wait' if app == 'Reddit' and first and not state.get('recovery_failed') else 'fail',
                 workflow='keyboard', phase='external_display_layout', reason=reason,
                 **({'execution_route': 'chrome_reddit_web'} if app == 'Reddit' else {}),
                 think_s=0)]


class SearchOpenNotReached(DecisionUnavailable):
    """Fresh non-Spotlight observation after bounded open-only launch waits."""
    def __init__(self, frame):
        super().__init__('Keyboard route did not reach search_open; stopped without repeating input')
        self.frame = frame


class LaunchBlocked(DecisionUnavailable):
    """A current blocked launch frame, distinct from uncertain delivery."""
    def __init__(self, frame, result):
        super().__init__('Keyboard route is blocked; no input sent', result)
        self.frame = frame


class _LaunchLayoutChanged(Exception):
    def __init__(self, actions):
        self.actions = actions


async def next_shortcut_actions(agent, goal):
    guard = await launch_layout_guard(agent, goal['app'])
    if guard:
        return guard
    target = agent.phone.target_info() if getattr(agent, 'phone', None) is not None else None
    if target and target.get('device') == 'ipad' and goal.get('text') is None:
        if goal['app'] == 'Reddit':
            from .reddit_web import next_reddit_web_actions
            return await next_reddit_web_actions(agent, {'app': 'Reddit', 'query': None}, open_only=True)
    try:
        actions = await _next_shortcut_actions(agent, goal)
        return await launch_layout_guard(agent, goal['app']) or actions
    except _LaunchLayoutChanged as changed:
        return changed.actions
    except DecisionUnavailable:
        guard = await launch_layout_guard(agent, goal['app'])
        if guard:
            return guard
        raise


async def _next_shortcut_actions(agent, goal):
    from .spotlight_launch import prepare, safe_frame, fail_action, receipt
    spotlight = await prepare(agent, goal)
    guard = await launch_layout_guard(agent, goal['app'])
    if guard:
        return guard
    if isinstance(spotlight, list):
        return spotlight
    finish_launch_observation(agent, goal)
    frame = agent.screen.frame
    app, text = goal['app'], goal['text']
    target = agent.phone.target_info() if hasattr(agent, 'phone') and agent.phone is not None else None
    ipad = bool(target and target.get('device')=='ipad')
    labels = APP_ALIASES.get(app, (app,))
    queries = spotlight_queries(app)
    history = [s for s in agent.steps if s.get('workflow') == 'keyboard']
    phases = [s.get('phase') for s in history]
    seconds = 0.0

    async def judge(questions, purpose, view=None, policy=None):
        nonlocal seconds
        view = frame if view is None else view
        if spotlight and not safe_frame(agent, app, frame):
            raise DecisionUnavailable('Spotlight privacy gate refused model frame; no upload')
        result = await agent.client.evaluate({'target_app': app, 'purpose': purpose, 'device_family': (target or {}).get('device'), 'app_card':load_app_card(app), **({'policy':policy} if policy else {})}, questions, view)
        guard = await launch_layout_guard(agent, app)
        if guard:
            raise _LaunchLayoutChanged(guard)
        if spotlight and not safe_frame(agent, app, frame):
            raise DecisionUnavailable('Spotlight privacy gate changed during app-ready check; no trace frame')
        agent.trace.write(view, kind='shortcut-check', purpose=purpose, **result)
        seconds += result['seconds']
        return result

    def emit(action, phase):
        if spotlight:
            if not safe_frame(agent, app, frame):
                return fail_action(agent, app, 'Spotlight privacy gate refused action evidence')
            action = {**action, 'launch_method': 'spotlight_first', 'launch_receipt': receipt(agent, app)}
        if action.get('_local_network_denial') or action.get('_tracking_denial') or action.get('_intro_notice_dismissal'):
            if not control_region_matches(frame,agent.screen.frame,action['_control_box'],allow_text_hover=True):return wait('permission_control_changed')
        elif not (action['action']=='done' and (action.get('_verified_icon_entry')
                  or action.get('_verified_foreground_entry')
                  or (action.get('_verified_open_entry') and frame == agent.screen.frame))) and verdict_scene_changed(
                agent, frame, agent.screen.frame, action, scene_changed,
                foreground=text is None, workflow='keyboard'):
            return wait('changed_scene')
        if phase=='launch' and action.get('action')=='key' and action.get('combo')=='enter' and text is None:
            start_launch_observation(agent, goal)
        return [{**action, 'workflow': 'keyboard', 'phase': phase,
                 'thought': 'Keyboard route: '+phase+'; current screen checked.',
                 'think_s': round(seconds, 3), '_shot': frame}]

    def wait(reason):
        if spotlight and not safe_frame(agent, app, frame):
            return fail_action(agent, app, 'Spotlight privacy gate refused wait evidence')
        if phases.count(reason) >= (6 if reason in ('app_loading','changed_scene') else 3):
            raise DecisionUnavailable('Keyboard route did not reach '+reason+'; stopped without repeating input')
        return [{'action': 'wait', 'workflow': 'keyboard', 'phase': reason,
                 'thought': 'Wait for verified screen: '+reason,
                 'think_s': round(seconds, 3), '_shot': frame}]

    def approved(result):
        return result.get('approved', result['answers']['ready']['noul'] >= .9)

    async def deny_local_network():
        if 'deny_local_network' in phases:return None
        present=await judge({'ready':{'type':'noul','instructions':'Is there an ACTUAL native Apple local-network permission SHEET asking to find/connect to devices on local networks, with BOTH explicit Do Not Allow / Don’t Allow / 不允许 and Allow buttons? A normal app Search page, voice Search, history list or absence of a permission sheet is FALSE. Do not infer from the requested recovery.'}},'check actual local network sheet before locating any button',policy='system_local_network_present')
        if present['answers']['ready']['noul']<.99:return None
        result=await judge({
            'ready':{'type':'noul','instructions':'Is this EXACTLY a native iPad/iPhone local-network permission sheet asking an app to find/connect to devices on local networks, with explicit Don’t Allow / Do Not Allow / 不允许 and Allow buttons? Require the actual system sheet. Exclude login, password, payment, terms, deletion, app content or any other modal.'},
            'target':{'type':'box','instructions':'Locate ONLY the explicit Don’t Allow / Do Not Allow / 不允许 button of that native LOCAL NETWORK permission sheet. Never Allow or another control.'}},
            'deny unnecessary local network permission',policy='system_local_network_denial')
        a=result['answers'];b=a['target']
        if not result.get('approved',False) or a['ready']['noul']<.99 or b.get('confidence',0)<.9:return None
        box=b['box']
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
                     'y':round((box['top']+box['bottom'])*500),'precise':True,
                     '_local_network_denial':True,'_control_box':box},'deny_local_network')

    async def deny_app_tracking():
        if 'deny_app_tracking' in phases:return None
        present=await judge({'ready':{'type':'noul','instructions':'Is this EXACTLY a native Apple App Tracking Transparency sheet asking to track activity across OTHER COMPANIES apps and websites, with BOTH Ask App Not to Track and Allow? A local-network request, account chooser, login, payment, app content or absent sheet is FALSE.'}},'check actual native tracking sheet',policy='system_tracking_present')
        if present['answers']['ready']['noul']<.99:return None
        result=await judge({
            'ready':{'type':'noul','instructions':'Is the native App Tracking Transparency sheet actually visible, and is the only proposed action refusing cross-company tracking? Never Allow, account selection, app content, payment or another permission.'},
            'target':{'type':'box','instructions':'Locate ONLY the actual Ask App Not to Track button on the native Apple cross-company tracking sheet. Never Allow or another control.'}},
            'deny optional cross-app tracking',policy='system_tracking_denial')
        a=result['answers'];b=a['target']
        if not result.get('approved',False) or a['ready']['noul']<.99 or b.get('confidence',0)<.9:return None
        box=b['box']
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
            'y':round((box['top']+box['bottom'])*500),'precise':True,
            '_tracking_denial':True,'_control_box':box},'deny_app_tracking')

    async def dismiss_bilibili_intro():
        if app!='Bilibili' or 'dismiss_bilibili_youth_intro' in phases:return None
        present=await judge({'ready':{'type':'noul','instructions':'Is this ONLY Bilibili optional first-use Youth Mode INTRODUCTION with an umbrella illustration, explanatory text saying guardians can choose/setup a guardian password, and an explicit Dont remind again button? It must NOT be an already enabled youth restriction, age verification, password/PIN gate, legal agreement or parental-control setting.'}},'check optional Bilibili feature introduction',policy='bilibili_intro_present')
        if present['answers']['ready']['noul']<.99:return None
        result=await judge({'ready':{'type':'noul','instructions':'Is the ONLY action dismissing the optional Bilibili first-use Youth Mode information with Dont remind again, without entering Youth Mode, disabling an existing restriction, agreeing to terms, entering age or changing a password?'},
            'target':{'type':'box','instructions':'Locate ONLY Dont remind again on the optional Bilibili Youth Mode introduction. Never Enter Youth Mode, password, age verification, or any active restriction control.'}},'dismiss harmless Bilibili feature notice',policy='bilibili_intro_dismissal')
        a=result['answers'];b=a['target']
        if not result.get('approved',False) or a['ready']['noul']<.99 or b.get('confidence',0)<.9:return None
        box=b['box']
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
            'y':round((box['top']+box['bottom'])*500),'precise':True,
            '_intro_notice_dismissal':True,'_control_box':box},'dismiss_bilibili_youth_intro')

    async def dismiss_douyin_intro():
        if app!='Douyin' or 'dismiss_douyin_youth_intro' in phases:return None
        present=await judge({'ready':{'type':'noul','instructions':'Is this ONLY the optional Douyin first-use Youth Mode INTRODUCTION (未成年人模式), showing learning/time/guardian/content feature icons, an invitation to ENABLE the mode, and a separate top-right X Close? Require this actual information card. Reject an already enabled restriction, age check, password/PIN gate, legal agreement, login or permission.'}},'check optional Douyin feature introduction',policy='douyin_intro_present')
        if present['answers']['ready']['noul']<.99:return None
        result=await judge({'ready':{'type':'noul','instructions':'Is the ONLY proposed action closing this optional Douyin Youth Mode INTRODUCTION using its separate top-right X Close, without enabling/disabling any mode or restriction, changing reminders, entering age/password, accepting terms, or granting permission?'},
            'target':{'type':'box','instructions':'Locate ONLY the top-right X Close of the optional Douyin Youth Mode INTRODUCTION card. Never Enable Youth Mode, Dont remind again, age/password/restriction controls, or any permission button.'}},'close optional Douyin feature introduction',policy='douyin_intro_dismissal')
        a=result['answers'];b=a['target']
        if not result.get('approved',False) or a['ready']['noul']<.99 or b.get('confidence',0)<.9:return None
        box=b['box']
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
            'y':round((box['top']+box['bottom'])*500),'precise':True,
            '_intro_notice_dismissal':True,'_control_box':box},'dismiss_douyin_youth_intro')

    async def dismiss_launch_notice():
        if text is not None:
            return None
        result = await judge({
            'ready': {'type': 'noul', 'instructions':
                f'Is the ACTIVE overlay a harmless optional feature introduction or promotional sheet belonging to {app}, '
                'with a separate close X or cancellable Escape? Require actual overlay pixels, not a dialog depicted in content. '
                'Reject system permissions, tracking, login, credentials, account selection, payment confirmation, terms, age/password gates or deletion. '
                'A trial advertisement may ONLY be cancelled, never Continue, Subscribe, Start trial or consent.'},
            'notice': {'type': 'choice', 'instructions': 'Classify only the actual topmost overlay.', 'criteria': {
                'whats_new': 'Optional app-owned What is New / new features introduction with an explicit separate X Close.',
                'promo': 'Optional app-owned promotion or trial advertisement sheet that can be cancelled with Escape without accepting anything.',
                'other': 'Any other, unknown, system permission, consent, login, account or payment dialog, or no actual overlay.'}},
            'target': {'type': 'box', 'instructions':
                'For What is New locate ONLY its separate X Close. Never Continue, trial, permission, payment or app content. '
                'For a promo or absent Close return confidence zero; Escape is used only after independent harmless-sheet classification.'}},
            'identify harmless launch overlay for one cancellation', policy='launch_benign_modal')
        a = result['answers']
        notice = a.get('notice', {})
        kind = notice.get('choice')
        phase = 'dismiss_launch_'+str(kind)
        if (not result.get('approved', True) or a.get('ready', {}).get('noul', 0) < .99
                or notice.get('confidence', 0) < .95 or kind not in ('whats_new', 'promo') or phase in phases):
            return None
        if kind == 'promo':
            return emit({'action': 'key', 'combo': 'esc'}, phase)
        close = a.get('target', {})
        if close.get('confidence', 0) < .9:
            return None
        box = close['box']
        return emit({'action': 'tap', 'x': round((box['left']+box['right'])*500),
                     'y': round((box['top']+box['bottom'])*500), 'precise': True,
                     '_intro_notice_dismissal': True, '_control_box': box}, phase)

    async def dismiss_known_launch_modal(*, benign_notice=True):
        helpers = (deny_local_network, deny_app_tracking, dismiss_bilibili_intro, dismiss_douyin_intro)
        for helper in helpers + ((dismiss_launch_notice,) if benign_notice else ()):
            action = await helper()
            if action:
                return action
        return None

    async def finish_open(action, *, recheck_changed=True):
        nonlocal frame, seconds
        # A fresh positive foreground verdict has the same read-only completion
        # scope as verified icon entry. Autoplay does not revoke app identity;
        # subsequent input/navigation must independently verify its own target.
        if (not ipad or action.get('_verified_icon_entry')
                or action.get('_verified_foreground_entry') or not recheck_changed):
            return emit(action, 'verified')
        if verdict_scene_changed(agent, frame, agent.screen.frame, action, scene_changed,
                                 foreground=True, workflow='keyboard'):
            # Dynamic app content can invalidate a full-frame verdict while the
            # app remains usable. Recheck the actual NEW frame once, read-only;
            # do not weaken motion/input gates or carry an old verdict forward.
            frame = agent.screen.frame
            checked = await judge({
                'ready': {'type': 'noul', 'instructions':
                    f'Is the actual ACTIVE FOREMOST usable app/window {app!r}, with real app navigation/content? '
                    'Identify from this current image alone, never the requested launch, old frame or history. '
                    'A floating target window can qualify. Another app, Home, icon, splash or logo cannot.'},
                'blocked': {'type': 'noul', 'instructions':
                    'Is ANY system Spotlight, other app overlap, permission, login, account chooser, feature introduction, promotion or blocking modal covering this app?'},
                'loading': {'type': 'noul', 'instructions': 'Is this only splash/loading/placeholder content without usable app navigation?'}},
                'reverify current foreground after stale open verdict', policy='launch_open_final')
            a = checked['answers']
            if (frame != agent.screen.frame or not checked.get('approved', True)
                    or a.get('ready', {}).get('noul', 0) < .99
                    or a.get('blocked', {}).get('noul', 1) > .01
                    or a.get('loading', {}).get('noul', 1) > .01):
                if frame == agent.screen.frame and a.get('blocked', {}).get('noul', 0) >= .99:
                    dismissal = await dismiss_known_launch_modal()
                    if dismissal:
                        return dismissal
                return wait('changed_scene')
            from .foreground_reuse import PUBLIC_APPS, identify_public_app
            if app in PUBLIC_APPS:
                identity = await identify_public_app(agent, frame)
                guard = await launch_layout_guard(agent, app)
                if guard:
                    raise _LaunchLayoutChanged(guard)
                agent.trace.write(frame, kind='shortcut-check',
                                  purpose='identify current foreground after stale open verdict', **identity)
                seconds += identity['seconds']
                observed = identity.get('answers', {}).get('visible_app', {})
                if (frame != agent.screen.frame or not identity.get('approved', True)
                        or observed.get('choice') != app or observed.get('confidence', 0) < .95):
                    return wait('changed_scene')
            action = {**action, '_verified_open_entry': True}
        return emit(action, 'verified')

    async def dismiss_notes_onboarding():
        if 'dismiss_notes_onboarding' in phases:
            return wait('notes_onboarding_closed')
        result = await judge({
            'ready': {'type':'noul','instructions':
                'Is this EXACTLY Apple Notes informational What is New in Notes feature tour, with a Continue button, and NO permission, account, payment, terms, deletion or consent request? Only the harmless feature introduction qualifies.'},
            'target': {'type':'box','instructions':
                'Locate the Continue button belonging to that Apple Notes informational feature tour. Not another app or a permission/terms dialog.'}},
            'dismiss Notes feature introduction', policy='notes_onboarding')
        target=result['answers'].get('target', {})
        if (result['answers'].get('ready', {}).get('noul',0) < .99 or target.get('confidence',0)<.9):
            return wait('notes_onboarding_closed')
        box=target['box']
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
                     'y':round((box['top']+box['bottom'])*500)}, 'dismiss_notes_onboarding')

    # After a receipt, only observe. Never repeat uncertain or already-sent text.

    async def recover_deleted_notes(probe_only=False):
        # This is navigation only. Never restore a note or acknowledge deletion.
        state=await judge({'page':{'type':'choice','instructions':
            'Classify actual Apple Notes for creating a NEW note. Never infer from goal/history.',
            'criteria':{'notice':'The exact Recently Deleted Note cannot be edited dialog with Cancel and Recover buttons.',
                        'deleted':'Recently Deleted is the selected folder or this is its read-only note editor, without the dialog. This includes a Folders drawer OVERLAID on the dimmed editor, with Recently Deleted highlighted; that is still deleted, not ordinary.',
                        'ordinary':'Ordinary editable Apple Notes folder/list/editor, NOT Recently Deleted, and no blocking dialog.',
                        'unknown':'Another app, permission/login/terms dialog, or uncertain Notes state.'}}},'classify Notes deleted-state recovery',policy='notes_deleted_state')
        page=state['answers']['page']
        if page.get('confidence',0)<.95:
            # A folders drawer can dim the old editor and make the page label
            # uncertain. This label never authorizes a click: after opening the
            # drawer, require the separate .99 navigation assertion and .90
            # ordinary-folder target below on the CURRENT frame.
            drawer_navigation=(page.get('choice')=='deleted' and 'leave_deleted_folder' in phases)
            if not drawer_navigation:
                return None if probe_only else wait('deleted_state')
        kind=page['choice']
        if kind=='ordinary':
            if probe_only: return None
            return emit({'action':'key','combo':'cmd+n'},'new_note_after_deleted')
        if kind=='notice':
            if 'dismiss_deleted_notice' in phases:
                return wait('deleted_notice_closed')
            instruction='Locate ONLY Cancel in the exact Recently Deleted Note cannot be edited dialog. Never Recover.'
            verification='Is this the exact Recently Deleted Note cannot be edited dialog, and is cancelling it the requested navigation? Recovering, deleting, or editing any old note is NOT authorized.'
            phase='dismiss_deleted_notice'
        elif kind=='deleted':
            if phases.count('leave_deleted_folder')>=2:
                raise DecisionUnavailable('Could not leave Recently Deleted safely; no note input sent')
            instruction=('Locate the ordinary Notes folder in the FOLDERS sidebar, excluding Recently Deleted. '
                         'If the folders sidebar is hidden, locate only its Folders/back/sidebar navigation button so the folder list can be shown. '
                         'Never locate a note row, Recover, trash/delete, New Note, or a permanent-deletion control.')
            verification=('Is Recently Deleted selected and is the proposed target only navigation to the ordinary Notes folder '
                          'or to show its folders sidebar, with no note recovery, deletion or editing?')
            phase='leave_deleted_folder'
        else:
            if probe_only: return None
            raise DecisionUnavailable('Notes is blocked by an unrelated or uncertain state; no input sent')
        result=await judge({'target':{'type':'box','instructions':instruction},
                            'ready':{'type':'noul','instructions':verification}},
                           'navigate safely out of Recently Deleted',policy='notes_deleted_navigation')
        box=result['answers'].get('target',{})
        proposed=box.get('box',{})
        if (kind=='deleted' and result['answers']['ready']['noul']>=.99
                and .5<=box.get('confidence',0)<.9 and proposed
                and (proposed['left']+proposed['right'])/2<.12
                and (proposed['top']+proposed['bottom'])/2<.1):
            # Resolve a tiny sidebar icon at readable scale. A crop cannot
            # authorize recovery/deletion; only the top-left folders toggle.
            original=Image.open(io.BytesIO(frame));w,h=original.size
            cw,ch=round(w*.4),round(h*.22)
            crop=original.crop((0,0,cw,ch));crop=crop.resize((cw*3,ch*3))
            data=io.BytesIO();crop.save(data,format='JPEG')
            result=await judge({'target':{'type':'box','instructions':
                'Locate only the top-left Notes sidebar/Folders toggle icon: a small rounded rectangle split into a narrow sidebar and wider content panel. Never Edit, a note row, or another toolbar control.'},
                'ready':{'type':'noul','instructions':
                'Is the identified control specifically the harmless top-left Apple Notes sidebar/Folders visibility toggle, with no Restore, Recover, Delete, Edit or new-note action?'}},
                'magnify Notes folders navigation',view=data.getvalue(),policy='notes_deleted_navigation')
            box=result['answers'].get('target',{})
            if box.get('box'):
                b=box['box'];box={**box,'box':{'left':b['left']*cw/w,'right':b['right']*cw/w,
                    'top':b['top']*ch/h,'bottom':b['bottom']*ch/h}}
        if (result['answers']['ready']['noul']<.99 or box.get('confidence',0)<.9 or not approved(result)):
            raise DecisionUnavailable('Safe Notes recovery navigation was not verified; no input sent')
        box=box['box']
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
                     'y':round((box['top']+box['bottom'])*500),'precise':True},phase)

    if 'payload' in phases:
        result = await judge({'ready': {'type': 'noul', 'instructions':
            f'Does the actual Apple Notes EDITOR visibly contain exactly the newly typed text {text!r}, '
            'in consecutive reading order (line wrapping allowed), with no duplicated copy? '
            'Require real note content, not search results, list previews, a prompt, or history.'}}, 'verify exact input')
        if approved(result):
            return emit({'action': 'done', 'answer': 'New Notes text visibly verified; nothing submitted.'}, 'verified')
        return wait('verify_payload')

    if any(p in phases for p in ('dismiss_deleted_notice','leave_deleted_folder')) and 'new_note_after_deleted' not in phases:
        return await recover_deleted_notes()

    # A new editor must really appear before any payload is typed.
    if 'new_note' in phases or 'new_note_after_deleted' in phases:
        result = await judge({'ready': {'type': 'noul', 'instructions':
            'Is a NEW EMPTY Apple Notes editor actually open and ready for hardware keyboard input? '
            'Require a blank editable note body/caret and note editing toolbar. The software keyboard may be hidden. '
            'A populated older note, Notes list, Spotlight, lock screen, alert, or folder view means NO.'},
            'onboarding': {'type':'noul','instructions':
                'Is the exact Apple Notes What is New / feature introduction card visible with a Continue button? Exclude permissions, login, terms, payment and any other modal.'},
            'deleted_view':{'type':'noul','instructions':'Is Apple Notes still in Recently Deleted or its exact cannot be edited dialog, rather than a new editable note?'}}, 'verify fresh blank note', policy='notes_blank')
        if (approved(result) and result['answers'].get('onboarding', {}).get('noul',0) <= .1
                and result['answers'].get('deleted_view',{}).get('noul',0) <= .1):
            return emit({'action': 'type', 'text': text}, 'payload')
        if result['answers'].get('deleted_view',{}).get('noul',0)>=.99:
            return await recover_deleted_notes()
        if result['answers'].get('onboarding', {}).get('noul',0) >= .99:
            return await dismiss_notes_onboarding()
        if 'dismiss_notes_onboarding' in phases and 'new_note_after_onboarding' not in phases:
            clear = await judge({'ready':{'type':'noul','instructions':
                'Is Apple Notes now the active foreground app, with its ordinary notes list or note editor visible and NO onboarding, alert, login, or other blocking overlay?'}}, 'verify Notes after feature tour')
            if approved(clear):
                return emit({'action':'key','combo':'cmd+n'}, 'new_note_after_onboarding')
        return wait('blank_editor')

    if ipad and text is None and not history and not spotlight:
        from .foreground_reuse import reuse_foreground
        reuse=await reuse_foreground(agent,app)
        if reuse is not None:
            seconds+=reuse['seconds']
            return emit({'action':'done','answer':app+' foreground rechecked locally against prior verified App pixels; no App relaunch.',
                         '_verified_foreground_reuse':True},'foreground_reused')

    if ipad and text is None and ('icon_launch' in phases or spotlight == 'ready'):
        if app=='rednote' and 'icon_cancel_overlay' not in phases:
            from .app_controls import public_search_control
            def known_paste():
                return public_search_control(agent.screen.frame,app,agent.phone.target_info(),control='native_paste_denial_notice')
            if known_paste():
                # Reuse the actual three-label native sheet, not changing feed
                # pixels. Escape never accepts clipboard access; no pointer or
                # default Return. Exactly once, then re-verify real App content.
                agent.trace.write(kind='local-notice-recovery',app=app,notice='native_paste',action='escape_once',permission_accepted=False)
                if known_paste():return [dict(action='key',combo='esc',workflow='keyboard',phase='icon_cancel_overlay',
                    thought='Dismiss known native Paste sheet once without accepting clipboard access; verify App next.',think_s=0,_shot=frame)]
        entry = (f'In this current run, canonical ASCII Spotlight query for {app} was typed ONCE and Return acknowledged; two fresh stable local observations proved system Spotlight closed and the frame changed. This is entry evidence only, not app identity.'
                 if spotlight == 'ready' else
                 f'In this current run, an independently verified Home grid and visually grounded {app} app icon permitted one precisely clicked and acknowledged pointer fallback. This is entry evidence only, not app identity.'
                 if any(s.get('_pointer_launch_fallback') == app for s in history) else
                 f'In this current run, a locally template-verified Home icon for {app} was precisely clicked and acknowledged.')
        result=await judge({
            'ready':{'type':'noul','instructions':f'{entry} Use this real entry evidence together with the CURRENT image, not the goal alone. Does the CURRENT image show the resulting USABLE app interface or its own app-local Search page, editor, feed or profile? An app-owned search page with history is valid app content even without the app brand; after this verified entry it need not display a logo. Reject if another app is visibly identified or Home remains. Require the app itself, not an icon, logo/splash/loading frame, another app, or app content behind system Spotlight/Control Center.'},
            'blocked':{'type':'noul','instructions':'Is actual SYSTEM Spotlight, Control Center, lock screen, login, permission, account chooser, feature introduction, Youth Mode notice or ANY other blocking overlay covering the app? An app-owned Search field, search-history list or its own voice-search UI is NOT system Spotlight or a blocking modal.'},
            'launch_notice':{'type':'noul','instructions':'Is an actual optional app-owned What is New introduction with separate X Close, or a cancellable promotion sheet, blocking this app? Exclude system permissions, login, account selection, terms, payment confirmation or unknown dialogs. Never infer from a dialog depicted inside content.'},
            'loading':{'type':'noul','instructions':f'Is only a startup logo/splash/loading placeholder for {app} shown, with no usable navigation or content?'}},
            'verify directly launched app content',policy='app_icon_ready')
        a=result['answers']
        if result.get('approved',a['ready']['noul']>=.99 and a['blocked']['noul']<=.01 and a['loading']['noul']<=.01):
            return await finish_open({'action':'done','answer':app+(' foreground content verified after Spotlight launch.' if spotlight == 'ready' else ' foreground content verified after direct icon launch.'),'_verified_icon_entry':True})
        if a['blocked']['noul']>=.5:
            denial=await dismiss_known_launch_modal(benign_notice=a.get('launch_notice',{}).get('noul',0)>=.99)
            if denial:return denial
            if ipad and 'icon_cancel_overlay' not in phases:
                return emit({'action':'key','combo':'esc'},'icon_cancel_overlay')
        # A click already happened. Never start Spotlight or type an app name
        # simply because a different in-app page or a cold start was observed.
        await asyncio.sleep(.5)
        return wait('icon_app_ready')
    if ipad and text is None and 'icon_launch' not in phases and 'home_icon_guard_refused' not in phases:
        from .home_icons import locate_home_icon,has_home_icon
        icon=locate_home_icon(frame,app,target)
        if icon:
            agent.trace.write(kind='home-icon-match',app=app,**icon)
            return emit({'action':'tap','x':icon['x'],'y':icon['y'],'precise':True,'_home_icon_app':app,'_icon_hitbox':icon['hitbox']},'icon_launch')
        if spotlight == 'fallback':
            return fail_action(agent, app, 'Verified Home icon fallback unavailable; no second app query')
        if has_home_icon(app,frame,target):
            if 'normalize_ipad_home' not in phases:
                return [dict(action='key',combo='cmd+h',workflow='keyboard',phase='normalize_ipad_home',think_s=0,_shot=frame),
                        dict(action='key',combo='esc',workflow='keyboard',phase='normalize_ipad_home_done',think_s=0,_shot=frame)]
            if 'collapse_home_group' not in phases:
                # A floating App can consume the first Command-H and reveal
                # the public App underneath, rather than the actual Home grid.
                # Permit one more system Home only after a real new frame.
                prior=next(((i,s) for i,s in enumerate(agent.steps)
                    if s.get('workflow')=='keyboard' and s.get('phase')=='normalize_ipad_home' and 'act_s' in s),None)
                old=(getattr(agent,'shots',{}).get(prior[0]) if prior else None)
                if (prior and old and frame!=old and getattr(agent.screen,'updated',0)>prior[1].get('t',float('inf'))):
                    return [dict(action='key',combo='cmd+h',workflow='keyboard',phase='collapse_home_group',
                                 thought='One new-frame-checked Home escape after a floating App revealed another App.',think_s=0,_shot=frame)]
            if 'home_icon_grid' in phases and 'cancel_home_overlay' not in phases:
                denial=await deny_local_network()
                if not denial:denial=await deny_app_tracking()
                if not denial:denial=await dismiss_bilibili_intro()
                if not denial:denial=await dismiss_douyin_intro()
                if denial:return denial
                # Native permission sheets can arrive AFTER Home + Escape.
                # One later non-accepting Escape is safe; verify the real grid
                # again before any click. Never choose Allow/default Enter.
                return emit({'action':'key','combo':'esc'},'cancel_home_overlay')
            await asyncio.sleep(.3)
            return wait('home_icon_grid')

    # Merge questions, never infer a Top Hit or foreground from an ACK. These
    # opt-ins apply only to open-only iPad transitions in this run. All answers
    # use the same immutable frame; emit() still checks the current scene.
    query_ack = any(s.get('phase') == 'query' and s.get('action') == 'type'
                    and s.get('text') in queries and 'act_s' in s for s in history)
    launch_ack = any(s.get('phase') == 'launch' and s.get('action') == 'key'
                     and s.get('combo') == 'enter' and 'act_s' in s for s in history)
    merge_result = (ipad and text is None and query_ack and phases.count('launch') < 2
                    and os.environ.get('SHORTCUT_MERGE_APP_RESULT', '0') == '1')
    merge_foreground = (ipad and text is None and query_ack and launch_ack
                        and os.environ.get('SHORTCUT_MERGE_FOREGROUND', '0') == '1')
    route_questions = {'page': {'type': 'choice', 'instructions':
        f'Classify the CURRENT screen for opening {app!r}, also labelled {labels!r}. Ignore goal/history as evidence. '
        'PRIORITY: ANY system Spotlight search capsule/panel OVERLAID on content means search, even when the target app is fully visible underneath or the capsule says Notes - Open with its icon. Never choose app for background content behind Search. Only choose app with NO system Search overlay. '
        'Spotlight on iPad may be a CENTERED FLOATING panel, including over another app, Split View or Stage Manager; a focused app-local search is NOT system Spotlight. On iPhone Spotlight has TWO valid layouts: a LARGE TOP query capsule over Home, OR a WIDE BOTTOM search field over a blurred screen with Siri suggestions/results (common in Chinese iOS). Both mean search, including bottom 搜索 with a caret and microphone even without a software keyboard. Only a SMALL bottom Search/搜索 pill over the normal unblurred Home icon grid is closed Home search. The specific Apple Notes What is New feature introduction is notes_onboarding. Expanded system Control Center with connectivity tiles, brightness and volume sliders is control_center. Other modal alerts and locked screens are blocked.',
        'criteria': {'notes_onboarding': 'Only the harmless Apple Notes What is New / feature introduction card with a Continue button, not any permission, login or terms dialog.',
                     'loading': f'Only a splash screen, large app logo or loading placeholder for {app}, with no usable app navigation or loaded content. Never app.',
                     'app': f'Actual ACTIVE FOREMOST {app} / {labels!r} USABLE content, including its editor or list and real app navigation. A splash/logo/loading placeholder is NOT app. On iPad, content partly hidden BEHIND an overlapping different app is NOT app; choose other. For rednote/小红书 its own Discover/Explore/发现/关注, app-owned Search/history page, note detail or profile qualifies; not another social app or system Spotlight.',
                     'search': 'Takes priority over app content visible behind it. Open system Spotlight: a floating TOP capsule (query plus Open/app icon), a centered iPad Search panel with system suggestions/installed app results, or either TOP query capsule or WIDE BOTTOM 搜索/Search field with blurred background and Siri suggestions/results, empty or filled.',
                     'control_center': 'Only expanded system Control Center, showing Wi-Fi/Bluetooth tiles and brightness/volume sliders; not an app, lock/passcode screen or a permission/modal dialog.',
                     'blocked': 'Lock/passcode screen, permission prompt, expanded system menu or modal alert.',
                     'other': 'An unlocked normal Home icon grid OR another normal app, without open system search. A SMALL bottom Search pill over an unblurred normal icon grid is part of Home.'}}}
    if merge_result:
        current_query = next((s.get('text', queries[0]) for s in reversed(history)
                              if s.get('phase') == 'query'), queries[0])
        route_questions['installed_app'] = installed_app_question(current_query, labels)
        route_questions['first_result'] = first_spotlight_result_question(labels)
        if app in SPOTLIGHT_QUERIES:
            route_questions['no_installed_app'] = no_installed_app_question()
    if merge_foreground:
        route_questions['foremost'] = foremost_app_question(app)
    route = await judge(route_questions, 'classify foreground',
                        policy='shortcut_same_frame' if merge_result or merge_foreground else None)
    if merge_result or merge_foreground:
        agent.trace.write(kind='shortcut-check-merged', questions=list(route_questions),
                          scope='Same-frame questions only; original gates and independent public identity remain.')
    page = route['answers']['page']
    from .foreground_reuse import PUBLIC_APPS, identify_public_app
    if (ipad and app in PUBLIC_APPS and page.get('choice')=='app'
            and probability(page.get('confidence')) and page['confidence']>=.85):
        # A target-conditioned yes/no can confidently mistake a similar app.
        # Identify the actual app without supplying the desired target or card,
        # then compare locally. Keep all downstream foreground/input checks.
        identity=await identify_public_app(agent, frame)
        agent.trace.write(frame,kind='shortcut-check',purpose='identify actual visible public app without requested target',**identity)
        seconds+=identity['seconds']
        observed=identity.get('answers',{}).get('visible_app',{})
        if (not probability(observed.get('confidence')) or observed['confidence']<.95
                or not identity.get('approved',True) or observed.get('choice') not in PUBLIC_APPS):
            return wait('foreground_identity_uncertain')
        if observed['choice']!=app:
            agent.trace.write(kind='foreground-identity-contrast',requested_app=app,observed_app=observed['choice'],
                scope='Task-independent actual app identity overrides target-conditioned app label; no completion or input proof.')
            page={'choice':'other','confidence':observed['confidence']}
    if page.get('confidence', 0) < .85:
        # iPad Safari can hide all browser controls after scrolling. Reveal its
        # own chrome once after an acknowledged launch, without typing a URL.
        if (ipad and app == 'Safari' and page.get('choice') == 'app'
                and page.get('confidence', 0) >= .6 and 'launch' in phases
                and 'reveal_safari_toolbar' not in phases):
            return [dict(action='key',combo='cmd+l',workflow='keyboard',phase='reveal_safari_toolbar',_shot=frame),
                    dict(action='key',combo='esc',workflow='keyboard',phase='reveal_safari_toolbar_done',_shot=frame)]
        return wait('classify_scene')
    if page['choice']=='loading':
        await asyncio.sleep(.7)
        return wait('app_loading')
    if page['choice'] == 'control_center':
        if not ipad:
            raise DecisionUnavailable('Keyboard route is blocked; no input sent')
        if 'dismiss_control_center' in phases:
            return wait('control_center_closed')
        overlay=await judge({
            'ready':{'type':'noul','instructions':
                'Is expanded system Control Center visibly OVER an unlocked HOME APP ICON GRID, showing connectivity tiles '
                'and brightness/volume sliders? Judge visible state only.'},
            'blocked':{'type':'noul','instructions':
                'Is a lock/passcode screen or any permission, account, payment, terms or deletion dialog visible in front of the Home grid?'},
            'target':{'type':'box','instructions':
                'Locate a dimmed background area OUTSIDE the expanded Control Center panel, on the LEFT edge of the screen, '
                'which can be clicked once to dismiss Control Center. Exclude all Control Center tiles and sliders.'}},
            'confirm harmless Control Center dismissal',policy='system_control_center')
        answers=overlay['answers'];target_box=answers['target'];box=target_box['box']
        if (answers['ready']['noul']<.99 or answers['blocked']['noul']>.01
                or target_box['confidence']<.9 or box['right']>.2 or not approved(overlay)):
            raise DecisionUnavailable('Control Center dismissal could not be verified; no input sent')
        return emit({'action':'tap','x':round((box['left']+box['right'])*500),
                     'y':round((box['top']+box['bottom'])*500)},'dismiss_control_center')
    if page['choice'] == 'notes_onboarding':
        if app == 'Notes':
            return await dismiss_notes_onboarding()
        raise DecisionUnavailable('Unexpected app onboarding; no input sent')
    if page['choice'] == 'blocked':
        if app=='Notes' and text is not None:
            return await recover_deleted_notes()
        if text is None and ('launch' in phases or 'icon_launch' in phases or spotlight == 'ready'):
            dismissal = await dismiss_known_launch_modal()
            if dismissal:
                return dismissal
        # rednote checks the clipboard at launch. Reject this incidental read;
        # never grant paste/location/contacts access merely to open an app.
        if app == 'rednote' and text is None and 'deny_paste' not in phases:
            denial = await judge({
                # Put localization first so JudgeFallback routes uncertain
                # geometry to Haiku rather than accepting only alert presence.
                'deny': {'type': 'box', 'instructions':
                    'Locate only the explicit deny-paste button labelled 不允许粘贴, Don’t Allow Paste, or Do Not Allow Paste inside that native paste permission alert. Never locate Allow Paste / 允许粘贴.'},
                'paste_prompt': {'type': 'noul', 'instructions':
                    'Is a native iOS PASTE PERMISSION alert visible asking this app to paste from another app, with an explicit DO NOT ALLOW PASTE / 不允许粘贴 button? Not a location, login, tracking, payment, notification or generic dialog.'}
            }, 'deny incidental clipboard access')
            answer = denial['answers']['deny']
            if denial['answers']['paste_prompt']['noul'] >= .95 and answer['confidence'] >= .9:
                box = answer['box']
                return emit({'action': 'tap', 'x': round((box['left']+box['right'])*500),
                             'y': round((box['top']+box['bottom'])*500)}, 'deny_paste')
        raise LaunchBlocked(frame, route)
    # The classifier explicitly gives Spotlight priority over app content.
    # Reuse only a direct high-confidence Jev decision on identical pixels for
    # open-only navigation. The separate foremost-window check below remains;
    # this never authorizes text, selection, submission or a new app identity.
    route_confidence=page.get('confidence')
    reuse_jev_route=(ipad and text is None and page.get('choice')=='app'
        and type(route_confidence) in (int,float) and .99<=route_confidence<=1
        and str(route.get('model','')).startswith('jev-')
        and route.get('fallback_used') is False and route.get('approved') is True
        and agent.screen.frame==frame)
    if (ipad and page['choice']=='app' and 'query' not in phases and 'launch' not in phases
            and reuse_jev_route):
        agent.trace.write(kind='shortcut-check-reuse',purpose='reuse direct Jev no-Spotlight classification',
            model=route['model'],confidence=route_confidence,
            independent_foreground_required=True,text_authorized=False)
    if (ipad and page['choice']=='app' and 'query' not in phases and 'launch' not in phases
            and not reuse_jev_route):
        overlay=await judge({'ready':{'type':'noul','instructions':
            'Is system Spotlight currently OPEN as a wide floating Search capsule near the TOP, with magnifying glass, editable caret and microphone, even though the app underneath is completely unblurred? '
            'On iPad this may show only an empty Search field and no suggestions/results or software keyboard. That IS active system Search, not the background Notes app. '
            'Do not count Notes own Search toolbar icon, browser address bar, or a field inside the note list.'}},
            'verify compact iPad Spotlight overlay',policy='ipad_foreground')
        if overlay['answers']['ready']['noul']>=.95 and approved(overlay):
            page={'choice':'search','confidence':1.0}
    if page['choice'] == 'app' and 'query' in phases and 'launch' not in phases:
        overlay = await judge({'ready':{'type':'noul','instructions':
            f'Is a system Spotlight Search capsule or panel CURRENTLY overlaid above the app content, with query {app!r}, matching app icon and/or Open label? A thin top rounded capsule over a fully visible Notes editor still means Search is OPEN. Do not mistake background app content for absence of Search.'}}, 'check same-app Spotlight overlay')
        if approved(overlay):
            page = {'choice':'search', 'confidence':1.0}
    if ipad and page['choice']=='app':
        foreground = ({**route, 'answers': {'ready': route['answers'].get('foremost', {'noul': 0})}}
                      if merge_foreground else await judge({'ready': foremost_app_question(app)},
                          'verify foremost iPad app window', policy='ipad_foreground'))
        if foreground['answers']['ready']['noul']<.95 or not approved(foreground):
            page={'choice':'other','confidence':1.0}
    if page['choice'] == 'app':
        if ipad and text is not None and 'launch' in phases:
            # Spotlight Return can leave hardware-keyboard ownership with the
            # previous app even though Notes is visible. A grounded compose tap
            # targets Notes itself; never infer Command-N ownership from launch.
            result=await judge({
                'ready':{'type':'noul','instructions':
                    'Is an ordinary editable Apple Notes pane visible with its own NEW NOTE / COMPOSE button accessible, '
                    'with NO Spotlight, blocking overlay, Recently Deleted or read-only state covering that Notes pane? '
                    'A different app beside Notes is allowed, but never target its controls.'},
                'target':{'type':'box','instructions':
                    'Locate ONLY the Apple Notes NEW NOTE / COMPOSE square-and-pencil toolbar button. '
                    'Never another app control, the drawing/markup button, an existing note row, Share or Delete.'}},
                'create new note using its visible compose control',policy='notes_new_note_compose')
            target_box=result['answers'].get('target',{})
            box=target_box.get('box',{})
            if (result['answers']['ready']['noul']<.99 or target_box.get('confidence',0)<.9
                    or not approved(result) or not box):
                return wait('notes_compose_visible')
            return emit({'action':'tap','x':round((box['left']+box['right'])*500),
                         'y':round((box['top']+box['bottom'])*500),'precise':True},'new_note')
        if text is not None and target and target.get('device') == 'ipad' and 'launch' not in phases:
            # A real caret in an unobstructed Notes editor can establish focus
            # without reopening the same app through Spotlight.
            focused=await judge({'ready':{'type':'noul','instructions':
                'Is ordinary editable Apple Notes the ACTIVE foreground app window that will receive the app-level Command-N shortcut to CREATE A NEW NOTE? '
                'Require visible active-window focus indicators or a real caret owned by Notes. A note-body caret is NOT required for this app-level shortcut. '
                'Notes merely visible in a neighboring INACTIVE window, another active app, system Spotlight, an app search field, '
                'Recently Deleted/read-only folder, or any blocking overlay means NO. '
                'This check permits only creation of a NEW note, NEVER typing into existing content.'}},
                'verify active Notes keyboard focus',policy='notes_new_note_focus')
            if focused['answers']['ready']['noul']>=.99 and approved(focused):
                return emit({'action':'key','combo':'cmd+n'}, 'new_note')
            recovery=await recover_deleted_notes(probe_only=True)
            if recovery is not None: return recovery
            # Otherwise activate via system Search; do not guess the active pane.
            if 'open_search' in phases:
                return wait('search_open')
            return emit({'action': 'key', 'combo': 'cmd+space'}, 'open_search')
        if text is None:
            return await finish_open({'action': 'done', 'answer': app+' actual app content verified.',
                                      '_verified_foreground_entry': ipad},
                                     recheck_changed=not merge_foreground)
        return emit({'action': 'key', 'combo': 'cmd+n'}, 'new_note')
    if page['choice'] != 'search':
        if ipad and not history:
            # Clear old app/window groups before activating the requested app.
            return [dict(action='key',combo='cmd+h',workflow='keyboard',phase='normalize_ipad_home',
                         thought='Normalize the iPad window context before app activation.',think_s=round(seconds,3),_shot=frame),
                    dict(action='key',combo='esc',workflow='keyboard',phase='normalize_ipad_home_done',_shot=frame)]
        if ipad and 'open_search' in phases and 'query' not in phases:
            if text is None and page['choice'] == 'other' and page['confidence'] >= .95 and phases.count('search_open') >= 3:
                raise SearchOpenNotReached(frame)
            return wait('search_open')
        if phases.count('open_search') >= 2 or 'launch' in phases or 'query' in phases:
            return wait('target_app')
        return emit({'action': 'key', 'combo': 'cmd+space'}, 'open_search')
    if not any(p in phases for p in ('open_search', 'query', 'reset_search')):
        # A task can start in Spotlight left by an earlier command. Its capsule
        # can be a launch preview rather than an active editing field. Normalize
        # this navigation surface once; standalone dictation never enters here.
        if scene_changed(frame, agent.screen.frame):
            return wait('changed_scene')
        return [dict(action='key', combo='cmd+h', workflow='keyboard', phase='reset_search',
                     thought='Leave old Spotlight before opening the requested app.', think_s=round(seconds,3), _shot=frame),
                dict(action='key', combo='esc', workflow='keyboard', phase='reset_search_done',
                     thought='Dismiss the old Search capsule.', _shot=frame)]
    if 'query' not in phases:
        if recent_keyboard_search(history, page):
            # Cmd+Space was acknowledged in THIS run, and a fresh independent
            # page classifier now confirms system Search. Re-guessing an
            # invisible/blinking caret adds no evidence to that known transition.
            agent.trace.write(frame, kind='shortcut-focus-evidence',
                              evidence='recent acknowledged Cmd+Space plus fresh high-confidence system Search',
                              skipped_redundant_focus_check=True)
        else:
            result = await judge({'ready': {'type': 'noul', 'instructions':
                'Is the system Spotlight search field OPEN and the active editable keyboard input, at the TOP or BOTTOM, or inside a CENTERED FLOATING iPad system Search panel? '
                'A WIDE BOTTOM rounded 搜索/Search field with caret and microphone over blurred Siri suggestions/results counts as OPEN active search. The SMALL Home Search pill over normal app icons does not count. Browser address bars and note editors do not count. '
                'An external hardware keyboard hides the software keyboard. IMPORTANT: the current query may name a DIFFERENT app; that is expected and must be replaced. Query text selected with selection handles is affirmative evidence of editable focus, even with no caret or software keyboard.'}}, 'verify Spotlight input focus', view=frame)
            if not approved(result):
                if ipad and page.get('confidence',0)>=.95 and 'reveal_search_caret' not in phases:
                    # A blank hardware-keyboard Spotlight field can blink its
                    # caret off. One non-editing arrow pulse reveals focus;
                    # still require a NEW visual focus check before any text.
                    return emit({'action':'key','combo':'right'},'reveal_search_caret')
                return wait('search_focus')
        # Selection and replacement share one verified focus; Return is a later,
        # separately checked action. No page navigation between these two inputs.
        if scene_changed(frame, agent.screen.frame):
            return wait('changed_scene')
        return [dict(action='key', combo='cmd+a', workflow='keyboard', phase='select_query',
                     thought='Select the verified Spotlight query.', think_s=round(seconds,3), _shot=frame),
                dict(action='type', text=queries[0], workflow='keyboard', phase='query',
                     thought='Replace query, without submitting.', _shot=frame)]
    # A Return receipt is not proof iOS launched. A second Return is allowed
    # only while a NEW observation still proves the exact Spotlight app result.
    # This never retries typed payload or Return in an app/form.
    query_steps = [s for s in history if s.get('phase') == 'query']
    if query_steps and query_steps[-1].get('action') == 'type' and 'act_s' not in query_steps[-1]:
        return wait('query_receipt_uncertain')
    if phases.count('launch') >= 2:
        if not ipad or text is not None:return wait('target_app')
        if 'launch_tap' in phases:return wait('target_app')
        result=await judge({
            'ready':{'type':'noul','instructions':f'Is CURRENT system Spotlight showing query from {queries!r} and the FIRST actual INSTALLED APP result labelled one of {labels!r}? Exclude web tabs, App Store suggestions, documents, Search in App and any other app.'},
            'no_installed_app': no_installed_app_question(),
            'target':{'type':'box','instructions':f'Locate ONLY the first INSTALLED APP result icon/label for {app}, matching {labels!r}, in Spotlight Apps. Never a web page, App Store download result, file or suggestion.'}},
            'locate installed app when Return did not activate it',policy='spotlight_app_launch')
        a=result['answers'];target_box=a['target']
        if a.get('no_installed_app', {}).get('noul', 0) >= .99 or a['ready']['noul']<.99 or target_box.get('confidence',0)<.9:
            return wait('target_app')
        b=target_box['box']
        return emit({'action':'tap','x':round((b['left']+b['right'])*500),'y':round((b['top']+b['bottom'])*500),'precise':True},'launch_tap')
    result = ({**route, 'answers': {'ready': route['answers'].get('installed_app', {'noul': 0}),
                                  'first_result': route['answers'].get('first_result', {'noul': 0}),
                                  'no_installed_app': route['answers'].get('no_installed_app', {'noul': 0})}}
              if merge_result else await judge({'ready': installed_app_question(
                  query_steps[-1].get('text', queries[0]) if query_steps else queries[0], labels),
                  **({'first_result': first_spotlight_result_question(labels)} if ipad and text is None else {}),
                  'no_installed_app': no_installed_app_question()},
                  'verify installed app before Return', frame))
    absent = result['answers'].get('no_installed_app', {}).get('noul', 0)
    if probability(absent) and absent >= .99:
        # Never replace/re-submit after Return, including a false positive ACK.
        # Candidate replacement is navigation only and requires a real type ACK.
        if not any(p in phases for p in ('launch', 'launch_tap')) and query_steps:
            previous = query_steps[-1].get('text', '').casefold()
            index = next((i for i, q in enumerate(queries) if q.casefold() == previous), None)
            if index is not None and index + 1 < len(queries):
                if spotlight and not safe_frame(agent, app, frame):
                    return fail_action(agent, app, 'Spotlight privacy gate refused alias evidence')
                if scene_changed(frame, agent.screen.frame):
                    return wait('changed_scene')
                return [dict(action='key', combo='cmd+a', workflow='keyboard', phase='select_query', _shot=frame),
                        dict(action='type', text=queries[index+1], workflow='keyboard', phase='query',
                             thought='Fresh no-installed-app evidence permits the next ASCII query.', _shot=frame)]
        return wait('app_result')
    # A later same-app overlay check can change page from app to search. The
    # aggregate classifier approval then says nothing about installed_app.
    # Always enforce that field's original gate at its point of consumption.
    installed = result['answers']['ready'].get('noul')
    if ipad and text is None:
        first = result['answers'].get('first_result', {}).get('noul')
        # Same .9 bar as installed_app. 2026-10-30 sr3: Jev gave .91-.98 on
        # 14/14 real Top Hits and .01-.02 when a Chrome suggestion was first.
        if not probability(first) or first < .9:
            # Stop rather than Return on a lower Apps row. A separate existing
            # precise app-row path may locate/tap it; presence cannot select it.
            return wait('app_result')
    if approved(result) and probability(installed) and installed >= .9:
        return emit({'action': 'key', 'combo': 'enter'}, 'launch')
    return wait('app_result')


def spotlight_input_view(frame):
    """Magnify the system query capsule, keeping its whole top-screen context."""
    picture = Image.open(io.BytesIO(frame)).convert('RGB')
    top = picture.crop((0, 0, picture.width, round(picture.height * .26)))
    top = top.resize((768, round(top.height * 768 / top.width)))
    output = io.BytesIO();top.save(output,format='JPEG',quality=92)
    return output.getvalue()


def recent_keyboard_search(history, page, now=None):
    """Trust a fresh, acknowledged system-search transition, never a stale screen."""
    if page.get('choice') != 'search' or page.get('confidence', 0) < .95:
        return False
    mutations = [s for s in history if s.get('action') in ('key','type','tap','swipe','home','back')]
    if not mutations:
        return False
    last = mutations[-1]
    age = (time.time() if now is None else now) - last.get('t', 0)
    return (last.get('phase') == 'open_search' and last.get('action') == 'key'
            and last.get('combo') == 'cmd+space' and 'act_s' in last and 0 <= age <= 3)
