"""App-independent local coordinate grounding. Caller verifies full-frame ownership; no HID."""
import hashlib,io,math,time
from PIL import Image,ImageDraw
from .decision import DecisionUnavailable,probability
from .frame_privacy import check_cloud_frame

KEYS=('left','top','right','bottom')
def valid_box(value):
    return (isinstance(value,dict) and set(value)==set(KEYS)
        and all(probability(value[k]) for k in KEYS)
        and value['left']<value['right'] and value['top']<value['bottom'])

def contains_control(parent,child):
    return (valid_box(parent) and valid_box(child)
            and parent['left']<=child['left']<child['right']<=parent['right']
            and parent['top']<=child['top']<child['bottom']<=parent['bottom'])

def observation_parking_point(control,container):
    """Move only, outside owned content, to avoid obscuring its postcondition."""
    if not valid_box(control) or not valid_box(container):
        raise DecisionUnavailable('Invalid observation parking geometry')
    cx=(control['left']+control['right'])/2;cy=(control['top']+control['bottom'])/2
    if not container['left']<cx<container['right'] or not container['top']<cy<container['bottom']:
        raise DecisionUnavailable('Observation control is outside owned content')
    candidates=((container['right']+.03,cy),(container['left']-.03,cy),
                (cx,container['top']-.03),(cx,container['bottom']+.03))
    for x,y in candidates:
        if .03<=x<=.97 and .03<=y<=.97:return x,y
    raise DecisionUnavailable('No bounded observation parking space')

def control_crop(frame,coarse,container):
    if not valid_box(coarse) or not valid_box(container):raise DecisionUnavailable('Invalid bounded control region')
    im=Image.open(io.BytesIO(frame)).convert('RGB');w,h=im.size
    cx=(coarse['left']+coarse['right'])/2;cy=(coarse['top']+coarse['bottom'])/2
    if not container['left']<cx<container['right'] or not container['top']<cy<container['bottom']:
        raise DecisionUnavailable('Coarse control center is outside its independently owned container')
    # Context margins are relative to the proposed control, never App coordinates.
    px=max(64,(coarse['right']-coarse['left'])*w*.5)
    py=max(64,(coarse['bottom']-coarse['top'])*h*2)
    bounds=(max(math.ceil(container['left']*w),math.floor(coarse['left']*w-px)),
            max(math.ceil(container['top']*h),math.floor(coarse['top']*h-py)),
            min(math.floor(container['right']*w),math.ceil(coarse['right']*w+px)),
            min(math.floor(container['bottom']*h),math.ceil(coarse['bottom']*h+py)))
    x0,y0,x1,y1=bounds
    if x1-x0<8 or y1-y0<8:raise DecisionUnavailable('Control crop is too small')
    crop=im.crop(bounds);scale=min(4,1080/max(crop.size),max(1,280/min(crop.size)))
    crop=crop.resize(tuple(max(1,round(v*scale)) for v in crop.size),Image.Resampling.LANCZOS)
    out=io.BytesIO();crop.save(out,format='JPEG',quality=95,subsampling=0)
    return out.getvalue(),{'source':[w,h],'bounds':list(bounds),'image_size':list(crop.size)}

def screen_box(local,geometry):
    if not valid_box(local):raise DecisionUnavailable('Invalid refined control coordinates')
    w,h=geometry['source'];x0,y0,x1,y1=geometry['bounds']
    return {'left':(x0+local['left']*(x1-x0))/w,'right':(x0+local['right']*(x1-x0))/w,
            'top':(y0+local['top']*(y1-y0))/h,'bottom':(y0+local['bottom']*(y1-y0))/h}

def label_hover_context(frame,box,container):
    """Geometry-only style context; never changes the semantic click center.

    Adjacent icon/label glyphs can share hover styling. Compare all their
    existing glyphs before ignoring background style, with the original rule.
    """
    if not valid_box(box) or not valid_box(container):raise DecisionUnavailable('Invalid control style context')
    w,h=Image.open(io.BytesIO(frame)).size
    pad=min((box['bottom']-box['top'])*h/w,max(0,(.35-(box['right']-box['left']))/2))
    height=box['bottom']-box['top']
    vertical=min(height,max(0,(.059-height)/2))
    return {**box,'left':max(container['left'],box['left']-pad),'right':min(container['right'],box['right']+pad),
            'top':max(container['top'],box['top']-vertical),'bottom':min(container['bottom'],box['bottom']+vertical)}

