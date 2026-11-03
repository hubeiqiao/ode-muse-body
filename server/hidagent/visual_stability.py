"""Conservative local patch stability under color-subsampling noise."""
from PIL import Image,ImageChops,ImageFilter,ImageDraw,ImageStat

def stable_patch(a,b,threshold=6,*,edge_context=None):
    if a.size!=b.size:return False
    if edge_context is not None:
        if not isinstance(edge_context,(tuple,list)) or len(edge_context)!=2:return False
        if any(not isinstance(im,Image.Image) or im.size!=(a.width+4,a.height+4) for im in edge_context):return False
        for core,context in zip((a,b),edge_context):
            if ImageChops.difference(core,context.crop((2,2,a.width+2,a.height+2))).getbbox() is not None:return False
    if sum(ImageStat.Stat(ImageChops.difference(a,b)).mean)/3<threshold:return True
    if min(a.size)<8:return False
    # Color-codec changes can alternate chroma around an unchanged glyph.
    # Require unchanged luminance AND mean chroma, not just a looser RGB limit.
    x,y=a.convert('YCbCr'),b.convert('YCbCr')
    # A one-pixel codec ringing pattern can also affect luma. Its low-frequency
    # structure must agree much MORE tightly than the RGB fast path, while
    # mean color and bidirectional unblurred edge geometry remain mandatory.
    ax=x.getchannel(0).filter(ImageFilter.GaussianBlur(1))
    by=y.getchannel(0).filter(ImageFilter.GaussianBlur(1))
    if ImageStat.Stat(ImageChops.difference(ax,by)).mean[0]>=2:return False
    xm,ym=ImageStat.Stat(x).mean,ImageStat.Stat(y).mean
    if any(abs(xm[i]-ym[i])>=2 for i in range(3)):return False
    edges=[]
    for im in ((a,b) if edge_context is None else edge_context):
        mask=im.convert('L').filter(ImageFilter.FIND_EDGES).point(lambda v:255 if v>40 else 0)
        ImageDraw.Draw(mask).rectangle((0,0,mask.width-1,mask.height-1),outline=0,width=1)
        edges.append(mask)
    for expected,actual in (edges,edges[::-1]):
        covered_mask=actual.filter(ImageFilter.MaxFilter(3))
        if edge_context is not None:
            rect=(2,2,a.width+2,a.height+2)
            expected=expected.crop(rect);covered_mask=covered_mask.crop(rect)
        count=ImageStat.Stat(expected).sum[0]/255
        if count<5:return False # no structural evidence for a color-only exception
        covered=ImageStat.Stat(ImageChops.multiply(expected,covered_mask)).sum[0]/255
        if covered/count<.98:return False
    return True
