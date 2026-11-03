"""Finite, app-owned public search. No general planner or engagement actions."""
import json
import os
import asyncio
import re
import time
from .decision import DecisionUnavailable, probability
from .jev_agent import scene_changed
from .app_cards import load_app_card
from .app_controls import public_search_control
from .scene_scope import verdict_scene_changed, start_launch_observation, finish_launch_observation

PREFIX='APP_PUBLIC_SEARCH '

def search_goal(task):
    if not isinstance(task,str) or not task.startswith(PREFIX):return None
    from .app_tools import PUBLIC_SEARCH_APPS,OPEN_APPS,SearchArgs
    try:
        data=json.loads(task[len(PREFIX):])
        if not isinstance(data,dict) or not {'app','query'}<=set(data) or set(data)-{'app','query','read_result'}:raise ValueError()
        if data['app'] not in [OPEN_APPS[k] for k in PUBLIC_SEARCH_APPS]:raise ValueError()
        validated=SearchArgs.model_validate({k:v for k,v in data.items() if k!='app'})
        return {'app':data['app'],'query':validated.query,**({'read_result':True} if validated.read_result else {})}
    except (ValueError,TypeError,KeyError):raise DecisionUnavailable('Invalid app search request; no input sent') from None

def match_search_intent(goal):
    from .app_tools import OPEN_APPS,PUBLIC_SEARCH_APPS
    from .shortcut_workflow import APP_ALIASES
    if not isinstance(goal,str):return None
    m=re.fullmatch(r'\s*(?:please )?(?:search (.+?) for |(?:open|launch) (.+?) and search for )(.+?)\s*[.!]?\s*',goal,re.I)
    if not m:return None
    query=m[3].strip()
    if search_engagement_requested(goal):return None
    suffix=re.search(r'(?:,?\s+and |,\s*)(?:look at (?:a|one|the) (?:post|result)(?:,? and tell me)?|read (?:a|one|the) (?:post|result)|tell me(?: what (?:it|they) says?)?|summari[sz]e(?: it)?)[.!]?$',query,re.I)
    read_result=bool(suffix or re.search(r'\b(?:recipes?|how to cook)\b',query,re.I))
    if suffix:query=query[:suffix.start()].strip(' ,')
    normalized=lambda s:re.sub(r'\s+','',s).casefold()
    for key in PUBLIC_SEARCH_APPS:
        app=OPEN_APPS[key]
        if normalized(m[1] or m[2]) in [normalized(a) for a in APP_ALIASES.get(app,(app,))]:
            return key+'.search',{'query':query,**({'read_result':True} if read_result else {})}
    return None


def search_engagement_requested(goal):
    return bool(isinstance(goal,str) and re.search(r'\bsearch\b',goal,re.I) and
                re.search(r'(?:,?\s+and |,\s*)(?:like|follow|comment|save|dm|post|share|send|reply)\b',goal,re.I))

class PublicSearchBlocked(DecisionUnavailable):pass

def blocked_launch_actions(agent, goal, frame):
    # A previous app's restriction page can otherwise prevent every later
    # search from reaching the existing Home normalization. Leave that surface
    # once on a NEW request only. Never dismiss/accept its restriction controls.
    phone=getattr(agent,'phone',None)
    if (phone is None or (phone.target_info() or {}).get('device')!='ipad'
            or not phone.calibrated or not agent.screen.live or frame!=agent.screen.frame
            or any(s.get('action') not in ('plan','replan (stuck)','wait') for s in agent.steps)):
        raise DecisionUnavailable('Blocked launch recovery refused after input or stale device evidence')
    state=getattr(agent,'_search_block_home',None)
    if state and state['steps'] is agent.steps:
        raise DecisionUnavailable('Blocked launch recovery already consumed; no resend')
    # Consume BEFORE returning input, including cancellation before a step is
    # recorded. Agent.start/clear creates a new steps list for a new request.
    agent._search_block_home=dict(steps=agent.steps,index=len(agent.steps))
    agent.trace.write(kind='app-search-block-recovery',app=goal['app'],
                      reason='new_request_blocked_before_input',attempts=1)
    return [dict(action='key',combo=combo,workflow='keyboard',phase=phase,
                 _search_block_home=True,_shot=frame,think_s=0)
            for combo,phase in (('cmd+h','normalize_ipad_home'),('esc','normalize_ipad_home_done'))]


