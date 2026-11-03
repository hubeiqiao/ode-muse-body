import io
from PIL import Image, ImageDraw
from hidagent.app_search import learned_target_changed

BOX = {'left': .95, 'top': .04, 'right': .99, 'bottom': .10}


def frame(video_shade, icon_shade=200):
    im = Image.new('RGB', (1646, 1080), (20, 20, 20))
    d = ImageDraw.Draw(im)
    d.rectangle((100, 150, 1500, 1000), fill=(video_shade,) * 3)  # autoplay video area
    d.rectangle((1580, 50, 1620, 100), fill=(icon_shade,) * 3)   # stable magnifier
    buf = io.BytesIO(); im.save(buf, 'JPEG'); return buf.getvalue()


def test_video_motion_outside_learned_box_is_not_a_change():
    assert not learned_target_changed(frame(40), frame(220), BOX)


def test_change_inside_learned_box_waits():
    assert learned_target_changed(frame(40, 200), frame(40, 60), BOX)


def test_invalid_box_is_conservative():
    assert learned_target_changed(frame(40), frame(40), {'left': .5})