def click_box(coarse,refined,geometry):
    """Prefer corroborated overlap; no overlap means use the local proposal.

    This selects a candidate only. Every chosen center needs independent proof.
    """
    overlap={'left':max(coarse['left'],refined['left']),'right':min(coarse['right'],refined['right']),
             'top':max(coarse['top'],refined['top']),'bottom':min(coarse['bottom'],refined['bottom'])}
    w,h=geometry['source']
    if valid_box(overlap) and (overlap['right']-overlap['left'])*w>=4 and (overlap['bottom']-overlap['top'])*h>=4:
        return overlap,'corroborated_overlap'
    return refined,'local_proposal'

def isolated_glyph_box(frame,proposal,container,*,contained=False):
    """Optional local symbol candidate, never a click authorization.

    Preserve original pixels. Only one small connected contrasting glyph is
    allowed near the model proposal; text, boundary clipping and ambiguity fall
    back to the existing verified model path.
    """
    from statistics import median
    if not valid_box(proposal) or not valid_box(container):return None
    im=Image.open(io.BytesIO(frame)).convert('L');w,h=im.size
    pw=(proposal['right']-proposal['left'])*w;ph=(proposal['bottom']-proposal['top'])*h
    if min(pw,ph)<4 or max(pw,ph)>40:return None
    cx=(proposal['left']+proposal['right'])*w/2;cy=(proposal['top']+proposal['bottom'])*h/2
    radius=min(64,max(pw,ph)*2)
    x0=max(math.ceil(container['left']*w),math.floor(cx-radius));y0=max(math.ceil(container['top']*h),math.floor(cy-radius))
    x1=min(math.floor(container['right']*w),math.ceil(cx+radius));y1=min(math.floor(container['bottom']*h),math.ceil(cy+radius))
    if x1-x0<8 or y1-y0<8:return None
    crop=im.crop((x0,y0,x1,y1));pixels=list(crop.get_flattened_data());bg=median(pixels);cw,ch=crop.size
    todo={(x,y) for y in range(ch) for x in range(cw) if abs(pixels[y*cw+x]-bg)>25}
    candidates=[]
    while todo:
        queue=[todo.pop()];points=[]
        while queue:
            x,y=queue.pop();points.append((x,y))
            for dx,dy in ((-1,0),(1,0),(0,1),(0,-1)):
                point=(x+dx,y+dy)
                if point in todo:todo.remove(point);queue.append(point)
        if len(points)<8:continue
        l=min(x for x,y in points);r=max(x for x,y in points)+1;t=min(y for x,y in points);b=max(y for x,y in points)+1
        if l==0 or t==0 or r==cw or b==ch:continue
        if min(r-l,b-t)<4 or max(r-l,b-t)>40:continue
        if min(r-l,b-t)/max(r-l,b-t)<.35:continue
        if abs(x0+(l+r)/2-cx)>radius or abs(y0+(t+b)/2-cy)>radius:continue
        # Hollow symbols have a closed interior. Nearby counters and open
        # text strokes are not alternate symbol centers.
        ink=set(points);outside=set()
        queue=([(x,t) for x in range(l,r)]+[(x,b-1) for x in range(l,r)]
               +[(l,y) for y in range(t,b)]+[(r-1,y) for y in range(t,b)])
        while queue:
            point=queue.pop()
            if point in outside or point in ink:continue
            x,y=point
            if not l<=x<r or not t<=y<b:continue
            outside.add(point);queue.extend(((x-1,y),(x+1,y),(x,y-1),(x,y+1)))
        interior=(r-l)*(b-t)-len(ink)-len(outside)
        if interior<max(2,(r-l)*(b-t)/8):continue # genuinely hollow, not a tiny text hole
        if contained and not (proposal['left']<=(x0+(l+r)/2)/w<=proposal['right'] and proposal['top']<=(y0+(t+b)/2)/h<=proposal['bottom']):continue
        candidates.append({'left':(x0+l)/w,'right':(x0+r)/w,'top':(y0+t)/h,'bottom':(y0+b)/h})
    return candidates[0] if len(candidates)==1 else None

