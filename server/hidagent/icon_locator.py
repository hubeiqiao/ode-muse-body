"""Find an app icon on the landscape iPad Home screen by local image match.

Icons move (dock recents, user rearranging), so fixed coordinates break. This
compares a saved 72x72 icon crop against the Home grid slots and the dock row,
in pure PIL (no model, ~50 ms). Frame size: 1646x1080 landscape mirror.
"""
import io
from pathlib import Path

from PIL import Image

ICON_DIR = Path(__file__).parent / 'assets' / 'home-icons'
SIZE = 18                      # compare 18x18 thumbnails
HALF = 36
GRID_X = (313, 517, 720, 923, 1127, 1330)
GRID_Y = (148, 350, 550, 752)
DOCK_Y = 990
MAX_DIFF = 38.0                # mean abs RGB difference on the thumbnails


def _thumb(im, x, y):
    return list(im.crop((x-HALF, y-HALF, x+HALF, y+HALF)).resize((SIZE, SIZE)).getdata())


def _diff(a, b):
    return sum(abs(p[0]-q[0])+abs(p[1]-q[1])+abs(p[2]-q[2]) for p, q in zip(a, b)) / (3*len(a))


def locate(frame, name):
    """Return (x, y) pixel centre of the icon, or None when no slot matches well."""
    path = ICON_DIR / f'{name}.png'
    if not path.exists() or not frame:
        return None
    im = Image.open(io.BytesIO(frame)).convert('RGB')
    if im.size != (1646, 1080):
        return None
    tpl = list(Image.open(path).convert('RGB').resize((SIZE, SIZE)).getdata())
    slots = [(x, y) for y in GRID_Y for x in GRID_X] + [(x, DOCK_Y) for x in range(300, 1400, 6)]
    best = min(((_diff(_thumb(im, x, y), tpl), x, y) for x, y in slots), default=None)
    if best is None or best[0] > MAX_DIFF:
        return None
    return best[1], best[2]
