import io

import pytest
from PIL import Image, ImageDraw

from hidagent.vision import changed, pointer

W, H = 498, 1080  # a mirrored iPhone frame


def screen(pointer_at=None, button=False) -> bytes:
    """A synthetic home screen: gradient background, optional button and pointer dot."""
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        draw.line([(0, y), (W, y)], fill=(40, 60 + y // 10, 120))
    if button:
        draw.rectangle([150, 400, 350, 600], fill=(250, 250, 250))
    if pointer_at:
        x, y = pointer_at
        draw.ellipse([x - 12, y - 12, x + 12, y + 12], fill=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue()


def test_pointer_move_is_not_a_screen_change():
    assert not changed(screen((260, 856)), screen((272, 856)), ignore=((260 / W, 856 / H), (272 / W, 856 / H)))


def test_new_content_is_a_screen_change():
    assert changed(screen((260, 856)), screen((260, 856), button=True))


def test_missing_frame_counts_as_changed():
    assert changed(None, screen())


def test_pointer_is_found_from_a_nudge():
    shift = 12 / W
    found = pointer(screen((262, 856)), screen((274, 856)), near=(0.5, 0.8), shift=shift)
    assert found is not None
    assert found[0] * W == pytest.approx(262, abs=3)
    assert found[1] * H == pytest.approx(856, abs=3)


def test_pointer_not_found_when_nothing_moved():
    frame = screen((262, 856))
    assert pointer(frame, frame, near=(0.5, 0.8), shift=12 / W) is None


def checkmark_screen(checked: bool, pointer_at=(100, 500)) -> bytes:
    img = Image.open(io.BytesIO(screen(pointer_at))).convert("RGB")
    if checked:
        ImageDraw.Draw(img).line([(430, 495), (438, 505), (452, 485)], fill=(255, 149, 0), width=4)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue()


def test_small_checkmark_counts_as_a_change():
    assert changed(checkmark_screen(False), checkmark_screen(True))


def test_masked_pointer_move_is_not_a_change():
    before, after = checkmark_screen(False, (100, 500)), checkmark_screen(False, (300, 500))
    assert not changed(before, after, ignore=((100 / W, 500 / H), (300 / W, 500 / H)))


def test_pointer_far_from_the_target_is_found_with_a_full_screen_search():
    before, after = screen((354, 136)), screen((366, 136))
    assert pointer(before, after, near=(0.9, 0.1), shift=12 / W) is None  # outside the window near the target
    found = pointer(before, after, near=(0.5, 0.5), shift=12 / W, reach=(0.5, 0.5))
    assert found[0] * W == pytest.approx(354, abs=3) and found[1] * H == pytest.approx(136, abs=3)


def test_blinking_text_caret_is_not_a_mouse_pointer():
    before=Image.new('RGB',(W,H),'white');after=before.copy()
    ImageDraw.Draw(after).rectangle((200,400,202,425),fill='black')
    def jpeg(img):
        out=io.BytesIO();img.save(out,'JPEG',quality=95);return out.getvalue()
    assert pointer(jpeg(before),jpeg(after),near=(.5,.5),shift=.02,reach=(.5,.5)) is None


def test_ipad_toolbar_pointer_is_above_iphone_status_exclusion():
    before,after=screen((262,42)),screen((274,42))
    found=pointer(before,after,near=(262/W,42/H),shift=12/W,top_margin=.025)
    assert found is not None and found[1]*H==pytest.approx(42,abs=3)
    assert pointer(before,after,near=(262/W,42/H),shift=12/W) is None


def test_verified_toolbar_region_excludes_moving_video_candidate():
    before,after=screen((220,120)),screen((232,120))
    args=dict(near=(.5,.042),shift=12/W,top_margin=.025)
    assert pointer(before,after,**args) is not None
    assert pointer(before,after,**args,bounds=(0,.024,1,.095)) is None


def test_toolbar_pointer_still_found_with_unrelated_video_motion():
    images=[]
    for x,sprite in ((262,220),(274,232)):
        im=Image.open(io.BytesIO(screen((x,42)))).convert('RGB')
        ImageDraw.Draw(im).ellipse((sprite-12,108,sprite+12,132),fill='white')
        output=io.BytesIO();im.save(output,format='JPEG',quality=90);images.append(output.getvalue())
    found=pointer(*images,near=(.5,.042),shift=12/W,top_margin=.025,bounds=(0,.024,1,.095))
    assert found and found[0]*W==pytest.approx(262,abs=3) and found[1]*H==pytest.approx(42,abs=3)


@pytest.mark.parametrize('bounds',[(0,True,1,.095),(0,float('nan'),1,.095),(0,.1,1,.02),(0,.024,2,.095)])
def test_invalid_pointer_region_fails_closed(bounds):
    assert pointer(screen((262,42)),screen((274,42)),near=(.5,.042),shift=12/W,top_margin=.025,bounds=bounds) is None


def test_tight_pointer_bounds_keep_full_image_coordinate_origin():
 before,after=screen((475,378)),screen((463,378))
 found=pointer(before,after,near=(.95,.35),shift=-12/W,top_margin=.025,bounds=(.90,.33,1,.40))
 assert found is not None
 assert abs(found[0]-475/W)<.005 and abs(found[1]-378/H)<.005


def test_pointer_diagnostics_distinguish_static_and_caret_without_changing_result():
 cases=[]
 static=Image.new('RGB',(W,H),'white');caret=static.copy()
 ImageDraw.Draw(caret).rectangle((200,400,202,425),fill='black')
 cases.append((static,static,'too_few_changes'))
 cases.append((static,caret,'non_pointer_shape'))
 for before,after,expected in cases:
  report={'stale':'must not survive'}
  kwargs=dict(near=(.5,.5),shift=.02,reach=(.5,.5))
  assert pointer(before,after,**kwargs,diagnostics=report)==pointer(before,after,**kwargs)
  assert report['outcome']==expected and 'stale' not in report
  assert report['changed_pixels']>=0 and len(report['roi'])==4


def test_pointer_diagnostics_do_not_change_valid_coordinate_or_include_pixels():
 before,after=screen((262,856)),screen((274,856));report={}
 kwargs=dict(near=(.5,.8),shift=12/W)
 assert pointer(before,after,**kwargs,diagnostics=report)==pointer(before,after,**kwargs)
 assert report['outcome']=='found' and report['change_span'][0]>=6
 assert set(report)=={'outcome','roi','roi_pixels','changed_pixels','change_span'}


def test_known_editor_caret_cannot_pull_pointer_centroid_sideways():
 def png(im):
  b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
 before=Image.new('RGB',(710,1080),'white');after=before.copy()
 ImageDraw.Draw(before).ellipse((346,48,364,66),fill='gray')
 ImageDraw.Draw(after).ellipse((352,48,370,66),fill='gray')
 ImageDraw.Draw(after).rectangle((272,45,274,67),fill='blue')
 kw=dict(near=(.5,.052),shift=6/710,top_margin=.025,bounds=(.16,.027,.96,.082))
 old=pointer(png(before),png(after),**kw)
 assert old is None or abs(old[0]*710-355)>5  # Demonstrate the actual union-centroid failure.
 fixed=pointer(png(before),png(after),ignore_caret=True,**kw)
 assert fixed and fixed[0]*710==pytest.approx(355,abs=2) and fixed[1]*1080==pytest.approx(57,abs=2)


def test_caret_filter_alone_never_authorizes_a_click():
 im=Image.new('RGB',(710,1080),'white');after=im.copy();ImageDraw.Draw(after).rectangle((272,45,274,67),fill='blue')
 def png(frame):
  b=io.BytesIO();frame.save(b,format='PNG');return b.getvalue()
 assert pointer(png(im),png(after),(.5,.052),.01,top_margin=.025,ignore_caret=True) is None


def test_instagram_search_roi_excludes_neighbor_activity_and_video():
 def png(im):
  b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
 before=Image.new('RGB',(710,1080),'white');after=before.copy()
 ImageDraw.Draw(before).ellipse((27,531,43,549),fill='gray')
 ImageDraw.Draw(after).ellipse((33,531,49,549),fill='gray')
 ImageDraw.Draw(after).rectangle((65,580,115,612),fill='red')
 args=dict(near=(.049,.5),shift=6/710,top_margin=.025)
 assert pointer(png(before),png(after),**args) is None
 found=pointer(png(before),png(after),bounds=(.008,.47,.09,.53),**args)
 assert found and found[0]*710==pytest.approx(35,abs=2)