def symbol_candidate(frame,coarse,refined,container):
    candidate=isolated_glyph_box(frame,refined,container)
    if candidate is not None:return candidate
    # A neighborhood can contain body characters. Restrict to actual proposals
    # only if they collectively identify ONE enclosed symbol. Still no authority.
    candidates=[]
    for proposal in (coarse,refined):
        item=isolated_glyph_box(frame,proposal,container,contained=True)
        if item is not None and item not in candidates:candidates.append(item)
    return candidates[0] if len(candidates)==1 else None

def text_line_box(frame,proposal,container):
    """A geometry proposal from one unique nearby high-contrast text row.

    Never identify text, authorize a click, change horizontal attribution, or
    choose between ambiguous rows. The independent original point check follows.
    """
    from statistics import median
    if not valid_box(proposal) or not valid_box(container):return None
    im=Image.open(io.BytesIO(frame)).convert('L');w,h=im.size
    pw=(proposal['right']-proposal['left'])*w;ph=(proposal['bottom']-proposal['top'])*h
    if not 12<=pw<=320 or not 4<=ph<=64:return None
    cy=(proposal['top']+proposal['bottom'])*h/2
    x0=max(math.ceil(container['left']*w),math.floor(proposal['left']*w))
    x1=min(math.floor(container['right']*w),math.ceil(proposal['right']*w))
    y0=max(math.ceil(container['top']*h),math.floor(cy-max(32,ph*2)))
    y1=min(math.floor(container['bottom']*h),math.ceil(cy+max(32,ph*2)))
    if x1-x0<12 or y1-y0<8:return None
    crop=im.crop((x0,y0,x1,y1));pixels=list(crop.get_flattened_data());cw,ch=crop.size;bg=median(pixels)
    rows=[]
    for y in range(ch):
        xs=[x for x in range(cw) if abs(pixels[y*cw+x]-bg)>60]
        if len(xs)>=max(2,cw*.015):rows.append((y,min(xs),max(xs)))
    groups=[]
    for row in rows:
        if not groups or row[0]-groups[-1][-1][0]>2:groups.append([])
        groups[-1].append(row)
    candidates=[]
    for group in groups:
        top,bottom=group[0][0],group[-1][0]+1
        if top==0 or bottom==ch or not 6<=bottom-top<=48:continue
        if max(v[2] for v in group)-min(v[1] for v in group)<max(8,cw*.25):continue
        distance=abs(y0+(top+bottom)/2-cy)
        candidates.append((distance,top,bottom))
    candidates.sort()
    if not candidates:return None
    best=candidates[0]
    if best[0]>max(16,ph):return None
    if len(candidates)>1 and candidates[1][0]-best[0]<max(4,(best[2]-best[1])/2):return None
    return {**proposal,'top':(y0+best[1])/h,'bottom':(y0+best[2])/h}

def marked_control(image,box,geometry):
    marked=Image.open(io.BytesIO(image)).convert('RGB')
    w,h=geometry['source'];x0,y0,x1,y1=geometry['bounds']
    x=(((box['left']+box['right'])/2*w-x0)/(x1-x0))*marked.width
    y=(((box['top']+box['bottom'])/2*h-y0)/(y1-y0))*marked.height
    # Only the center dot: crosshair arms overwrite thin small-icon strokes.
    # Its exact crop-space coordinate is supplied separately to the verifier.
    pen=ImageDraw.Draw(marked)
    pen.ellipse((x-1,y-1,x+1,y+1),fill=(255,0,0))
    buf=io.BytesIO();marked.save(buf,format='JPEG',quality=95,subsampling=0);return buf.getvalue()

