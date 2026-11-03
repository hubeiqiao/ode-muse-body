import asyncio
from dataclasses import replace
import time
from unittest.mock import AsyncMock
import pytest
from hidagent.exploration_round import ExplorationRound,RoundRejected,InputDeliveryUncertain,public_url

URL='https://en.wikipedia.org/wiki/Earth'

@pytest.mark.parametrize('url',[
 'http://en.wikipedia.org/wiki/Earth','https://en.wikipedia.org.evil.test/wiki/Earth',
 'https://127.0.0.1/','https://user:secret@en.wikipedia.org/wiki/Earth',
 'https://en.wikipedia.org:444/wiki/Earth','https://en.wikipedia.org/wiki/Special:UserLogin',
 'https://en.wikipedia.org/wiki/Special%3AUserLogin','https://en.wikipedia.org/w/api.php?action=edit',
 'https://en.wikipedia.org/wiki/Earth?action=edit','https://www.nasa.gov/account',
 'https://www.nasa.gov/x?subscribe=1','file:///etc/passwd',
 'https://en.wikipedia.org/wiki/User%253ASecret','https://www.nasa.gov/\\account',
 'https://www.nasa.gov/\x00science',
])
def test_public_reading_scope_rejects_private_account_edit_and_lookalike_urls(url):
    with pytest.raises(RoundRejected):public_url(url)

def test_public_search_and_sources_are_allowed_without_fetching():
    assert public_url(URL)==URL
    assert public_url('https://en.wikipedia.org/wiki/Special:Search?search=public+art')
    assert public_url('https://science.nasa.gov/earth/')

@pytest.mark.parametrize('seconds,actions',[(91,8),(float('nan'),8),(True,8),(90,13),(90,True)])
def test_round_budget_cannot_be_extended(seconds,actions):
    with pytest.raises(RoundRejected):ExplorationRound('iphone:8894','Explore',AsyncMock(),seconds=seconds,max_actions=actions)

def test_action_tickets_are_one_use_and_payload_tampering_never_reaches_adapter():
    async def run():
        release=AsyncMock();r=await ExplorationRound('iphone:8894','Explore',release).start()
        operation=AsyncMock(return_value='ok');t=await r.grant('navigate',{'url':URL})
        t.payload['url']='https://evil.test/'
        assert t.payload['url']==URL
        with pytest.raises(RoundRejected):await r.execute(replace(t,payload_json='{}'),operation)
        assert not operation.called
        assert await r.execute(t,operation)=='ok'
        with pytest.raises(RoundRejected):await r.execute(t,operation)
        assert operation.await_count==1
        await r.stop();release.assert_awaited_once()
    asyncio.run(run())

def test_action_limit_counts_attempts_and_blocks_ninth_input():
    async def run():
        r=await ExplorationRound('ipad:8890','Explore',AsyncMock()).start();op=AsyncMock()
        for _ in range(8):await r.execute(await r.grant('scroll',{'direction':'down'}),op)
        with pytest.raises(RoundRejected):await r.grant('scroll',{'direction':'down'})
        assert op.await_count==8 and r.state=='action_limit'
    asyncio.run(run())

@pytest.mark.parametrize('kind,payload',[('type',{'text':'hello'}),('publish',{}),('camera',{}),('scroll',{'direction':'down','count':2}),('navigate',42)])
def test_exploration_exposes_no_free_typing_publishing_camera_or_continuous_gesture(kind,payload):
    async def run():
        r=await ExplorationRound('ipad:8890','Explore',AsyncMock()).start()
        with pytest.raises(RoundRejected):await r.grant(kind,payload)
        assert r.action_count==0;await r.stop()
    asyncio.run(run())

def test_deadline_cancels_inflight_model_or_input_and_requests_release():
    async def run():
        release=AsyncMock();r=await ExplorationRound('ipad:8890','Explore',release,seconds=.02).start()
        entered=asyncio.Event()
        async def operation(*args):entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(r.execute(await r.grant('observe'),operation))
        await entered.wait()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.sleep(.01)
        assert r.state=='expired';release.assert_awaited_once()
        with pytest.raises(RoundRejected):await r.start()
    asyncio.run(run())

