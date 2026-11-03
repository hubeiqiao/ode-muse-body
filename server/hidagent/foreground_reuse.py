"""Task-local proof reuse for unchanged public App foregrounds.
No cached name authorizes input. Conservatively matching visible pixels (outside status/cursor/system Home indicator),
current board/geometry, and a newly received picture are required. No cloud call.
"""
import asyncio,io,time,math
from PIL import Image
from . import vision

PUBLIC_APPS={'YouTube','X','rednote','Instagram','LinkedIn','Bilibili','TikTok','Douyin','Google Maps','Reddit','App Store'}

def public_app_identity_questions():
    """Task-independent app identity; the desired app/card must not enter state."""
    return {'visible_app': {'type': 'choice', 'instructions': 'Identify the ACTUAL active foreground app from its visible UI. No desired target app is supplied. Topic/content and typed query do not establish app identity. An app behind system Spotlight or another covering app is not the active foreground. Choose other if uncertain.', 'criteria': {'TikTok': 'TikTok public app, including Top/Videos/Users/Photos/Sounds/LIVE/Places search tabs.', 'rednote': 'rednote / Xiaohongshu public app, including its All/Users/Goods or 全部/用户/商品 search tabs, OR its distinctive unbranded search-history page: camera and Search button in the top query bar, Recent searches chips, You may like suggestions, and bottom red mic with Hold to ask, answer all. This combination is rednote even without a logo. A lone search field/history label or matching topic is insufficient.', 'YouTube': 'YouTube video app, including YouTube-specific Home/Shorts/Subscriptions bottom navigation.', 'X': 'X / Twitter public app.', 'Instagram': 'Instagram public app.', 'LinkedIn': 'LinkedIn public app.', 'Bilibili': 'Bilibili public app.', 'Douyin': 'Douyin / 抖音 public app.', 'Google Maps': 'Google Maps app.', 'Reddit': 'Reddit public website or app.', 'App Store': 'Apple App Store.', 'other': 'System Home/Spotlight, another app, covering modal or ambiguous active app.'}}}

async def identify_public_app(agent, frame):
    """Task-independent identity using actual registered UI or the existing vision path."""
    from .app_controls import public_search_control
    target = agent.phone.target_info() or {}
    known = public_search_control(frame, 'rednote', target, control='public_search_history', anchors_only=True)
    if known:
        return {'answers': {'visible_app': {'type': 'choice', 'choice': 'rednote', 'confidence': 1.0}},
                'seconds': 0, 'approved': True, 'model': 'local-registered-ui',
                'evidence': known, 'scope': 'Foreground identity only; no focus/query/results proof.'}
    return await agent.client.evaluate(
        {'policy': 'public_app_identity', 'purpose': 'identify actual visible public app without requested target'},
        public_app_identity_questions(), frame)

def same_visible_app(before,after,pointer=None):
    if not before or not after:return False
    try:
        a=Image.open(io.BytesIO(before)).convert('RGB');b=Image.open(io.BytesIO(after)).convert('RGB')
        if a.size!=b.size:return False
        w,h=a.size
        # Reuse the existing coarse+fine change detector. A low global mean
        # alone cannot hide new text. Only fixed system chrome/cursor is masked.
        for im in (a,b):
            im.paste((0,0,0),(0,0,w,round(h*.025)))
            im.paste((0,0,0),(round(w*.29),round(h*.984),round(w*.71),h))
            if pointer and len(pointer)==2 and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in pointer):
                x,y=pointer[0]*w,pointer[1]*h;r=round(w*.045)
                im.paste((0,0,0),(max(0,round(x-r)),max(0,round(y-r)),min(w,round(x+r)),min(h,round(y+r))))
        return not vision.changed(a,b,threshold=.25,top_margin=.025)
    except (OSError,ValueError,TypeError):return False

