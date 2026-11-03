"""Read-only header text proposes navigation only; OCR never proves input/focus."""
import time
import io
from PIL import Image
from .decision import probability
from .foreground_reuse import same_visible_app
from .hid_lease import check_hid
from .local_text import LocalTextError
from .frame_privacy import known_excluded_overlay


def navigation_binding(phone):
    target=phone.target_info() or {}
    return [*[target.get(k) for k in ('device','orientation','calibration_key','width','height')],
            getattr(phone.device,'links',None)]


def valid_header_box(box):
    if not isinstance(box,dict) or not all(probability(box.get(k)) for k in ('left','right','top','bottom')):return False
    return (.02<=box['left']<box['right']<=.995 and .025<=box['top']<box['bottom']<=.09
            and .10<=box['right']-box['left']<=.97 and .006<=box['bottom']-box['top']<=.045)


def navigation_chrome_matches(before,after,pointer=None):
    # A navigation-only point was independently judged on the full source.
    # Retain all top navigation UI, not the changing public result/video body.
    # The original coarse/fine thresholds are unchanged; this never proves focus.
    if known_excluded_overlay(after):return False
    try:
        a=Image.open(io.BytesIO(before)).convert('RGB');b=Image.open(io.BytesIO(after)).convert('RGB')
        if a.size!=b.size:return False
        w,h=a.size;top,bottom=round(h*.025),round(h*.15)
        region=(0,top,w,bottom)
        local=None
        if pointer and len(pointer)==2 and top/h<=pointer[1]<=bottom/h:
            local=(pointer[0],(pointer[1]*h-top)/(bottom-top))
        aa,bb=io.BytesIO(),io.BytesIO()
        a.crop(region).save(aa,format='PNG');b.crop(region).save(bb,format='PNG')
        return same_visible_app(aa.getvalue(),bb.getvalue(),local)
    except (OSError,ValueError,TypeError,IndexError):return False


def observation_bound(phone,screen,proof,frame,pointer=None):
    return (screen.live and phone.device.connected and phone.calibrated
            and navigation_binding(phone)==proof.get('binding')
            and valid_header_box(proof.get('box'))
            and navigation_chrome_matches(frame,screen.frame,pointer))


def enclosing_flat_control(frame,box):
    """Generic bounded flat background around observed text, never an App template."""
    try:
        image=Image.open(io.BytesIO(frame)).convert('RGB');w,h=image.size
        left,right=box['left']*w,box['right']*w
        top,bottom=box['top']*h,box['bottom']*h
        x=round((left+right)/2);runs=[]
        for y in (round(top)-3,round(top)-6,round(bottom)+3,round(bottom)+6):
            if not .025*h<=y<=.09*h:continue
            seed=image.getpixel((x,y))
            def distance(pixel):return sum(abs(a-b) for a,b in zip(seed,pixel))/3
            l=r=x
            while l>0 and distance(image.getpixel((l-1,y)))<=8:l-=1
            while r<w-1 and distance(image.getpixel((r+1,y)))<=8:r+=1
            if (not .02*w<=l<left-3 or not right+3<r<=.995*w
                    or r-l<max(.35*w,(right-left)*1.3)):continue
            if min(distance(image.getpixel((max(0,l-3),y))),distance(image.getpixel((min(w-1,r+3),y))))<12:continue
            runs.append((l,r))
        agreeing=[run for run in runs if sum(abs(run[0]-other[0])<=5 and abs(run[1]-other[1])<=5 for other in runs)>=2]
        if not agreeing:return None
        l,r=agreeing[0];candidate={**box,'left':l/w,'right':(r+1)/w}
        return candidate if valid_header_box(candidate) else None
    except (OSError,ValueError,IndexError,TypeError):return None


def observation_pointer_region(proof):
    b=proof['box']
    return (max(0,b['left']-.06),max(.025,b['top']-.05),min(1,b['right']+.06),min(1,b['bottom']+.05))


async def header_navigation_proposal(agent,frame):
    reader=getattr(agent,'navigation_text_reader',None)
    phone=getattr(agent,'phone',None)
    if reader is None or phone is None or known_excluded_overlay(frame):return None
    binding=navigation_binding(phone)
    if binding[0:2]!=['ipad','portrait'] or not binding[2] or not phone.calibrated or not agent.screen.live:return None
    check_hid('move')
    started=time.perf_counter()
    try:observed=await reader.read(frame,mode='fast')
    except LocalTextError:return None
    check_hid('move')
    candidates=[]
    for row in observed.get('rows',[]):
        text=row.get('text','');box=row.get('box')
        # Position proposal only, including imperfect OCR spelling. No OCR
        # string is interpreted as the requested query or outgoing text.
        if (isinstance(text,str) and 8<=len(text)<=100 and sum(c.isascii() and c.isalpha() for c in text)>=6
                and valid_header_box(box)):
            candidates.append(dict(box))
    if len(candidates)!=1:return None
    box=enclosing_flat_control(frame,candidates[0]) or candidates[0];proof={'box':box,'binding':binding}
    if not observation_bound(phone,agent.screen,proof,frame):return None
    return {**proof,'x':round((box['left']+box['right'])*500),'y':round((box['top']+box['bottom'])*500),
            'seconds':time.perf_counter()-started}