def identity_crop(frame,box,container,*,text_context=False):
    if not contains_control(container,box):raise DecisionUnavailable('Pixel control is outside owned container')
    im=Image.open(io.BytesIO(frame)).convert('RGB');w,h=im.size
    margin=max(8,min(48,2*math.ceil((box['bottom']-box['top'])*h))) if text_context else max(8,min(24,math.ceil(max((box['right']-box['left'])*w,(box['bottom']-box['top'])*h))))
    rect=(max(math.ceil(container['left']*w),math.floor(box['left']*w)-margin),
          max(math.ceil(container['top']*h),math.floor(box['top']*h)-margin),
          min(math.floor(container['right']*w),math.ceil(box['right']*w)+margin),
          min(math.floor(container['bottom']*h),math.ceil(box['bottom']*h)+margin))
    crop=im.crop(rect)
    if min(crop.size)<4:raise DecisionUnavailable('Pixel control identity crop is incomplete')
    scale=min(16,1024/max(crop.size),max(1,280/min(crop.size)))
    crop=crop.resize(tuple(max(1,round(v*scale)) for v in crop.size),Image.Resampling.LANCZOS)
    out=io.BytesIO();crop.save(out,format='JPEG',quality=95,subsampling=0)
    return out.getvalue(),{'source':[w,h],'bounds':list(rect),'image_size':list(crop.size)}

