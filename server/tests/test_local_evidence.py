"""Offline public-search evidence contracts using saved frames and fake judges."""
import asyncio
import copy
import io
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from hidagent import app_query_text as text
from hidagent import app_controls as controls
from hidagent.vision_judge import JudgeFallback
from hidagent.local_text import LocalTextReader
from test_landscape_query_ocr import agent, observation, row, frame
from test_landscape_caret import editor
from test_judge_overlap import Judge, result, Q
FRAMES = Path(__file__).parent / 'local_evidence_frames'
TARGET = {'device':'ipad','orientation':'landscape'}


def local_native_reader():
    # Snapshot tests never execute a binary from the owner's live formal tree.
    binary = Path(__file__).resolve().parents[2] / 'work/native-probe/vision-ocr'
    if not binary.is_file():
        pytest.skip('Optional worktree-local native OCR binary unavailable; fake OCR tests still run')
    return LocalTextReader([str(binary), '--serve'], timeout=30)


@pytest.mark.parametrize('glyph', ['|','I','l'])
def test_caret_suffix_requires_actual_row_end_caret_pixels(monkeypatch, glyph):
    a=agent(monkeypatch);a.screen.frame,r,_=editor('journey')
    a.query_text_reader.read.side_effect=[observation([{**r,'text':'journey'+glyph}]),observation([r])]
    assert asyncio.run(text.observe_landscape_query(a, {'query':'journey'},a.screen.frame))
    a.screen.frame,r,_=editor('journey'+glyph,caret=False)
    a.query_text_reader.read.side_effect=None
    a.query_text_reader.read.return_value=observation([r])
    assert asyncio.run(text.observe_landscape_query(a, {'query':'journey'},a.screen.frame)) is None

def test_app_band_excludes_button_and_suggestions(monkeypatch):
    a=agent(monkeypatch);a.query_text_reader.read.return_value=observation([row('Ielts speaking')])
    assert asyncio.run(text.observe_landscape_query(a,{'app':'TikTok','query':'ielts speaking'},a.screen.frame))
    crop=Image.open(io.BytesIO(a.query_text_reader.read.call_args.args[0]))
    assert crop.size==((1480-117)*2,(94-40)*2)

def test_results_label_never_strips_caret_like_real_suffix(monkeypatch):
    a=agent(monkeypatch,submitted=True)
    a.query_text_reader.read.return_value=observation([row('journeyl')])
    assert asyncio.run(text.observe_landscape_query(a,{'app':'TikTok','query':'journey'},a.screen.frame,results=True)) is None

def test_real_residue_rejected_by_native_fast_ocr(monkeypatch):
    async def go():
        a=agent(monkeypatch);reader=local_native_reader()
        a.query_text_reader=reader;a.screen.frame=(FRAMES/'r07-tt-A-post.jpg').read_bytes()
        goal={'app':'TikTok','query':'ielts speaking part 3 public libraries future'}
        a.steps[1]['text']=goal['query']
        try:
            assert await text.verify_landscape_literal(a,goal,a.screen.frame) is None
            assert reader._cached is not None
        finally:await reader.close()
    asyncio.run(go())

@pytest.mark.parametrize('app,file',[('YouTube','r01-yt-A-post.jpg'),('TikTok','r03-tt-A-post.jpg'),('rednote','r05-rn-A-post.jpg')])
def test_results_anchors_match_real_chrome_reject_other_app_and_blank(app,file):
    saved=(FRAMES/file).read_bytes()
    assert controls.landscape_results_control(saved,app,TARGET)
    assert not controls.landscape_results_control(frame(),app,TARGET)
    assert not controls.landscape_results_control(saved,app,{'device':'ipad','orientation':'portrait'})
    for other in ('YouTube','TikTok','rednote'):
        if other!=app:assert not controls.landscape_results_control(saved,other,TARGET)

@pytest.mark.parametrize('signal',['query','anchors','loaded_body','context','stale','flag'])
def test_results_requires_all_current_task_signals(monkeypatch,signal):
    a=agent(monkeypatch,submitted=True);monkeypatch.setenv('LANDSCAPE_RESULTS_LOCAL','1')
    monkeypatch.setattr(controls,'landscape_results_control',lambda *args:{'template':'offline-tested'})
    monkeypatch.setattr(text,'observe_landscape_query',AsyncMock(return_value={'seconds':.01}))
    monkeypatch.setattr(text,'_landscape_loaded_results',AsyncMock(return_value=True))
    if signal=='query':text.observe_landscape_query.return_value=None
    if signal=='anchors':monkeypatch.setattr(controls,'landscape_results_control',lambda *args:None)
    if signal=='loaded_body':text._landscape_loaded_results.return_value=False
    if signal=='context':a.steps[-1].pop('act_s')
    if signal=='stale':a.screen.frame=frame(typed=False)
    if signal=='flag':monkeypatch.delenv('LANDSCAPE_RESULTS_LOCAL')
    assert asyncio.run(text.verify_landscape_results(a,{'app':'YouTube','query':'IELTS speaking'},frame(results=True))) is None

