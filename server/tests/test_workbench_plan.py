import asyncio,copy,json
import pytest
from hidagent.workbench_plan import allocate,plan_work_order
from hidagent.decision import DecisionUnavailable

SOURCE={'source_sufficient':True,'budget':'35.00','items':[
 {'name':'Adapters','in_stock':2,'minimum':5,'unit_price':'8.50'},
 {'name':'Clips','in_stock':1,'minimum':6,'unit_price':'2.20'},
 {'name':'Labels','in_stock':0,'minimum':2,'unit_price':'3.75'}]}

def test_budget_priority_and_deferred_work_are_exact():
    result=allocate(SOURCE,['Adapters','Clips','Labels'])
    assert result['buy']==[{'name':'Adapters','quantity':3},{'name':'Clips','quantity':4}]
    assert result['deferred']==[{'name':'Clips','quantity':1},{'name':'Labels','quantity':2}]
    assert (result['total'],result['remaining'])==('34.30','0.70')

@pytest.mark.parametrize('priority',[[],['Adapters']*3,['Adapters','Clips','Unknown'],[{},'Clips','Labels']])
def test_rejects_duplicate_missing_unknown_and_nonstring_model_items(priority):
    with pytest.raises(DecisionUnavailable):allocate(SOURCE,priority)

def test_zero_budget_and_free_item_cannot_overbuy():
    source=copy.deepcopy(SOURCE);source['budget']='0.00';source['items'][0]['unit_price']='0.00'
    result=allocate(source,['Adapters','Clips','Labels'])
    assert result['buy']==[{'name':'Adapters','quantity':3}] and result['remaining']=='0.00'

def test_invalid_source_rejected_before_model_called():
    class Writer:
        async def chat(self,*a,**k):raise AssertionError('must not call model')
    with pytest.raises(DecisionUnavailable):asyncio.run(plan_work_order(Writer(),{**SOURCE,'source_sufficient':False},'Repair'))

def test_ambiguous_work_order_has_no_action():
    class Writer:
        supports_native_json=True
        async def chat(self,*a,**k):return json.dumps({'sufficient':False,'priority':[],'reason':'Not enough context'})
    with pytest.raises(DecisionUnavailable):asyncio.run(plan_work_order(Writer(),SOURCE,'Buy something'))

def test_model_priority_is_used_but_model_money_is_never_accepted():
    class Writer:
        supports_native_json=True;model='explicit-meta'
        async def chat(self,system,text,**kwargs):
            assert 'Do not calculate money' in system
            assert 'budget' not in json.loads(text)
            return json.dumps({'sufficient':True,'priority':['Labels','Adapters','Clips'],'reason':'Traceability first'})
    result=asyncio.run(plan_work_order(Writer(),SOURCE,'Label first then repair then tidy'))
    assert result['author_model']=='explicit-meta' and result['preview_only'] is True
    assert result['total']=='33.00' and result['remaining']=='2.00'
