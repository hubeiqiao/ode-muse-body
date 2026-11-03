"""Small image checks on screen frames: did the screen change, and where is the pointer."""

import io
import math
import os

from PIL import Image, ImageChops


def _gray(jpeg: bytes) -> Image.Image:
    return jpeg.convert("L") if isinstance(jpeg,Image.Image) else Image.open(io.BytesIO(jpeg)).convert("L")


def changed(before: bytes | Image.Image | None, after: bytes | Image.Image | None, ignore: tuple = (), threshold: float = 1.0, top_margin: float = .06) -> bool:
    """True when the screen content changed, including small things such as a checkmark.

    `ignore` lists screen points (fractions) where the pointer was or went: those spots are masked, so pointer
    moves never count. The status bar (clock) is ignored too.
    """
    if not before or not after:
        return True
    a, b = _gray(before), _gray(after)
    if a.size != b.size:
        return True
    small = ImageChops.difference(a.resize((32, 64), Image.BILINEAR), b.resize((32, 64), Image.BILINEAR)).tobytes()
    if sum(small) / len(small) > threshold:  # a big change: new screen, scrolled list, keyboard
        return True
    w, h = a.size[0] // 2, a.size[1] // 2  # fine pass for small changes, at half resolution
    diff = ImageChops.difference(a.resize((w, h), Image.BILINEAR), b.resize((w, h), Image.BILINEAR)).tobytes()
    masks = [(px * w, py * h) for px, py in ignore]
    hits = 0
    for i, v in enumerate(diff):
        if v > 25:
            x, y = i % w, i // w
            if y > top_margin * h and all((x - mx) ** 2 + (y - my) ** 2 > (0.05 * w) ** 2 for mx, my in masks):
                hits += 1
    return hits >= 6


