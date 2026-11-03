"""Offline UI-only crop fixtures, with positions independent of the registry JSON."""
import io
import json

import pytest
from PIL import Image, ImageDraw, ImageEnhance

from hidagent import app_controls

TARGET = {"device": "ipad", "orientation": "landscape"}
# app, directory, control, expected normalized tap, (crop, pixel box) fixtures.
FIXTURES = [('TikTok',
  'tiktok',
  'public_search_entry',
  (969, 67),
  [('landscape-entry-live.png', [18, 46, 73, 68]),
   ('landscape-entry-create.png', [789, 1001, 856, 1042]),
   ('landscape-entry-friends.png', [654, 1006, 699, 1037]),
   ('landscape-entry-search.png', [1570, 47, 1621, 99])]),
 ('TikTok',
  'tiktok',
  'public_search_field',
  (300, 62),
  [('landscape-field-back.png', [12, 48, 36, 83]),
   ('landscape-field-submit.png', [1549, 53, 1587, 81]),
   ('landscape-field-magnifier.png', [78, 48, 110, 82])]),
 ('YouTube',
  'youtube',
  'public_search_entry',
  (973, 74),
  [('landscape-entry-home.png', [497, 986, 542, 1037]),
   ('landscape-entry-shorts.png', [648, 987, 692, 1040]),
   ('landscape-entry-subscriptions.png', [940, 988, 1012, 1040]),
   ('landscape-entry-search.png', [1580, 61, 1623, 100])]),
 ('rednote',
  'rednote',
  'public_search_entry',
  (895, 57),
  [('landscape-entry-discover.png', [780, 48, 840, 87]),
   ('landscape-entry-home.png', [124, 1006, 209, 1042]),
   ('landscape-entry-create.png', [794, 1000, 853, 1043]),
   ('landscape-entry-search.png', [1344, 48, 1376, 78])]),
 ('Bilibili',
  'bilibili',
  'public_search_entry',
  (150, 60),
  [('landscape-entry-home.png', [543, 993, 585, 1051]),
   ('landscape-entry-history.png', [1496, 47, 1543, 88]),
   ('landscape-entry-search.png', [124, 50, 157, 81])]),
 ('Douyin',
  'douyin',
  'public_search_entry',
  (915, 73),
  [('landscape-entry-home.png', [466, 1003, 522, 1037]),
   ('landscape-entry-create.png', [794, 994, 851, 1041]),
   ('landscape-entry-recommended.png', [1009, 65, 1053, 100]),
   ('landscape-entry-search.png', [1399, 66, 1419, 87])]),
 ('Instagram',
  'instagram',
  'public_search_entry',
  (32, 500),
  [('landscape-entry-home.png', [31, 276, 73, 318]),
   ('landscape-entry-reels.png', [31, 356, 73, 398]),
   ('landscape-entry-create.png', [31, 681, 73, 723]),
   ('landscape-entry-search.png', [30, 519, 74, 561])]),
 ('X',
  'x',
  'public_search_entry',
  (217, 926),
  [('landscape-entry-home.png', [153, 988, 194, 1026]),
   ('landscape-entry-lists.png', [1267, 988, 1308, 1026]),
   ('landscape-entry-bookmarks.png', [1453, 988, 1494, 1026]),
   ('landscape-entry-search.png', [337, 988, 378, 1026])])]


def frame_bytes(picture):
    stream = io.BytesIO()
    picture.save(stream, format="PNG")
    return stream.getvalue()


def fixture_picture(case, background="black"):
    _, directory, _, _, patches = case
    picture = Image.new("RGB", (1646, 1080), background)
    for filename, box in patches:
        with Image.open(app_controls.ROOT / directory / filename) as crop:
            assert crop.size == (box[2] - box[0], box[3] - box[1])
            picture.paste(crop, tuple(box[:2]))
    return picture


def match(case, picture, target=TARGET, **kwargs):
    return app_controls.public_search_control(
        frame_bytes(picture), case[0], target, control=case[2], **kwargs
    )


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: f"{case[0]}-{case[2]}")
def test_landscape_search_matches_current_ui_and_rejects_missing_glyphs(case):
    picture = fixture_picture(case)
    hit = match(case, picture)
    assert hit is not None, "Observed landscape Search controls must be registered"
    assert (hit["x"], hit["y"]) == case[3]
    assert hit["control"] == case[2]
    assert match(case, fixture_picture(case, "magenta")) == hit
    path = app_controls.ROOT / case[1] / hit["template"]
    data = json.loads(path.read_text())
    assert data["reference_size"] == [1646, 1080]
    assert data["orientation"] == "landscape"
    assert len(data["anchors"]) >= 2
    box = data["patches"][data["target"]]["box"]
    assert hit["target_box"] == dict(zip(
        ("left", "top", "right", "bottom"),
        (box[0]/1646, box[1]/1080, box[2]/1646, box[3]/1080),
    ))
    for _, box in case[4]:
        altered = picture.copy()
        ImageDraw.Draw(altered).rectangle(box, fill="red")
        assert match(case, altered) is None, "Every identity/target patch is required"
    assert match(case, ImageEnhance.Brightness(picture).enhance(.55)) is None
    assert match(case, picture, {"device": "ipad", "orientation": "portrait"}) is None
    assert match(case, picture, {"device": "iphone", "orientation": "landscape"}) is None
    assert match(case, picture.resize((823, 540))) is None
    assert match(case, picture.crop((0, 0, 1646, 220))) is None
    assert match(case, picture, template="../search-landscape.json") is None


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: f"{case[0]}-{case[2]}")
def test_landscape_search_has_no_cross_app_or_blank_frame_match(case):
    assert match(case, Image.new("RGB", (1646, 1080), "black")) is None
    assert match(case, Image.new("RGB", (1646, 1080), "white")) is None
    for other in FIXTURES:
        if other[:3] != case[:3]:
            assert match(case, fixture_picture(other)) is None


def test_tiktok_video_below_fixed_live_crest_does_not_hide_search():
    picture = fixture_picture(FIXTURES[0])
    # The verified For You video reaches y=77, behind the lower LIVE letters.
    ImageDraw.Draw(picture).rectangle((18, 77, 73, 95), fill="white")
    assert match(FIXTURES[0], picture) is not None


def test_x_post_editor_header_blocks_search_even_with_tab_bar():
    case = next(case for case in FIXTURES if case[0] == "X")
    picture = fixture_picture(case)
    # X leaves its tab bar visible on the observed inline reply composer.
    path = app_controls.ROOT / "x/reply-bar-landscape.json"
    data = json.loads(path.read_text())
    for name in ("back", "title"):
        spec = data["patches"][name]
        with Image.open(path.parent / spec["file"]) as crop:
            picture.paste(crop, tuple(spec["box"][:2]))
    assert match(case, picture) is None
