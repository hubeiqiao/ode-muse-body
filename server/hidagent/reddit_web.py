"""Bounded public Reddit search in Chrome; native Reddit breaks AirPlay.
Only a new tab, public fixed-host URL and independent pixel checks. No old tabs,
network page retrieval, account actions or automatic typing replay.
"""
import io
import os
from urllib.parse import quote
from PIL import Image
from .decision import DecisionUnavailable
from .ui_guard import control_region_matches
from .local_text import LocalTextError, LocalTextReader

URL='https://reddit.com/search'
ROUTE='chrome_reddit_web'


async def exact_local_address(agent, frame, expected):
    """Read only the omnibox, never tabs, suggestions or page content.

    This is literal evidence after a one-shot URL probe, not generic OCR focus
    proof. A missing/partial/wrong reading cannot authorize Return or retyping.
    """
    reader = getattr(agent, 'query_text_reader', None)
    owned = reader is None
    if owned:
        reader = LocalTextReader([os.environ.get('LOCAL_OCR_BIN',
            'vision-ocr'), '--serve'])
    try:
        observed = await reader.read(address_band(frame), mode='fast')
        rows = observed.get('rows', [])
        valid = [r for r in rows if r.get('confidence', 0) >= .95
                 and 0 <= r.get('box', {}).get('left', -1) < r.get('box', {}).get('right', -1) <= 1
                 and 0 <= r.get('box', {}).get('top', -1) < r.get('box', {}).get('bottom', -1) <= 1]
        # Chrome may hide the scheme in the actual editable row. No fuzzy
        # matching, whitespace deletion, case folding or historical suffixes.
        return any(r.get('text') in (expected, expected.removeprefix('https://')) for r in valid)
    except LocalTextError:
        return False
    finally:
        if owned:
            await reader.close()

def browser_bands(frame):
    with Image.open(io.BytesIO(frame)) as im:
        w,h=im.size
    # app-learning-20261029 apps/chrome.json + reddit.json: landscape
    # tabs end near .095, toolbar .11..17, Reddit header starts near .19.
    # Portrait retains its original crop boundaries and encoded bytes.
    if w>h:
        return .11,.17,.18
    return .067,.125,.125

def address_band(frame):
    im=Image.open(io.BytesIO(frame));w,h=im.size;out=io.BytesIO()
    # Omnibox only: exclude existing tab titles and page body.
    top,bottom,_=browser_bands(frame)
    im.crop((0,int(h*top),w,int(h*bottom))).save(out,format='JPEG',quality=95)
    return out.getvalue()

def page_band(frame):
    im=Image.open(io.BytesIO(frame));w,h=im.size;out=io.BytesIO()
    _,_,top=browser_bands(frame)
    im.crop((0,int(h*top),w,h)).save(out,format='JPEG',quality=95)
    return out.getvalue()

