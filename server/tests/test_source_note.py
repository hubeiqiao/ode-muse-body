import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from hidagent.source_note import source_note_goal, compile_source_note, next_source_note_actions
from hidagent.decision import DecisionUnavailable

TASK='From the current page, create a new Notes note titled "ESP32QA-RESTOCK-F104" with a short original restock summary, quantities, total cost and remaining budget.'
NOTE={'title':'ESP32QA-RESTOCK-F104','body':'Restock three adapters for the field kit.','author_model':'test-writer'}


def agent(reply=None,ready=1):
    image=io.BytesIO();Image.new('RGB',(710,1080)).save(image,format='JPEG')
    calls=[]
    class Writer:
        model='test-writer'
        async def chat(self,*args,**kwargs):return reply
    class Judge:
        async def evaluate(self,state,q,frame):
            calls.append(state);return {'seconds':.1,'answers':{'ready':{'noul':ready}}}
    return SimpleNamespace(planner=Writer(),client=Judge(),screen=SimpleNamespace(frame=image.getvalue()),
                           trace=SimpleNamespace(write=lambda *a,**k:None),steps=[],calls=calls)


def test_source_workflow_requires_explicit_current_page_new_test_note():
    assert source_note_goal(TASK)['title']==NOTE['title']
    assert source_note_goal('Open Notes and write hello') is None
    assert source_note_goal(TASK+' Then send it to someone.') is None
    assert source_note_goal(TASK.replace('ESP32QA-RESTOCK-F104','personal note')) is None


@pytest.mark.parametrize('change',['wrong_title','insufficient','unicode','oversize','invalid_json'])
def test_invalid_writer_output_never_advances_to_note_creation(change):
    data={**NOTE,'source_sufficient':True}
    if change=='wrong_title':data['title']='another note'
    if change=='insufficient':data['source_sufficient']=False
    if change=='unicode':data['body']='这不是 ASCII 的测试内容。'*3
    if change=='oversize':data['body']='x'*241
    a=agent('not JSON' if change=='invalid_json' else json.dumps(data))
    with pytest.raises(DecisionUnavailable):asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert not a.calls


def test_writer_content_requires_independent_current_source_check():
    a=agent(json.dumps({**NOTE,'source_sufficient':True}),ready=0)
    with pytest.raises(DecisionUnavailable,match='claims'):asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert a.calls[0]['policy']=='source_note_faithful'


def test_note_title_body_are_separated_and_body_sent_once():
    a=agent();a.steps=[{'workflow':'source_note','phase':'title_verified','action':'wait'}]
    paragraph=asyncio.run(next_source_note_actions(a,NOTE))[0]
    assert paragraph['action']=='paragraph' and paragraph['count']==1
    a.steps.append({**paragraph,'act_s':.1})
    body=asyncio.run(next_source_note_actions(a,NOTE))[0]
    assert body['action']=='type' and body['text']==NOTE['body']
    a.steps.append({**body,'act_s':.1})
    assert asyncio.run(next_source_note_actions(a,NOTE))[0]['action']=='done'


def test_unverified_sent_body_never_retries_and_uncertain_delivery_blocks_all_input():
    a=agent(ready=0);a.steps=[{'workflow':'source_note','phase':'title_verified'},
        {'workflow':'source_note','phase':'body','action':'type','act_s':.1}]
    for _ in range(4):
        if len([s for s in a.steps if s.get('phase')=='verify_wait'])==3:
            with pytest.raises(DecisionUnavailable):asyncio.run(next_source_note_actions(a,NOTE))
        else:
            action=asyncio.run(next_source_note_actions(a,NOTE))[0];assert action['action']=='wait';a.steps.append(action)
    a.steps=[{'action':'type_uncertain'}]
    with pytest.raises(DecisionUnavailable,match='uncertain'):asyncio.run(next_source_note_actions(a,NOTE))


