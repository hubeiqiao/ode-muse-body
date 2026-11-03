import asyncio,random,string
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from hidagent.phone import Phone,ascii_typing_segments
from hidagent.device import DeviceError
from hidagent.hybrid_agent import HybridAgent


def setup(tmp_path,monkeypatch,chunk='8',device='ipad'):
 monkeypatch.setenv('IPAD_TYPE_CHUNK_CHARS',chunk)
 p=Phone(SimpleNamespace(links=1,connected=True),tmp_path/'map.json')
 p.target_info=lambda:{'device':device,'orientation':'portrait','calibration_key':'test','width':710,'height':1080}
 p._send=AsyncMock();p.ensure_keyboard_live=AsyncMock()
 return p


def firmware_render(segments):
 text=''
 for command,count in segments:
  line=command.strip()  # actual legacy firmware trims command lines
  name,_,arg=line.partition(' ')
  text+=arg if name=='type' else ' ' if line=='key space' else '<unexpected>'
  assert count<=32
 return text


@pytest.mark.parametrize('text',['hello world  ','  hello   world',' '*20,'one "two" \\ three','ab'*60])
def test_chunk_protocol_preserves_literal_spaces_and_punctuation(text):
 segments=list(ascii_typing_segments(text,8))
 assert firmware_render(segments)==text
 assert all(not command.endswith(' ') for command,_ in segments)


def test_chunk_protocol_many_unseen_printable_inputs():
 rng=random.Random(73)
 for _ in range(120):
  text=''.join(rng.choice(string.ascii_letters+string.digits+'  !?\\\"[]') for _ in range(rng.randrange(1,160)))
  assert firmware_render(list(ascii_typing_segments(text,16)))==text


def test_acknowledged_chunks_are_counted_without_storing_literal_text(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch);asyncio.run(p.type('abcdefghijklmnop'))
 assert [c.args[0] for c in p._send.await_args_list]==['type abcdefgh','type ijklmnop']
 m=p.last_typing_timing
 assert m['acknowledged_chars']==16 and m['unconfirmed_chars']==0 and m['chunked']
 assert 'abcdefghijklmnop' not in str(m)


@pytest.mark.parametrize('mode',['reconnect','offline','rotate','not_sent','unknown'])
def test_failed_later_chunk_never_retypes_acknowledged_prefix(tmp_path,monkeypatch,mode):
 p=setup(tmp_path,monkeypatch);calls=[]
 async def send(command):
  calls.append(command)
  if len(calls)==1:
   if mode=='reconnect':p.device.links=2
   if mode=='offline':p.device.connected=False
   if mode=='rotate':p.target_info=lambda:{'device':'ipad','orientation':'landscape'}
  elif mode in ('not_sent','unknown'):raise DeviceError('test',delivery=mode,command_name='type')
 p._send=send
 with pytest.raises(DeviceError) as caught:asyncio.run(p.type('abcdefghijklmnop'))
 assert caught.value.typing_may_have_happened
 assert p.last_typing_timing['acknowledged_chars']==8
 assert len(calls)==(2 if mode in ('not_sent','unknown') else 1)
 assert p.last_typing_timing['unconfirmed_chars']==(8 if mode=='unknown' else 0)


@pytest.mark.parametrize('delivery,possible',[('not_sent',False),('unknown',True)])
def test_first_payload_delivery_distinction(tmp_path,monkeypatch,delivery,possible):
 p=setup(tmp_path,monkeypatch);p._send.side_effect=DeviceError('test',delivery=delivery,command_name='type')
 with pytest.raises(DeviceError) as caught:asyncio.run(p.type('abcdefghijklmnop'))
 assert caught.value.typing_may_have_happened is possible
 assert p._send.await_count==1


def test_cancelled_chunk_stops_suffix_and_agent_receipt_keeps_input_uncertain(tmp_path,monkeypatch):
 async def run():
  p=setup(tmp_path,monkeypatch);entered=asyncio.Event();calls=[]
  async def send(command):
   calls.append(command)
   if len(calls)==2:entered.set();await asyncio.Future()
  p._send=send
  a=HybridAgent(p,SimpleNamespace(live=True),SimpleNamespace(model='judge'),SimpleNamespace(model='haiku-test'),Mock())
  a._record=Mock()
  task=asyncio.create_task(a._do_impl({'action':'type','text':'abcdefghijklmnopqrstuvwx'}))
  await entered.wait();task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
  await asyncio.sleep(0)
  assert len(calls)==2 and p.last_typing_timing['acknowledged_chars']==8
  assert p.last_typing_timing['unconfirmed_chars']==8
  a._record.assert_called_once()
  assert a._record.call_args.kwargs['action']=='type_uncertain'
 asyncio.run(run())