async def next_reddit_web_actions(agent,goal,*,open_only=False):
    from .shortcut_workflow import next_shortcut_actions, launch_layout_guard
    guard=await launch_layout_guard(agent,'Reddit')
    if guard:return guard
    frame=agent.screen.frame;target=agent.phone.target_info() or {}
    from .spotlight_launch import receipt, safe_frame, fail_action
    if receipt(agent,None) and not safe_frame(agent,None,frame):
        return fail_action(agent,None,'Spotlight privacy gate refused Reddit browser frame')
    if not agent.phone.calibrated:
        raise DecisionUnavailable('Reddit Chrome route has uncalibrated geometry; no point or query sent')
    with Image.open(io.BytesIO(frame)) as im:size=im.size
    portrait=target.get('orientation')=='portrait'
    landscape=target.get('orientation')=='landscape' and size==(1646,1080)
    if target.get('device')!='ipad' or not (portrait or landscape) or not agent.screen.live or not agent.phone.calibrated:
        raise DecisionUnavailable('Reddit Chrome route needs a live independently calibrated portrait or verified 1646x1080 landscape iPad')
    top,bottom,_=browser_bands(frame)
    query=goal.get('query')
    url=URL+('?q='+quote(query,safe='') if query and not open_only else '')+'#esp32'
    # A fixed harmless fragment terminates Chrome inline history completion.
    # Otherwise typing a shorter public query/root can select a historical
    # suffix, and arrow keys would accept that suffix. Never delete/retype it.
    attempted=[s for s in agent.steps if s.get('workflow')=='reddit_web']
    steps=[s for s in attempted if 'act_s' in s]
    phases=[s.get('phase') for s in steps]
    if len(url)>60:raise DecisionUnavailable('Reddit public browser query exceeds verified address capacity; shorten it')
    def wait(reason):
        if sum(s.get('phase')==reason for s in agent.steps)>=3:
            raise DecisionUnavailable('Reddit Chrome route could not verify '+reason+'; no replay')
        return [dict(action='wait',workflow='reddit_web',phase=reason,think_s=0,_shot=frame)]
    def emit(action,phase,seconds=0):
        if phase!='home' and not control_region_matches(frame,agent.screen.frame,{'left':0,'right':1,'top':top,'bottom':bottom}):
            return wait('browser_chrome_changed')
        return [{**action,'workflow':'reddit_web','phase':phase,'think_s':seconds,'_shot':frame,
                 'thought':'Reddit public website in Chrome; native AirPlay route unavailable.'}]
    async def check_address(expected=None,edge=None):
        result=await agent.client.evaluate({'policy':'reddit_browser_address','execution_route':ROUTE,'expected_address':expected,'visible_edge':edge},
            {'ready':{'type':'noul','instructions':'Is the actual Chrome omnibox visibly in EDIT mode, with a caret, selection or attached cancel/edit controls? This is a crop of the URL row only. A requested shortcut or passive domain is not focus evidence.'},
             'literal':{'type':'noul','instructions':('Read the actual Chrome editable URL pixels. Does its '+edge+' edge show exactly '+repr(expected[:30] if edge=='prefix' else expected[-30:])+'? Require the actual '+('beginning through this complete prefix' if edge=='prefix' else 'complete suffix through the end')+', no missing/extra characters. Clipping at the opposite edge is permitted. Never infer from expected context or a suggestion.' if expected else 'Is the actual editable Chrome URL field EMPTY with only its Search or type URL placeholder? Reject any old address or query. This run has acknowledged a NEW tab and cmd+l, but those alone are not proof.')},
             'blocked':{'type':'noul','instructions':'Is a login, permission, account chooser, system Search or competing editable field obstructing this address field?'}},address_band(frame))
        agent.trace.write(address_band(frame),kind='reddit-web-check',purpose='verify exact public URL' if expected else 'verify empty new Chrome address',**result)
        a=result['answers']
        return result if a.get('ready',{}).get('noul',0)>=.99 and a.get('literal',{}).get('noul',0)>=.99 and a.get('blocked',{}).get('noul',1)<=.01 else None
    if any(s.get('phase') in ('new_tab', 'url_focus', 'url_type', 'url_submit')
           and 'act_s' not in s for s in attempted):
        return wait('browser_input_receipt_uncertain')
    if not steps:return emit({'action':'key','combo':'cmd+h'},'home')
    if 'chrome_ready' not in phases:
        actions=await next_shortcut_actions(agent,{'app':'Chrome','text':None})
        complete=next((a for a in actions if a.get('action')=='done'),None)
        if complete:
            return emit({'action':'wait'},'chrome_ready',complete.get('think_s',0))
        # An Enter/launch ACK is not actual foreground readiness. Let the
        # existing launcher independently verify Chrome before any tab/URL key.
        return actions
    if 'new_tab' not in phases:return emit({'action':'key','combo':'cmd+t'},'new_tab')
    if 'url_focus' not in phases:return emit({'action':'key','combo':'cmd+l'},'url_focus')
    if 'url_type' not in phases:
        result=await check_address()
        if result is None:
            # cmd+l ACK proves transport only. Without actual focus/empty-field
            # evidence it must never become a speculative typing probe.
            return wait('known_public_address_focus')
        return emit({'action':'type','text':url},'url_type',result['seconds'])
    probe = next((s for s in steps if s.get('phase') == 'url_type' and s.get('local_url_probe')), None)
    if probe and 'url_submit' not in phases:
        mutations = [s for s in agent.steps if s.get('action') in ('key','type','tap','swipe','home','back')]
        if (not mutations or mutations[-1] is not probe or probe.get('text') != url or not landscape
                or not await exact_local_address(agent, frame, url)):
            return wait('exact_local_public_address')
        if receipt(agent,None) and not safe_frame(agent,None,agent.screen.frame):
            return fail_action(agent,None,'Spotlight privacy gate refused local URL evidence')
        return emit({'action':'key','combo':'enter'},'url_submit')
    if 'url_submit' not in phases and 'url_left' not in phases:return emit({'action':'key','combo':'cmd+left'},'url_left')
    if 'url_submit' not in phases and 'url_prefix' not in phases:
        result=await check_address(url,'prefix')
        if result is None:return wait('exact_public_address_prefix')
        return emit({'action':'key','combo':'cmd+right'},'url_prefix',result['seconds'])
    if 'url_submit' not in phases:
        result=await check_address(url,'suffix')
        if result is None:return wait('exact_public_address_suffix')
        return emit({'action':'key','combo':'enter'},'url_submit',result['seconds'])
    result=await agent.client.evaluate({'policy':'app_public_search','app':'Reddit','query':query,'execution_route':ROUTE},
        {'ready':{'type':'noul','instructions':('Is the actual page a public Reddit search results page, with Reddit branding, Posts/Communities/Comments filters and visible public results, NOT loading/start page/error/login/private messages? The actual Reddit query field must show EXACTLY '+repr(query)+', and actual result cards must be visible. The submitted URL is NOT proof.' if query and not open_only else 'Is the actual page the public Reddit Search landing interface with Reddit branding and a real accessible Search Reddit field? Results/filters may not exist before a query. Exclude browser start page, loading/error, login, private messages and other sites. A typed address alone is not proof.')},
         'blocked':{'type':'noul','instructions':'Is a login, permission, cookie modal, app-opening banner or account chooser obstructing the public results? Do not interact with it.'}},page_band(frame))
    agent.trace.write(page_band(frame),kind='reddit-web-check',purpose='verify actual public Reddit results',**result)
    a=result['answers']
    if a.get('ready',{}).get('noul',0)<.99 or a.get('blocked',{}).get('noul',1)>.01:return wait('public_reddit_search_page')
    return [dict(action='done',workflow='app_search' if not open_only else 'reddit_web',phase='verified',execution_route=ROUTE,
        _verified_public_results=not open_only,_verified_browser_entry=True,think_s=result['seconds'],_shot=frame,
        answer='Reddit public '+('search results for '+repr(query) if not open_only else 'Search page')+' visibly verified in Chrome. Native AirPlay route unavailable. Nothing published or changed in existing tabs.')]
