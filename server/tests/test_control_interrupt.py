import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from hidagent.control_intent import is_stop_intent, scroll_request
from hidagent.live_voice import VoiceBridge, VoiceControl, ToolCall
from hidagent.phone import Phone


CASES=[('不划了',True),('不滑了',True),('别滑了',True),('先停一下',True),('不要再划了',True),('停止滑动',True),('Stop scrolling.',True),('不要停',False),('不停地滑',False),("don't stop",False),('Type "stop"',False),('输入不划了',False),('open stopwatch',False)]


@pytest.mark.parametrize('text,expected',CASES)
def test_local_stop_intent(text,expected):
    assert is_stop_intent(text) is expected


def test_browser_stop_intents_match_server():
    module=Path(__file__).parents[1]/'hidagent/static/voice-intents.js'
    script='const m=require(process.argv[1]); const cases=JSON.parse(process.argv[2]); for(const [s,e] of cases) if(m.isStopIntent(s)!==e) throw Error(s);'
    subprocess.run(['node','-e',script,str(module),json.dumps(CASES)],check=True)


@pytest.mark.parametrize('goal,count',[('scroll down',1),('Scroll down a little',1),('往下滑一下',1),('继续向下滚动',1),('keep scrolling down',3),('一直往下滑',3)])
def test_scroll_has_finite_explicit_limit(goal,count):
    assert scroll_request(goal)==('down',count)


@pytest.mark.parametrize('stop_text',['不划了','先别导航','先别导航，看看天气'])
def test_stop_bypasses_running_command_and_latches_late_calls(stop_text):
    async def run():
        state={'device':True,'screen':True,'calibrated':True,'agent':{'running':False,'task':'','steps':[]}}
        starts=[];ready=asyncio.Event()
        async def start(goal):starts.append(goal);state['agent'].update(running=True,task=goal);ready.set()
        async def stop():state['agent'].update(running=False,steps=[{'action':'stopped'}])
        b=VoiceBridge(lambda:state,start,stop);sid='s'*32;b.sessions[sid]={'expires':float('inf'),'calls':{}}
        pending=asyncio.create_task(b.call(ToolCall(session=sid,id='old',name='phone_command',args={'goal':'scroll down'})))
        await ready.wait()
        result=await asyncio.wait_for(b.interrupt(VoiceControl(session=sid,text=stop_text)), .2)
        assert result['status']=='stopped' and not state['agent']['running']
        await pending
        blocked=await b.call(ToolCall(session=sid,id='late',name='phone_command',args={'goal':'scroll down'}))
        assert blocked['status']=='stopped' and len(starts)==1
        assert b.resume(VoiceControl(session=sid,text='继续向下滑'))['paused'] is False
    asyncio.run(run())


def test_late_cancellation_never_stops_newer_task():
    async def run():
        state={'agent':{'running':True,'task':'new task'}};stops=[]
        async def stop():stops.append(True)
        b=VoiceBridge(lambda:state,None,stop);sid='s'*32;b.sessions[sid]={'expires':float('inf'),'calls':{}}
        b.owner=(sid,'new task','new-id')
        assert (await b.interrupt(VoiceControl(session=sid,ids=['old-id'])))['status']=='ignored'
        assert not stops
    asyncio.run(run())


def test_drag_segments_preserve_vector_and_stop_is_release_only(tmp_path,monkeypatch):
    monkeypatch.setenv('HID_GESTURE_CHUNK','60')
    class Device:
        links=0
        def __init__(self):self.sent=[]
        async def send(self,line):self.sent.append(line)
    d=Device();p=Phone(d,tmp_path/'c.json')
    async def run():
        await p._move_xy(29,-257,point_move=False)
        moves=[list(map(int,x.split()[1:])) for x in d.sent]
        assert sum(x[0] for x in moves)==29 and sum(x[1] for x in moves)==-257
        assert max(max(map(abs,x)) for x in moves)<=60
        d.sent.clear();await p.release_input();assert d.sent==['release']
    asyncio.run(run())


def test_browser_interrupt_controller_protocol():
    subprocess.run(['node',str(Path(__file__).with_name('voice_interrupt.test.cjs'))],check=True)


@pytest.mark.parametrize('goal', ['scroll down the feed once', 'Scroll up the page once.', 'swipe down the list one time', 'scroll down feed'])
def test_scroll_surface_words_keep_single_gesture(goal):
    expected = 'up' if 'up' in goal.lower().split() else 'down'
    assert scroll_request(goal) == (expected, 1)


@pytest.mark.parametrize('goal', ['scroll down the feed until you find Notes', 'type scroll down the feed once', 'scroll down the feed and open a post'])
def test_compound_scroll_goal_is_not_truncated(goal):
    assert scroll_request(goal) is None


