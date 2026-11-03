import asyncio

import pytest

from hidagent.debug import Trace
from hidagent.decision import DecisionUnavailable
from hidagent.jev_agent import JevAgent, expected_result, text_candidates, is_home_goal


class Screen:
    live, frame, aspect = True, b'jpeg', .46
    async def settled(self):
        pass


class Phone:
    def __init__(self, device='iphone'):
        self.device_name, self.actions = device, []
    def target_info(self):
        return {'device': self.device_name}
    async def tap(self, x, y, aim=False):
        self.actions.append(('tap', x, y))
    async def type(self, text):
        self.actions.append(('type', text))
    async def key(self, combo):
        self.actions.append(('key', combo))


class Decisions:
    model = 'jev-test'
    def __init__(self, choices, confidence=1):
        self.choices, self.confidence, self.calls = iter(choices), confidence, []
        self.pages = iter(["home", "calculator", "calculator", "calculator"])
    async def evaluate(self, state, questions, image):
        self.calls.append((state, questions, image))
        if 'page' in questions:
            return {'model': self.model, 'seconds': .1, 'answers': {
                'page': {'choice': next(self.pages), 'confidence': 1},
                'app_visible': {'noul': 0}, 'zero': {'noul': 1}, 'result': {'noul': 1}, 'query_empty': {'noul': 1}}}
        choice = next(self.choices)
        if choice == 'expression':
            choice = next(k for k, v in questions['next']['criteria'].items() if "'2+2'" in v)
        return {'model': self.model, 'seconds': .1, 'answers':
                {'next': {'choice': choice, 'confidence': self.confidence},
                 'target': {'confidence': 1, 'box': {'left': .2, 'right': .4, 'top': .3, 'bottom': .5}}}}
    async def chat(self, *args, **kwargs):
        raise AssertionError('A Jev-only task must not use chat completion')


@pytest.mark.parametrize('device', ['iphone', 'ipad'])
def test_complete_jev_task_has_no_chat_calls_and_checks_result(device, monkeypatch):
    monkeypatch.setattr('hidagent.jev_agent.scene_changed', lambda *a, **kw: True)
    monkeypatch.setattr('hidagent.agent.vision.changed', lambda *a, **kw: True)
    phone = Phone(device)
    client = Decisions(['search', 'expression', 'enter', 'done'])
    agent = JevAgent(phone, Screen(), client, Trace(None))
    agent.task = '打开计算器，计算2+2'
    asyncio.run(agent._run())
    assert phone.actions == [('key', 'cmd+space')] + [('type', '2+2'), ('key', 'enter')]
    assert agent.steps[-1]['action'] == 'done'
    assert all(call[0]['expected_result'] == '4' for call in client.calls)
    action_calls = [call for call in client.calls if 'next' in call[1]]
    assert 'done' not in action_calls[0][1]['next']['criteria']
    assert not any(key.startswith('type_') for key in action_calls[0][1]['next']['criteria'])
    assert 'done' in action_calls[-1][1]['next']['criteria']
    assert client.calls[0][0]['device']['device'] == device


def test_uncertain_jev_does_not_click_or_fallback():
    client = Decisions(['tap'], confidence=.6)
    phone = Phone()
    agent = JevAgent(phone, Screen(), client, Trace(None))
    agent.task = '打开设置'
    asyncio.run(agent._run())
    assert not phone.actions
    assert len(client.calls) == 2
    assert agent.steps[-1]['action'] == 'error'


def test_literal_candidates_do_not_invent_text():
    values = text_candidates('打开备忘录，输入「hello world」')
    assert 'hello world' in values and 'Notes' in values
    assert 'Calculator' in text_candidates('open Calculator and calculate 2+2')
    assert '2+2' in text_candidates('打开计算器，计算2+2')


@pytest.mark.parametrize('text', ['__import__("os").system("x")', '2**100000000', '1/0', 'True', '1e1000'])
def test_calculation_expectation_is_bounded_and_never_executes(text):
    assert expected_result(text) is None


