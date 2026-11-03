"""Simulated transport/pixel contracts. These do not prove device acceptance."""
import asyncio,io,json,time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from hidagent.public_browser import PublicBrowserDriver,address_from_text
from hidagent.hid_lease import leased_input,check_hid
from hidagent.exploration_round import ExplorationRound,RoundRejected,InputDeliveryUncertain
from hidagent.curiosity_plan import choose_topic
from hidagent.device import DeviceError
from hidagent.vision_judge import JudgeFallback

URL='https://en.wikipedia.org/wiki/Octopus'

def image():
    out=io.BytesIO();Image.new('RGB',(498,1080),'white').save(out,format='JPEG');return out.getvalue()

class Browser:
    """Fake Safari. No real input or screenshot is used by this test."""
    def __init__(self):
        self.live=True;self.frame=image();self.address=URL;self.focus=False;self.events=[]
        self.device=SimpleNamespace(links=1);self.calibrated=True;self.family='iphone';self.profile='iphone-portrait'
        self.row=True;self.center=.5;self.bad_type=False;self.redirect=None;self.uncertain=False
        self.before_type=None
    def target_info(self):return {'device':self.family,'calibration_key':self.profile}
    async def key(self,key):
        check_hid('key '+key);self.events.append(key);self.cursor=key
        if key=='cmd+l':self.focus=True
        elif key=='escape':self.focus=False
        elif key=='enter':self.focus=False;self.address=self.redirect or self.address
    async def type(self,text):
        if self.before_type:await self.before_type()
        check_hid('type '+text);self.events.append('type')
        if self.uncertain:
            e=DeviceError('uncertain');e.typing_may_have_happened=True;raise e
        self.address=text+'x' if self.bad_type else text
    async def fresh(self,*_):return True
    async def settled(self,**_):pass
    async def read(self,_):
        text=self.address if self.focus else 'en.wikipedia.org'
        if self.focus and getattr(self,'clip',False):text=self.address[-24:] if getattr(self,'cursor','')=='cmd+right' else self.address[:28]
        rows=[] if not self.row else [{'text':text,
            'confidence':.99,'box':{'left':self.center-.1,'right':self.center+.1,'top':.1,'bottom':.2}}]
        return {'rows':rows}

async def fixture(**attrs):
    b=Browser()
    for k,v in attrs.items():setattr(b,k,v)
    judge=SimpleNamespace(evaluate=AsyncMock(return_value={'answers':{'ready':{'noul':.999},'blocked':{'noul':.001}},'model':'jev-latest','approved':True}))
    proposal={'topic':'Octopus','query':'Octopus','reason':'Investigate an unfamiliar animal.'}
    finding={'source_ready':True,'title':'SIMULATED Octopus','quote':'SIMULATED visible passage','claim':'SIMULATED supported finding'}
    writer=SimpleNamespace(model='test-haiku',chat=AsyncMock(side_effect=[json.dumps(proposal),json.dumps(finding)]))
    driver=PublicBrowserDriver(b,b,b,judge,writer)
    r=await ExplorationRound('iphone:8894','Explore a public topic',AsyncMock()).start()
    return b,judge,writer,driver,r

async def perform(driver,r):
    proposals=[]
    try:await driver(r,proposals.append)
    finally:await r.stop()
    return proposals

def test_simulated_full_driver_has_single_url_delivery_and_bound_source_evidence():
    async def run():
        b,j,w,d,r=await fixture();proposals=await perform(d,r)
        assert b.events==['cmd+l','cmd+left','cmd+right','cmd+a','type','cmd+left','cmd+right','cmd+a','enter','cmd+l','cmd+left','cmd+right','cmd+a','escape']
        assert proposals[0]['kind']=='unverified_exploration_proposal'
        assert len(r.evidence)==1 and r.evidence[0]['quote']=='SIMULATED visible passage'
        assert r.evidence[0]['url']==URL and r.action_count==1 and r.read_count==3
        assert j.evaluate.await_count==8 and w.chat.await_count==2
    asyncio.run(run())

@pytest.mark.parametrize('attrs',[{'row':False},{'center':.02},{'live':False},{'calibrated':False},{'family':'ipad'}])
def test_admission_fails_before_model_or_hid(attrs):
    async def run():
        b,j,w,d,r=await fixture(**attrs)
        with pytest.raises(RoundRejected):await perform(d,r)
        assert not b.events;j.evaluate.assert_not_called();w.chat.assert_not_called()
    asyncio.run(run())

def test_wrong_typed_url_stops_before_enter_and_never_retypes():
    async def run():
        b,j,w,d,r=await fixture(bad_type=True)
        with pytest.raises(RoundRejected,match='differ'):await perform(d,r)
        assert b.events.count('type')==1 and 'enter' not in b.events and w.chat.await_count==1
    asyncio.run(run())

@pytest.mark.parametrize('redirect',['https://example.com/private','https://en.wikipedia.org/wiki/Special:UserLogin'])
def test_redirect_outside_public_read_scope_never_uploads_source(redirect):
    async def run():
        b,j,w,d,r=await fixture(redirect=redirect)
        with pytest.raises(RoundRejected):await perform(d,r)
        assert b.events.count('type')==1 and b.events.count('enter')==1 and 'escape' not in b.events
        assert w.chat.await_count==1 and not r.evidence
    asyncio.run(run())

def test_uncertain_input_never_retries_or_submits():
    async def run():
        b,j,w,d,r=await fixture(uncertain=True)
        with pytest.raises(InputDeliveryUncertain):await perform(d,r)
        assert b.events.count('type')==1 and 'enter' not in b.events and w.chat.await_count==1
    asyncio.run(run())

