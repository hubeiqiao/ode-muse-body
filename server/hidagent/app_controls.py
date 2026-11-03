"""Whitelisted local App UI controls learned from actual device evidence.
No general coordinate cache: match App brand, multiple navigation anchors and
the target before proposing a public Search tap. Templates are local UI only.
"""
import io,json,os
from pathlib import Path
from PIL import Image,ImageChops,ImageStat,ImageFilter,ImageDraw
ROOT=Path(os.environ.get('APP_CONTROLS_DIR', Path(__file__).resolve().parents[2]/'runtime/ipad/app-controls'))
FILES={('X','preview_post_profile_tab'):ROOT/'x/post-profile.json',('X','preview_return_home_tab'):ROOT/'x/profile-return-home.json',('X','preview_empty_composer'):ROOT/'x/preview-empty.json',('X','example_creator_account_menu'):ROOT/'x/account-menu.json',('X','preview_account_menu_entry'):(ROOT/'x/account-menu-entry.json',ROOT/'x/account-menu-entry-actual.json'),('X','preview_discard_prompt'):(ROOT/'x/preview-discard.json',ROOT/'x/preview-discard-two.json'),('X','preview_filled_composer'):ROOT/'x/preview-filled.json',('X','preview_profile_navigation'):(ROOT/'x/profile-navigation.json',ROOT/'x/profile-navigation-hover.json'),('X','preview_profile_tab'):ROOT/'x/preview-profile.json',('System UI','excluded_password_fill'):ROOT/'system-ui/password-fill-exclusion.json',('Grok Bot','prepared_reply_copy'):(ROOT/'grok/copy-dark.json',ROOT/'grok/copy-compact.json'),('X','example_creator_self_profile'):(ROOT/'x/joe-profile.json',ROOT/'x/joe-profile-dark.json'),('Grok Bot','example_creator_action_pane'):(ROOT/'grok/joe-action.json',ROOT/'grok/joe-action-dark.json',ROOT/'grok/joe-action-status.json'),('System UI','excluded_chatgpt_sidebar'):ROOT/'system-ui/chatgpt-sidebar-exclusion.json',('System UI','excluded_grok_sidebar'):ROOT/'system-ui/grok-sidebar-exclusion.json',('TikTok','public_search_field'):ROOT/'tiktok/field.json',('rednote','native_paste_denial_notice'):ROOT/'rednote/paste-denial.json',('System UI','excluded_notification_overlay'):ROOT/'system-ui/notification-exclusion.json',('Douyin','public_search_field'):ROOT/'douyin/field.json',('Douyin','public_query_text'):ROOT/'douyin/query.json',('App Store','public_query_text'):ROOT/'app_store/query.json',('App Store','public_search_field'):(ROOT/'app_store/field.json',ROOT/'app_store/field-editing.json'),('App Store','public_search_entry'):ROOT/'app_store/search.json',('Safari','public_browser_blank_address'):ROOT/'safari/reddit-blank.json',('Douyin','public_search_form_entry'):ROOT/'douyin/results.json',('Instagram','public_query_text'):ROOT/'instagram/query.json',('YouTube','public_search_field'):ROOT/'youtube/field.json',('Douyin','public_search_entry'):(ROOT/'douyin/search.json',ROOT/'douyin/search-video.json'),('TikTok','public_search_entry'):ROOT/'tiktok/search.json',('TikTok','public_search_form_entry'):(ROOT/'tiktok/results.json',ROOT/'tiktok/results-photos.json',ROOT/'tiktok/results-users-first.json'),('rednote','public_search_entry'):ROOT/'rednote/search.json',('X','public_search_entry'):(ROOT/'x/search.json',ROOT/'x/search-light.json'),('X','public_search_form_entry'):(ROOT/'x/focus.json',ROOT/'x/results.json',ROOT/'x/results-light.json'),('Bilibili','public_query_text'):ROOT/'bilibili/query.json',('Bilibili','public_search_entry'):(ROOT/'bilibili/search.json',ROOT/'bilibili/search-fullheight.json'),('Instagram','public_search_entry'):(ROOT/'instagram/search.json',ROOT/'instagram/search-light.json'),('Instagram','public_search_form_entry'):(ROOT/'instagram/focus.json',ROOT/'instagram/focus-light.json')}