def test_cancel_before_payload_does_not_reuse_old_uncertain_metrics(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch);p.last_typing_timing={'typing_may_have_happened':True}
 p.ensure_keyboard_live.side_effect=asyncio.CancelledError()
 a=HybridAgent(p,SimpleNamespace(live=True),SimpleNamespace(model='judge'),SimpleNamespace(model='haiku-test'),Mock());a._record=Mock()
 with pytest.raises(asyncio.CancelledError):asyncio.run(a._do_impl({'action':'type','text':'hello'}))
 a._record.assert_not_called();p._send.assert_not_awaited()


def test_opt_in_does_not_change_iphone_or_default_commands(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,device='iphone');asyncio.run(p.type('abcdefghijklmnop'))
 p._send.assert_awaited_once_with('type abcdefghijklmnop')
 p=setup(tmp_path,monkeypatch,chunk='0');asyncio.run(p.type('abcdefghijklmnop'))
 p._send.assert_awaited_once_with('type abcdefghijklmnop')


@pytest.mark.parametrize('chunk,text',[('7','hello'),('33','hello'),('8','hello\nworld')])
def test_invalid_chunk_input_rejected_before_payload(tmp_path,monkeypatch,chunk,text):
 p=setup(tmp_path,monkeypatch,chunk=chunk)
 with pytest.raises(ValueError):asyncio.run(p.type(text))
 p._send.assert_not_awaited()


def test_stopped_raw_voice_receipt_keeps_cancelled_typing_prefix_uncertain():
 from hidagent.live_voice import VoiceBridge
 state={'device':True,'screen':True,'calibrated':True,'agent':{'running':False,'task':'type a literal line','steps':[{'t':1,'action':'plan'},{'action':'type_uncertain','reason':'Stopped during typing'},{'action':'stopped'}]}}
 bridge=VoiceBridge(lambda:state,None,None)
 receipt=bridge.snapshot(raw=True)
 assert receipt['status']=='stopped' and receipt['input_was_sent'] and receipt['never_retry_typing']
 assert not receipt['running'] and receipt['terminal']


def test_revoked_lease_before_first_payload_is_not_a_sent_prefix(tmp_path,monkeypatch):
 from hidagent.hid_lease import leased_input
 p=setup(tmp_path,monkeypatch)
 def denied():raise asyncio.CancelledError()
 with leased_input(denied):
  with pytest.raises(asyncio.CancelledError):asyncio.run(p.type('abcdefghijk'))
 assert p.last_typing_timing['payload_attempted_chars']==0
 assert not p.last_typing_timing['typing_may_have_happened']
 p._send.assert_not_awaited()


def test_per_call_keyboard_pace_never_mutates_default_and_retunes_next_call(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch);monkeypatch.setenv('HID_KEY_MS','40');p.device.links=1
 async def run():
  await p.type('sample',keyboard_ms=80)
  assert p.last_typing_timing['keyboard_ms']==80
  await p.type('other')
  assert p.last_typing_timing['keyboard_ms']==40
 asyncio.run(run())
 assert [x.args[0] for x in p._send.await_args_list]==['typing 80','type sample','typing 40','type other']
 import os
 assert os.environ['HID_KEY_MS']=='40'


@pytest.mark.parametrize('app,device,orientation,kwargs', [('LinkedIn','ipad','portrait',{'keyboard_ms':80}),('X','ipad','portrait',{}),('LinkedIn','iphone','portrait',{}),('LinkedIn','ipad','landscape',{})])
def test_linkedin_search_pacing_is_app_and_device_scoped(app,device,orientation,kwargs):
 a=HybridAgent.__new__(HybridAgent);a.task='APP_PUBLIC_SEARCH '+__import__('json').dumps({'app':app,'query':'IELTS fluency'})
 a.phone=SimpleNamespace(type=AsyncMock(),target_info=lambda:{'device':device,'orientation':orientation})
 asyncio.run(a._do_impl({'action':'type','text':'IELTS fluency','workflow':'app_search','phase':'query'}))
 a.phone.type.assert_awaited_once_with('IELTS fluency',**kwargs)