def remember_completed_app(agent):
    from .shortcut_workflow import shortcut_goal
    from .app_search import search_goal
    last=agent.steps[-1] if agent.steps else {}
    if last.get('action')!='done' or not any(last.get(k) for k in ('_verified_icon_entry','_verified_public_results','_verified_foreground_reuse')):return None
    goal=search_goal(agent.task) or shortcut_goal(agent.task)
    phone=agent.phone;target=phone.target_info() if phone else None
    if not goal or goal.get('text') is not None or goal['app'] not in PUBLIC_APPS or not target or target.get('device')!='ipad':return None
    frame=agent.shots.get(len(agent.steps)-1)
    if not frame or not getattr(phone,'calibrated',False):return None
    link=getattr(getattr(phone,'device',None),'links',None)
    if type(link) is not int:return None
    return {'app':goal['app'],'frame':frame,'pointer':getattr(phone,'last_pointer_check',{}).get('observed'),
        'key':target.get('calibration_key'),'link':link,'at':time.monotonic(),'checked_at':time.time(),
        'task_id':str(agent.steps[0].get('t',''))}

def current_foreground_app(agent):
    """Read-only identity from an existing verified proof, rechecked on current pixels.

    A saved app name or recent task alone is never foreground evidence. No probe,
    HID, OCR, or cloud request is made when assembling voice context/status.
    """
    if agent is None:
        return None
    proof = getattr(agent, '_foreground_candidate', None) or getattr(agent, '_completed_app_source', None)
    phone, screen = getattr(agent, 'phone', None), getattr(agent, 'screen', None)
    if not proof or phone is None or screen is None or proof.get('app') not in PUBLIC_APPS:
        return None
    target = phone.target_info() or {}
    if (0 <= time.monotonic()-proof['at'] <= 120 and target.get('device') == 'ipad'
            and target.get('calibration_key') == proof['key'] and phone.device.links == proof['link']
            and phone.device.connected and phone.calibrated and screen.live
            and proof['checked_at'] < screen.updated and 0 <= time.time()-screen.updated < 2
            and same_visible_app(proof['frame'], screen.frame, proof.get('pointer'))):
        return proof['app']
    return None

async def reuse_foreground(agent,app):
    proof=getattr(agent,'_foreground_candidate',None);phone=getattr(agent,'phone',None)
    if not proof or phone is None or app not in PUBLIC_APPS or proof.get('app')!=app:return None
    epoch=getattr(agent,'_foreground_epoch',0)
    def bound():
        target=phone.target_info() or {}
        return (0<=time.monotonic()-proof['at']<=120 and target.get('device')=='ipad'
            and target.get('calibration_key')==proof['key'] and phone.device.links==proof['link']
            and phone.device.connected and phone.calibrated and agent.screen.live)
    if not bound() or not same_visible_app(proof['frame'],agent.screen.frame,proof.get('pointer')):return None
    started=time.perf_counter();used_probe=False
    if not (proof['checked_at']<agent.screen.updated and 0<=time.time()-agent.screen.updated<2):
        # Observe liveness without opening the App again. Only a conservatively matched
        # match to the previously verified public view reaches this safe probe.
        used_probe=True
        await phone.ensure_live(force_probe=True)
    if epoch!=getattr(agent,'_foreground_epoch',0):raise asyncio.CancelledError()
    if (not bound() or not (proof['checked_at']<agent.screen.updated and 0<=time.time()-agent.screen.updated<2)
            or not same_visible_app(proof['frame'],agent.screen.frame,proof.get('pointer'))):return None
    seconds=time.perf_counter()-started
    agent.trace.write(kind='foreground-reuse',app=app,source_task_id=proof['task_id'],liveness_probe_used=used_probe,
        seconds=round(seconds,4),scope='Current App pixels pass coarse/fine comparison to prior verified public App, excluding status/cursor/system Home indicator and fresh frame receipt; not focus/query or account proof.')
    return {'seconds':seconds,'liveness_probe_used':used_probe}