async def next_search_actions(agent,goal):
    from .app_search_keyboard import KeyboardRouteFallback, route_state, fallback, annotate
    if goal.get('read_result'):
        from .app_research import next_research_actions
        research=await next_research_actions(agent,goal,results_verified=any(
            s.get('workflow')=='app_search' and s.get('phase')=='submit' and 'act_s' in s for s in agent.steps))
        if research is not None:return research
    from .shortcut_workflow import SearchOpenNotReached, LaunchBlocked
    recovery=getattr(agent,'_search_block_home',None)
    if recovery and recovery['steps'] is agent.steps:
        receipts=agent.steps[recovery['index']:recovery['index']+2]
        if (len(receipts)!=2 or any(not s.get('_search_block_home') or 'act_s' not in s for s in receipts)):
            raise DecisionUnavailable('Blocked launch recovery receipt uncertain; no resend')
    if any(s.get('_pointer_launch_fallback') and 'act_s' not in s for s in agent.steps):
        raise DecisionUnavailable('Pointer launch receipt uncertain; no fallback or resend')
    state=route_state(agent,goal)
    if state and (state.get('uncertain') or any(
            s.get('_search_keyboard_entry') and 'act_s' not in s for s in agent.steps)):
        raise DecisionUnavailable('Keyboard search delivery uncertain; no fallback or resend')
    try:
        actions=await _recover_search_actions(agent,goal)
    except KeyboardRouteFallback as error:
        fallback(agent,goal,str(error))
        actions=await _recover_search_actions(agent,goal)
    except SearchOpenNotReached as error:
        from .app_search_keyboard import pointer_launch_actions
        actions=await pointer_launch_actions(agent,goal,error.frame)
    except LaunchBlocked as error:
        actions=blocked_launch_actions(agent,goal,error.frame)
    if goal.get('read_result') and any(a.get('_verified_public_results') for a in actions):
        return await next_research_actions(agent,goal,results_verified=True)
    return annotate(agent,goal,actions)

async def _recover_search_actions(agent,goal):
    try:return await _next_search_actions(agent,goal)
    except PublicSearchBlocked:
        # A delayed permission/introduction can arrive after app_ready. Only
        # bounded refusal/dismissal markers may recover it, never arbitrary
        # navigation, account selection, a new query or private composers.
        if not (hasattr(agent,'phone') and (agent.phone.target_info() or {}).get('device')=='ipad'
                and any(s.get('workflow')=='keyboard' and s.get('phase')=='icon_launch' for s in agent.steps)):raise
        from .shortcut_workflow import next_shortcut_actions
        actions=await next_shortcut_actions(agent,{'app':goal['app'],'text':None})
        allowed={'deny_local_network':'_local_network_denial','deny_app_tracking':'_tracking_denial','dismiss_bilibili_youth_intro':'_intro_notice_dismissal','dismiss_douyin_youth_intro':'_intro_notice_dismissal'}
        if actions and all(a.get('action')=='tap' and a.get('workflow')=='keyboard'
                and a.get('phase') in allowed and a.get(allowed[a['phase']]) for a in actions):return actions
        raise