def test_initial_gap_is_bounded_once_after_first_ack(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,chunk='16');monkeypatch.setenv('IPAD_TYPE_INITIAL_GAP_MS','400')
 events=[]
 async def send(command):events.append(command)
 async def sleep(seconds):events.append(('sleep',seconds))
 p._send=send;monkeypatch.setattr('hidagent.phone.asyncio.sleep',sleep)
 asyncio.run(p.type('IELTS input test 8160'))
 assert events[0]=='type I'
 assert events[1]==('sleep',.4)
 # A chunk starting with a space loses it on the device; it goes as its own key.
 assert [e for e in events if isinstance(e,str)]==['type I','type ELTS input test','key space','type 8160']
 assert events.count(('sleep',.4))==1
 assert p.last_typing_timing['acknowledged_chars']==21
 assert p.last_typing_timing['initial_gap_s']==.4


def test_initial_gap_cancel_keeps_prefix_and_sends_no_suffix(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch,chunk='16');monkeypatch.setenv('IPAD_TYPE_INITIAL_GAP_MS','400')
 async def sleep(seconds):raise asyncio.CancelledError()
 monkeypatch.setattr('hidagent.phone.asyncio.sleep',sleep)
 with pytest.raises(asyncio.CancelledError):asyncio.run(p.type('IELTS input test 9271'))
 p._send.assert_awaited_once_with('type I')
 assert p.last_typing_timing['acknowledged_chars']==1
 assert p.last_typing_timing['typing_may_have_happened']


@pytest.mark.parametrize('value',['-1','99','1001','text'])
def test_initial_gap_invalid_rejected_before_payload(tmp_path,monkeypatch,value):
 p=setup(tmp_path,monkeypatch);monkeypatch.setenv('IPAD_TYPE_INITIAL_GAP_MS',value)
 with pytest.raises(ValueError):asyncio.run(p.type('new text'))
 p._send.assert_not_awaited()


def test_initial_gap_does_not_enable_iphone_or_disabled_chunks(tmp_path,monkeypatch):
 monkeypatch.setenv('IPAD_TYPE_INITIAL_GAP_MS','400')
 for kwargs in ({'device':'iphone'},{'chunk':'0'}):
  p=setup(tmp_path,monkeypatch,**kwargs);asyncio.run(p.type('IELTS input'))
  p._send.assert_awaited_once_with('type IELTS input')


def test_initial_gap_preserves_leading_space_and_one_character(tmp_path,monkeypatch):
 p=setup(tmp_path,monkeypatch);monkeypatch.setenv('IPAD_TYPE_INITIAL_GAP_MS','100')
 asyncio.run(p.type(' hello world '))
 assert firmware_render([(c.args[0],1) for c in p._send.await_args_list])==' hello world '
 p=setup(tmp_path,monkeypatch);asyncio.run(p.type('I'))
 p._send.assert_awaited_once_with('type I')
 assert p.last_typing_timing['initial_gap_s']==0


@pytest.mark.parametrize('change',['link','orientation','lease'])
def test_initial_gap_rechecks_binding_and_stop_before_suffix(tmp_path,monkeypatch,change):
 from hidagent.hid_lease import leased_input
 p=setup(tmp_path,monkeypatch,chunk='16');monkeypatch.setenv('IPAD_TYPE_INITIAL_GAP_MS','400')
 stopped=False
 def guard():
  if stopped:raise asyncio.CancelledError()
 async def sleep(seconds):
  nonlocal stopped
  if change=='link':p.device.links=2
  elif change=='orientation':p.target_info=lambda:{'device':'ipad','orientation':'landscape'}
  else:stopped=True
 monkeypatch.setattr('hidagent.phone.asyncio.sleep',sleep)
 with leased_input(guard):
  with pytest.raises((DeviceError,asyncio.CancelledError)):asyncio.run(p.type('IELTS input test 0382'))
 p._send.assert_awaited_once_with('type I')
 assert p.last_typing_timing['acknowledged_chars']==1
 assert p.last_typing_timing['typing_may_have_happened']


def test_chunk_never_starts_with_a_space_the_firmware_would_trim():
 # Live 2026-10-31: limit 12 sent "type  Apps" and the iPad received "Full ScreenApps".
 for limit in range(8,33):
  for text in ('Full Screen Apps','noise cancelling headphones','a  b   c'):
   segments=list(ascii_typing_segments(text,limit))
   assert firmware_render(segments)==text
   assert all(not command.startswith('type  ') for command,_ in segments)
