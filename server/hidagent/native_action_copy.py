"""Native code-block copy only. No reading arbitrary clipboard or typing/posting."""
import asyncio,hashlib,io,time
from PIL import Image,ImageChops,ImageStat
from .action_review import completed_call
from .app_controls import public_search_control
from .decision import DecisionUnavailable
from .hid_lease import leased_input

HEADER_BOXES={'copy-dark.json':(20,260,630,290),'copy-compact.json':(20,412,415,440)}
# Landscape card rows scroll: this box is relative to the matched Copy height
# (dy). It covers the card title row(s) and first body line, excluding the
# Copy glyph column so pointer hover over Copy is not a card change.
LANDSCAPE_HEADER_BOXES={'copy-landscape.json':(24,535,880,662)}

def same_card_header(before,after,template='copy-dark.json',dy=0):
    try:
        a=Image.open(io.BytesIO(before)).convert('RGB');b=Image.open(io.BytesIO(after)).convert('RGB')
        if template in LANDSCAPE_HEADER_BOXES:
            if a.size!=b.size or a.size!=(1646,1080) or not isinstance(dy,int) or isinstance(dy,bool):return False
            x0,y0,x1,y1=LANDSCAPE_HEADER_BOXES[template];box=(x0,y0+dy,x1,y1+dy)
            if not 0<=box[1]<box[3]<=a.height:return False
        else:
            if a.size!=b.size or a.size!=(710,1080):return False
            # Only this registered current card layout. Distinct per-tile checks
            # catch a changed index/author instead of averaging across a whole page.
            box=HEADER_BOXES.get(template)
            if box is None:return False
        a=a.crop(box);b=b.crop(box);delta=ImageChops.difference(a,b);width,height=a.size
        return all(sum(ImageStat.Stat(delta.crop((x,y,min(x+16,width),min(y+16,height)))).mean)/3<6
                   for x in range(0,width,16) for y in range(0,height,16))
    except (OSError,TypeError,ValueError):return False

async def copy_reply(tools,request_id,source_id,epoch):
    source=completed_call(tools,source_id,'grok.read_action');frame=source.get('_action_source_frame');data=source['result']
    if not data.get('verification_passed') or data.get('workspace')!='Example Action Workspace' or not frame or hashlib.sha256(frame).hexdigest()!=data.get('frame_sha256'):
        raise DecisionUnavailable('Fresh process-owned dedicated card evidence is required')
    a=tools.result_reader.agent;p=a.phone;target=p.target_info();link=p.device.links;owner=asyncio.current_task()
    def active():
        tools._active(epoch)
        if owner.done() or tools.calls[request_id]['status']!='in_progress' or a.running or not a.screen.live or not p.device.connected or not p.calibrated or p.device.links!=link or p.target_info()!=target:raise asyncio.CancelledError()
    source_control=public_search_control(frame,'Grok Bot',target,control='prepared_reply_copy')
    if not source_control:raise DecisionUnavailable('Source card has no registered native Copy layout')
    template=source_control.get('template','copy-dark.json')
    dy=source_control.get('dy',0)
    if template in LANDSCAPE_HEADER_BOXES and not isinstance(source_control.get('dy'),int):raise DecisionUnavailable('Source card Copy height was not uniquely located')
    def matched(anchors_only=False):
        active()
        if not same_card_header(frame,a.screen.frame,template,dy):return None
        hit=public_search_control(a.screen.frame,'Grok Bot',target,control='prepared_reply_copy',anchors_only=anchors_only,template=template)
        # A scrolled card at another height is a different source binding.
        if hit and not anchors_only and template in LANDSCAPE_HEADER_BOXES and hit.get('dy')!=dy:return None
        return hit
    current=matched()
    if not current:raise DecisionUnavailable('Actual source card/header/copy glyph changed; no copy sent')
    original=a.screen.frame;start=time.perf_counter()
    with leased_input(active):
        await p.tap(current['x']/1000,current['y']/1000,aim=True,pre_click=lambda:bool(matched(True)))
        active()
    tools.calls[request_id]['_copy_source_frame']=original
    return {'workspace':'Example Action Workspace','card_id':data['card_id'],'target_handle':data['target_handle'],'source_read_request_id':source_id,'source_frame_sha256':hashlib.sha256(original).hexdigest(),'copied_at':time.time(),'copy_command_acknowledged':True,'clipboard_contents_read':False,'byte_exact_copy_verified':False,'requires_destination_verification':True,'copy_s':round(time.perf_counter()-start,4),'published':False,'acknowledged':False,'scope':'Native current code-block Copy command only. Clipboard delivery/text is not inferred from ACK. Paste only into a freshly verified empty correct reply composer; never replay uncertain paste.'}