async def _next_search_actions(agent,goal):
    from .shortcut_workflow import next_shortcut_actions
    finish_launch_observation(agent,goal,workflow='app_search')
    frame=agent.screen.frame
    app,query=goal['app'],goal['query']
    history=[s for s in agent.steps if s.get('workflow')=='app_search']
    from .app_search_keyboard import active, route_state, eligible as keyboard_eligible, entry_actions, KeyboardRouteFallback
    keyboard_state=route_state(agent,goal)
    # Preserve the input ledger. Only a failed keyboard ENTRY loses focus
    # authority; query/submit receipts remain and can never be replayed.
    if keyboard_state and keyboard_state['fallback']:
        history=[s for s in history if not s.get('_search_keyboard_entry')]
    phases=[s.get('phase') for s in history]
    if any(s.get('_search_foreground_verified') is True and 'act_s' in s for s in history):phases.append('app_ready')
    # Gate launch before learned-entry, native/web route or any model sees a
    # potentially private Spotlight panel. The old path is unchanged by default.
    from .spotlight_launch import eligible, receipt, safe_frame, fail_action
    reddit_web_route=bool(app=='Reddit' and hasattr(agent,'phone')
                          and (agent.phone.target_info() or {}).get('device')=='ipad')
    if reddit_web_route:
        from .shortcut_workflow import launch_layout_guard
        guard=await launch_layout_guard(agent,app)
        if guard:return guard
    if receipt(agent, None) and not safe_frame(agent, None, frame):
        return fail_action(agent, None, 'Spotlight privacy gate refused public-search frame')
    # Keep the private-Spotlight gate ahead of the browser handoff. Reddit's
    # route then owns one bounded layout recovery before browser/query input.
    if reddit_web_route:
        from .reddit_web import next_reddit_web_actions
        return await next_reddit_web_actions(agent,goal)
    if 'app_ready' in phases and getattr(getattr(agent,'phone',None),'calibrated',True) is False:
        raise DecisionUnavailable('Public App changed to uncalibrated geometry; no point or query sent. Use bounded Home recovery before a new task.')
    if (app=='rednote' and os.environ.get('APP_PUBLIC_SEARCH_NATIVE_LINK','0')=='1'
            and (agent.phone.target_info() or {}).get('device')=='ipad'):
        from .native_public_search import next_native_public_search_actions
        return await next_native_public_search_actions(agent,goal)
    seconds=0
    confident_negative=False
    async def judge(questions,purpose,policy=None,input_continuity=False):
        nonlocal seconds, confident_negative
        if receipt(agent, None) and not safe_frame(agent, None, frame):
            raise DecisionUnavailable('Spotlight privacy gate refused public-search model frame')
        questions={**questions,'blocked':{'type':'noul','instructions':'Is ANY blocking modal, first-use feature/Youth Mode notice, login, permission, paywall, account chooser or private-message/post/comment composer covering the public search controls?'}}
        result=await agent.client.evaluate({'policy':policy or 'app_public_search','app':app,'query':query,'purpose':purpose,
            'app_card':load_app_card(app),'current_run_input_continuity':input_continuity,
            'device_family':(agent.phone.target_info() or {}).get('device') if hasattr(agent,'phone') else None,
            'orientation':(agent.phone.target_info() or {}).get('orientation') if hasattr(agent,'phone') else None,
            'foreground_verified_this_run':'app_ready' in phases,
            'public_search_navigation_ack':any(s.get('phase') in ('search_control','search_form') and 'act_s' in s for s in history),
            'learned_search_entry_ack':any(s.get('_learned_public_search')==app and 'act_s' in s for s in history),
            'selection_ack':any(s.get('phase')=='select' and 'act_s' in s for s in history),
            'learned_editable_field_ack':any(s.get('_learned_search_control')=='public_search_field' and s.get('phase')=='focus' and 'act_s' in s for s in history),
            'field_activation_ack':any(s.get('phase')=='focus' and 'act_s' in s for s in history),
            'focus_visually_verified_before_selection':any(s.get('_search_focus_verified') and s.get('phase')=='select' and 'act_s' in s for s in history),
            'scope':'public app search only; no engagement, private messages, login or permissions'},questions,frame)
        agent.trace.write(frame,kind='app-search-check',purpose=purpose,**result)
        seconds+=result['seconds']
        confident_negative=result.get('confident_negative') is True
        if result['answers'].get('blocked',{}).get('noul',1)>.01:
            raise PublicSearchBlocked('Public search blocked or private composer visible; no further input')
        return result['answers']
    def emit(action,phase):
        if action.get('_observed_public_navigation'):
            from .navigation_observation import observation_bound
            if not observation_bound(agent.phone,agent.screen,action['_observed_public_navigation'],frame):return wait('observed_navigation_changed')
        elif action.get('_learned_public_search'):
            matched=public_search_control(agent.screen.frame,app,agent.phone.target_info(),control=action.get('_learned_search_control','public_search_entry'))
            if not matched or (matched['x'],matched['y'])!=(action['x'],action['y']):return wait('search_control_changed')
            # The registered template re-matched on the CURRENT frame at the same
            # point (a covering modal or dimming breaks the match). Only the target
            # box itself, without margin, must also be unchanged since the verdict
            # frame; full-frame/chrome/motion-area rules fail on autoplay video.
            box=matched.get('target_box')
            if (learned_target_changed(frame,agent.screen.frame,box) if box else
                    verdict_scene_changed(agent,frame,agent.screen.frame,action,scene_changed,box=None,workflow='app_search')):
                return wait('scene_changed')
            if matched.get('template'):action={**action,'_learned_control_template':matched['template']}
        elif action['action']=='tap' and isinstance(a.get('target',{}).get('box'),dict):
            # A fresh judge located this control on `frame`; like a learned control,
            # only the located box itself (no margin) must be unchanged now, so
            # autoplay thumbnails/video elsewhere no longer block the tap.
            if learned_target_changed(frame,agent.screen.frame,a['target']['box']):return wait('scene_changed')
        elif not (action['action']=='done' and action.get('_verified_public_results')) and verdict_scene_changed(
                # Non-tap actions keep the scoped full-frame gate.
                agent,frame,agent.screen.frame,action,scene_changed,
                box=None,workflow='app_search'):return wait('scene_changed')
        if action['action']=='type' and phase=='query':
            clearing={'select':None,'caret_to_end':'cmd+right','delete_to_line_start':'cmd+backspace','backspaces':4}
            # iPadOS delete-to-line-start is not Select-All, including in
            # Instagram/Douyin where Command-A must remain disabled. A tap can
            # leave the caret mid-text (TikTok kept the old tail), so move it
            # to the line end first.
            keys=[{'action':'key','combo':'cmd+right'},{'action':'key','combo':'cmd+backspace'}]+[{'action':'key','combo':'backspace'} for _ in range(4)]
            return [{**key,'workflow':'app_search','phase':'clear_query','_search_focus_verified':True,
                     '_search_clear_index':index,'think_s':round(seconds,3) if index==0 else 0,'_shot':frame}
                    for index,key in enumerate(keys)]+[
                {**action,'workflow':'app_search','phase':phase,'_search_focus_verified':True,
                 '_search_clear_sequence':clearing,'think_s':0,'_shot':frame}]
        return [{**action,'workflow':'app_search','phase':phase,'think_s':round(seconds,3),'_shot':frame}]
    def wait(phase):
        if active(agent,goal) and phase in ('search_focus','selected_search_focus','exact_query_visible','results_visible'):
            raise KeyboardRouteFallback(phase)
        if phases.count(phase)>=2:raise DecisionUnavailable('App search could not verify '+phase+'; stopped without retrying input')
        if phase=='scene_changed':start_launch_observation(agent,goal,workflow='app_search')
        return [{'action':'wait','workflow':'app_search','phase':phase,'think_s':round(seconds,3),'_shot':frame}]
    def yes(a,key):return a.get(key,{}).get('noul',0)>=.99
    def focused_field_action():
        # A resumed editor's model focus verdict can be a keyboard ring. Only
        # this run's completed field activation plus fresh focus proof permits input.
        tapped=any(s.get('phase')=='focus' and 'act_s' in s and
                   (s.get('action')=='tap' or (active(agent,goal) and s.get('_search_keyboard_entry')==app)) for s in history)
        if not tapped:
            if 'focus' in phases:return wait('search_focus')
            field=public_search_control(frame,app,agent.phone.target_info(),control='public_search_field') if hasattr(agent,'phone') else None
            if not field:return wait('search_focus')
            return emit({'action':'tap','x':field['x'],'y':field['y'],'precise':True,
                         '_learned_public_search':app,'_learned_search_control':'public_search_field'},'focus')
        return emit({'action':'type','text':query},'query')
    async def point(answer):
        if answer.get('confidence',0)<.5:raise DecisionUnavailable('No usable search point proposal; no click sent')
        b=answer['box']
        if answer['confidence']<.9:
            check=await judge({'ready':q(f'Is the point x={(b["left"]+b["right"])/2:.4f}, y={(b["top"]+b["bottom"])/2:.4f} in FULL IMAGE fractions strictly INSIDE the actual public Search query field or public Search navigation control belonging to {app}? It must NOT hit a suggestion, history row, private message, compose, profile, microphone or account control.')},'independently ground public search point')
            if not yes(check,'ready'):raise DecisionUnavailable('Search point did not pass independent visual ownership check')
        return round((b['left']+b['right'])*500),round((b['top']+b['bottom'])*500)
    q=lambda text:{'type':'noul','instructions':text}
    def focus_q(text):
        return q(text+' Require an actual text caret, selected text, or an editing menu inside this field. '
                 'A blue/colored Full Keyboard Access focus ring or outline around a field alone is NOT text-editing focus. '
                 'Field styling, search controls, suggestions and click/key ACKs alone are not editing evidence.')
    display_app=app
    public=f'Actual active foreground {display_app}, not another app or system Spotlight. Identity need not have a logo: foreground_verified_this_run plus real public_search_navigation_ack and matching App Card search-page composition are actual transition evidence, never the requested goal alone. Recheck fresh visible controls and reject any competing App. No login, permission, paywall, account chooser or private-message composer.'
    if 'app_ready' not in phases:
        if keyboard_eligible(agent,goal):
            # Reuse the existing launch/foreground/privacy checks, without the
            # pointer-only current-entry shortcut consuming Search first.
            start_launch_observation(agent,goal,workflow='app_search',current_entry=True)
            actions=await next_shortcut_actions(agent,{'app':app,'text':None})
            return [dict(a,action='wait',workflow='app_search',phase='app_ready',
                         thought='Actual target app verified before keyboard Search entry.')
                    if a['action']=='done' else a for a in actions]
        if app == 'Instagram' and hasattr(agent, 'phone'):
            # Registered multi-anchor search/results UI is independent local
            # identity evidence, including an old query. Keep the fresh modal
            # gate; this read-only readiness step grants no focus or text.
            identity = next((match for kind in ('public_search_entry', 'public_search_form_entry')
                if (match := public_search_control(frame, app, agent.phone.target_info(), control=kind))), None)
            identity = identity or _registered_search_variant(frame, app, agent.phone.target_info())
            if identity:
                await judge({}, 'check overlays on locally identified Instagram search')
                if not (public_search_control(agent.screen.frame, app, agent.phone.target_info(), control=identity['control'])
                        or _registered_search_variant(agent.screen.frame, app, agent.phone.target_info())):
                    return wait('current_entry_ownership')
                agent.trace.write(kind='app-search-local-identity', app=app, template=identity.get('template'),
                    scope='Registered Instagram search navigation anchors; no focus, query or result completion proof.')
                return emit({'action': 'wait', '_search_foreground_verified': True}, 'app_ready')
        if eligible(agent, {'app': app, 'text': None}):
            start_launch_observation(agent,goal,workflow='app_search',current_entry=True)
            actions=await next_shortcut_actions(agent, {'app':app, 'text':None})
            return [dict(a, action='wait', workflow='app_search', phase='app_ready',
                         thought='Actual target app verified after private local launch.')
                    if a['action']=='done' else a for a in actions]
        # Search-only entry from current real App pixels, not a remembered App
        # name. Keep an independent strict modal/ownership check and the same
        # learned-target checks before movement and before click. This never
        # marks a general Open request complete and never authorizes typing.
        current=None
        if hasattr(agent,'phone'):
            for kind in ('public_search_field','public_search_entry','public_search_form_entry'):
                current=public_search_control(frame,app,agent.phone.target_info(),control=kind)
                if current:break
        if current:
            questions={'ready':q(f'The fresh image independently matches registered multiple {app} public navigation and Search-control anchors. Is the ACTUAL active foreground this public {app} view, with the Search navigation available and NO competing app or covering overlay? Template match alone and requested goal are not proof. Reject any dimming, login, modal, private composer or system interface.')}
            if current['control']=='public_search_field':
                questions.update(focused=focus_q('Is this ACTUAL app-owned public query editor already visibly focused? Never infer focus from the template, goal, a static query label or historical tap.'))
            a=await judge(questions,'verify current learned public search entry before relaunch')
            if yes(a,'ready'):
                agent.trace.write(kind='app-search-current-entry',app=app,evidence='Fresh learned navigation plus independent strict foreground/modal judgment; no Home or App-open completion inferred.')
                return emit({'action':'tap','x':current['x'],'y':current['y'],'precise':True,'_learned_public_search':app,'_learned_search_control':current['control'],'_search_foreground_verified':True},'focus' if current['control']=='public_search_field' else 'search_form' if current['control']=='public_search_form_entry' else 'search_control')
            return wait('current_entry_ownership')
        # Sample already arriving frames during read-only foreground checks,
        # as the following 0.15 s handoff alone may contain fewer than 3 frames.
        start_launch_observation(agent,goal,workflow='app_search',current_entry=True)
        actions=await next_shortcut_actions(agent,{'app':app,'text':None})
        return [dict(a,action='wait',workflow='app_search',phase='app_ready',thought='Actual target app verified. Locate its own public search next.') if a['action']=='done' else a for a in actions]
    keyboard=await entry_actions(agent,goal,history,judge,emit)
    if keyboard is not None:return keyboard
    # A learned editable-field locator is distinct from Search navigation.
    # Always place the caret once in a cold editor, even if a model calls a
    # keyboard focus ring focused. Never re-tap after focus or text delivery.
    if not any(p in phases for p in ('focus','select','query','submit')) and hasattr(agent,'phone'):
        field=public_search_control(frame,app,agent.phone.target_info(),control='public_search_field')
        if field:
            reuse_visible=(os.environ.get('APP_SEARCH_REUSE_VISIBLE_FOCUS','0')=='1'
                           and agent.phone.target_info().get('device')=='ipad' and app!='Instagram')
            if app in ('App Store','Douyin') or reuse_visible:
                editor_controls=('Filters, magnifier, query/Clear' if app=='App Store' else
                                 'Back, top query/Chinese Search and bottom microphone' if app=='Douyin' else
                                 'registered app-owned public query editor navigation/query/Clear anchors')
                a=await judge({'ready':q(f'This run verified {app} foreground and its registered public editor controls match fresh pixels ({editor_controls}). Is this SAME public query editor visible, with no competing App, private composer or modal? Missing branding on a full-screen query-only editor is normal. Never interpret that alone as another App.'),
                    'focused':focus_q('Is the ACTUAL top public query field already focused for text editing? Never infer active focus from a task, placeholder or click ACK.')},'check existing learned editor focus before unnecessary movement',policy='app_public_search_focus')
                if not yes(a,'ready'):return wait('known_editor_ownership')
            agent.trace.write(kind='app-search-learned-control',app=app,control=field['control'],evidence='Actual query editor UI anchors matched. Field tap only; independent focus still required.')
            return emit({'action':'tap','x':field['x'],'y':field['y'],'precise':True,'_learned_public_search':app,'_learned_search_control':'public_search_field'},'focus')
    # Already independently verified App foreground; local observation only
    # proposes one header position. Independent point/owner/modal judgment is
    # still required, and this path never supplies focus or text permission.
    if not any(p in phases for p in ('search_control','search_form','focus','select','query','submit','public_search_control')):
        from .navigation_observation import header_navigation_proposal
        proposal=await header_navigation_proposal(agent,frame)
        if proposal:
            seconds+=proposal['seconds']
            agent.trace.write(kind='app-search-navigation-observation',source='local_vision_fast_ocr',
                seconds=round(proposal['seconds'],4),box=proposal['box'],scope='Position proposal only; no literal text/focus/ownership inferred.')
            a=await judge({'ready':q(f'Is the point x={proposal["x"]/1000:.4f}, y={proposal["y"]/1000:.4f} strictly INSIDE the ACTUAL public Search navigation/query control belonging to {app} in this fresh image? The point was proposed by local text observation, which is NOT proof. Verify actual App ownership and control. An old query may be visible. Reject a static title, result/history/suggestion, private composer, URL, system Spotlight, profile, microphone or account control.')},'independently verify observed public Search navigation point')
            if yes(a,'ready'):
                return emit({'action':'tap','x':proposal['x'],'y':proposal['y'],'precise':True,
                    '_observed_public_navigation':{'box':proposal['box'],'binding':proposal['binding']}},'search_control')

    if 'submit' in phases:
        # A fast Jev rejection must not burn the two waits on loading frames.
        # Scope state to the actual submit receipt, never another search/run.
        submit=next((s for s in reversed(history) if s.get('phase')=='submit' and 'act_s' in s),None)
        pace=getattr(agent,'_app_search_results_pace',None)
        if (os.environ.get('JEV_CONFIDENT_NEGATIVE','0')=='1' and pace
                and pace['submit'] is submit and submit is not None):
            delay=max(0,pace['mono']+1.5-time.monotonic())
            if delay:await asyncio.sleep(delay)
            fresh=getattr(agent.screen,'fresh',None)
            newer=await fresh(since=max(pace['epoch']+1.5,pace['frame_at']),timeout=.8) if fresh else None
            if newer is None or getattr(agent.screen,'updated',0)<=max(pace['epoch']+1.5,pace['frame_at']):
                agent.trace.write(kind='app-search-results-pace',reason='waiting_for_newer_frame')
                return wait('results_frame')
            frame=newer
        check_mono,check_epoch=time.monotonic(),time.time()
        from .app_query_text import reuse_landscape_results, remember_landscape_results, verify_landscape_results
        local=await verify_landscape_results(agent,goal,frame)
        if local is None:
            local=await reuse_landscape_results(agent,goal,frame)
        if local is not None:
            seconds+=local['seconds']
            return emit({'action':'done','_verified_public_results':True,'execution_route':'native_app','answer':f'{display_app} public search results visibly verified for {query!r}. Nothing liked, followed, sent or published.'},'verified')
        a=await judge({'results':q(f'{public} Does this app visibly show public search RESULTS for exact query {query!r}, with at least one actual result or explicit no-results state? The query merely typed in a field, autocomplete suggestions, Home feed or loading placeholder do NOT count.')},'verify public search results')
        if yes(a,'results'):
            remember_landscape_results(agent,goal,frame)
            return emit({'action':'done','_verified_public_results':True,'execution_route':'native_app','answer':f'{display_app} public search results visibly verified for {query!r}. Nothing liked, followed, sent or published.'},'verified')
        if (os.environ.get('JEV_CONFIDENT_NEGATIVE','0')=='1' and submit is not None
                and (confident_negative or (pace and pace['submit'] is submit))):
            agent._app_search_results_pace={'submit':submit,'mono':check_mono,
                'epoch':max(check_epoch,submit.get('t',check_epoch)),
                'frame_at':getattr(agent.screen,'updated',0)}
            agent.trace.write(kind='app-search-results-pace',reason='confident_negative',minimum_s=1.5)
        return wait('results_visible')
    if 'query' in phases:
        from .app_query_text import verify_local_query
        local=await verify_local_query(agent,goal,frame)
        if local is not None:
            seconds+=local['seconds']
            return emit({'action':'key','combo':'enter'},'submit')
        from .app_query_text import verify_landscape_literal
        local=await verify_landscape_literal(agent,goal,frame)
        if local is not None:
            seconds+=local['seconds']
            a=await judge({'ready':focus_q(f'{public} Is the SAME app-owned PUBLIC SEARCH query field still focused and actively being edited, ready for hardware Return? Verify current ownership, rather than a static query label on results. Exclude every message/comment/post/URL/password field, system Spotlight, competing app and modal. Local OCR checked only text and grants no focus or ownership proof.')},'verify query editor after local landscape literal check')
            if not yes(a,'ready'):return wait('exact_query_visible')
            return emit({'action':'key','combo':'enter'},'submit')
        owned_form=public_search_control(frame,app,agent.phone.target_info(),control='public_query_text') if hasattr(agent,'phone') else None
        typed=next((s for s in reversed(history) if s.get('phase')=='query' and 'act_s' in s),None)
        selected_this_run=any(s.get('phase')=='select' and s.get('_search_focus_verified') and 'act_s' in s for s in history)
        if owned_form and typed and typed.get('text')==query and (typed.get('_search_was_empty_verified') or (app=='App Store' and selected_this_run) or (app=='Douyin' and typed.get('_search_selection_pixels_verified'))):
            # Current local anchors prove App-owned form, while this run proved
            # empty focused input before typing. Caret blinking is not a new
            # failure of that transition. The actual literal still needs a fresh
            # independent image judgment; never infer it from the ACK alone.
            question=f'Current {app} public Search form matches independently measured app-specific public query-editor controls. This run independently verified the focused Search field, then verified it empty or selected its old query before acknowledged typing. Inspect the ACTUAL text pixels: does the top public Search query show exactly {query!r}, with no missing or extra characters? Letter case is NOT a difference: iPadOS may auto-capitalize the first letter (e.g. "Ielts" for "ielts"). Reject placeholder, other app, autocomplete-only text or composer. A blinking caret may be invisible in this frame.'
            query_question=q(question)
        else:
            question=f'{public} Is the app-owned PUBLIC SEARCH field focused and showing exactly {query!r}, ready to submit a search via hardware Return? Letter case is NOT a difference: iPadOS may auto-capitalize the first letter (e.g. "Ielts" for "ielts"). Exclude message, comment, caption, post, password, URL, system Spotlight and any text composer.'
            query_question=focus_q(question)
        # Input continuity is execution evidence, not a text ACK alone. Only
        # this run's actual independently verified focus/selection or empty-field
        # proof plus completed typing qualifies. Current ownership/literal/modal
        # still receive independent fresh-image checks before Return.
        completed=[s for s in history if 'act_s' in s and s.get('action') not in ('wait','done')]
        continuity=bool(typed and typed.get('text')==query and completed and completed[-1] is typed
            and len([s for s in history if s.get('phase')=='query' and 'act_s' in s])==1
            and (typed.get('_search_was_empty_verified') or (len(completed)>=2 and completed[-2].get('phase')=='select' and completed[-2].get('_search_focus_verified') is True)))
        if os.environ.get('HID_SEARCH_INPUT_CONTINUITY','0')=='1' and continuity:
            a=await judge({'ready':focus_q(f'This task already independently verified {app} public Search focus before actual completed typing. Is the SAME app-owned public query editor still visibly focused, with no competing app or editable control? Current task foreground and navigation evidence may establish identity without a logo, but not current text-editing focus. Reject unrelated editors, Spotlight, URL/password, comment/post/DM fields or an ambiguous transition.'),
                'literal':q(f'Inspect only the actual current app-owned Search QUERY text, not suggestions/history. Is its entire visible value EXACTLY {query!r}, with no missing, additional or duplicated characters? Letter case is NOT a difference: iPadOS may auto-capitalize the first letter (e.g. "Ielts" for "ielts"). Never infer text from ACK or intended goal.'),
                'editing_mode':{'type':'choice','instructions':'Classify the CURRENT public Search interaction state. A matching query label alone is not an active editor. Context that typing previously occurred cannot override a visible transition to results.',
                    'criteria':{'query_editor':'The app-owned Search query is actively being edited: require an actual text caret, selected text, or an editing menu inside the field. A blue/colored keyboard focus ring or outline alone is NOT text-editing focus. Results behind an active editing overlay do not invalidate that overlay.',
                        'results':'Public search results are displayed with a static query label and NO active query editing/suggestion interface.',
                        'other':'Wrong app, system Spotlight, private composer, modal or uncertain editing state.'}}},'verify actual query with current-run input continuity',policy='app_public_search_literal',input_continuity=True)
            mode=a.get('editing_mode',{})
            editor_verified=(mode.get('choice')=='query_editor' and probability(mode.get('confidence')) and mode['confidence']>=.95)
            if not (yes(a,'ready') and yes(a,'literal') and editor_verified):return wait('exact_query_visible')
        else:
            a=await judge({'ready':query_question},'verify query before search submission')
            if not yes(a,'ready'):return wait('exact_query_visible')
        return emit({'action':'key','combo':'enter'},'submit')
    if 'select' in phases:
        # Compatibility with a previously acknowledged select step: reverify
        # focus, never infer it from selection ACK or require an empty value.
        a=await judge({'ready':focus_q(f'{public} Is the SAME app-owned PUBLIC SEARCH query field actually focused for hardware keyboard input, with no competing editor, modal or system Spotlight? A previous selection ACK alone is not focus proof.')},'verify selected search field')
        if not yes(a,'ready'):return wait('selected_search_focus')
        return focused_field_action()
    if 'focus' in phases:
        from .focus_local import verify_local_focus
        local=await verify_local_focus(agent,goal,frame)
        if local is not None:
            seconds+=local['seconds']
            return focused_field_action()
        if app in ('YouTube','App Store','Douyin') and any(s.get('_learned_search_control')=='public_search_field' and 'act_s' in s for s in history):
            a=await judge({'ready':q(f'The foreground {app} was independently verified this run, then its registered public query-editor UI anchors matched before/after the field click. Inspect this fresh frame: is the SAME public query editor still visible (YouTube: Back/query/Clear, App Store: Filters/query/magnifier/Clear; Douyin: Back/query/Chinese Search and bottom microphone, optionally public search suggestions), with no competing App or private composer? Do not require a logo on a full-screen query editor.'),
                'focused':focus_q('Is the query bar visibly active for text editing? A mere static query label is NOT enough. Never infer focus from the requested task or click ACK alone.')},'verify learned query ownership and focus',policy='app_public_search_focus')
            if yes(a,'ready') and yes(a,'focused'):return focused_field_action()
            return wait('search_focus')
        a=await judge({'ready':focus_q(f'{public} Is the app-owned PUBLIC SEARCH field really focused for hardware keyboard input? An acknowledged field tap alone is not focus evidence, even with an external keyboard. Do not infer focus from goal/history alone. For Instagram require the actual EDITABLE query form after a separate field tap. Its Explore Search with Meta AI bar is navigation only; a click that merely opens the form is NOT focus. Never approve a comment/message/post composer, URL, password or system Search.')},'verify public search focus',policy='app_public_search_focus')
        if yes(a,'ready'):return focused_field_action()
        return wait('search_focus')
    if 'search_control' in phases or 'search_form' in phases:
        local=public_search_control(frame,app,agent.phone.target_info(),control='public_search_form_entry') if hasattr(agent,'phone') else None
        if local and any(s.get('_learned_public_search')==app and s.get('_learned_search_control')=='public_search_form_entry' and s.get('phase')=='search_form' and 'act_s' in s for s in history):
            # Pointer/hover changes can satisfy generic observation before the
            # App has finished navigation. An ACKed form-entry tap must not be
            # repeated on that stale results frame. Wait for actual UI departure,
            # then retain normal independent editable-field/focus checks.
            await asyncio.sleep(.35)
            return wait('form_navigation_transition')
        if local:
            agent.trace.write(kind='app-search-learned-control',app=app,control=local['control'],evidence='Actual public Explore Search entry and independent App navigation anchors match. No text authorization yet.')
            return emit({'action':'tap','x':local['x'],'y':local['y'],'precise':True,'_learned_public_search':app,'_learned_search_control':'public_search_form_entry'},'search_form')
        a=await judge({'ready':q(f'{public} Is its public search page or search panel visible?'),
            'focused':focus_q(f'Is the editable public search query field owned by {app} ALREADY focused for hardware keyboard input? Never infer from an unfocused search icon.'),
            'target':{'type':'box','instructions':f'Locate the actual editable PUBLIC SEARCH query field owned by {app}. Not any compose, message, URL, system Search or comment field.'}},'locate editable app search')
        if (app == 'X' and 'explore_scroll_top' not in phases
                and any(s.get('phase') in ('search_control', 'search_form') and 'act_s' in s for s in history)
                and (not yes(a, 'ready') or a.get('target', {}).get('confidence', 0) < .5)):
            # Explore can restore well below its query field. Re-select its
            # registered navigation tab once, then rerun field/focus checks.
            # Consume even an uncertain receipt, never repeat on stale frames.
            local = public_search_control(frame, app, agent.phone.target_info()) if hasattr(agent, 'phone') else None
            local = local or (_registered_search_variant(frame, app, agent.phone.target_info()) if hasattr(agent, 'phone') else None)
            if local:
                # Use the existing generic precise-tap dispatch, with its
                # target-box freshness gate. Learned-entry dispatch deliberately
                # accepts only search_control, never this recovery phase.
                a = {**a, 'target': {'box': local['target_box']}}
                return emit({'action': 'tap', 'x': local['x'], 'y': local['y'], 'precise': True}, 'explore_scroll_top')
        if not yes(a,'ready'):return wait('search_page')
        x,y=await point(a['target'])
        from PIL import Image
        import io
        return emit({'action':'tap','x':x,'y':y,'precise':True,
            '_search_field_box':dict(a['target']['box']),
            '_search_field_size':list(Image.open(io.BytesIO(frame)).size)},'focus')
    local=public_search_control(frame,app,agent.phone.target_info()) if hasattr(agent,'phone') else None
    if local:
        agent.trace.write(kind='app-search-learned-control',app=app,control=local['control'],evidence='Actual App identity/navigation anchors and Search glyph match local templates; video content ignored.')
        return emit({'action':'tap','x':local['x'],'y':local['y'],'precise':True,'_learned_public_search':app,'_learned_search_control':'public_search_entry'},'search_control')
    # The App may reopen directly in Explore from an earlier completed or
    # failed request. Recognize its actual query-entry layout independently.
    local=public_search_control(frame,app,agent.phone.target_info(),control='public_search_form_entry') if hasattr(agent,'phone') else None
    if local:
        agent.trace.write(kind='app-search-learned-control',app=app,control=local['control'],evidence='Actual reopened Explore layout, not an assumed Home feed. Navigation only.')
        return emit({'action':'tap','x':local['x'],'y':local['y'],'precise':True,'_learned_public_search':app,'_learned_search_control':'public_search_form_entry'},'search_form')
    a=await judge({'focused':focus_q(f'Is the actual public search query field owned by {app} ALREADY focused for hardware keyboard input? Empty field and hidden keyboard are allowed; a mere Search navigation icon is not focus.'),'ready':q(f'{public} Is either a PUBLIC SEARCH navigation control OR an already-open editable PUBLIC SEARCH query field visible and accessible? This can be the Search/Explore tab, magnifying glass, search bar, or actual query editor. Do not reject an already-open query editor merely because it is past navigation. Never a DM search or compose control.'),
        'target':{'type':'box','instructions':f'Locate only {app} PUBLIC SEARCH navigation control (Search/Explore tab, magnifying glass or public search bar). Exclude DMs, Share, Post, Reply, Like, Follow, profile/account switcher and system Spotlight.'}},'locate public search entry')
    if not yes(a,'ready'):return wait('public_search_control')
    x,y=await point(a['target']);return emit({'action':'tap','x':x,'y':y,'precise':True},'search_control')