def test_inventory_voice_is_a_fresh_action_intent():
    from hidagent.control_intent import is_action_intent
    assert is_action_intent('根据这个库存在备忘录里新建补货笔记')
    assert is_action_intent('Read the current inventory and create a new Notes note')
    assert not is_action_intent('停止操作')
    assert not is_action_intent('This inventory is interesting')


@pytest.mark.parametrize('text',['先别导航','先不要导航','先不导航','不要导航','请先别导航',"don't navigate",'please do not navigate','cancel navigation','先别导航，看看明天下午会不会下雨'])
def test_navigation_cancellation_stops_before_another_action(text):
    from hidagent.control_intent import is_action_intent
    assert is_stop_intent(text) and not is_action_intent(text)

@pytest.mark.parametrize('text',['那回地图，换成公共交通看看','那就回到地图','然后查看天气','看看明天下午的天气','查一下这里的天气','再打开地图'])
def test_fresh_conversational_navigation_can_resume_after_stop(text):
    from hidagent.control_intent import is_action_intent
    assert is_action_intent(text) and not is_stop_intent(text)

@pytest.mark.parametrize('text',['那就算了','这个天气不错','别停',"don't stop navigation",'输入 先别导航','write do not navigate'])
def test_navigation_stop_does_not_capture_negation_or_literal_body(text):
    assert not is_stop_intent(text)


def test_navigation_intents_match_in_browser_and_server():
    from hidagent.control_intent import is_action_intent
    module=Path(__file__).parents[1]/'hidagent/static/voice-intents.js'
    cases=[('先别导航',True,False),('先别导航，看看天气',True,False),('那回地图，换成公共交通看看',False,True),('看看明天下午的天气',False,True),('输入 先别导航',False,True),('那就算了',False,False)]
    for text,stop,action in cases:
        assert (is_stop_intent(text),is_action_intent(text))==(stop,action)
    script='const m=require(process.argv[1]);const a=require("node:assert/strict");for(const [t,s,g] of JSON.parse(process.argv[2])){a.equal(m.isStopIntent(t),s,t);a.equal(m.isActionIntent(t),g,t)}'
    subprocess.run(['node','-e',script,str(module),json.dumps(cases)],check=True)


@pytest.mark.parametrize('pending',[True,False])
def test_voice_stop_preserves_controller_release_confirmation(pending):
 async def run():
  state={'device':not pending,'screen':False,'calibrated':True,'agent':{'running':False,'steps':[]}}
  async def stop():return {'ok':True,'release_pending_reconnect':pending}
  bridge=VoiceBridge(lambda:state,None,stop);sid='s'*32
  bridge.sessions[sid]={'expires':float('inf'),'calls':{}}
  result=await bridge.interrupt(VoiceControl(session=sid,text='Stop now'))
  assert result['status']=='stopped' and result['paused'] and result['release_pending_reconnect'] is pending
  plain=await bridge.execute('stop_phone',{})
  assert plain['release_pending_reconnect'] is pending
 asyncio.run(run())


RECOVERY_UTTERANCES = [
    "um could you yeah could you do something by yourself to find some interesting and and just tell me yeah this is your iPad you can do whatever else you like.",
    "No, you you can do the exploration now.",
    "No, I mean you can do exploration. You don't need to stop.",
]


@pytest.mark.parametrize('text', ["don't stop", "do not stop", "doesn't need to stop", "don't need to stop", "don't have to stop", "You don't need to stop", "you don't have to stop", "no need to stop", "never mind stopping", "不用停", "不需要停", "没必要停", "不必停"])
def test_negated_stop_does_not_interrupt(text):
    assert not is_stop_intent(text)


@pytest.mark.parametrize('text', RECOVERY_UTTERANCES + ['you can do the exploration', 'do something by yourself', 'do whatever you like', 'explore', 'go ahead', 'carry on', 'you can continue', '去探索', '你自己玩', '随便做点什么'])
def test_fresh_action_releases_server_latch(text):
    bridge = VoiceBridge(lambda: {}, None, None)
    sid = 's' * 32
    bridge.sessions[sid] = {'expires': float('inf'), 'calls': {}, 'paused': True}
    assert bridge.resume(VoiceControl(session=sid, text=text)) == {'status': 'ready', 'paused': False}
    assert bridge.sessions[sid]['paused'] is False


@pytest.mark.parametrize('text', ['stop', 'stop stop stop', 'okay stop', '停', '停下来', 'thanks', 'how are you', 'good morning', '今天天气有点冷'])
def test_stop_and_chit_chat_keep_server_latch(text):
    bridge = VoiceBridge(lambda: {}, None, None)
    sid = 's' * 32
    bridge.sessions[sid] = {'expires': float('inf'), 'calls': {}, 'paused': True}
    assert bridge.resume(VoiceControl(session=sid, text=text))['paused'] is True


def test_browser_filler_and_negation_intents():
    subprocess.run(['node', str(Path(__file__).with_name('voice_intents.test.cjs'))], check=True)
