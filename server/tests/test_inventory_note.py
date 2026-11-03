import asyncio,json
from types import SimpleNamespace
import pytest
from hidagent.inventory_note import calculate_note,transcribe_note
from hidagent.decision import DecisionUnavailable

SOURCE={'source_sufficient':True,'budget':'40.00','items':[
 {'name':'USB-C adapters','in_stock':4,'minimum':5,'unit_price':'8.50'},
 {'name':'AA batteries','in_stock':1,'minimum':4,'unit_price':'1.25'},
 {'name':'Cable clips','in_stock':5,'minimum':6,'unit_price':'2.20'},
 {'name':'Notebooks','in_stock':2,'minimum':4,'unit_price':'4.00'},
 {'name':'Spare labels','in_stock':2,'minimum':2,'unit_price':'3.75'}]}


def test_decimal_inventory_math_and_zero_needed_rows():
 note,rows=calculate_note(json.dumps(SOURCE),'ESP32QA-MATH')
 assert 'Total $22.45; budget left $17.55.' in note['body']
 assert 'AA batteries 3' in note['body'] and 'Notebooks 2' in note['body']
 assert 'Spare labels' not in note['body'] and len(rows['items'])==5


def test_overstock_is_not_negative_restock_and_shortfall_is_explicit():
 source={**SOURCE,'budget':'1.00','items':[{'name':'Kit','in_stock':10,'minimum':4,'unit_price':'8.50'}]}
 assert 'Total $0.00; budget left $1.00.' in calculate_note(json.dumps(source),'ESP32QA-MATH')[0]['body']
 source['items'][0]['in_stock']=0
 assert 'budget shortfall $33.00.' in calculate_note(json.dumps(source),'ESP32QA-MATH')[0]['body']


@pytest.mark.parametrize('bad',[
 {'source_sufficient':False}, {'budget':'NaN'},{'budget':'1e4'},{'items':[]},
 {'items':[{'name':'Kit','in_stock':True,'minimum':2,'unit_price':'1.00'}]},
 {'items':[{'name':'Kit','in_stock':-1,'minimum':2,'unit_price':'1.00'}]},
 {'items':[{'name':'Kit','in_stock':1,'minimum':2,'unit_price':'0.001'}]},
 {'items':[SOURCE['items'][0],SOURCE['items'][0]]}])
def test_invalid_source_never_creates_a_note(bad):
 with pytest.raises(DecisionUnavailable):calculate_note(json.dumps({**SOURCE,**bad}),'ESP32QA-MATH')


def test_model_is_asked_to_transcribe_not_invent_calculation():
 class Writer:
  supports_native_json=True
  async def chat(self,system,text,image,**kwargs):
   assert 'Do NOT calculate' in system and kwargs['json_schema']['required']==['source_sufficient','budget','items']
   return json.dumps(SOURCE)
 note,rows,raw=asyncio.run(transcribe_note(Writer(),{'title':'ESP32QA-MATH'},b'source'))
 assert note['title']=='ESP32QA-MATH' and '22.45' in note['body'] and rows==SOURCE