def test_dual_signal_results_bypasses_judge(monkeypatch):
    from hidagent.app_search import next_search_actions
    a=agent(monkeypatch,submitted=True);monkeypatch.setenv('LANDSCAPE_RESULTS_LOCAL','1')
    monkeypatch.setattr(controls,'landscape_results_control',lambda *args:{'template':'offline-tested'})
    monkeypatch.setattr(text,'observe_landscape_query',AsyncMock(return_value={'seconds':.01}))
    monkeypatch.setattr(text,'_landscape_loaded_results',AsyncMock(return_value=True))
    assert asyncio.run(next_search_actions(a,{'app':'YouTube','query':'IELTS speaking'}))[0]['action']=='done'
    a.client.evaluate.assert_not_awaited()

@pytest.mark.parametrize('purpose',['locate public search entry','locate editable app search'])
def test_skip_jev_locate_optin_landscape_only(monkeypatch,purpose):
    monkeypatch.setenv('JEV_SKIP_LANDSCAPE_LOCATE','1')
    async def go():
        async def primary():raise AssertionError('Jev must be skipped')
        async def backup():return result(1)
        c=JudgeFallback(Judge('jev',primary),Judge('muse',backup,'meta'))
        s={'policy':'app_public_search','purpose':purpose,'device_family':'ipad','orientation':'landscape'}
        r=await c.evaluate(s,Q,frame())
        assert r['approved'] and r['primary_skipped'] and not c.primary.calls
        assert c.stats['primary_calls']==0 and c.stats['backup_calls']==1
    asyncio.run(go())

@pytest.mark.parametrize('case',['unset','off','portrait','iphone','no_geometry','other_purpose','other_policy'])
def test_skip_other_routes_keep_primary(monkeypatch,case):
    monkeypatch.setenv('JEV_SKIP_LANDSCAPE_LOCATE','1')
    if case=='unset':monkeypatch.delenv('JEV_SKIP_LANDSCAPE_LOCATE')
    if case=='off':monkeypatch.setenv('JEV_SKIP_LANDSCAPE_LOCATE','0')
    s={'policy':'app_public_search','purpose':'locate public search entry','device_family':'ipad','orientation':'landscape'}
    if case=='portrait':s['orientation']='portrait'
    if case=='iphone':s['device_family']='iphone'
    if case=='other_purpose':s['purpose']='verify public search results'
    if case=='other_policy':s['policy']='precise_control_grounding'
    async def go():
        async def p():return result(1)
        c=JudgeFallback(Judge('jev',p),Judge('muse',p,'meta'))
        await c.evaluate(s,Q,b'invalid' if case=='no_geometry' else frame())
        assert len(c.primary.calls)==1
    asyncio.run(go())

@pytest.mark.parametrize('change',['same','questions','purpose','state','frame','task','failure','mutation'])
def test_same_frame_cache_task_context_and_defensive_copy(change):
    async def go():
        async def p():return result(.5)
        async def b():return result(1)
        c=JudgeFallback(Judge('jev',p),Judge('muse',b,'meta'))
        s={'policy':'app_public_search','purpose':'verify public search results'};q=copy.deepcopy(Q);image=frame()
        r=await c.evaluate(s,q,image)
        if change=='questions':q['ready']['instructions']='different'
        if change=='purpose':s['purpose']='another'
        if change=='state':s['query']='changed'
        if change=='frame':image=frame(typed=False)
        if change=='task':c.reset_stats()
        if change=='mutation':r['answers']['ready']['noul']=0
        if change=='failure':
            from hidagent.decision import DecisionUnavailable
            async def fail():raise DecisionUnavailable('offline failure')
            c.reset_stats();c.backup.fn=fail
            for _ in range(2):
                with pytest.raises(DecisionUnavailable):await c.evaluate(s,q,image)
            assert len(c.backup.calls)==3
            return
        r=await c.evaluate(s,q,image)
        assert len(c.backup.calls)==(1 if change in ('same','mutation') else 2)
        if change in ('same','mutation'):assert r['cache_hit'] and r['seconds']==0 and r['answers']['ready']['noul']==1
    asyncio.run(go())

