import asyncio
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest

from hidagent import cli
from hidagent.client import IPhoneClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HIDAGENT_DATA", str(tmp_path))
    module = importlib.import_module("hidagent.app")
    calls = []

    class Phone:
        span = {"w": 400, "h": 800}
        calibrated = True
        async def tap(self, x, y, aim=False): calls.append(("tap", x, y, "aim") if aim else ("tap", x, y))
        async def type(self, text): calls.append(("type", text))
        async def key(self, text): calls.append(("key", text))
        async def start_calibration(self): return True
        def forget_position(self): calls.append(("forget",))
        def finish_calibration(self, x, y): calls.append(("calibrate", x, y))

    class Agent:
        running, task, steps = False, "", []
        trace = SimpleNamespace(dir=None)
        actor = planner = SimpleNamespace(model="fake")
        def start(self, goal): self.running, self.task = True, goal
        def stop(self): self.running = False

    monkeypatch.setattr(module, "phone", Phone())
    monkeypatch.setattr(module, "agent", Agent())
    monkeypatch.setattr(module, "device", SimpleNamespace(connected=False))
    monkeypatch.setattr(module, "screen", SimpleNamespace(live=True, frame=b"jpeg", aspect=.46))
    monkeypatch.setattr(module, "manual_lock", asyncio.Lock())
    return module, calls


def test_client_uses_legacy_routes_and_single_current_task():
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"agent": {"running": False, "task": "", "steps": []}})
        return httpx.Response(200, json={"ok": True})

    with IPhoneClient("http://legacy", transport=httpx.MockTransport(respond)) as client:
        client.delegate("Open Clock")
        assert client.task_status()["running"] is False
        client.stop()
        client.action("key", text="cmd+a")
        client.calibrate(.5, .4)
        with pytest.raises(ValueError):
            client.calibrate(.5)
        with pytest.raises(ValueError):
            client.action("raw", text="home")
    assert [path for _, path, _ in requests] == [
        "/api/agent", "/api/status", "/api/agent/stop", "/api/key", "/api/calibrate/finish"]
    assert requests[0][2] == b'{"text":"Open Clock"}'


def test_cli_keeps_json_on_stdout_and_errors_on_stderr(monkeypatch, capsys):
    def respond(request):
        return httpx.Response(409, json={"detail": "phone is busy"})

    monkeypatch.setattr(cli, "IPhoneClient", lambda url: IPhoneClient(url, transport=httpx.MockTransport(respond)))
    assert cli.main(["--url", "http://legacy", "type", "Calendar"]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)['error']=='phone is busy'