def test_separate_source_writer_does_not_replace_execution_or_judge():
    a=agent('this original planner must not be called')
    class MetaWriter:
        model='meta-local-author'
        async def chat(self,*args,**kwargs):return json.dumps({**NOTE,'source_sufficient':True})
    original_planner=a.planner;original_judge=a.client
    a.source_writer=MetaWriter()
    note=asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert note['author_model']=='meta-local-author'
    assert a.planner is original_planner and a.client is original_judge
    assert a.calls[0]['policy']=='source_note_faithful'


@pytest.mark.parametrize('task',[
    'Read the current inventory and create a new Notes note with a concise restock summary, cost and remaining budget.',
    'From the current page, create a new Notes note with a restock summary.',
    'Based on the current inventory, create a new restock note in the Notes app, detailing the quantities needed, the total cost, and the remaining budget.',
    '根据这个库存新建一条补货备忘录，写清补货数量、总价和剩余预算。'])
def test_voice_inventory_goal_gets_a_fresh_test_title_without_inventing_content(task):
    first=source_note_goal(task);second=source_note_goal(task)
    assert first['title'].startswith('ESP32QA-VOICE-') and first['request']==task
    assert first['title']!=second['title'] and 'body' not in first


@pytest.mark.parametrize('task',[
    'Read the current inventory and calculate the total.',
    'Read the current inventory and edit an existing Notes note.',
    'Read the current inventory and create a new Notes note then buy the items.',
    'Read the current inventory and create a new Notes note then send it.',
    'Read the current inventory and create a new Notes note titled "My personal list".',
    'Read the current inventory and create a new Notes note with text "literal user words".'])
def test_voice_route_never_expands_readonly_dictation_or_external_action_scope(task):
    assert source_note_goal(task) is None


def test_smaller_author_image_preserves_full_independent_source(monkeypatch):
    monkeypatch.setenv('SOURCE_NOTE_IMAGE_MAX_SIDE','1080')
    a=agent();out=io.BytesIO();Image.new('RGB',(1646,1080)).save(out,format='JPEG');a.screen.frame=out.getvalue()
    original=a.screen.frame;seen={}
    class Writer:
        model='test-scaled'
        async def chat(self,system,text,image,**kwargs):
            seen['author']=Image.open(io.BytesIO(image)).size
            return json.dumps({**NOTE,'source_sufficient':True})
    class Judge:
        async def evaluate(self,state,questions,image):
            seen['judge']=image
            return {'seconds':.1,'answers':{'ready':{'noul':1}}}
    a.source_writer=Writer();a.client=Judge()
    asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert seen['author']==(1080,709) and seen['judge']==original and a.screen.frame==original


def test_invalid_author_image_limit_stops_before_model_call(monkeypatch):
    monkeypatch.setenv('SOURCE_NOTE_IMAGE_MAX_SIDE','1')
    a=agent()
    with pytest.raises(DecisionUnavailable,match='size'):asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert not a.calls


def test_generated_math_typography_is_normalized_before_independent_check():
    a=agent(json.dumps({**NOTE,'body':'Need 3 adapters: 3×$8.50 = $25.50.','source_sufficient':True}))
    note=asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert note['body']=='Need 3 adapters: 3 x $8.50 = $25.50.'
    assert a.calls[0]['proposed_note']['body']==note['body']


def test_inventory_route_keeps_raw_rows_for_independent_source_check(monkeypatch):
    monkeypatch.setenv('SOURCE_NOTE_INVENTORY_MATH','1')
    source={'source_sufficient':True,'budget':'40.00','items':[{'name':'Kit','in_stock':1,'minimum':4,'unit_price':'1.25'}]}
    a=agent(json.dumps(source))
    note=asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert 'Total $3.75; budget left $36.25.' in note['body']
    assert a.calls[0]['source_rows']=={k:v for k,v in source.items() if k!='source_sufficient'} and a.calls[0]['policy']=='source_note_faithful'