# Landscape (1646x1080) is a separate, closed allowlist of controls actually
# captured in landscape. Portrait templates are never rotated or stretched;
# every template still requires its exact reference_size.
LANDSCAPE_FILES={('Grok Bot','example_creator_action_pane'):ROOT/'grok/joe-action-landscape.json',('Grok Bot','prepared_reply_copy'):ROOT/'grok/copy-landscape.json',('X','example_creator_self_profile'):ROOT/'x/joe-profile-landscape.json',('X','example_creator_reply_composer'):ROOT/'x/reply-account-landscape.json',('X','preview_empty_composer'):ROOT/'x/preview-empty-landscape.json',('X','preview_filled_composer'):ROOT/'x/preview-filled-landscape.json',('X','preview_post_detail'):ROOT/'x/post-detail-landscape.json',('X','preview_reply_bar'):ROOT/'x/reply-bar-landscape.json',('X','preview_x_tab_bar'):ROOT/'x/tab-bar-landscape.json'}

# Public Search controls learned independently in landscape; no portrait reuse.
LANDSCAPE_FILES.update({
    ('rednote', 'public_search_history'): ROOT / 'rednote' / 'search-history-landscape.json',
    ('TikTok', 'public_search_entry'): ROOT / 'tiktok' / 'search-landscape.json',
    ('TikTok', 'public_search_field'): ROOT / 'tiktok' / 'field-landscape.json',
    ('YouTube', 'public_search_entry'): ROOT / 'youtube' / 'search-landscape.json',
    ('rednote', 'public_search_entry'): ROOT / 'rednote' / 'search-landscape.json',
    ('Bilibili', 'public_search_entry'): ROOT / 'bilibili' / 'search-landscape.json',
    ('Douyin', 'public_search_entry'): ROOT / 'douyin' / 'search-landscape.json',
    ('Instagram', 'public_search_entry'): ROOT / 'instagram' / 'search-landscape.json',
    ('X', 'public_search_entry'): ROOT / 'x' / 'search-landscape.json',
})

def registry(target):
    if (target or {}).get('device')!='ipad':return None
    return {'portrait':FILES,'landscape':LANDSCAPE_FILES}.get(target.get('orientation'))

def public_search_control(frame,app,target,*,anchors_only=False,control='public_search_entry',template=None):
    files=registry(target)
    if files is None or (app,control) not in files:return None
    try:picture=Image.open(io.BytesIO(frame)).convert('RGB')
    except (OSError,ValueError,TypeError):return None
    paths=files[(app,control)]
    if isinstance(paths,Path):paths=(paths,)
    for path in paths:
        # Select only a named, registered variant; never load a caller path.
        if template is not None and path.name!=template:continue
        try:
            data=json.loads(path.read_text())
            if data['app']!=app or data['control']!=control:continue
            if list(picture.size)!=data['reference_size'] or len(data['anchors'])<2:continue
            blockers=data.get('blockers',[])
            if (not isinstance(blockers,list) or any(not isinstance(group,list) or len(group)<2
                    or len(set(group))!=len(group) or any(not isinstance(n,str) or n not in data['patches'] for n in group)
                    for group in blockers)):continue
            # Known overlays are negative evidence, including after pointing.
            # They never become another action target or a permission bypass.
            if any(_patches_match(picture,path,data,group) for group in blockers):continue
            dy=None
            if data.get('search') is not None:
                # A control whose row scrolls: anchors stay fixed, the target
                # (and only declared patches) move together vertically. Exactly
                # one visible match is required; ambiguity is a refusal.
                if not _patches_match(picture,path,data,list(data['anchors'])):continue
                if anchors_only:
                    return {'app':app,'control':control,'template':path.name,'dy':None}
                dy=_search_dy(picture,path,data)
                if dy is None:continue
                data=_shifted(data,dy,picture.size)
                if data is None:continue
            names=list(data['anchors'])+([] if anchors_only else [data['target']])
            # Public navigation only: a transparent/selected background may
            # change while the same bright glyph remains. Keep at least TWO
            # anchors on their original exact RGB rule, permit only one
            # independently shape/brightness-checked anchor, never the target.
            nav=(os.environ.get('APP_SEARCH_NAV_ANCHORS','0')=='1'
                 and control in ('public_search_entry','public_search_form_entry'))
            if nav:
                if not _navigation_anchors_match(picture,path,data):continue
                if not anchors_only and not _patches_match(picture,path,data,[data['target']]):continue
            elif not _patches_match(picture,path,data,names):continue
            result={'x':round(data['center'][0]*1000),'y':round(data['center'][1]*1000),'app':app,'control':control,'template':path.name}
            if dy is not None:result['dy']=dy
            if not anchors_only:
                rect=data['patches'][data['target']]['box'];w,h=picture.size
                if (len(rect)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in rect)
                    or not 0<=rect[0]<rect[2]<=w or not 0<=rect[1]<rect[3]<=h):continue
                result['target_box']={'left':rect[0]/w,'top':rect[1]/h,'right':rect[2]/w,'bottom':rect[3]/h}
            region=data.get('pointer_region')
            if region is not None:
                if (len(region)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=1 for v in region)
                        or not region[0]<data['center'][0]<region[2] or not region[1]<data['center'][1]<region[3]):continue
                result['pointer_region']=region
            return result
        except (OSError,ValueError,KeyError,TypeError):continue
    return None