async def refine_control(client,frame,coarse,description,*,container,check_active=None,symbol=False,minimum_confidence=.5,text_label=False,pixel_identity=False):
    def active():
        if check_active is not None:check_active()
    active();check_cloud_frame(frame) # Original privacy check before cropping.
    if minimum_confidence not in (.5,.9):raise DecisionUnavailable('Unsupported grounding confidence contract')
    image,geometry=control_crop(frame,coarse,container);start=time.perf_counter()
    q={'ready':{'type':'noul','instructions':'Is the requested control glyph/label clearly and completely visible in this enlarged crop? Judge local visibility and exact target identity; actual App/owner and modal state are independently verified on the original full frame by the caller. Reject absent, clipped, wrong or ambiguous targets.'},
       'blocked':{'type':'noul','instructions':'Within this crop, is the requested target obscured, visibly disabled, ambiguous or overlapped by another control? Do not invent off-crop conditions.'},
       'target':{'type':'box','instructions':'Locate '+description+'. Return a tight box for the EXACT requested icon or text label. Do not include an adjacent counter, neighboring label, whole row/card, or unrelated glyph; include associated parts only when explicitly requested. Use THIS CROP coordinates, not the original screen. Its center must be INSIDE the actual clickable label or icon, not whitespace just above/below it, a border, neighbor or its containing card. If absent or uncertain return confidence0 and a harmless valid box.'}}
    result=await client.evaluate({'policy':'precise_control_grounding','coordinate_space':'attached enlarged crop only','target_description':description},q,image)
    active()
    a=result.get('answers',{});ready=a.get('ready',{}).get('noul');blocked=a.get('blocked',{}).get('noul');target=a.get('target',{});confidence=target.get('confidence')
    if not probability(ready) or ready<.99 or not probability(blocked) or blocked>.01 or not probability(confidence) or confidence<(.5 if pixel_identity else minimum_confidence):
        raise DecisionUnavailable('Actual control center not precisely grounded', {'ready':ready,'blocked':blocked,'target_confidence':confidence,'model':result.get('model'),'fallback_used':result.get('fallback_used')})
    refined=screen_box(target.get('box'),geometry)
    box,strategy=click_box(coarse,refined,geometry)
    # Bounded geometry only: distinguish model proposal from selection drift.
    # Keep the initial selection even if a later independent correction occurs.
    proposal_geometry={'coarse_box':{k:coarse[k] for k in KEYS},
                       'refined_box':dict(refined),'selected_box':dict(box),
                       'selection':strategy,'confidence':confidence}
    if symbol:
        glyph=symbol_candidate(frame,coarse,refined,container)
        if glyph is not None:box=glyph;strategy='isolated_glyph_candidate'
    if text_label and not symbol:
        line=text_line_box(frame,box,container)
        if line is not None:box=line;strategy='unique_text_line_candidate'
    proposal_geometry['selected_box']=dict(box);proposal_geometry['selection']=strategy
    if pixel_identity and strategy in ('isolated_glyph_candidate','unique_text_line_candidate'):
        isolated,identity_geometry=identity_crop(frame,box,container,text_context=text_label)
        identity_questions={'ready':{'type':'noul','instructions':'In this tight crop of original UI pixels, is the central control glyph/text line clearly and completely the requested '+description+'? Classify the actual visible control identity, NOT a hypothetical coordinate. Parent App/card/post ownership is independently verified on the full source frame.'},
                            'blocked':{'type':'noul','instructions':'Is that central control wrong, absent, clipped, obscured, disabled or ambiguous? Small surrounding margins are context, not a blocker. Do not infer missing off-crop ownership. Reject an adjacent body character, count, wrong label or incomplete symbol.'}}
        active();identity=await client.evaluate({'policy':'precise_control_identity','target_description':description,'geometry_source':'one unique original-pixel symbol or text row; no model coordinate is accepted as center proof'},identity_questions,isolated);active()
        answer=identity.get('answers',{});ir=answer.get('ready',{}).get('noul');ib=answer.get('blocked',{}).get('noul')
        if not probability(ir) or ir<.99 or not probability(ib) or ib>.01:
            raise DecisionUnavailable('Pixel control failed independent semantic identity',{'ready':ir,'blocked':ib,'model':identity.get('model'),'box':box,'proposal_geometry':proposal_geometry,'identity_geometry':identity_geometry})
        return {'box':box,'proposal_geometry':proposal_geometry,'seconds':round(time.perf_counter()-start,4),'model':result.get('model'),
                'fallback_used':result.get('fallback_used'),'point_verified':True,'point_strategy':'original_pixels_and_independent_identity','point_checks':1,
                'point_model':identity.get('model'),'point_effort':identity.get('reasoning_effort'),'reasoning_effort':result.get('reasoning_effort'),
                'geometry':geometry,'identity_geometry':identity_geometry,'pixel_strategy':strategy,'geometry_source':'original_pixels','model_coordinates_used_as_authority':False,'source_sha256':hashlib.sha256(frame).hexdigest()}
    if confidence<minimum_confidence:raise DecisionUnavailable('Model-only control coordinates are below the original confidence contract',{'target_confidence':confidence,'proposal_geometry':proposal_geometry})
    point_q={'ready':{'type':'noul','instructions':'This single image is the actual UI crop with a tiny red dot at the PROPOSED CLICK CENTER, also given as proposed_center in these same crop coordinates. Is the red dot center safely INSIDE the actual requested control '+description+'? Reject boundaries, background OUTSIDE its hit area, adjacent controls, incomplete targets or uncertainty. Spaces inside a real text link or a hollow icon interior are valid only when clearly enclosed by that actual control. Merely overlapping some target pixels is insufficient.'},
             'blocked':{'type':'noul','instructions':'Is the requested control actually absent, obscured, disabled or ambiguous in this crop? A marker outside an otherwise clearly visible control means ready=false, not blocked=true. The red marker is a coordinate aid, not a real overlay.'},
             'target':{'type':'box','instructions':'If the marker is OUTSIDE an otherwise visible requested control, locate its actual tight complete control box in this SAME single crop, coordinates normalized0..1 across this image only. Ignore the synthetic red coordinate marker. This is only a correction proposal. Otherwise return confidence0 with harmless valid box {left:0,top:0,right:0.01,bottom:0.01}.'}}
    for attempt in range(2):
        active()
        sw,sh=geometry['source'];x0,y0,x1,y1=geometry['bounds']
        center={'x':((box['left']+box['right'])*sw/2-x0)/(x1-x0),
                'y':((box['top']+box['bottom'])*sh/2-y0)/(y1-y0)}
        point_check=await client.evaluate({'policy':'precise_control_point','target_description':description,'proposed_center':center,'coordinate_space':'same attached crop, normalized0..1; coordinates are proposals, never proof'},point_q,marked_control(image,box,geometry));active()
        pa=point_check.get('answers',{});pr=pa.get('ready',{}).get('noul');pb=pa.get('blocked',{}).get('noul')
        if probability(pr) and pr>=.99 and probability(pb) and pb<=.01:break
        correction=pa.get('target',{});cb=correction.get('box')
        # One geometry correction, never a repeated physical click or a changed
        # negative ownership/blocked assessment. Verify the NEW center again.
        if (attempt==0 and probability(pr) and pr<=.01 and probability(pb) and pb<=.01
            and probability(correction.get('confidence')) and correction['confidence']>=.9
            and valid_box(cb)):
            box=screen_box(cb,geometry);strategy='independently_corrected';continue
        raise DecisionUnavailable('Proposed control center failed independent point verification',{'ready':pr,'blocked':pb,'box':box,'geometry':geometry,'proposal_geometry':proposal_geometry,'model':point_check.get('model'),'attempt':attempt+1,'correction_confidence':correction.get('confidence'),'correction_box':cb})
    return {'box':box,'proposal_geometry':proposal_geometry,'seconds':round(time.perf_counter()-start,4),'model':result.get('model'),
            'fallback_used':result.get('fallback_used'),'point_verified':True,'point_strategy':strategy,'point_checks':attempt+1,
            'point_model':point_check.get('model'),'point_effort':point_check.get('reasoning_effort'),'reasoning_effort':result.get('reasoning_effort'),'geometry':geometry,'source_sha256':hashlib.sha256(frame).hexdigest()}