def test_jev_observer_does_not_wait_for_airplay_to_stop_sending(monkeypatch):
    import io
    from PIL import Image
    def jpeg(color):
        out = io.BytesIO()
        Image.new('RGB', (50, 100), color).save(out, format='JPEG')
        return out.getvalue()
    before, after = jpeg('black'), jpeg('white')
    clock = [0.0]
    async def sleep(seconds):
        clock[0] += seconds
    monkeypatch.setattr('hidagent.jev_agent.time.monotonic', lambda: clock[0])
    monkeypatch.setattr('hidagent.jev_agent.asyncio.sleep', sleep)
    class RepeatedFrames:
        frame = after
        async def settled(self):
            raise AssertionError('Do not wait for stream inactivity')
    agent = JevAgent(Phone(), RepeatedFrames(), None, Trace(None))
    asyncio.run(agent._observe(before, ()))
    assert clock[0] < .5


def test_changed_screen_cancels_an_old_jev_click(monkeypatch):
    screen = Screen()
    client = Decisions(['tap'])
    original = client.evaluate
    async def evaluate(state, questions, image):
        result = await original(state, questions, image)
        if 'next' in questions:
            screen.frame = b'new page'
        return result
    client.evaluate = evaluate
    monkeypatch.setattr('hidagent.jev_agent.scene_changed', lambda *a, **kw: True)
    agent = JevAgent(Phone(), screen, client, Trace(None))
    agent.task = '打开设置'
    action = asyncio.run(agent._next_actions())[0]
    assert action['action'] == 'wait' and 'x' not in action


@pytest.mark.parametrize('goal', ['返回主屏', '回到主屏幕', 'go home', 'return to home'])
def test_home_goal_is_explicit(goal):
    assert is_home_goal(goal)
    assert not is_home_goal('打开计算器并计算2+2')


@pytest.mark.parametrize('visible,shift,home,accepted', [(1,0,True,True),(.7,0,True,False),(1,.1,True,False),(1,0,False,False)])
def test_low_contrast_home_control_needs_two_view_agreement_and_identity(visible,shift,home,accepted):
    from hidagent.jev_agent import grounding_accepted
    box = {'left': .8, 'right': .9, 'top': .7, 'bottom': .8}
    mapped = {k:v+shift for k,v in box.items()}
    refined = {'mapped_box': mapped, 'answers': {'target': {'confidence': .5}, 'visible': {'noul': visible}}}
    assert grounding_accepted(refined,box,home) is accepted


@pytest.mark.parametrize('ready', [True,False])
def test_direct_dictation_uses_one_focus_check_and_one_result_check_without_submitting(monkeypatch,ready):
    monkeypatch.setattr('hidagent.agent.vision.changed',lambda *a,**kw:True)
    class DictationModel:
        model='jev-test'
        def __init__(self): self.calls=0
        async def evaluate(self,state,questions,image):
            self.calls+=1
            return {'model':self.model,'seconds':.1,'answers':{
                'condition':{'noul':1 if ready else 0},
                'field':{'box':{'left':.1,'right':.9,'top':.1,'bottom':.3}},
                'next':{'choice':'done' if state['already_typed'] else 'type','confidence':1}}}
        async def chat(self,*a,**kw): raise AssertionError('No text generation')
    model=DictationModel();phone=Phone();screen=Screen()
    import io
    from PIL import Image
    out=io.BytesIO();Image.new('RGB',(50,100),'white').save(out,format='JPEG');screen.frame=out.getvalue()
    agent=JevAgent(phone,screen,model,Trace(None));agent.task='输入hello2026'
    asyncio.run(agent._run())
    if ready:
        assert phone.actions==[('type','hello2026')]
        assert model.calls==2 and agent.steps[-1]['action']=='done'
    else:
        assert not phone.actions and model.calls==1 and agent.steps[-1]['action']=='error'