def pointer(before: bytes, after: bytes, near: tuple[float, float], shift: float,
            reach: tuple[float, float] = (0.12, 0.08), *, top_margin: float = .05, bounds: tuple[float,float,float,float] | None = None, diagnostics: dict | None = None, ignore_caret: bool = False, axis: str = "x", confirm_motion: bool = False, faint_motion: bool = False) -> tuple[float, float] | None:
    """Where the pointer is in `before`, given it moved right by `shift` (fraction of width) in `after`.

    Only the pointer moved between the two frames, so the changed pixels within `reach` of `near` are the
    pointer at both spots; their centroid sits shift/2 to the right of the first one. None if unclear:
    nothing moved, or the change is too big or too spread out to be one small pointer.

    POINTER_TWO_BLOB_CONFIRM=1 instead confirms two temporal blobs and returns
    the BEFORE position, matching callers that restore the nudge. Only that
    opt-in path supports axis="y" (shift is then a fraction of height). The
    default remains 0 pending device validation.

    confirm_motion explicitly requires that same temporal proof for a local
    Home reacquisition, independent of the legacy detector's environment flag.
    faint_motion requires a translated disk silhouette and temporal polarity,
    allowing low-contrast landscape pointers without accepting arbitrary motion.
    """
    def reject(reason):
        if diagnostics is not None:diagnostics["outcome"]=reason
        return None
    if diagnostics is not None:diagnostics.clear()
    a, b = _gray(before), _gray(after)
    if a.size != b.size:
        return reject("geometry_changed")
    w, h = a.size
    rx, ry = reach
    left, top = max(0, int((near[0] - rx) * w)), max(int(top_margin * h), int((near[1] - ry) * h))  # skip the status bar
    box = (left, top, min(w, int((near[0] + rx) * w)), min(h, int((near[1] + ry) * h)))
    if bounds is not None:
        if len(bounds)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<=v<=1 for v in bounds):return reject("invalid_bounds")
        l,t,r,bt=bounds
        if l>=r or t>=bt:return reject("invalid_bounds")
        box=(max(box[0],int(l*w)),max(box[1],int(t*h)),min(box[2],int(r*w)),min(box[3],int(bt*h)))
    if box[0]>=box[2] or box[1]>=box[3]:return reject("empty_roi")
    if diagnostics is not None:diagnostics["roi"]=list(box)
    diff = ImageChops.difference(a.crop(box), b.crop(box)).tobytes()
    bw = box[2] - box[0]
    hits = [(i % bw, i // bw) for i, v in enumerate(diff) if v > (8 if faint_motion else 30)]
    if diagnostics is not None:diagnostics.update(changed_pixels=len(hits),roi_pixels=len(diff))
    if not 15 <= len(hits) <= len(diff) // 4:  # nothing moved, or much more than a pointer
        return reject("too_few_changes" if len(hits)<15 else "too_many_changes")
    # Opt-in only for whitelisted iPad editor toolbars. A disconnected,
    # densely filled narrow vertical caret can blink during the pointer probe.
    # Remove at most two such strokes; every remaining pointer count/shape/
    # spread check stays unchanged. Never pick the nearest arbitrary component.
    if ignore_caret and len(hits)<=2000:
        remaining=set(hits);strokes=set();count=0
        while remaining:
            seed=remaining.pop();component={seed};todo=[seed]
            while todo:
                px,py=todo.pop()
                for dx,dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                    point=(px+dx,py+dy)
                    if point in remaining:remaining.remove(point);component.add(point);todo.append(point)
            xx=[p[0] for p in component];yy=[p[1] for p in component]
            width=max(xx)-min(xx)+1;height=max(yy)-min(yy)+1
            if width<=4 and 12<=height<=40 and len(component)/(width*height)>=.8:
                strokes.update(component);count+=1
        if strokes and count<=2 and len(strokes)<=160:
            hits=[p for p in hits if p not in strokes]
            if diagnostics is not None:diagnostics['excluded_caret_pixels']=len(strokes)
            if len(hits)<15:return reject('too_few_changes')
    if faint_motion or confirm_motion or os.environ.get('POINTER_TWO_BLOB_CONFIRM', '0') == '1':
        return _pointer_two_blob(a, b, box, hits, shift, axis, diagnostics, faint_motion=faint_motion)
    xs, ys = [x for x, _ in hits], [y for _, y in hits]
    if diagnostics is not None:diagnostics["change_span"]=[max(xs)-min(xs)+1,max(ys)-min(ys)+1]
    if max(xs) - min(xs) > 0.15 * w or max(ys) - min(ys) > 0.07 * h:  # not one compact spot
        return reject("change_too_spread")
    # A blinking insertion caret is a thin vertical stroke, not a circular
    # mouse pointer translated horizontally. It must not poison calibration.
    span_x, span_y = max(xs)-min(xs)+1, max(ys)-min(ys)+1
    if span_x < 6 or span_y < 6 or span_x < .55 * span_y:
        return reject("non_pointer_shape")
    cx = sum(x for x, _ in hits) / len(hits) + box[0]
    cy = sum(y for _, y in hits) / len(hits) + box[1]
    if diagnostics is not None:diagnostics["outcome"]="found"
    return cx / w - shift / 2, cy / h


def _pointer_two_blob(before, after, box, hits, shift, axis, diagnostics, *, faint_motion=False):
    """Confirm a translated compact pair, including its temporal direction.

    Unsigned difference alone cannot distinguish opposite movements. Local
    contrast against unchanged surrounding pixels must disappear in BEFORE's
    blob and appear in AFTER's blob, with the same light/dark polarity.
    No numpy dependency, morphology, learned coordinates, or nearest-blob pick.
    """
    def reject(reason):
        if diagnostics is not None:
            diagnostics['outcome'] = reason
        return None

    w, h = before.size
    bw, bh = box[2]-box[0], box[3]-box[1]
    xs, ys = zip(*hits)
    if diagnostics is not None:
        diagnostics['change_span'] = [max(xs)-min(xs)+1, max(ys)-min(ys)+1]
    if faint_motion:
        return _faint_pointer_pair(before, after, box, hits, shift, axis, diagnostics)
    remaining = set(hits)
    ap, bp = before.load(), after.load()
    polarity = {(x,y): bp[x+box[0], y+box[1]] > ap[x+box[0], y+box[1]] for x,y in hits}
    blobs = []
    while remaining:
        seed = remaining.pop()
        blob, todo = [seed], [seed]
        while todo:
            x, y = todo.pop()
            for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                point = x+dx, y+dy
                # Overlapping pointer disks leave touching opposite-sign
                # crescents. They are two temporal blobs, not one union blob.
                if point in remaining and polarity[point] == polarity[seed]:
                    remaining.remove(point)
                    blob.append(point)
                    todo.append(point)
        blobs.append(blob)
    blobs.sort(key=lambda blob: min(blob))
    if diagnostics is not None:
        diagnostics['blob_count'] = len(blobs)
        diagnostics['blob_areas'] = [len(blob) for blob in blobs]
    if len(blobs) == 1:
        return reject('single_blob')
    if len(blobs) != 2 or axis not in ('x', 'y') or not math.isfinite(shift) or shift == 0:
        return reject('shift_inconsistent')

    # Retain the legacy total-spread gate, rotated for explicit vertical probes.
    limits = (.15*w, .07*h) if axis == 'x' else (.07*w, .15*h)
    if max(xs)-min(xs) > limits[0] or max(ys)-min(ys) > limits[1]:
        return reject('change_too_spread')
    if min(map(len, blobs)) / max(map(len, blobs)) < .65:
        return reject('shift_inconsistent')

    all_hits = set(hits)
    centers, contrasts = [], []
    for blob in blobs:
        xx, yy = zip(*blob)
        l, t, r, bt = min(xx), min(yy), max(xx), max(yy)
        width, height = r-l+1, bt-t+1
        if (min(width, height) < 6 or max(width, height) > .05*min(w, h)
                or not .55 <= width/height <= 1/.55
                or len(blob)/(width*height) < .45):
            return reject('shift_inconsistent')
        ring = [(x, y) for x in range(max(0,l-3), min(bw,r+4))
                for y in range(max(0,t-3), min(bh,bt+4))
                if (x < l or x > r or y < t or y > bt) and (x,y) not in all_hits]
        if not ring:
            return reject('shift_inconsistent')
        contrast = []
        for frame in (before, after):
            pixels = frame.load()
            mean = sum(pixels[x+box[0], y+box[1]] for x,y in blob)/len(blob)
            background = sum(pixels[x+box[0], y+box[1]] for x,y in ring)/len(ring)
            contrast.append(mean-background)
        contrasts.append(contrast)
        centers.append((sum(xx)/len(blob)+box[0], sum(yy)/len(blob)+box[1]))
    if diagnostics is not None:
        diagnostics['blob_centers_px'] = [list(center) for center in centers]
        diagnostics['blob_contrast'] = contrasts
    # Require independent appearance/disappearance, rather than assigning the
    # AFTER side from the command (which would accept a wrong-direction pair).
    appearing = [i for i,(a,b) in enumerate(contrasts) if abs(b)-abs(a) >= 15]
    disappearing = [i for i,(a,b) in enumerate(contrasts) if abs(a)-abs(b) >= 15]
    if len(appearing) != 1 or len(disappearing) != 1:
        return reject('shift_inconsistent')
    end, start = appearing[0], disappearing[0]
    if contrasts[start][0]*contrasts[end][1] <= 0:
        return reject('shift_inconsistent')
    ai = 0 if axis == 'x' else 1
    delta = [centers[end][i]-centers[start][i] for i in (0,1)]
    expected = shift*(w if ai == 0 else h)
    # Evidence: settled gain ratios 0.992..1.04; 20u overlapping difference
    # crescents bias the centers outward. 35% allows that segmentation error;
    # <=2px off-axis covers JPEG jitter (measured <=0.23px).
    if diagnostics is not None:
        diagnostics.update(expected_shift_px=expected, observed_shift_px=delta,
                           magnitude_tolerance=.35, off_axis_tolerance_px=2)
    if delta[ai]*expected <= 0 or abs(delta[ai]-expected) > .35*abs(expected) or abs(delta[1-ai]) > 2:
        return reject('shift_inconsistent')
    if diagnostics is not None:
        diagnostics['outcome'] = 'found'
    return centers[start][0]/w, centers[start][1]/h


def _faint_pointer_pair(before, after, box, hits, shift, axis, diagnostics):
    """Fit the signed change of two equal disks translated by the actual probe.

    Overlapping disks leave crescents whose centroids overstate the movement.
    Fit their union silhouette instead, then check appearance/disappearance
    against unchanged surrounding pixels. All motion must fit this one pair;
    never choose a convenient pair out of animation or a partial pointer.
    """
    def reject():
        if diagnostics is not None:diagnostics['outcome']='shift_inconsistent'
        return None
    w,h=before.size
    if axis not in ('x','y') or not math.isfinite(shift) or not shift:return reject()
    dx,dy=(shift*w,0) if axis=='x' else (0,shift*h)
    xs,ys=zip(*hits)
    l,t,r,b=min(xs),min(ys),max(xs),max(ys)
    diameter=(b-t+1) if axis=='x' else (r-l+1)
    # Entire round silhouette must be observed, including both outer edges.
    if (not 8<=diameter<=.05*min(w,h) or l<=0 or t<=0
            or r>=box[2]-box[0]-1 or b>=box[3]-box[1]-1):return reject()
    if abs((r-l+1)-abs(dx)-diameter)>3 or abs((b-t+1)-abs(dy)-diameter)>3:return reject()
    start=((l+r-dx)/2,(t+b-dy)/2)
    end=(start[0]+dx,start[1]+dy)
    radius=(diameter-1)/2+.3
    predicted={}
    for y in range(t,b+1):
        for x in range(l,r+1):
            first=(x-start[0])**2+(y-start[1])**2<=radius**2
            second=(x-end[0])**2+(y-end[1])**2<=radius**2
            if first!=second:predicted[x,y]=first
    if not predicted:return reject()
    support=sum(point in predicted for point in hits)
    # >=90% of observed motion must fit the round pair (a translated square
    # only fits ~84%). Text under a translucent disk can leave holes, so
    # require >=70% of the predicted silhouette to be independently visible.
    if support/len(hits)<.9 or support/len(predicted)<.7:return reject()
    ap,bp=before.load(),after.load()
    # A dark disk lightens at its old location and darkens at its new one;
    # a bright disk does the inverse. Require one consistent polarity.
    signs=[(bp[x+box[0],y+box[1]]>ap[x+box[0],y+box[1]])==predicted[x,y]
           for x,y in hits if (x,y) in predicted]
    if max(sum(signs),len(signs)-sum(signs))/len(signs)<.9:return reject()
    # Unchanged ring establishes that a real contrast disappears and appears,
    # rather than two patches merely changing brightness on a flat page.
    ring=[(x,y) for y in range(max(0,t-3),min(box[3]-box[1],b+4))
          for x in range(max(0,l-3),min(box[2]-box[0],r+4))
          if x<l or x>r or y<t or y>b]
    if not ring:return reject()
    backgrounds=[sum(p[x+box[0],y+box[1]] for x,y in ring)/len(ring) for p in (ap,bp)]
    contrasts=[]
    for old in (True,False):
        points=[(x,y) for x,y in hits if predicted.get((x,y)) is old]
        if len(points)<15:return reject()
        contrasts.append([sum(p[x+box[0],y+box[1]] for x,y in points)/len(points)-bg
                          for p,bg in zip((ap,bp),backgrounds)])
    if (abs(contrasts[0][0])-abs(contrasts[0][1])<8
            or abs(contrasts[1][1])-abs(contrasts[1][0])<8
            or contrasts[0][0]*contrasts[1][1]<=0):return reject()
    if diagnostics is not None:
        diagnostics.update(outcome='found',faint_motion=True,expected_shift_px=[dx,dy],
                           silhouette_support=round(support/len(hits),3),blob_contrast=contrasts)
    return (start[0]+box[0])/w,(start[1]+box[1])/h
