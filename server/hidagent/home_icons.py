"""Local template-checked home icons for a dedicated iPad. No cloud inference."""
import io,json,os,math
from pathlib import Path
from PIL import Image,ImageChops,ImageStat,ImageDraw

_cache={}
def _layout_for_geometry(frame,target,path=None):
    """Select an existing binding for the actual frame, never rotate a layout."""
    path=path or os.environ.get('APP_ICON_LAYOUT')
    if not path or not frame or not target or target.get('device')!='ipad' or target.get('orientation') not in ('portrait','landscape'):return None
    try:
        p=Path(path);data=json.loads(p.read_text())
        with Image.open(io.BytesIO(frame)) as picture:w,h=picture.size
        rw,rh=data['reference_size']
        orientation='landscape' if rw>rh else 'portrait'
        if target['orientation']!=orientation or data.get('orientation',orientation)!=orientation:return None
        if abs(w/h-rw/rh)>.005:return None
        return p,data
    except (OSError,ValueError,KeyError,TypeError,ZeroDivisionError):return None


def locate_home_icon(frame,app,target,path=None,*,pointer_occlusion=None,_witness=False):
    selected=_layout_for_geometry(frame,target,path)
    if selected is None:return None
    try:
        p,data=selected;stamp=p.stat().st_mtime_ns
        if data.get('witness_only') and not _witness:return None
        if _cache.get('key')!=(str(p),stamp):
            templates={name:[Image.open(p.parent/file).convert('RGB') for file in [v['file'],*v.get('variants',[])]] for name,v in data['icons'].items()}
            guard=[Image.open(p.parent/f).convert('RGB') for f in [data['home_guard']['file'],*data['home_guard'].get('variants',[])]] if data.get('home_guard') else None
            _cache.clear();_cache.update(key=(str(p),stamp),data=data,templates=templates,guard=guard)
        data,templates=_cache['data'],_cache['templates']
        picture=Image.open(io.BytesIO(frame)).convert('RGB');w,h=picture.size
        rw,rh=data['reference_size']
        if app not in data['icons'] or app=='Threads':return None
        if _cache.get('guard') is not None:
            l,t,rr,b=data['home_guard']['crop']
            patch=picture.crop((round(l*w),round(t*h),round(rr*w),round(b*h)))
            if min(sum(ImageStat.Stat(ImageChops.difference(patch.resize(g.size),g)).mean)/3 for g in _cache['guard'])>=14:return None
        matches={}
        for name,item in data['icons'].items():
            l,t,r,b=item['crop'];patch=picture.crop((round(l*w),round(t*h),round(r*w),round(b*h)))
            keep=None
            if name==app and pointer_occlusion is not None:
                observed=pointer_occlusion.get('observed',[]);shift=pointer_occlusion.get('shift')
                if (len(observed)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in observed)
                        or type(shift) not in (int,float) or not math.isfinite(shift) or not 0<abs(shift)<=.03
                        or not l<observed[0]<r or not t<observed[1]<b):return None
                # Only the independently observed pointer's small restore/probe
                # sweep, never an arbitrary caller rectangle. >=65% of the
                # exact target interior must remain visible at the same delta.
                keep=Image.new('L',patch.size,255);draw=ImageDraw.Draw(keep)
                radius=11*w/rw
                centers=[(observed[0],observed[1]),(observed[0]+shift,observed[1])]
                requested=pointer_occlusion.get('target')
                if requested is not None:
                    expected=item['center']
                    if (len(requested)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in requested)
                        or max(abs(requested[i]-expected[i]) for i in (0,1))>.001
                        or not l<requested[0]<r or not t<requested[1]<b):return None
                    margin=min(requested[0]-l,r-requested[0],requested[1]-t,b-requested[1])
                    if max(abs(requested[i]-observed[i]) for i in (0,1))>min(.02,margin*.8):return None
                    centers.append(tuple(requested))
                    # A fresh actual frame AFTER the probe restore no longer
                    # contains the nudged location. Mask fewer pixels, retain
                    # the same visible-area/delta/grid requirements.
                    if pointer_occlusion.get('probe_restored') is True:
                        centers=[tuple(observed),tuple(requested)]
                for cx,cy in centers:
                    px,py=cx*w-round(l*w),cy*h-round(t*h)
                    draw.ellipse((px-radius,py-radius,px+radius,py+radius),fill=0)
                if ImageStat.Stat(keep).mean[0]/255<.65:return None
            delta=min(sum(ImageStat.Stat(ImageChops.difference(patch.resize(template.size),template),
                    keep.resize(template.size) if keep else None).mean)/3 for template in templates[name])
            matches[name]=round(delta,3)
        # Multiple distant exact icons establish the home grid, not an app view,
        # Spotlight blur, page transition or an unrelated screenshot.
        if sum(v<12 for v in matches.values())<8 or matches[app]>=12:return None
        item=data['icons'][app]
        return {'x':round(item['center'][0]*1000),'y':round(item['center'][1]*1000),
                'hitbox':item['crop'],
                'matched_icons':sum(v<12 for v in matches.values()),'target_delta':matches[app]}
    except (OSError,ValueError,KeyError,TypeError):return None


def has_home_icon(app,frame,target,path=None):
    selected=_layout_for_geometry(frame,target,path)
    if selected is None or app=='Threads':return False
    try:return not selected[1].get('witness_only') and app in selected[1]['icons']
    except (OSError,ValueError,KeyError,TypeError):return False


def _witness_layout(frame,target,path=None):
    selected=_layout_for_geometry(frame,target,path)
    if selected is not None:return selected
    # An orientation-specific witness supplies Home proof only, never launch
    # coordinates. Keep custom/missing layout directories isolated as well.
    configured=path or os.environ.get('APP_ICON_LAYOUT')
    if not configured:return None
    orientation=(target or {}).get('orientation')
    if orientation not in ('portrait','landscape'):return None
    selected=_layout_for_geometry(frame,target,Path(configured).with_name(f'witness-{orientation}.json'))
    return selected if selected and selected[1].get('witness_only') is True else None


def home_reference_available(frame,target,path=None):
    """Distinguish absent/unreadable proof from a negative Home observation."""
    selected=_witness_layout(frame,target,path)
    if selected is None:return False
    p,data=selected
    try:
        if len(data['icons'])<8:return False
        items=list(data['icons'].values())
        if data.get('home_guard'):items.append(data['home_guard'])
        for item in items:
            for file in [item['file'],*item.get('variants',[])]:
                with Image.open(p.parent/file) as template:template.verify()
        return True
    except (OSError,ValueError,KeyError,TypeError):return False



def home_grid_witness(frame,target,excluded_app,path=None):
    """Read-only Home witness from two OTHER exact registered icon bindings.

    Each match retains the original background/8-anchor/pixel thresholds.
    This never supplies a click target or weakens the rejected target match.
    """
    selected=_witness_layout(frame,target,path)
    if selected is None:return False
    path,data=selected
    try:names=data['icons']
    except (OSError,ValueError,KeyError):return False
    count=0
    for name in names:
        if name in (excluded_app,'Threads'):continue
        if locate_home_icon(frame,name,target,path,_witness=True):
            count+=1
            if count==2:return True
    return False