def test_open_app_uses_confirmed_spotlight_result_then_verifies_app():
    class Model:
        async def evaluate(self, state, questions, image):
            return {'seconds': .1, 'answers': {'page': {'choice': 'search', 'confidence': .99},
                                             'top_app': {'noul': .99}}}
    agent = JevAgent(Phone(), Screen(), Model(), Trace(None))
    agent.task = 'open joe speaking app'
    action = asyncio.run(agent._next_actions())[0]
    assert action['action'] == 'key' and action['combo'] == 'enter'
    assert text_candidates(agent.task) == ['joe speaking']
    class OpenModel:
        async def evaluate(self, *args):
            return {'seconds': .1, 'answers': {'page': {'choice': 'target_app', 'confidence': .99}}}
    agent.client = OpenModel()
    assert asyncio.run(agent._next_actions())[0]['action'] == 'done'


def test_haiku_backup_handles_jev_uncertainty_without_duplicate_typing(monkeypatch):
    monkeypatch.setattr('hidagent.jev_agent.scene_changed', lambda *a: False)
    class Uncertain:
        async def evaluate(self, *a): raise DecisionUnavailable('uncertain')
    class Backup:
        model = 'haiku-test'
        async def chat(self, *a, **kw): return 'THINK: App content is visible\nDO: done App is open'
    import io
    from PIL import Image
    out = io.BytesIO(); Image.new('RGB',(498,1080)).save(out,format='JPEG')
    screen = Screen(); screen.frame = out.getvalue()
    agent = JevAgent(Phone(), screen, Uncertain(), Trace(None), fallback=Backup())
    agent.task = 'open joe speaking app'
    action = asyncio.run(agent._next_actions())[0]
    assert action['action'] == 'done' and action['thought'].startswith('Haiku backup:')
    assert agent._backup_count == 1
    class Duplicate(Backup):
        async def chat(self,*a,**kw): return 'DO: type hello'
    agent.fallback = Duplicate(); agent.task = 'type hello'
    agent.steps = [{'action':'type','text':'hello'}]
    with pytest.raises(DecisionUnavailable, match='duplicate'):
        asyncio.run(agent._fallback_actions('uncertain'))


def test_dictation_result_keeps_text_below_original_placeholder_box(monkeypatch):
    import io
    from PIL import Image,ImageDraw
    from types import SimpleNamespace
    monkeypatch.setattr('hidagent.jev_agent.scene_changed',lambda *args:False)
    im=Image.new('RGB',(498,1080),'white');ImageDraw.Draw(im).rectangle((50,360,440,430),fill='green')
    out=io.BytesIO();im.save(out,format='PNG');frame=out.getvalue()
    class Judge:
        async def evaluate(self,state,questions,image):
            seen=Image.open(io.BytesIO(image))
            assert seen.size==(498,1080)
            assert seen.getpixel((100,400))==(0,128,0)
            return {'seconds':.1,'answers':{'condition':{'noul':1}}}
    a=JevAgent(None,SimpleNamespace(frame=frame),Judge(),Trace(None))
    a.steps=[{'action':'type','text':'A long wrapped tweet'}]
    a._dictation_size=(498,1080);a._dictation_field={'top':.17,'bottom':.22,'left':.11,'right':.54}
    assert asyncio.run(a._dictate('A long wrapped tweet'))[0]['action']=='done'
    assert len(a.steps)==1 # verification does not execute any input


def test_dictation_result_still_refuses_orientation_change_before_judgment():
    import io
    from PIL import Image
    from types import SimpleNamespace
    from hidagent.agent import BlindError
    out=io.BytesIO();Image.new('RGB',(1080,498)).save(out,format='JPEG')
    class Judge:
        async def evaluate(self,*args):raise AssertionError('Must reject changed geometry first')
    a=JevAgent(None,SimpleNamespace(frame=out.getvalue()),Judge(),Trace(None))
    a.steps=[{'action':'type','text':'Already typed'}];a._dictation_size=(498,1080)
    with pytest.raises(BlindError,match='orientation changed'):
        asyncio.run(a._dictate('Already typed'))
