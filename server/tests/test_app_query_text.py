import asyncio,io,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from PIL import Image
import pytest
from hidagent import app_query_text as m

def image(color):
 b=io.BytesIO();Image.new('RGB',(710,1080),color).save(b,format='PNG');return b.getvalue()

def fake(tmp_path,monkeypatch):
 p=tmp_path/'query.json';p.write_text(json.dumps({'reference_size':[710,1080],'query_text_box':[146,28,560,62]}))
 monkeypatch.setattr(m,'FILES',{('Bilibili','public_query_text'):p})
 monkeypatch.setattr(m,'public_search_control',lambda *a,**kw:True)
 reader=SimpleNamespace(read=AsyncMock(return_value={'mode':'accurate','rows':[{'text':'IELTS','confidence':1}],'read_s':.1}))
 a=SimpleNamespace(query_text_reader=reader,phone=SimpleNamespace(target_info=lambda:{'device':'ipad','orientation':'portrait'}),steps=[{'workflow':'app_search','phase':'query','act_s':.7,'text':'IELTS','_search_was_empty_verified':True}],shots={0:image('gray')},trace=Mock())
 return a,image('white')

def test_current_empty_input_ack_and_changed_owned_query_can_be_observed(tmp_path,monkeypatch):
 a,frame=fake(tmp_path,monkeypatch)
 assert asyncio.run(m.verify_local_query(a,{'app':'Bilibili','query':'IELTS'},frame))
 assert a.query_text_reader.read.call_args.kwargs['mode']=='accurate'

@pytest.mark.parametrize('bad',['no_ack','not_empty','wrong_literal','same_pixels','uncertain_ocr','wrong_ocr','fast_mode','wrong_ui'])
def test_local_query_never_replaces_focus_or_wrong_source_checks(tmp_path,monkeypatch,bad):
 a,frame=fake(tmp_path,monkeypatch)
 if bad=='no_ack':a.steps[0].pop('act_s')
 elif bad=='not_empty':a.steps[0]['_search_was_empty_verified']=False
 elif bad=='wrong_literal':a.steps[0]['text']='IELT5'
 elif bad=='same_pixels':a.shots[0]=frame
 elif bad=='uncertain_ocr':a.query_text_reader.read.return_value['rows'][0]['confidence']=.8
 elif bad=='wrong_ocr':a.query_text_reader.read.return_value['rows'][0]['text']='IELT5'
 elif bad=='fast_mode':a.query_text_reader.read.return_value['mode']='fast'
 elif bad=='wrong_ui':monkeypatch.setattr(m,'public_search_control',lambda *a,**kw:None)
 assert asyncio.run(m.verify_local_query(a,{'app':'Bilibili','query':'IELTS'},frame)) is None


@pytest.mark.parametrize('bad',[None,'not_empty','wrong_literal','same_pixels','uncertain_ocr','wrong_ui'])
def test_instagram_exact_query_keeps_current_run_focus_and_literal_requirements(tmp_path,monkeypatch,bad):
 a,frame=fake(tmp_path,monkeypatch)
 path=m.FILES[('Bilibili','public_query_text')]
 monkeypatch.setattr(m,'FILES',{('Instagram','public_query_text'):path})
 if bad=='not_empty':a.steps[0]['_search_was_empty_verified']=False
 elif bad=='wrong_literal':a.steps[0]['text']='IELT5'
 elif bad=='same_pixels':a.shots[0]=frame
 elif bad=='uncertain_ocr':a.query_text_reader.read.return_value['rows'][0]['confidence']=.8
 elif bad=='wrong_ui':monkeypatch.setattr(m,'public_search_control',lambda *a,**kw:None)
 result=asyncio.run(m.verify_local_query(a,{'app':'Instagram','query':'IELTS'},frame))
 assert bool(result)==(bad is None)