@pytest.mark.parametrize('app,file',[('YouTube','r01-yt-A-post.jpg'),('TikTok','r03-tt-A-post.jpg'),('rednote','r05-rn-A-post.jpg')])
def test_native_loaded_results_and_same_chrome_skeleton(monkeypatch,app,file):
    from PIL import ImageDraw
    async def go():
        a=agent(monkeypatch,submitted=True);monkeypatch.setenv('LANDSCAPE_RESULTS_LOCAL','1')
        reader=local_native_reader()
        a.query_text_reader=reader
        goals={'YouTube':'ielts speaking part 1 bicycles and cycling','TikTok':'ielts speaking part 1 weekend breakfast habits','rednote':'ielts speaking part 1 rainy days and umbrellas'}
        goal={'app':app,'query':goals[app]};a.steps[1]['text']=goal['query'];a.screen.frame=(FRAMES/file).read_bytes()
        try:
            assert await text.verify_landscape_results(a,goal,a.screen.frame)
            picture=Image.open(io.BytesIO(a.screen.frame)).convert('RGB');draw=ImageDraw.Draw(picture)
            # Preserve actual query, tabs and bottom navigation. Spinner and
            # skeleton rectangles cannot prove loaded result cards.
            body={'YouTube':(0,120,1646,970),'TikTok':(0,165,1646,1080),'rednote':(0,240,1646,1080)}[app]
            draw.rectangle(body,fill='#eeeeee');draw.rounded_rectangle((430,400,800,680),radius=20,fill='#dddddd')
            draw.arc((805,480,845,520),20,260,fill='#999999',width=4)
            buf=io.BytesIO();picture.save(buf,format='PNG');a.screen.frame=buf.getvalue()
            assert controls.landscape_results_control(a.screen.frame,app,TARGET)
            assert await text.verify_landscape_results(a,goal,a.screen.frame) is None
        finally:await reader.close()
    asyncio.run(go())


def test_cache_inflight_previous_task_cannot_seed_next_task():
    async def go():
        began=asyncio.Event();resume=asyncio.Event()
        async def p():began.set();await resume.wait();return result(1)
        c=JudgeFallback(Judge('jev',p),Judge('muse',p,'meta'));s={'policy':'app_public_search'}
        first=asyncio.create_task(c.evaluate(s,Q,frame()))
        await began.wait();c.reset_stats();resume.set();await first
        await c.evaluate(s,Q,frame());assert len(c.primary.calls)==2
    asyncio.run(go())

@pytest.mark.parametrize('glyph',['|','I','l'])
def test_separate_ocr_caret_row_needs_matching_caret_geometry(monkeypatch,glyph):
    a=agent(monkeypatch);data,r,box=editor('journey');a.screen.frame=data
    extra=row(glyph,left=box[0]/1646,right=(box[2]+1)/1646,top=(box[1]-32)/98,bottom=(box[3]+1-32)/98)
    a.query_text_reader.read.return_value=observation([r,extra])
    assert asyncio.run(text.observe_landscape_query(a,{'query':'journey'},data))
    # Identical OCR but no physical caret cannot erase an extra real glyph.
    a.screen.frame,_,_=editor('journeyl',caret=False)
    assert asyncio.run(text.observe_landscape_query(a,{'query':'journey'},a.screen.frame)) is None


def test_app_crop_observation_box_is_in_full_image_coordinates(monkeypatch):
    a=agent(monkeypatch);a.query_text_reader.read.return_value=observation([row('ielts speaking')])
    hit=asyncio.run(text.observe_landscape_query(a,{'app':'TikTok','query':'ielts speaking'},a.screen.frame))
    assert hit['box']['left']==pytest.approx((117+.05*(1480-117))/1646)
    assert hit['box']['right']==pytest.approx((117+.4*(1480-117))/1646)

@pytest.mark.parametrize('glyph',['I','l','|'])
def test_real_extra_letter_before_caret_is_never_trimmed(monkeypatch,glyph):
    a=agent(monkeypatch);a.screen.frame,r,_=editor('journey'+glyph)
    a.query_text_reader.read.return_value=observation([r])
    assert asyncio.run(text.observe_landscape_query(a,{'query':'journey'},a.screen.frame)) is None


def test_cached_call_has_zero_new_provider_cost():
    async def go():
        async def p():return result(.5)
        async def b():await asyncio.sleep(.01);return result(1)
        c=JudgeFallback(Judge('jev',p),Judge('muse',b,'meta'));s={'policy':'app_public_search'}
        first=await c.evaluate(s,Q,frame());assert first['backup_s']>0
        cached=await c.evaluate(s,Q,frame())
        assert cached['seconds']==cached['primary_s']==cached['backup_s']==0
    asyncio.run(go())


# Private real-device frames are deliberately excluded from the public snapshot.
pytestmark = pytest.mark.skip(reason="Requires private real-device capture excluded from the open-source snapshot; synthetic matcher tests run separately")
