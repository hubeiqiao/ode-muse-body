"""Local stability of a visually grounded control, ignoring unrelated video."""
import io
from PIL import Image,ImageChops,ImageStat

def control_region_matches(before,after,box,*,allow_text_hover=False):
    try:
        a=Image.open(io.BytesIO(before)).convert('RGB');b=Image.open(io.BytesIO(after)).convert('RGB')
        if a.size!=b.size:return False
        w,h=a.size
        crop=tuple(round(box[k]*(w if k in ('left','right') else h)) for k in ('left','top','right','bottom'))
        if crop[2]-crop[0]<12 or crop[3]-crop[1]<12:return False
        aa=a.crop(crop);bb=b.crop(crop)
        if sum(ImageStat.Stat(aa).stddev)/3<10:return False
        if sum(ImageStat.Stat(ImageChops.difference(aa,bb)).mean)/3<12:return True
        if not allow_text_hover:return False
        # Known native denial buttons can darken under iPad pointer hover.
        # Preserve actual white label geometry instead of raising RGB tolerance.
        a_mask=[v>180 for v in aa.convert('L').tobytes()]
        b_mask=[v>180 for v in bb.convert('L').tobytes()]
        coverage=sum(a_mask)/len(a_mask)
        if not .01<=coverage<=.45:return False
        union=sum(x or y for x,y in zip(a_mask,b_mask))
        intersection=sum(x and y for x,y in zip(a_mask,b_mask))
        return union>0 and intersection/union>=.9
    except (OSError,ValueError,KeyError,TypeError):return False