def test_stop_revokes_old_plan_even_when_adapter_swallows_cancellation():
    async def run():
        release=AsyncMock();r=await ExplorationRound('iphone:8894','Explore',release).start()
        entered=asyncio.Event();continue_old=asyncio.Event();sent=[];blocked=[]
        ticket=await r.grant('navigate',{'url':URL})
        async def operation(*args):
            r.check_action(ticket);sent.append('first chunk');entered.set()
            try:await continue_old.wait()
            except asyncio.CancelledError:await continue_old.wait()
            try:r.check_action(ticket)
            except RoundRejected:blocked.append(True);return
            sent.append('late chunk')
        task=asyncio.create_task(r.execute(ticket,operation));await entered.wait()
        await r.stop();assert r.state=='stopped' and r.release_pending
        continue_old.set();await task
        assert sent==['first chunk'] and blocked==[True];release.assert_awaited_once()
        with pytest.raises(RoundRejected):await r.grant('observe')
    asyncio.run(run())

def test_input_uncertainty_terminates_round_without_replaying():
    async def run():
        r=await ExplorationRound('ipad:8890','Explore',AsyncMock()).start();calls=[]
        async def operation(*args):calls.append(1);raise InputDeliveryUncertain()
        ticket=await r.grant('navigate',{'url':URL})
        with pytest.raises(InputDeliveryUncertain):await r.execute(ticket,operation)
        with pytest.raises(RoundRejected):await r.execute(ticket,operation)
        assert calls==[1] and r.state=='input_uncertain'
    asyncio.run(run())

def test_findings_need_fresh_bound_independently_checked_source_metadata():
    async def run():
        r=await ExplorationRound('iphone:8894','Explore',AsyncMock()).start()
        with pytest.raises(RoundRejected):await r.finish()
        good=dict(round_id=r.id,device=r.device,url=URL,frame_sha256='a'*64,
            quote='Visible source quote',claim='Independently checked claim',confidence=.99,
            verifier='claude-haiku-5-5',observed_at=time.monotonic())
        for change in [dict(round_id='old'),dict(device='ipad:8890'),dict(confidence=True),dict(confidence=.98),dict(verifier='self-reported'),dict(observed_at=r.started-1),dict(frame_sha256='not-a-frame')]:
            with pytest.raises(RoundRejected):r.add_verified_evidence(**{**good,**change})
        r.add_verified_evidence(**good)
        result=await r.finish()
        assert result['state']=='completed' and len(result['evidence'])==1
        result['evidence'][0]['claim']='changed outside'
        assert r.evidence[0]['claim']=='Independently checked claim'
    asyncio.run(run())

def test_old_device_ticket_cannot_be_used_in_new_round():
    async def run():
        first=await ExplorationRound('iphone:8894','Explore',AsyncMock()).start()
        second=await ExplorationRound('ipad:8890','Explore',AsyncMock()).start()
        old=await first.grant('observe');await first.stop()
        with pytest.raises(RoundRejected):await second.execute(old,AsyncMock())
        await second.stop()
    asyncio.run(run())

def test_read_budget_cannot_spin_forever_without_device_actions():
    async def run():
        r=await ExplorationRound('iphone:8894','Explore',AsyncMock()).start();op=AsyncMock()
        for _ in range(9):await r.execute(await r.grant('observe'),op)
        with pytest.raises(RoundRejected):await r.grant('observe')
        assert r.action_count==0 and r.read_count==9 and r.state=='action_limit'
    asyncio.run(run())

def test_reserved_action_is_revoked_on_stop_before_any_dispatch():
    async def run():
        r=await ExplorationRound('iphone:8894','Explore',AsyncMock()).start();op=AsyncMock()
        ticket=await r.grant('navigate',{'url':URL});await r.stop()
        with pytest.raises(RoundRejected):await r.execute(ticket,op)
        op.assert_not_called()
    asyncio.run(run())

def test_release_failure_is_reported_without_resuming_or_replaying():
    async def run():
        r=await ExplorationRound('iphone:8894','Explore',AsyncMock(side_effect=RuntimeError('offline'))).start()
        await r.stop()
        assert r.release_pending and r.state=='stopped'
        with pytest.raises(RoundRejected):await r.start()
    asyncio.run(run())


def test_twelve_input_budget_still_blocks_thirteenth_delivery():
    async def run():
        r = await ExplorationRound('ipad:8890', 'Explore', AsyncMock(), max_actions=12).start()
        op = AsyncMock()
        for _ in range(12):
            await r.execute(await r.grant('scroll', {'direction': 'down'}), op)
        with pytest.raises(RoundRejected):
            await r.grant('scroll', {'direction': 'down'})
        assert op.await_count == 12 and r.state == 'action_limit'
    asyncio.run(run())