def test_inventory_ocr_sends_text_to_writer_and_original_pixels_to_judge(monkeypatch):
    monkeypatch.setenv('SOURCE_NOTE_INVENTORY_MATH','1');monkeypatch.setenv('SOURCE_NOTE_INVENTORY_OCR','1')
    source={'source_sufficient':True,'budget':'40.00','items':[{'name':'Kit','in_stock':1,'minimum':4,'unit_price':'1.25'}]}
    a=agent();original=a.screen.frame
    class Reader:
        async def read(self,image):
            assert image==original
            return {'source':'local_tesseract_ocr','table_text':'Kit 1 4 $1.25','sparse_text':'$40.00'}
    class Writer:
        model='meta-text';supports_native_json=True
        async def chat(self,system,text,image,**kwargs):
            assert image is None and set(json.loads(text))=={'table_text','budget_text'}
            assert 'ESP32QA' not in text and 'frame_sha256' not in text
            return json.dumps(source)
    a.inventory_reader=Reader();a.inventory_writer=Writer()
    a.source_writer=SimpleNamespace(model='legacy-visual',chat=AsyncMock(side_effect=AssertionError('visual source writer must remain unused for OCR')))
    note=asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert 'Total $3.75' in note['body'] and a.screen.frame==original
    assert a.calls[0]['source_rows']=={k:v for k,v in source.items() if k!='source_sufficient'}



@pytest.mark.parametrize('confidence,passes',[(.98,False),(1,True)])
def test_uncertain_ocr_transcription_requires_stricter_independent_original_proof(monkeypatch,confidence,passes):
    monkeypatch.setenv('SOURCE_NOTE_INVENTORY_MATH','1');monkeypatch.setenv('SOURCE_NOTE_INVENTORY_OCR','1')
    source={'source_sufficient':False,'budget':'40.00','items':[{'name':'Kit','in_stock':1,'minimum':4,'unit_price':'1.25'}]}
    a=agent(json.dumps(source),ready=confidence)
    class Reader:
        async def read(self,image):return {'table_text':'Kit 1 4 $1.25','sparse_text':'$40.00'}
    a.inventory_reader=Reader()
    if passes:assert '3.75' in asyncio.run(compile_source_note(a,source_note_goal(TASK)))['body']
    else:
        with pytest.raises(DecisionUnavailable,match='claims'):asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert a.calls[0]['policy']=='source_note_inventory_confirmation'
    assert 'source_sufficient' not in a.calls[0]['source_rows']
    assert 'title' not in a.calls[0]['proposed_note']


@pytest.mark.parametrize('visual_ready',[1,0])
def test_rejected_ocr_gets_one_explicit_visual_reread_before_any_note_input(monkeypatch,visual_ready):
    monkeypatch.setenv('SOURCE_NOTE_INVENTORY_MATH','1')
    monkeypatch.setenv('SOURCE_NOTE_INVENTORY_OCR','1')
    source={'source_sufficient':True,'budget':'40.00','items':[{'name':'Kit','in_stock':1,'minimum':4,'unit_price':'1.25'}]}
    a=agent();original=a.screen.frame;calls=[];judgments=iter([0,visual_ready])
    class Writer:
        supports_native_json=True
        def __init__(self,model):self.model=model
        async def chat(self,system,text,image=None,**kwargs):
            calls.append((self.model,image))
            return json.dumps(source)
    class Reader:
        async def read(self,image):return {'table_text':'Kit 1 4 $1.25','sparse_text':'$40.00'}
    class Judge:
        async def evaluate(self,*args):return {'seconds':.1,'answers':{'ready':{'noul':next(judgments)}},'approved':bool(len(calls)>1 and visual_ready)}
    a.inventory_writer=Writer('local-text-meta');a.source_writer=Writer('local-visual-meta')
    a.inventory_reader=Reader();a.client=Judge()
    if visual_ready:
        note=asyncio.run(compile_source_note(a,source_note_goal(TASK)))
        assert note['author_model']=='local-visual-meta' and '3.75' in note['body']
    else:
        with pytest.raises(DecisionUnavailable):asyncio.run(compile_source_note(a,source_note_goal(TASK)))
    assert calls==[('local-text-meta',None),('local-visual-meta',original)]
    assert not a.steps
