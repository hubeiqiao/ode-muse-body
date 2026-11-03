import asyncio,time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from hidagent.live_voice import VoiceBridge,ToolCall,VoiceControl,voice_context
from hidagent.exploration_voice import is_exploration_intent

SID='v'*32

def fixture():
 state={'device':True,'screen':True,'calibrated':True,'target':{'device':'ipad'},'agent':{'running':False,'steps':[],'task':''},'exploration':{'available':True}}
 ex=SimpleNamespace(busy=False)
 snapshot={'available':True,'busy':False,'state':'idle'}
 ex.status=lambda:dict(snapshot)
 async def start(request,goal):
  snapshot.update(round_id='a'*32,request_id=request,state='completed',goal=goal,evidence=[{'claim':'SIMULATED finding','quote':'SIMULATED quote','url':'https://en.wikipedia.org/wiki/Earth','verifier':'jev-latest'}]);return dict(snapshot)
 ex.start=AsyncMock(side_effect=start);ex.stop=AsyncMock()
 normal=AsyncMock();stop=AsyncMock()
 b=VoiceBridge(lambda:state,normal,stop,ex)
 b.sessions[SID]={'expires':time.monotonic()+60,'calls':{},'device_family':'ipad','cancelled_ids':set()}
 return b,state,ex,normal,stop

def test_voice_selects_scoped_tool_and_never_normal_agent():
 async def run():
  b,s,e,normal,_=fixture()
  c=ToolCall(session=SID,id='one',name='phone_explore',args={'goal':'自己选题，带回一个真实发现'})
  r=await b.call(c);again=await b.call(c)
  assert r['status']=='verified_discovery' and 'SIMULATED' in r['evidence'][0]['claim'] and r==again
  e.start.assert_awaited_once();normal.assert_not_called()
  assert 'image' not in str(r) and 'base64' not in str(r)
 asyncio.run(run())

def test_generic_tool_exploration_intent_cannot_escape_budget():
 async def run():
  b,s,e,normal,_=fixture();await b.execute('phone_command',{'goal':'Explore a public topic yourself'},SID,'two')
  e.start.assert_awaited_once();normal.assert_not_called()
 asyncio.run(run())

def test_stop_latch_blocks_late_exploration_then_explicit_new_request_resumes():
 async def run():
  b,s,e,normal,stop=fixture()
  await b.interrupt(VoiceControl(session=SID,text='停止探索'))
  r=await b.execute('phone_explore',{'goal':'自己选题'},SID,'late')
  assert r['status']=='stopped';e.start.assert_not_called();stop.assert_awaited_once()
  assert not b.resume(VoiceControl(session=SID,text='探索一个新的话题'))['paused']
  await b.execute('phone_explore',{'goal':'自己选题'},SID,'new');e.start.assert_awaited_once()
 asyncio.run(run())

def test_exploration_context_requires_actual_enabled_capability():
 _,tools=voice_context({'target':{'device':'ipad'},'exploration':{'available':True}})
 assert 'phone_explore' in [x['name'] for x in tools[0]['functionDeclarations']]
 _,tools=voice_context({'target':{'device':'ipad'}})
 assert 'phone_explore' not in [x['name'] for x in tools[0]['functionDeclarations']]

def test_literal_dictation_does_not_initiate_exploration():
 assert not is_exploration_intent('type "Explore something"')
 assert not is_exploration_intent('输入 自己选题')
 assert is_exploration_intent('自己找个有意思的话题，带回来')


def test_original_human_utterance_overrides_invented_voice_topic():
 async def run():
  b,s,e,normal,_=fixture()
  original='Choose an interesting public topic yourself and bring back a discovery.'
  call=ToolCall(session=SID,id='grounded',name='phone_explore',args={'goal':'Find Mars news'},utterance=original)
  await b.call(call)
  assert e.start.call_args.args[1]==original
 asyncio.run(run())


def test_open_app_literal_writing_stays_normal_input():
 assert not is_exploration_intent('Open Notes and write "Explore the world"')
 assert is_exploration_intent('Choose a topic yourself and explore it')


def test_explore_accepts_empty_transcription_and_unclassified_owner_words():
 async def run():
  for utterance in ('', 'I want to explore something, find me some fun'):
   b,s,e,normal,_=fixture()
   r=await b.call(ToolCall(session=SID,id='fun',name='phone_explore',args={'goal':'Find something fun'},utterance=utterance))
   assert r['status']=='verified_discovery'
   e.start.assert_awaited_once();normal.assert_not_called()
 asyncio.run(run())
