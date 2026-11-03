"""Experimental fixed public-search URI route on the existing owned search task.

Disabled unless explicitly enabled by app_search. Safari uses its independently observed address rectangle, including a floating
window. The Chrome custom-URI candidate failed physically and is not retried.
No fallback/retyping after URI dispatch, no arbitrary link and no publication.
"""
from .decision import DecisionUnavailable, probability
from .jev_agent import scene_changed
import io
from PIL import Image

from .public_search_links import build_public_search_link

WORKFLOW = 'native_public_search'
ROUTE = 'safari_rednote_public_link'
EDGE_CHARS = 21


def valid_address_box(box,frame):
    if not isinstance(box,dict) or not all(probability(box.get(k)) for k in ('left','right','top','bottom')):return False
    if not (box['left']<box['right'] and box['top']<box['bottom']):return False
    w,h=Image.open(io.BytesIO(frame)).size
    return (box['right']-box['left'])*w>=160 and 12<=(box['bottom']-box['top'])*h<=120


def address_band(frame,box):
    # Full-frame privacy check must precede cropping; an omitted notification
    # header is not permission to send the remaining private text.
    from .frame_privacy import check_cloud_frame
    check_cloud_frame(frame)
    im=Image.open(io.BytesIO(frame));w,h=im.size
    if not valid_address_box(box,frame):raise DecisionUnavailable('Invalid observed Safari address rectangle')
    out=io.BytesIO();im.crop(tuple(round(box[k]*(w if k in ('left','right') else h)) for k in ('left','top','right','bottom'))).save(out,format='JPEG',quality=95)
    return out.getvalue()


def accepted_native_state(answers):
    def yes(key):
        value=answers.get(key,{}).get('noul')
        return probability(value) and value>=.99
    blocked=answers.get('blocked',{}).get('noul')
    if not probability(blocked) or blocked>.01:return None
    if all(yes(k) for k in ('native','literal','results')):return 'results'
    target=answers.get('target',{})
    if yes('open_prompt') and probability(target.get('confidence')) and target['confidence']>=.9:
        box=target.get('box',{})
        if (all(probability(box.get(k)) for k in ('left','right','top','bottom'))
                and box['left']<box['right'] and box['top']<box['bottom']):return 'open_prompt'
    return None


def device_binding(phone):
    target=phone.target_info() or {}
    return tuple(target.get(k) for k in ('device','orientation','calibration_key','width','height'))+(getattr(getattr(phone,'device',None),'links',None),)