def test_cli_status_and_screenshot_do_not_import_mcp(monkeypatch, capsys, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "mcp", None)

    def respond(request):
        if request.url.path == "/api/frame.jpg":
            return httpx.Response(200, content=b"synthetic jpeg")
        return httpx.Response(200, json={"device": True})

    monkeypatch.setattr(cli, "IPhoneClient", lambda url: IPhoneClient(url, transport=httpx.MockTransport(respond)))
    assert cli.main(["--url", "http://legacy", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"device": True}
    output = tmp_path / "phone.jpg"
    assert cli.main(["--url", "http://legacy", "screenshot", str(output)]) == 0
    assert output.read_bytes() == b"synthetic jpeg"
    assert json.loads(capsys.readouterr().out)["path"] == str(output)


@pytest.mark.parametrize("url", ["file:///tmp/phone", "http://legacy?key=secret", "http://legacy#fragment"])
def test_client_rejects_unsupported_urls(url):
    with pytest.raises(ValueError):
        IPhoneClient(url)


def test_direct_action_and_agent_start_cannot_interleave(api, monkeypatch):
    module, calls = api

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def tap(x, y):
            entered.set()
            await release.wait()
            calls.append(("tap", x, y))

        monkeypatch.setattr(module.phone, "tap", tap)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1") as client:
            pending = asyncio.create_task(client.post("/api/tap", json={"x": .5, "y": .4}))
            await entered.wait()
            for path, body in [("/api/key", {"text": "enter"}), ("/api/agent", {"text": "Open Clock"}),
                               ("/api/calibrate/start", None), ("/api/calibrate/finish", {"x": .5, "y": .4})]:
                assert (await client.post(path, json=body)).status_code == 409
            assert not calls
            assert (await client.post("/api/agent/stop")).status_code == 200
            release.set()
            result=await pending
            assert result.status_code==409
            assert result.json()['detail']=='Manual action stopped; do not retry automatically'
            assert not calls  # stop cancels the direct tap rather than letting it resume
            assert (await client.post("/api/agent", json={"text": "Open Clock"})).status_code == 200
            assert (await client.post("/api/type", json={"text": "Calendar"})).status_code == 409
            assert not calls

    asyncio.run(scenario())


def test_precise_tap_finds_the_pointer_first(api):
    module, calls = api

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1") as client:
            assert (await client.post("/api/tap", json={"x": .5, "y": .4, "precise": True})).status_code == 200
            assert (await client.post("/api/tap", json={"x": .2, "y": .3})).status_code == 200

    asyncio.run(scenario())
    assert calls == [("tap", .5, .4, "aim"), ("tap", .2, .3)]


@pytest.mark.parametrize("path,body", [
    ("/api/tap", {"x": 2, "y": .5}),
    ("/api/swipe", {"x1": 0, "y1": 0, "x2": -1, "y2": .5}),
    ("/api/scroll", {"x": .5, "y": .5, "lines": 101}),
    ("/api/type", {"text": "Calendar\nhome"}),
    ("/api/key", {"text": "enter\rhome"}),
    ("/api/type", {"text": "你好\u2028世界"}),
    ("/api/key", {"text": "cmd+你"}),
    ("/api/type", {"text": "\u007f"}),
    ("/api/calibrate/finish", {"x": 0, "y": .5}),
])
def test_invalid_remote_input_is_rejected_before_hid(api, path, body):
    module, calls = api

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1") as client:
            assert (await client.post(path, json=body)).status_code == 422
        assert not calls

    asyncio.run(scenario())


def test_non_ascii_text_reaches_the_phone(api):
    module, calls = api

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1") as client:
            assert (await client.post("/api/type", json={"text": "打开设置 ⚙️"})).status_code == 200

    asyncio.run(scenario())
    assert calls == [("type", "打开设置 ⚙️")]


def test_raw_board_commands_make_the_pointer_position_unknown(api, monkeypatch):
    module, calls = api
    sent = []

    async def send(line):
        sent.append(line)
    monkeypatch.setattr(module, "DEBUG", True)
    monkeypatch.setattr(module, "device", type("D", (), {"send": staticmethod(send), "connected": True})())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1") as client:
            assert (await client.post("/api/raw", json={"text": "move 40 0"})).status_code == 200

    asyncio.run(scenario())
    assert sent == ["move 40 0"] and calls == [("forget",)]


def test_raw_board_commands_forget_the_pointer_even_when_the_board_errs(api, monkeypatch):
    # the board may have moved the pointer before the reply timed out
    from hidagent.device import DeviceError
    module, calls = api

    async def send(line):
        raise DeviceError("board did not answer")
    monkeypatch.setattr(module, "DEBUG", True)
    monkeypatch.setattr(module, "device", type("D", (), {"send": staticmethod(send), "connected": True})())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://127.0.0.1") as client:
            assert (await client.post("/api/raw", json={"text": "move 40 0"})).status_code == 409

    asyncio.run(scenario())
    assert calls == [("forget",)]


@pytest.mark.parametrize('running,last,expected', [(False,{'action':'done'},'completed'),
                                                  (False,{'action':'error'},'failed'),
                                                  (True,{},'in_progress')])
def test_single_call_command_does_not_repeat_start_or_hide_failure(running,last,expected):
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/api/status':
            return httpx.Response(200,json={'engine':'jev','agent':{'running':running,'task':'输入hello','steps':[last]}})
        return httpx.Response(200,json={'ok':True})
    with IPhoneClient('http://legacy',transport=httpx.MockTransport(respond)) as client:
        result=client.command('输入hello',0)
    assert result['status']==expected and result['engine']=='jev'
    assert calls==['/api/agent','/api/status']


@pytest.mark.parametrize("running", [False, True])
def test_voice_cli_structured_goal_runs_once(monkeypatch, capsys, tmp_path, running):
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path=='/api/status':
            return httpx.Response(200,json={'agent':{'running':running,'steps':[] if running else [{'action':'done'}]}})
        if request.url.path=='/api/frame.jpg':return httpx.Response(200,content=b'actual frame')
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(cli,'IPhoneClient',lambda url:IPhoneClient(url,transport=httpx.MockTransport(respond)))
    goal=tmp_path/'goal.json';goal.write_text(json.dumps({'goal':'Open Calculator'}))
    image=tmp_path/'actual.jpg'
    assert cli.main(['command','--goal-file',str(goal),'--wait-seconds','0','--screenshot',str(image)])==0
    receipt=json.loads(capsys.readouterr().out)
    assert receipt['status']==('in_progress' if running else 'completed')
    assert receipt['screenshot_path']==str(image) and image.read_bytes()==b'actual frame'
    assert calls.count('/api/agent')==1


def test_voice_cli_image_failure_keeps_executed_receipt(monkeypatch, capsys, tmp_path):
    calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path=='/api/status':return httpx.Response(200,json={'agent':{'running':False,'steps':[{'action':'done'}]}})
        if request.url.path=='/api/frame.jpg':return httpx.Response(503)
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(cli,'IPhoneClient',lambda url:IPhoneClient(url,transport=httpx.MockTransport(respond)))
    assert cli.main(['command','--goal','Open Calculator','--wait-seconds','0','--screenshot',str(tmp_path/'frame.jpg')])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='completed' and 'screenshot_error' in result
    assert calls.count('/api/agent')==1


@pytest.mark.parametrize('payload',[{'goal':''},{'goal':73},[],{'wrong':'Open Calculator'}])
def test_voice_cli_invalid_goal_never_sends_input(monkeypatch,capsys,tmp_path,payload):
    def respond(request):raise AssertionError('No HTTP request permitted')
    monkeypatch.setattr(cli,'IPhoneClient',lambda url:IPhoneClient(url,transport=httpx.MockTransport(respond)))
    p=tmp_path/'goal.json';p.write_text(json.dumps(payload))
    assert cli.main(['command','--goal-file',str(p)])==2
    assert not capsys.readouterr().out


def test_ipad_direct_tap_uses_pointer_verification_by_default(api,monkeypatch):
    module,calls=api
    monkeypatch.setattr(module.phone,'target_info',lambda:{'device':'ipad'},raising=False)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/tap',json={'x':.5,'y':.4})).status_code==200
    asyncio.run(run())
    assert calls==[('tap',.5,.4,'aim')]


def test_status_reports_live_and_finished_whole_task_elapsed(monkeypatch):
    import hidagent.app as api
    from types import SimpleNamespace
    from unittest.mock import Mock
    fake=SimpleNamespace(running=True,task='read only',steps=[],started_at=100.,finished_at=None,
        planner=SimpleNamespace(model='test'),actor=SimpleNamespace(model='test'),trace=SimpleNamespace(dir=None))
    monkeypatch.setattr(api,'agent',fake)
    monkeypatch.setattr(api.time,'time',lambda:174.)
    monkeypatch.setattr(api,'phone',SimpleNamespace(target_info=lambda:{'aspect':1.5},calibrated=True))
    monkeypatch.setattr(api,'screen',SimpleNamespace(live=True,aspect=1.5))
    monkeypatch.setattr(api,'device',SimpleNamespace(connected=True,readiness=lambda:{"connected":True,"stable":True}))
    monkeypatch.setattr(api,'ENGINE','legacy')
    assert api.status()['agent']['elapsed_s']==74
    fake.running=False;fake.finished_at=180.
    assert api.status()['agent']['elapsed_s']==80


def test_full_calibration_has_longer_read_timeout_without_retrying():
    import httpx
    from hidagent.client import IPhoneClient
    seen=[]
    def handle(request):
        seen.append((request.url.path,request.extensions['timeout']['read']))
        return httpx.Response(200,json={'ok':True,'auto':True})
    with IPhoneClient('http://127.0.0.1:8890',transport=httpx.MockTransport(handle)) as client:
        client.calibrate()
        client.status()
    assert seen==[('/api/calibrate/start',180),('/api/status',60)]


def test_bounded_probe_never_homes_clicks_or_types(api,monkeypatch):
    from unittest.mock import AsyncMock,Mock
    module,calls=api
    monkeypatch.setattr(module,'screen',SimpleNamespace(live=True))
    monkeypatch.setattr(module,'device',SimpleNamespace(connected=True))
    send=AsyncMock();forget=Mock()
    monkeypatch.setattr(module,'phone',SimpleNamespace(_send=send,forget_position=forget))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as client:
            assert (await client.post('/api/probe',json={'dx':60,'dy':0})).status_code==200
            for data in ({'dx':201,'dy':0},{'dx':True,'dy':0},{'dx':1.5,'dy':0}):
                assert (await client.post('/api/probe',json=data)).status_code==422
    asyncio.run(check())
    assert [c.args[0] for c in send.await_args_list]==['release','move 60 0']
    forget.assert_called_once()


def test_motion_timing_changes_only_bounded_protocol_pacing(api,monkeypatch):
    monkeypatch.setenv('HID_POINTER_MS','40')
    from unittest.mock import AsyncMock,Mock
    module,_=api;send=AsyncMock()
    monkeypatch.setattr(module,'device',SimpleNamespace(send=send))
    monkeypatch.setattr(module,'phone',SimpleNamespace(forget_position=Mock()))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as c:
            assert (await c.post('/api/motion-timing',json={'milliseconds':80})).status_code==200
            for value in (0,101,True,80.5):
                assert (await c.post('/api/motion-timing',json={'milliseconds':value})).status_code==422
    asyncio.run(check());send.assert_awaited_once_with('tune 10 80')


def test_motion_timing_invalidates_actual_profile_override(api,monkeypatch):
    # Global default equals requested cadence, but the active geometry is 80ms.
    # Compare with the active cadence, otherwise the 80ms mapping stays ready.
    monkeypatch.setenv('HID_POINTER_MS','40')
    monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":80,"ipad-landscape-66":80}')
    from unittest.mock import AsyncMock,Mock
    module,_=api
    phone=SimpleNamespace(forget_position=Mock(),_pointer_ms=lambda:80,
        target_info=lambda:{'calibration_key':'ipad-portrait-66'},
        span={'w':900,'h':1300},_profile_key='ipad-portrait-66')
    send=AsyncMock()
    monkeypatch.setattr(module,'device',SimpleNamespace(send=send,links=7))
    monkeypatch.setattr(module,'phone',phone)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as c:
            response=await c.post('/api/motion-timing',json={'milliseconds':40})
            assert response.status_code==200
    asyncio.run(check())
    assert phone.span is None and phone._profile_key is None
    import os,json
    assert json.loads(os.environ['HID_POINTER_PROFILE_MS'])=={'ipad-portrait-66':40,'ipad-landscape-66':80}
    send.assert_awaited_once_with('tune 10 40')


def test_emergency_stop_cancels_direct_manual_action_before_next_command(api,monkeypatch):
    from unittest.mock import AsyncMock,Mock
    module,_=api;commands=[]
    async def run():
        entered=asyncio.Event()
        async def send(command):
            commands.append(command);entered.set();await asyncio.Event().wait()
        monkeypatch.setattr(module,'screen',SimpleNamespace(live=True))
        monkeypatch.setattr(module,'device',SimpleNamespace(connected=True))
        monkeypatch.setattr(module,'phone',SimpleNamespace(_send=send,forget_position=Mock(),release_input=AsyncMock()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as c:
            move=asyncio.create_task(c.post('/api/probe',json={'dx':100,'dy':0}))
            await entered.wait()
            assert (await c.post('/api/agent/stop')).status_code==200
            result=await move
            assert result.status_code==409
            assert result.json()['detail']=='Manual action stopped; do not retry automatically'
            assert module.manual_task is None and not module.manual_stop_tasks
    asyncio.run(run());assert commands==['release']


def test_non_stop_cancellation_is_not_misreported_as_user_stop(api):
 module,_=api
 async def action():raise asyncio.CancelledError
 with pytest.raises(asyncio.CancelledError):asyncio.run(module.act(action))
 assert module.manual_task is None and not module.manual_stop_tasks


@pytest.mark.parametrize('entry',['manual','agent','clear'])
def test_exploration_ownership_blocks_other_device_entrypoints(api,monkeypatch,entry):
    module,calls=api
    monkeypatch.setattr(module,'exploration',SimpleNamespace(busy=True))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        if entry=='manual':module.check_manual()
        elif entry=='agent':asyncio.run(module.agent_start(module.Text(text='Open Calculator')))
        else:module.agent_clear()
    assert exc.value.status_code==409 and calls==[]

def test_emergency_stop_also_stops_owned_exploration(api,monkeypatch):
    from unittest.mock import AsyncMock
    module,_=api;stop=AsyncMock()
    monkeypatch.setattr(module,'exploration',SimpleNamespace(busy=True,stop=stop))
    assert asyncio.run(module.agent_stop())['ok']
    stop.assert_awaited_once()


def test_keyboard_pacing_is_bounded_local_and_sends_no_text(api,monkeypatch):
 from fastapi.testclient import TestClient
 module,calls=api;monkeypatch.setenv('HID_KEY_MS','80')
 client=TestClient(module.app)
 assert client.post('/api/keyboard-timing',json={'milliseconds':60}).status_code==200
 assert module.os.environ['HID_KEY_MS']=='60' and calls==[]
 assert client.post('/api/keyboard-timing',json={'milliseconds':20}).status_code==422
 assert client.post('/api/keyboard-timing',json={'milliseconds':True}).status_code==422
 assert client.post('/api/keyboard-timing',json={'milliseconds':60},headers={'Origin':'https://evil.example'}).status_code==403
 module.agent.running=True
 assert client.post('/api/keyboard-timing',json={'milliseconds':40}).status_code==409
 assert module.os.environ['HID_KEY_MS']=='60'


def test_disconnected_stop_does_not_claim_release_was_confirmed(api,monkeypatch):
 from unittest.mock import AsyncMock
 module,_=api
 monkeypatch.setattr(module,'device',SimpleNamespace(connected=False))
 release=AsyncMock();monkeypatch.setattr(module,'phone',SimpleNamespace(release_input=release))
 result=asyncio.run(module.agent_stop())
 assert result['ok'] and result['release_pending_reconnect']
 release.assert_not_awaited()


def test_manual_stop_revokes_late_click_even_if_move_swallows_cancel(api,monkeypatch,tmp_path):
 from hidagent.phone import Phone
 from fastapi import HTTPException
 module,_=api
 async def run():
  entered=asyncio.Event();cancel_seen=asyncio.Event();finish=asyncio.Event();sent=[]
  async def send(command):
   sent.append(command)
   if command=='move 100 0':
    entered.set()
    try:await finish.wait()
    except asyncio.CancelledError:cancel_seen.set();await finish.wait()
  device=SimpleNamespace(connected=True,links=1,send=send)
  phone=Phone(device,tmp_path/'map');monkeypatch.setattr(module,'device',device);monkeypatch.setattr(module,'phone',phone)
  async def action():
   try:await phone._send('move 100 0');await phone._send('click')
   finally:await phone.release_input()
  task=asyncio.create_task(module.act(action));await entered.wait()
  stop=asyncio.create_task(module.agent_stop());await cancel_seen.wait();finish.set();await stop
  try:await task
  except HTTPException as exc:assert exc.status_code==409
  else:raise AssertionError('stopped manual action reported success')
  assert 'click' not in sent and sent[0]=='move 100 0' and all(x=='release' for x in sent[1:])
 asyncio.run(run())


@pytest.mark.parametrize('flag,family,orientation,stable,live,ms,expected',[
 ('1','ipad','portrait',True,True,30,200),
 ('1','ipad','portrait',True,True,20,200),
 ('0','ipad','portrait',True,True,30,422),
 ('1','iphone','portrait',True,True,30,422),
 ('1','ipad','landscape',True,True,30,422),
 ('1','ipad','portrait',True,True,25,422),
 ('1','ipad','portrait',False,True,30,409),
 ('1','ipad','portrait',True,False,30,409),
])
def test_fast_pointer_api_is_explicit_isolated_and_not_mapped(api,monkeypatch,flag,family,orientation,stable,live,ms,expected):
 from unittest.mock import AsyncMock,Mock
 import os
 module,_=api
 monkeypatch.setenv('HID_POINTER_FAST_EXPERIMENT',flag)
 monkeypatch.setenv('HID_POINTER_MS','40')
 monkeypatch.setenv('HID_POINTER_PROFILE_MS','{"ipad-portrait-66":40,"ipad-landscape-66":80}')
 send=AsyncMock()
 monkeypatch.setattr(module,'device',SimpleNamespace(connected=True,links=1,send=send,readiness=lambda:{'stable':stable}))
 monkeypatch.setattr(module,'screen',SimpleNamespace(live=live))
 target={'device':family,'orientation':orientation,'calibration_key':f'{family}-{orientation}-66'}
 phone=SimpleNamespace(_pointer_ms=lambda:40,target_info=lambda:target,calibrated=True,
     span={'w':968,'h':1424},_profile_key=target['calibration_key'],forget_position=Mock())
 monkeypatch.setattr(module,'phone',phone)
 async def check():
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as c:
   r=await c.post('/api/motion-timing',json={'milliseconds':ms});assert r.status_code==expected,r.text
 asyncio.run(check())
 if expected==200:
  assert phone.span is None and phone._profile_key is None
  assert json.loads(os.environ['HID_POINTER_PROFILE_MS'])=={'ipad-portrait-66':ms,'ipad-landscape-66':80}
  assert os.environ['HID_POINTER_MS']=='40'
  send.assert_awaited_once_with(f'tune 10 {ms}')
 else:send.assert_not_awaited()


def test_timing_unknown_delivery_never_caches_success(api,monkeypatch):
 from unittest.mock import AsyncMock,Mock
 from hidagent.device import DeviceError
 module,_=api
 monkeypatch.setenv('HID_POINTER_MS','40')
 monkeypatch.delenv('HID_POINTER_PROFILE_MS',raising=False)
 phone=SimpleNamespace(forget_position=Mock(),_pointer_ms=lambda:40,_pointer_tuned_link=(1,40))
 monkeypatch.setattr(module,'phone',phone)
 monkeypatch.setattr(module,'device',SimpleNamespace(links=1,send=AsyncMock(side_effect=DeviceError('lost ACK'))))
 async def check():
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as c:
   assert (await c.post('/api/motion-timing',json={'milliseconds':80})).status_code==409
 asyncio.run(check())
 assert phone._pointer_tuned_link is None
 phone.forget_position.assert_called_once()


@pytest.mark.parametrize('case',['link_changes','cross_origin','busy'])
def test_fast_pacing_cannot_outlive_binding_or_take_busy_device(api,monkeypatch,case):
 from unittest.mock import AsyncMock,Mock
 import os
 module,_=api
 monkeypatch.setenv('HID_POINTER_FAST_EXPERIMENT','1')
 monkeypatch.setenv('HID_POINTER_MS','40')
 original='{"ipad-portrait-66":40}'
 monkeypatch.setenv('HID_POINTER_PROFILE_MS',original)
 target={'device':'ipad','orientation':'portrait','calibration_key':'ipad-portrait-66'}
 board=SimpleNamespace(connected=True,links=1,readiness=lambda:{'stable':True})
 async def tune(command):
  if case=='link_changes':board.links=2
 board.send=AsyncMock(side_effect=tune)
 phone=SimpleNamespace(_pointer_ms=lambda:40,target_info=lambda:target,calibrated=True,
     span={'w':968,'h':1424},_profile_key='ipad-portrait-66',forget_position=Mock())
 monkeypatch.setattr(module,'device',board);monkeypatch.setattr(module,'phone',phone)
 if case=='busy':module.agent.running=True
 async def check():
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),base_url='http://127.0.0.1') as c:
   r=await c.post('/api/motion-timing',json={'milliseconds':30},headers={'Origin':'https://foreign.example'} if case=='cross_origin' else {})
   assert r.status_code==(403 if case=='cross_origin' else 409)
 asyncio.run(check())
 assert os.environ['HID_POINTER_PROFILE_MS']==original
 if case=='link_changes':
  board.send.assert_awaited_once_with('tune 10 30');assert phone._pointer_tuned_link is None
 else:board.send.assert_not_awaited()
