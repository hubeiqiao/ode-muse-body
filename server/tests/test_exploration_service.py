import asyncio
import time
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from hidagent.exploration_service import ExplorationService,router_for

URL='https://en.wikipedia.org/wiki/Earth'
PROPOSAL={'topic':'Earth','query':'Earth','reason':'A public topic to investigate','author_model':'test-haiku',
 'public_search_url':URL,'kind':'unverified_exploration_proposal','evidence':[]}

def fixture(driver=None,**kwargs):
    ready={'device':True,'screen':True,'calibrated':True,'binding':'iphone:8894'}
    busy=[False];release=AsyncMock()
    s=ExplorationService(lambda:ready,lambda:busy[0],release,driver,**kwargs)
    return s,ready,busy,release

async def settle(service):
    async def wait():
        while service.busy:await asyncio.sleep(.001)
    await asyncio.wait_for(wait(),1)

async def fake_driver(r,publish):
    publish(PROPOSAL)
    r.add_verified_evidence(round_id=r.id,device=r.device,url=URL,frame_sha256='a'*64,
       quote='SIMULATED source',claim='SIMULATED checked claim',confidence=1,
       verifier='claude-haiku-5-5',observed_at=time.monotonic())

def test_status_and_disabled_start_never_call_models_or_release_device():
    async def run():
        s,_,_,release=fixture()
        assert s.status()=={'available':False,'state':'idle','busy':False,'reason':'public_browser_adapter_not_configured'}
        with pytest.raises(HTTPException) as exc:await s.start('request-one','Explore')
        assert exc.value.status_code==503 and s.status()['state']=='idle'
        await s.stop();release.assert_not_called()
    asyncio.run(run())

@pytest.mark.parametrize('field',['device','screen','calibrated'])
def test_readiness_failure_does_not_start_driver(field):
    async def run():
        driver=AsyncMock();s,ready,_,release=fixture(driver);ready[field]=False
        with pytest.raises(HTTPException) as exc:await s.start('request-one','Explore')
        assert exc.value.status_code==409;driver.assert_not_called();release.assert_not_called()
    asyncio.run(run())

def test_existing_agent_or_manual_action_owns_device_before_exploration():
    async def run():
        driver=AsyncMock();s,_,busy,_=fixture(driver);busy[0]=True
        with pytest.raises(HTTPException) as exc:await s.start('request-one','Explore')
        assert exc.value.status_code==409;driver.assert_not_called()
    asyncio.run(run())

def test_completed_and_concurrent_duplicate_ids_never_start_another_round():
    async def run():
        calls=[]
        async def driver(*args):calls.append(1);await fake_driver(*args)
        s,_,_,release=fixture(driver)
        first,duplicate=await asyncio.gather(s.start('request-one','Explore'),s.start('request-one','Explore'))
        assert first['round_id']==duplicate['round_id']
        await settle(s)
        again=await s.start('request-one','Explore')
        assert again['state']=='completed' and again['deduplicated'] and calls==[1]
        assert again['evidence'][0]['quote']=='SIMULATED source';release.assert_awaited_once()
        again['evidence'][0]['quote']='tampered outside'
        assert s.status()['evidence'][0]['quote']=='SIMULATED source'
        with pytest.raises(HTTPException) as exc:await s.start('request-one','Different goal')
        assert exc.value.status_code==409
    asyncio.run(run())

def test_stopping_a_previous_round_cannot_interrupt_a_new_round():
    async def run():
        entered=asyncio.Event()
        async def wait_driver(r,publish):entered.set();await asyncio.Event().wait()
        s,_,_,_=fixture(fake_driver)
        old=await s.start('request-one','Explore');await settle(s)
        s.driver=wait_driver
        new=await s.start('request-two','Different exploration');await entered.wait()
        receipt=await s.stop(old['round_id'])
        assert receipt['round_id']==old['round_id'] and s.status()['state']=='running'
        await s.stop(new['round_id']);assert not s.busy
    asyncio.run(run())

def test_deadline_cancels_driver_work_even_outside_a_round_operation():
    async def run():
        cancelled=[]
        async def driver(*args):
            try:await asyncio.Event().wait()
            finally:cancelled.append(True)
        s,_,_,release=fixture(driver,seconds=.02)
        await s.start('request-one','Explore');await settle(s)
        assert s.status()['state']=='expired' and cancelled==[True]
        release.assert_awaited_once()
    asyncio.run(run())

def test_empty_or_fabricated_discovery_never_becomes_completed():
    async def run():
        async def driver(*args):pass
        s,_,_,_=fixture(driver);await s.start('request-one','Explore');await settle(s)
        assert s.status()['state']=='failed' and not s.status()['evidence']
    asyncio.run(run())

def test_raw_provider_error_never_leaks_to_api_status():
    async def run():
        async def driver(*args):raise RuntimeError('fake secret credential')
        s,_,_,_=fixture(driver);await s.start('request-one','Explore');await settle(s)
        assert s.status()['error']=='RuntimeError' and 'fake secret' not in str(s.status())
    asyncio.run(run())

def test_unverified_proposal_cannot_inject_evidence_or_late_feedback():
    async def run():
        entered=asyncio.Event()
        async def driver(r,publish):entered.set();await asyncio.Event().wait()
        s,_,_,_=fixture(driver);first=await s.start('request-one','Explore');await entered.wait()
        from hidagent.exploration_round import RoundRejected
        with pytest.raises(RoundRejected):s.publish_proposal(first['round_id'],{**PROPOSAL,'evidence':['model said so']})
        with pytest.raises(RoundRejected):s.publish_proposal('0'*32,PROPOSAL)
        await s.stop(first['round_id'])
        with pytest.raises(RoundRejected):s.publish_proposal(first['round_id'],PROPOSAL)
    asyncio.run(run())

def test_http_endpoints_are_explicit_local_and_cannot_override_budgets():
    async def run():
        s,_,_,release=fixture();app=FastAPI();app.include_router(router_for(s))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8894') as c:
            assert (await c.get('/api/exploration/status')).json()['available'] is False
            assert (await c.get('/api/exploration/status',headers={'Origin':'https://evil.example'})).status_code==403
            assert (await c.post('/api/exploration/start',json={'request_id':'request-one','goal':'Explore'})).status_code==503
            assert (await c.post('/api/exploration/start',json={'request_id':'request-one','goal':'Explore','seconds':999})).status_code==422
            assert (await c.post('/api/exploration/stop',json={})).status_code==422
        release.assert_not_called()
    asyncio.run(run())


def test_terminal_elapsed_time_is_frozen():
    async def run():
        s,_,_,_=fixture(fake_driver)
        await s.start('request-one','Explore');await settle(s)
        first=s.status()['elapsed_s'];await asyncio.sleep(.02)
        assert s.status()['elapsed_s']==first
    asyncio.run(run())

def test_noncooperative_old_driver_retains_ownership_until_it_really_finishes():
    async def run():
        entered=asyncio.Event();finish=asyncio.Event()
        async def driver(*args):
            entered.set()
            try:await finish.wait()
            except asyncio.CancelledError:await finish.wait()
        s,_,_,_=fixture(driver)
        first=await s.start('request-one','Explore');await entered.wait()
        stopped=await s.stop(first['round_id'])
        assert stopped['release_pending'] and s.busy
        with pytest.raises(HTTPException) as exc:await s.start('request-two','New goal')
        assert exc.value.status_code==409
        finish.set();await settle(s)
        assert s.status()['state']=='stopped'
    asyncio.run(run())