async def next_native_public_search_actions(agent,goal):
    link=build_public_search_link(goal)
    if not link:raise DecisionUnavailable('No allowlisted native public search route')
    # The initial narrow-window trial requires two overlapping21-char edges.
    # Longer encoded input is rejected rather than inferred from clipped text.
    if len(link['url'])>2*EDGE_CHARS:raise DecisionUnavailable('Native search URI exceeds verified address capacity')
    target=agent.phone.target_info() or {}
    if (target.get('device')!='ipad' or target.get('orientation')!='portrait'
            or not agent.phone.calibrated or not agent.screen.live):
        raise DecisionUnavailable('Native search route needs a live independently calibrated portrait iPad')
    frame=agent.screen.frame
    steps=[s for s in agent.steps if s.get('workflow')==WORKFLOW and 'act_s' in s]
    phases=[s.get('phase') for s in steps]
    if not steps:agent._native_public_route_binding=device_binding(agent.phone)
    binding=getattr(agent,'_native_public_route_binding',None)
    if device_binding(agent.phone)!=binding:raise DecisionUnavailable('Native public route device binding changed; no replay')
    judgment_s=0
    q=lambda text:{'type':'noul','instructions':text}
    def wait(reason):
        if sum(s.get('phase')==reason for s in agent.steps)>=2:
            raise DecisionUnavailable('Native public search could not verify '+reason+'; no replay')
        return [dict(action='wait',workflow=WORKFLOW,phase=reason,think_s=round(judgment_s,3),_shot=frame)]
    def emit(action,phase,seconds=0):
        if not agent.screen.live or not agent.phone.calibrated or device_binding(agent.phone)!=binding:
            raise DecisionUnavailable('Native public route binding changed during judgment; no replay')
        if scene_changed(frame,agent.screen.frame):return wait('scene_changed')
        return [{**action,'workflow':WORKFLOW,'phase':phase,'think_s':seconds,'_shot':frame,
                 'execution_route':ROUTE,'thought':'Allowlisted public rednote search via independently verified Safari address; no engagement.'}]
    async def address(expected=None,edge=None):
        nonlocal judgment_s
        box=getattr(agent,'_native_public_address_box',None)
        if expected and not box:raise DecisionUnavailable('Missing observed Safari address rectangle')
        picture=address_band(frame,box) if expected else frame
        questions={
            'ready':q('Is this the ACTUAL Safari address field in EDIT mode with a real insertion caret or text selection INSIDE it? For a crop require actual visible caret/selection pixels, not its rounded border, gray placeholder or keyboard shortcut history. The full-frame ownership check is separate. Reject page Search or a competing editable control.'),
            'literal':q(('Does the actual editable URL show exactly '+repr(expected[:EDGE_CHARS] if edge=='prefix' else expected[-EDGE_CHARS:])+' at its '+edge+' through that edge? No missing/extra characters or selected history completion. Opposite-edge clipping is permitted. Suggestions and requested context are never proof.') if expected else 'Is the actual Safari editable address EMPTY, showing only its placeholder with no old address or selected history completion?'),
            'blocked':q('Is a modal, login, permission, account chooser, Spotlight or competing editable field obstructing the actual Safari address?')}
        if not expected:
            questions['owner']=q('Is this actual visible empty input the Safari BROWSER ADDRESS field of the foremost visible Safari window, with no competing window or system Search? This asks control ownership only, NOT keyboard focus. Reject page Search, private composer, password or another App.')
            questions['target']={'type':'box','instructions':'Locate the WHOLE actual Safari editable address input rectangle, not just its placeholder letters, in FULL IMAGE fractions. Include all its visible text capacity but exclude adjacent menu/close controls. If absent return confidence0 and a valid small dummy rectangle; it will never authorize input.'}
        result=await agent.client.evaluate({'policy':'native_browser_address' if expected else 'native_browser_address_observe','execution_route':ROUTE,
                'expected_address':expected,'visible_edge':edge},questions,picture)
        judgment_s+=result['seconds']
        if not agent.screen.live or not agent.phone.calibrated or device_binding(agent.phone)!=binding:
            raise DecisionUnavailable('Native public route binding changed during judgment; no replay')
        agent.trace.write(picture,kind='native-search-address',phase=edge or 'empty',**result)
        a=result['answers']
        required=('ready','literal') if expected else ('owner','literal')
        ok=all(probability(a.get(k,{}).get('noul')) and a[k]['noul']>=.99 for k in required)
        blocked=a.get('blocked',{}).get('noul')
        if not (ok and probability(blocked) and blocked<=.01):return None
        if not expected:
            observed=a.get('target',{})
            if not probability(observed.get('confidence')) or observed['confidence']<.5 or not valid_address_box(observed.get('box'),frame):return None
            b=observed['box']
            if observed['confidence']<.9:
                # Same existing public-search rule: a low-confidence box is
                # only a proposal until a separate exact-point ownership check.
                point=await agent.client.evaluate({'policy':'native_browser_address_point','execution_route':ROUTE},
                    {'ready':q(f'Is the point x={(b["left"]+b["right"])/2:.4f}, y={(b["top"]+b["bottom"])/2:.4f} in FULL IMAGE fractions strictly INSIDE the actual Safari BROWSER ADDRESS input of its foreground window? This is navigation ownership, NOT keyboard focus. Reject page Search, menus, closing controls, password/private composers or another app.'),
                     'blocked':q('Is any competing window, system Search, modal, login or permission covering that exact browser-address point?')},frame)
                judgment_s+=point['seconds'];agent.trace.write(frame,kind='native-search-address-point',**point)
                pa=point['answers']
                if not (probability(pa.get('ready',{}).get('noul')) and pa['ready']['noul']>=.99
                        and probability(pa.get('blocked',{}).get('noul')) and pa['blocked']['noul']<=.01):return None
            agent._native_public_address_box=b
            focus=probability(a.get('ready',{}).get('noul')) and a['ready']['noul']>=.99
            if not focus:
                # Resolution differential: real tiny carets disappeared in
                # whole-screen judgments. Read-only ROI adds visible proof;
                # owner/empty/modal/point gates still come from the full frame.
                crop=address_band(frame,b)
                local=await agent.client.evaluate({'policy':'native_browser_address','execution_route':ROUTE,
                    'purpose':'Actual address insertion evidence at native crop resolution; full-frame ownership separately checked.'},
                    {'ready':q('Is an ACTUAL BLUE insertion caret or visible text selection present INSIDE this Safari address input? Require actual caret/selection pixels. A gray rounded border, placeholder or requested keyboard command is not proof. Full-frame Safari ownership is separate, not focus evidence.'),
                     'literal':q('Is this actual editable address EMPTY, showing only its gray Search or enter website placeholder and no typed URL/query or selected old address?'),
                     'blocked':q('Is the input itself obscured or visibly a password, private composer or competing editable field instead of that address?')},crop)
                judgment_s+=local['seconds'];agent.trace.write(crop,kind='native-search-address-focus-roi',**local)
                la=local['answers'];focus=all(probability(la.get(k,{}).get('noul')) and la[k]['noul']>=.99 for k in ('ready','literal')) and probability(la.get('blocked',{}).get('noul')) and la['blocked']['noul']<=.01
            result={**result,'seconds':judgment_s}
            return {**result,'focus_verified':focus}
        return {**result,'focus_verified':probability(a.get('ready',{}).get('noul')) and a['ready']['noul']>=.99}
    if not steps:return emit({'action':'key','combo':'cmd+h'},'home')
    if 'safari_ready' not in phases:
        from .shortcut_workflow import next_shortcut_actions
        actions=await next_shortcut_actions(agent,{'app':'Safari','text':None})
        complete=next((a for a in actions if a.get('action')=='done'),None)
        return emit({'action':'wait'},'safari_ready',complete.get('think_s',0)) if complete else actions
    if 'new_tab' not in phases:return emit({'action':'key','combo':'cmd+t'},'new_tab')
    if 'url_focus' not in phases:return emit({'action':'key','combo':'cmd+l'},'url_focus')
    if 'url_type' not in phases:
        if 'address_activate' in phases and 'address_activate_focus' not in phases:
            return emit({'action':'key','combo':'cmd+l'},'address_activate_focus')
        result=await address()
        if not result:return wait('empty_safari_address')
        if result['focus_verified']:return emit({'action':'type','text':link['url']},'url_type',result['seconds'])
        if 'address_activate' in phases:return wait('empty_safari_address')
        b=agent._native_public_address_box
        return emit({'action':'tap','x':round((b['left']+b['right'])*500),
                     'y':round((b['top']+b['bottom'])*500),'precise':True,
                     '_native_public_address':True,'_control_box':b},'address_activate',result['seconds'])
    if 'url_left' not in phases:return emit({'action':'key','combo':'cmd+left'},'url_left')
    if 'url_prefix' not in phases:
        result=await address(link['url'],'prefix')
        return emit({'action':'key','combo':'cmd+right'},'url_prefix',result['seconds']) if result else wait('exact_uri_prefix')
    if 'url_submit' not in phases:
        result=await address(link['url'],'suffix')
        return emit({'action':'key','combo':'enter'},'url_submit',result['seconds']) if result else wait('exact_uri_suffix')
    result=await agent.client.evaluate({'policy':'native_public_search_result','app':'rednote','query':link['query'],
            'execution_route':ROUTE,'reference':link['reference']},
        {'native':q('Is the actual foreground the NATIVE rednote public search page, not Chrome/Safari, a typed URL, system Search or private composer?'),
         'literal':q('Does the actual native rednote editable/public search query show EXACTLY '+repr(link['query'])+'? Never infer from the requested URI or old results.'),
         'results':q('Are actual native rednote PUBLIC search result cards visibly loaded for this query, not an old page, error or loading skeleton?'),
         'open_prompt':q('Is the ONLY covering dialog the browser navigation confirmation explicitly asking to open this page in rednote? Reject any permissions, clipboard access, tracking, login, different destination or account dialog. A generic Open label alone is not proof.'),
         'blocked':q('Is any OTHER modal, permission request, login, clipboard access request, account chooser, private message/post/comment composer or competing window obstructing navigation? The exact browser Open-in-rednote navigation confirmation alone is not a permission request.')},frame)
    judgment_s+=result['seconds']
    agent.trace.write(frame,kind='native-search-result',**result)
    state=accepted_native_state(result['answers'])
    if state=='results':
        return emit({'action':'done','_verified_public_results':True,
                     'answer':'Native rednote query and public results independently verified. Nothing published.'},'verified',result['seconds'])
    a=result['answers']
    is_open=(probability(a.get('open_prompt',{}).get('noul')) and a['open_prompt']['noul']>=.99
             and probability(a.get('blocked',{}).get('noul')) and a['blocked']['noul']<=.01)
    if is_open and 'open_native' not in phases:
        point=await agent.client.evaluate({'policy':'native_public_search_open','app':'rednote','execution_route':ROUTE},
            {'ready':q('Is the actual covering browser navigation dialog explicitly asking to Open THIS page in rednote? Never approve generic Open or a different destination.'),
             'blocked':q('Is this instead a permission, clipboard access, tracking, account, login or different navigation request?'),
             'target':{'type':'box','instructions':'Locate ONLY that exact browser Open-in-rednote affirmative navigation button in FULL IMAGE fractions. No permission or clipboard control. If absent confidence0 and valid dummy rectangle.'}},frame)
        judgment_s+=point['seconds']
        agent.trace.write(frame,kind='native-search-open-point',**point)
        pa=point['answers'];merged={**a,'target':pa.get('target',{})}
        if not (probability(pa.get('ready',{}).get('noul')) and pa['ready']['noul']>=.99
                and probability(pa.get('blocked',{}).get('noul')) and pa['blocked']['noul']<=.01
                and accepted_native_state(merged)=='open_prompt'):return wait('exact_native_open_button')
        b=pa['target']['box']
        return emit({'action':'tap','x':round((b['left']+b['right'])*500),
                     'y':round((b['top']+b['bottom'])*500),'precise':True,'_native_public_open':True,'_control_box':b},'open_native',result['seconds']+point['seconds'])
    return wait('native_query_and_results')