def test_geometry_change_between_verification_and_dispatch_is_blocked():
    async def run():
        b,j,w,d,r=await fixture()
        async def rotate():
            out=io.BytesIO();Image.new('RGB',(1080,498),'white').save(out,format='JPEG');b.frame=out.getvalue()
        b.before_type=rotate
        with pytest.raises(RoundRejected,match='Orientation'):await perform(d,r)
        assert b.events==['cmd+l','cmd+left','cmd+right','cmd+a']
    asyncio.run(run())

def test_cancelled_lease_blocks_following_stage_and_still_permits_release():
    active=True
    def guard():
        if not active:raise RoundRejected('stopped')
    with leased_input(guard):
        check_hid('key cmd+l');active=False
        with pytest.raises(RoundRejected):check_hid('type ignored')
        check_hid('release')
    check_hid('key enter')  # Ordinary tasks outside the lease remain unchanged.

@pytest.mark.parametrize('value',['http://en.wikipedia.org/wiki/Octopus','https://evil@en.wikipedia.org/wiki/Octopus',
    'https://en.wikipedia.org.evil/wiki/Octopus','https://en.wikipedia.org/wiki/Oct…',
    'https://en.wikipedia.org/wiki/Octopus trailing words','https://en.wikipedia.org/wiki/Special:UserLogin'])
def test_partial_or_deceptive_address_is_not_a_navigation_proof(value):assert address_from_text(value) is None

def test_complete_address_can_omit_visually_hidden_https():assert address_from_text('en.wikipedia.org/wiki/Octopus')==URL

@pytest.mark.parametrize('query',['public art','x'*13,'Bad/Path','Foo?bar','99bottles'])
def test_short_article_mode_rejects_truncated_or_non_article_titles(query):
    writer=SimpleNamespace(model='test',chat=AsyncMock(return_value=json.dumps({'topic':'Test','query':query,'reason':'Test'})))
    with pytest.raises(RoundRejected):asyncio.run(choose_topic(writer,'Explore',short_article=True))

@pytest.mark.parametrize('policy',['exploration_browser_focus','exploration_public_source'])
def test_public_scope_uses_strict_fallback_threshold(policy):
    state={'policy':policy};questions={'ready':{'type':'noul'},'blocked':{'type':'noul'}}
    assert not JudgeFallback._approved(state,questions,{'ready':{'noul':.98},'blocked':{'noul':0}})
    assert not JudgeFallback._approved(state,questions,{'ready':{'noul':1},'blocked':{'noul':.02}})
    assert JudgeFallback._approved(state,questions,{'ready':{'noul':.99},'blocked':{'noul':.01}})

def test_real_phone_send_rechecks_lease_after_async_tuning():
    from hidagent.phone import Phone
    async def run():
        active=True;sent=[]
        async def send(command):
            nonlocal active
            sent.append(command);active=False
        phone=object.__new__(Phone);phone.device=SimpleNamespace(links=1,send=send)
        phone._pointer_ms=lambda:80;phone._pointer_tuned_link=None
        phone.axes={'x':1,'y':1}
        def guard():
            if not active:raise RoundRejected('stopped during tuning')
        with leased_input(guard),pytest.raises(RoundRejected):await phone._send('move 2 0')
        assert sent==['tune 10 80']
    asyncio.run(run())

def test_exploration_stale_mirror_does_not_restart_airplay():
    from hidagent.phone import Phone,PhoneUnavailable
    async def run():
        phone=object.__new__(Phone);phone._pos=None
        phone.screen=SimpleNamespace(updated=time.time()-1000,name='test',fresh=AsyncMock(return_value=False),restart=AsyncMock())
        phone._send=AsyncMock()
        with leased_input(lambda:None),pytest.raises(PhoneUnavailable,match='left unchanged'):await phone.ensure_live()
        phone.screen.restart.assert_not_called()
    asyncio.run(run())

def test_source_rejection_cannot_be_reported_as_discovery():
    async def run():
        b,j,w,d,r=await fixture()
        j.evaluate.side_effect=[j.evaluate.return_value]*7+[{'answers':{'ready':{'noul':.9},'blocked':{'noul':.1}},'model':'jev-latest','approved':False}]
        with pytest.raises(RoundRejected):await perform(d,r)
        assert not r.evidence and b.events.count('type')==1
    asyncio.run(run())

def test_stop_between_focus_and_typing_invalidates_old_hid_stage():
    async def run():
        b,j,w,d,r=await fixture();b.before_type=r.stop
        with pytest.raises(RoundRejected):await perform(d,r)
        assert b.events==['cmd+l','cmd+left','cmd+right','cmd+a'] and not r.evidence
    asyncio.run(run())

def test_board_reconnect_after_focus_requires_new_explicit_round():
    async def run():
        b,j,w,d,r=await fixture()
        async def reconnect():b.device.links+=1
        b.before_type=reconnect
        with pytest.raises(RoundRejected,match='binding'):await perform(d,r)
        assert b.events==['cmd+l','cmd+left','cmd+right','cmd+a']
    asyncio.run(run())

def test_missing_post_input_frame_stops_before_typing():
    async def run():
        b,j,w,d,r=await fixture();b.fresh=AsyncMock(return_value=None)
        with pytest.raises(RoundRejected,match='fresh'):await perform(d,r)
        assert b.events==['cmd+l'];assert j.evaluate.await_count==1
    asyncio.run(run())


def test_clipped_realistic_address_requires_overlapping_visible_endpoints():
    async def run():
        b,j,w,d,r=await fixture(clip=True);await perform(d,r)
        assert len(r.evidence)==1 and b.events.count('type')==1
    asyncio.run(run())