def _registered_search_variant(frame, app, target):
    """Selected-tab variant of existing registered landscape Search controls.

    Identity/navigation only: two unchanged independent anchors plus the
    existing bright Search glyph geometry. Never a focus or results proof.
    Home can change from filled to outline on these actual restored pages.
    """
    import io
    from PIL import Image
    from .app_controls import registry, _patches_match, bright_glyphs_match
    if app not in ('Instagram', 'X') or (target or {}).get('orientation') != 'landscape':
        return None
    files = registry(target)
    path = (files or {}).get((app, 'public_search_entry'))
    if path is None or isinstance(path, tuple):
        return None
    try:
        data = json.loads(path.read_text())
        image = Image.open(io.BytesIO(frame)).convert('RGB')
        if data['app'] != app or list(image.size) != data['reference_size']:
            return None
        if any(_patches_match(image, path, data, group) for group in data.get('blockers', [])):
            return None
        # Only the known Home selected-state patch may differ. All remaining
        # registered anchors retain their original exact RGB rule.
        anchors = [n for n in data['anchors'] if n != 'home']
        if len(anchors) < 2 or not _patches_match(image, path, data, anchors):
            return None
        spec = data['patches'][data['target']]
        reference = (path.parent / spec['file']).resolve()
        if not reference.is_relative_to(path.parent.resolve()):
            return None
        with Image.open(reference) as stored:
            if not bright_glyphs_match(stored.convert('RGB'), image.crop(tuple(spec['box']))):
                return None
        w, h = image.size
        l, t, r, b = spec['box']
        return {'app': app, 'control': 'public_search_entry', 'template': path.name,
                'x': round(data['center'][0]*1000), 'y': round(data['center'][1]*1000),
                'target_box': {'left': l/w, 'top': t/h, 'right': r/w, 'bottom': b/h}}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def learned_target_changed(before,after,box):
    """True if the learned control's own box (no margin) changed, or cannot be checked."""
    import math
    from .scene_scope import _image,_delta
    try:
        coords=[box[k] for k in ('left','top','right','bottom')]
        if not all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in coords):return True
        a,b=_image(before),_image(after)
        if a.size!=b.size:return True
        w,h=a.size;l,t,r,bt=coords
        crop=(math.floor(l*w),math.floor(t*h),math.ceil(r*w),math.ceil(bt*h))
        if crop[2]<=crop[0] or crop[3]<=crop[1]:return True
        return _delta(a.crop(crop),b.crop(crop))
    except (OSError,ValueError,TypeError,KeyError):
        return True