async def verify_content_center(client,frame,proposal,description,*,container,proposal_confidence,check_active=None):
    """Independent point proof for an already grounded plain-content region.

    A body region is not a UI glyph. Do not ask a second model to invent a
    button there; independently verify the actual proposed center once.
    """
    def active():
        if check_active is not None:check_active()
    active();check_cloud_frame(frame)
    if not probability(proposal_confidence) or proposal_confidence<.9 or not contains_control(container,proposal):
        raise DecisionUnavailable('Content candidate is not fully owned and confidently proposed')
    image,geometry=control_crop(frame,proposal,container);w,h=geometry['source'];x0,y0,x1,y1=geometry['bounds']
    center={'x':((proposal['left']+proposal['right'])*w/2-x0)/(x1-x0),'y':((proposal['top']+proposal['bottom'])*h/2-y0)/(y1-y0)}
    questions={'ready':{'type':'noul','instructions':'The tiny red dot marks the proposed click center. Is it safely inside the actual plain NON-LINK original post BODY text region '+description+'? Parent identity is independently verified on the full frame. This opens detail from content, so a button/standalone UI label is not required. Reject author/avatar, URL, quoted card, media, engagement/submission controls, borders and uncertain centers.'},
               'blocked':{'type':'noul','instructions':'Is the requested original body region absent, obscured, ambiguous, or actually an author, URL, media, quote or engagement/submission control? A dot outside otherwise visible valid body means ready=false. Do not invent a missing button for a body-navigation operation.'}}
    start=time.perf_counter()
    result=await client.evaluate({'policy':'precise_control_point','target_description':description,'proposed_center':center,'coordinate_space':'same attached crop; candidate is an independently owned body region, never publication permission'},questions,marked_control(image,proposal,geometry));active()
    a=result.get('answers',{});r=a.get('ready',{}).get('noul');b=a.get('blocked',{}).get('noul')
    if not probability(r) or r<.99 or not probability(b) or b>.01:
        raise DecisionUnavailable('Plain content center failed independent point verification',{'ready':r,'blocked':b,'box':proposal,'geometry':geometry,'model':result.get('model')})
    return {'box':dict(proposal),'seconds':round(time.perf_counter()-start,4),'model':result.get('model'),'fallback_used':result.get('fallback_used'),'point_verified':True,'point_strategy':'independently_verified_plain_content_center','point_checks':1,'source_sha256':hashlib.sha256(frame).hexdigest(),'geometry':geometry}