def _search_dy(picture,path,data):
    search=data['search'];spec=data['patches'][data['target']]
    if (not isinstance(search,dict) or search.get('unique') is not True or data['target'] not in search.get('patches',())
            or spec.get('matching') is not None or any(n in data['anchors'] for n in search['patches'])):return None
    low,high=search['dy'];box=spec['box'];w,h=picture.size
    if not (isinstance(low,int) and isinstance(high,int) and low<=high and box[1]+low>=0 and box[3]+high<=h):return None
    file=(path.parent/spec['file']).resolve()
    if not file.is_relative_to(path.parent.resolve()):return None
    with Image.open(file) as stored:expected=stored.convert('RGB')
    if expected.size!=(box[2]-box[0],box[3]-box[1]) or sum(ImageStat.Stat(expected).stddev)/3<10:return None
    hits=[]
    for dy in range(low,high+1):
        actual=picture.crop((box[0],box[1]+dy,box[2],box[3]+dy))
        score=sum(ImageStat.Stat(ImageChops.difference(actual,expected)).mean)/3
        if score<10:hits.append((score,dy))
    if not hits:return None
    best=min(hits)[1]
    # Neighbouring offsets of the same glyph are one match; any match more
    # than 6px away is a second visible control (ambiguous card).
    if any(abs(dy-best)>6 for _,dy in hits):return None
    return best

def _shifted(data,dy,size):
    w,h=size;moved=dict(data);moved['patches']={k:dict(v) for k,v in data['patches'].items()}
    for name in data['search']['patches']:
        b=moved['patches'][name]['box'];moved['patches'][name]['box']=[b[0],b[1]+dy,b[2],b[3]+dy]
        if not 0<=b[1]+dy<b[3]+dy<=h:return None
    moved['center']=[data['center'][0],data['center'][1]+dy/h]
    if data.get('pointer_region') is not None:return None
    return moved

def _navigation_anchors_match(picture,path,data):
    anchors=data['anchors']
    exact=[name for name in anchors if _patches_match(picture,path,data,[name])]
    pending=[name for name in anchors if name not in exact]
    if not pending:return True
    if len(exact)<2 or len(pending)!=1:return False
    spec=data['patches'][pending[0]]
    if spec.get('matching') is not None:return False
    file=(path.parent/spec['file']).resolve()
    if not file.is_relative_to(path.parent.resolve()):return False
    actual=picture.crop(tuple(spec['box']))
    with Image.open(file) as stored:expected=stored.convert('RGB')
    if actual.size!=expected.size or sum(ImageStat.Stat(expected).stddev)/3<10:return False
    if not bright_glyphs_match(expected,actual):return False
    reference=expected.convert('L');observed=actual.convert('L')
    mask=reference.point(lambda v:255 if v>210 else 0)
    # Reject dimming as well as altered geometry. This fallback is stricter
    # than the existing absolute glyph-brightness floor and never ignores
    # an anchor or a known blocking overlay.
    return ImageStat.Stat(observed,mask).mean[0]>=.98*ImageStat.Stat(reference,mask).mean[0]


def _patches_match(picture,path,data,names):
    for name in names:
        spec=data['patches'][name];file=(path.parent/spec['file']).resolve()
        if not file.is_relative_to(path.parent.resolve()):return False
        actual=picture.crop(tuple(spec['box']))
        with Image.open(file) as stored:expected=stored.convert('RGB')
        if actual.size!=expected.size or sum(ImageStat.Stat(expected).stddev)/3<10:return False
        if spec.get('matching') in ('white_glyph_edges','gray_search_glyph_edges'):
            # Only explicitly learned bright UI glyphs over transparent video.
            # Require the original glyph edge geometry AND undimmed foreground;
            # plain bright backgrounds and dimmed permission overlays reject.
            if not bright_glyphs_match(expected,actual,gray_search=spec.get('matching')=='gray_search_glyph_edges'):return False
        elif spec.get('matching') is not None:return False
        elif sum(ImageStat.Stat(ImageChops.difference(actual,expected)).mean)/3>=10:return False
    return True

def bright_glyphs_match(expected,actual,*,gray_search=False):
    """Existing glyph-edge rule shared by registered controls and owned UI hover."""
    if actual.size!=expected.size:return False
    reference=expected.convert('L');observed=actual.convert('L')
    mask=reference.filter(ImageFilter.FIND_EDGES).point(lambda v:255 if v>60 else 0)
    edges=observed.filter(ImageFilter.FIND_EDGES).point(lambda v:255 if v>40 else 0).filter(ImageFilter.MaxFilter(3))
    for part in (mask,edges):ImageDraw.Draw(part).rectangle((0,0,part.width-1,part.height-1),outline=0,width=2)
    required=ImageStat.Stat(mask).sum[0]/255
    foreground=reference.point(lambda v:255 if v>(180 if gray_search else 210) else 0)
    if required<20 or ImageStat.Stat(foreground).sum[0]==0:return False
    if ImageStat.Stat(ImageChops.multiply(mask,edges)).sum[0]/255/required<.90:return False
    return ImageStat.Stat(observed,foreground).mean[0]>=(185 if gray_search else 200)


# UI-only crops measured from independently inspected saved retest9 frames.
# Kept with the offline evidence package; no portrait rotation or scaling.
LANDSCAPE_RESULTS_FILES = {
    app: Path(__file__).resolve().parents[2] / 'tools/judge_replay/templates' / (app.lower() + '.json')
    for app in ('TikTok', 'YouTube', 'rednote')
}


def landscape_results_control(frame, app, target):
    """Read/match only. Results tabs can match loading, so callers need body OCR."""
    if (target or {}).get('device') != 'ipad' or target.get('orientation') != 'landscape':
        return None
    path = LANDSCAPE_RESULTS_FILES.get(app)
    if path is None:
        return None
    try:
        picture = Image.open(io.BytesIO(frame)).convert('RGB')
        data = json.loads(path.read_text())
        if (data['app'] != app or list(picture.size) != data['reference_size']
                or len(data['anchors']) < 3):
            return None
        if not _patches_match(picture, path, data, data['anchors']):
            return None
        # Reuse the already registered YouTube bottom navigation as an
        # additional independent App signal, with its existing matching rules.
        if app == 'YouTube' and not public_search_control(frame, app, target, anchors_only=True):
            return None
        return {'app': app, 'template': path.name}
    except (OSError, ValueError, KeyError, TypeError):
        return None
