"""Web API + control page. Run with `python -m hidagent`."""

import asyncio
import hashlib
import json
import os
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .debug import Trace
from .device import Device, DeviceError, InputTimelines, timeline_event, command_source
from .llm import LLM
from .local_text import LocalTextReader, LocalTextError
from .phone import Phone
from .profiles import TargetProfiles
from .screen import Screen
from .hid_lease import leased_input
from .camera_conversation import active_any as camera_active_any, end_all_instances as camera_end_all

DATA_DIR = Path(os.environ.get("HIDAGENT_DATA", Path.home() / ".hidagent"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEBUG = os.environ.get("HIDAGENT_DEBUG") == "1"

local_text = LocalTextReader([os.environ["LOCAL_OCR_BIN"], "--serve"]) if os.environ.get("LOCAL_OCR_BIN") else None

device = Device()
# MIRROR_STREAM=127.0.0.1:8891 follows a standalone receiver (python -m hidagent.receiver) instead of running
# UxPlay here, so restarting the server keeps the phone's Screen Mirroring session
screen = Screen(os.environ.get("AIRPLAY_NAME", "iPhone Agent"), stream=os.environ.get("MIRROR_STREAM"),
                pidfile=DATA_DIR / "uxplay.pid")
targets = TargetProfiles(DATA_DIR / "target-profile.json")
phone = Phone(device, DATA_DIR / "calibration.json", screen, profiles=targets)
ENGINE = os.environ.get("AGENT_ENGINE", "llm").lower()
if ENGINE == "haiku_demo":
    from .vision_judge import VisionJudge
    from .hybrid_agent import HybridAgent
    agent = HybridAgent(phone, screen, VisionJudge(LLM.from_env("ACTOR")), LLM.from_env("ACTOR"),
                        Trace(DATA_DIR / "traces" if DEBUG or os.environ.get("HIDAGENT_TRACE") == "1" else None))
elif ENGINE in ("hybrid", "meta_hybrid", "meta_local_hybrid"):
    from .decision import Jev
    from .hybrid_agent import HybridAgent
    from .vision_judge import JudgeFallback, VisionJudge
    from .meta_model import meta_llm
    model_client = meta_llm if ENGINE in ("meta_hybrid", "meta_local_hybrid") else lambda: LLM.from_env("ACTOR")
    judge = JudgeFallback(Jev.from_env(), VisionJudge(model_client()))
    agent = HybridAgent(phone, screen, judge, model_client(),
                        Trace(DATA_DIR / "traces" if DEBUG or os.environ.get("HIDAGENT_TRACE") == "1" else None))
elif ENGINE == "jev":
    from .decision import Jev
    from .jev_agent import JevAgent
    agent = JevAgent(phone, screen, Jev.from_env(), Trace(DATA_DIR / "traces" if DEBUG or os.environ.get("HIDAGENT_TRACE") == "1" else None), fallback=LLM.from_env("ACTOR") if os.environ.get("JEV_FALLBACK") == "1" else None)
else:
    agent = Agent(phone, screen, LLM.from_env("ACTOR"), LLM.from_env("PLANNER"),
                  Trace(DATA_DIR / "traces" if DEBUG or os.environ.get("HIDAGENT_TRACE") == "1" else None))


if ENGINE in ("hybrid", "meta_hybrid", "meta_local_hybrid"):
    agent.keyboard_workflows = os.environ.get("KEYBOARD_WORKFLOWS", "1") == "1"
    source_author = os.environ.get("SOURCE_NOTE_AUTHOR", "default")
    if source_author == "meta_local":
        # Meta writes once from source pixels; the existing fast execution and
        # Jev/Haiku judgment clients remain unchanged.
        agent.source_writer = meta_llm(engine="meta_local_hybrid")
    elif source_author != "default":
        raise RuntimeError("Unsupported SOURCE_NOTE_AUTHOR; no writer substituted")
    if os.environ.get("SOURCE_NOTE_INVENTORY_OCR")=="1" and os.environ.get("SOURCE_NOTE_OCR_MODEL"):
        agent.inventory_writer=meta_llm(engine="meta_local_hybrid",local_model=os.environ["SOURCE_NOTE_OCR_MODEL"])


@asynccontextmanager
async def lifespan(_):
    if voice_bridge.driver_name != 'esp32':
        # Native drivers must not start the Bluetooth-board listener, AirPlay or idle nudges.
        try:
            yield
        finally:
            voice_bridge.driver_executor.stop()
            if voice_bridge.driver_executor.busy:
                await asyncio.shield(voice_bridge.driver_executor.task)
            if hasattr(voice_bridge.driver, 'aclose'):
                await voice_bridge.driver.aclose()
            if local_text is not None:
                await local_text.close()
            if globals().get('receipt_journal') is not None:
                receipt_journal.close()
            for client in {globals().get('mission_writer'), globals().get('draft_writer')} - {None, agent.actor}:
                await client._http.aclose()
            if hasattr(agent, 'aclose'):
                await agent.aclose()
        return
    server = await device.serve("0.0.0.0", int(os.environ.get("DEVICE_PORT", 7300)))
    screen.start()
    awake = asyncio.create_task(device.keep_awake(screen=screen, busy=lambda: agent.running or manual_lock.locked() or exploration.busy or camera_active_any(),
                                                  nudge=os.environ.get("IDLE_NUDGE", "1") == "1"))
    yield
    if globals().get('receipt_journal') is not None:
        await agent_stop()
        await app_tools.flush_history()
    awake.cancel()
    if local_text is not None:
        await local_text.close()
    screen.stop()
    server.close()
    if globals().get('receipt_journal') is not None:receipt_journal.close()
    for client in {globals().get('mission_writer'),globals().get('draft_writer')} - {None,agent.actor}:
        await client._http.aclose()
    if hasattr(agent, "aclose"):
        await agent.aclose()


agent.navigation_text_reader=local_text if os.environ.get("APP_LOCAL_NAVIGATION_OCR")=="1" else None  # Experimental proposals only.
agent.query_text_reader=local_text if os.environ.get("APP_LOCAL_QUERY_OCR")=="1" else None  # Opt-in until actual OCR worker acceptance.

from .access import OwnerAccessMiddleware

app = FastAPI(lifespan=lifespan)
app.add_middleware(OwnerAccessMiddleware)
class Static(StaticFiles):
    """Static files that browsers re-check on every load, so a new version shows up on refresh."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


input_timelines = InputTimelines(DATA_DIR / 'traces')
device.diagnostics = input_timelines


@app.middleware('http')
async def input_timeline(request: Request, call_next):
    # Metadata only: no bodies, headers, response content or query values are logged.
    paths = {'/api/tap', '/api/motion-timing', '/api/keyboard-timing', '/api/long-press', '/api/swipe', '/api/scroll',
             '/api/type', '/api/key', '/api/home', '/api/back', '/api/recover-home',
             '/api/raw', '/api/calibrate/start', '/api/probe', '/api/device-health'}
    passive = (request.url.path == '/api/device-health'
               and request.query_params.get('timelines', '').lower() in ('1', 'true', 'on', 'yes', 't', 'y'))
    if request.url.path not in paths or passive:
        return await call_next(request)
    with input_timelines.capture(request.url.path) as row:
        timeline_event('received', method=request.method)
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            input_timelines.finish(row, status_code)


app.mount("/static", Static(directory=Path(__file__).parent / "static"), name="static")


class Point(BaseModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class Tap(Point):
    precise: bool = False  # find the pointer on screen and correct a miss before tapping (about 1 s slower)


class MotionTiming(BaseModel):
    milliseconds: int = Field(ge=20,le=100,strict=True)


class KeyboardTiming(BaseModel):
    milliseconds: int = Field(ge=40,le=100,strict=True)


class PointerProbe(BaseModel):
    dx: int = Field(ge=-200,le=200,strict=True)
    dy: int = Field(ge=-200,le=200,strict=True)


class Swipe(BaseModel):
    x1: float = Field(ge=0, le=1, allow_inf_nan=False)
    y1: float = Field(ge=0, le=1, allow_inf_nan=False)
    x2: float = Field(ge=0, le=1, allow_inf_nan=False)
    y2: float = Field(ge=0, le=1, allow_inf_nan=False)


class Scroll(Point):
    lines: int = Field(ge=-100, le=100)


class Text(BaseModel):
    text: str


class KeyboardText(Text):
    text: str = Field(min_length=1, max_length=4000)


def check_keyboard(text, ascii_only=False):
    """One line, no control characters. Non-ASCII text is fine for typing (it is pasted), never for keys."""
    if (ascii_only and not text.isascii()) or any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in text):
        raise HTTPException(422, "keys must be printable ASCII" if ascii_only else "text must be one printable line")


manual_lock = asyncio.Lock()
manual_task = None
manual_stop_tasks = set()

def app_tools_busy():
    tools=globals().get('app_tools')
    demos=getattr(globals().get('voice_bridge'),'demos',None)
    return bool((tools and tools.busy and not tools.owns_current())
                or (demos and demos.busy and not demos.owns_current()))


from .exploration_service import ExplorationService,router_for as exploration_router

def exploration_readiness():
    state=status()
    return {'device':state['device'] and state.get('controller',{}).get('stable',False),'screen':state['screen'],'calibrated':state['calibrated'],
            'binding':str(state['target'].get('device'))+':'+os.environ.get('PORT','8890')}

async def exploration_release():
    if not device.connected:raise RuntimeError('Device disconnected; release unconfirmed')
    await phone.release_input()

# Explicit opt-in only; no page load, idle heartbeat or ordinary agent task
# starts an exploration round. Existing runtime profiles remain disabled.
from .companion import CompanionMemory,Companion,router_for as companion_router
companion_memory=CompanionMemory(Path(os.environ.get('VOICE_MEMORY_DIR', DATA_DIR / 'companion')))
from .identity import configure_settings as configure_ai_name_settings
configure_ai_name_settings(companion_memory.path / "settings.json")  # owner-chosen AI name, next to memory
public_browser_driver=None
if os.environ.get('EXPLORATION_PUBLIC_BROWSER')=='1':
    if ENGINE not in ('hybrid','meta_hybrid') or local_text is None:
        raise RuntimeError('Public exploration requires cloud hybrid and local browser-chrome OCR')
    from .exploration_agent import AgentExplorationDriver
    from .public_browser import PublicBrowserDriver
    ipad_exploration_driver=AgentExplorationDriver(agent,memory=companion_memory)
    legacy_exploration_driver=PublicBrowserDriver(phone,screen,local_text,agent.client,agent.actor,agent.trace,memory=companion_memory)
    async def public_browser_driver(round,publish):
        driver=ipad_exploration_driver if round.device.startswith('ipad:') else legacy_exploration_driver
        await driver(round,publish)
exploration=ExplorationService(exploration_readiness,
    lambda:agent.running or manual_lock.locked() or app_tools_busy(),exploration_release,driver=public_browser_driver,on_complete=companion_memory.record_round)
app.include_router(exploration_router(exploration))


def check_manual():
    if agent.running or manual_lock.locked() or exploration.busy or app_tools_busy():
        raise HTTPException(409, "phone is busy; stop the agent or wait for the current action")


async def act(action):
    timeline_event('act_entered')
    current_device = device
    current_device.foreground_pending = getattr(current_device, 'foreground_pending', 0) + 1
    try:
        return await _act(action)
    finally:
        current_device.foreground_pending -= 1


async def _act(action):
    global manual_task
    check_manual()
    if hasattr(agent,"invalidate_foreground"):agent.invalidate_foreground()
    try:
        async with manual_lock:
            timeline_event('manual_lock_acquired')
            owner=asyncio.current_task()
            manual_task=owner
            def active():
                if owner in manual_stop_tasks or manual_task is not owner:raise asyncio.CancelledError()
            try:
                with leased_input(active):
                    await action()
                    active()
            except asyncio.CancelledError:
                if owner in manual_stop_tasks:
                    raise HTTPException(409, 'Manual action stopped; do not retry automatically') from None
                raise
            finally:
                manual_stop_tasks.discard(owner)
                if manual_task is owner:manual_task=None
    except (DeviceError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


STATIC_DIR = Path(__file__).parent / "static"
def ui_revision():
    return hashlib.sha256(b"".join((STATIC_DIR / name).read_bytes() for name in
                                  ("index.html", "app.js", "i18n.js", "style.css", "voice.js", "voice-worklet.js", "voice-intents.js", "exploration.js"))).hexdigest()[:12]


@app.get("/")
def index():
    html = (STATIC_DIR / "index.html").read_text()
    for name in ("style.css", "i18n.js", "app.js", "voice.js", "voice-intents.js", "exploration.js"):
        html = html.replace(f'/static/{name}"', f'/static/{name}?v={ui_revision()}"')
    return HTMLResponse(html, headers={"Cache-Control":"no-store"})


@app.get("/api/status")
def status():
    target = phone.target_info()
    target["live_frame"] = screen.live  # cached geometry is useful, but not a live connection
    return {"engine": ENGINE, "source_note_enabled": os.environ.get("SOURCE_NOTE_WORKFLOWS")=="1", "ui_revision": ui_revision(), "device": device.connected, "controller": device.readiness(), "screen": screen.live, "calibrated": phone.calibrated,
            "mirror_diagnostics": {**(screen.mirror_health() if hasattr(screen,"mirror_health") else {}),"frame_age_s":round(time.time()-screen.updated,3) if getattr(screen,'updated',None) else None,
                "receiver_stream_connected":getattr(screen,'_sock',None) is not None,
                "frame_size":[target.get('width'),target.get('height')],
                "automatic_restart_enabled":os.environ.get('MIRROR_AUTO_RESTART','1')=='1',
                "note":"Receiver stream connection is not proof of an updating device picture."},
            "debug": DEBUG, "trace": str(agent.trace.dir or ""), "aspect": round(target["aspect"] or screen.aspect, 4),
            "models": {"inventory_writer": getattr(getattr(agent,"inventory_writer",None),"model",None), "source_writer": getattr(agent,"source_writer",agent.planner).model, "planner": agent.planner.model, "actor": agent.actor.model, "judge": agent.client.model if ENGINE in ("hybrid", "meta_hybrid", "meta_local_hybrid", "haiku_demo") else None, "backup": getattr(getattr(agent,"client",None),"fallback_model",None)},
            "agent": {"running": agent.running, "task": agent.task, "steps": agent.steps,
                      "elapsed_s": round(max(0,(getattr(agent,"finished_at",None) or time.time())-agent.started_at),3) if getattr(agent,"started_at",None) is not None else None,
                      "content_ocr": bool(getattr(agent,"_source_ocr",False)), "query_text_ocr_enabled":getattr(agent,"query_text_reader",None) is not None,
                      "local_ocr_observed_mode":(getattr(local_text,"_cached",None) or {}).get("mode"),
                      "content_math": bool(getattr(agent,"_source_math",False)),
                      "content_author": agent.content_author_model if hasattr(agent,"content_author_model") else None},
            "control_activity": {"app_tool_running":app_tools_busy(),"manual_running":manual_lock.locked(),
                "voice_sessions":sum(item.get('expires',0)>time.monotonic() for item in list(getattr(globals().get('voice_bridge'), 'sessions', {}).values()))},
            "exploration": exploration.status(), "target": target, "airplay_name": os.environ.get("AIRPLAY_NAME", "iPhone Agent")}


class TargetChoice(BaseModel):
    mode: Literal["auto", "ipad", "iphone"]


@app.get("/api/device-health")
async def device_health(timelines: bool = False):
    """Read-only firmware counters; no mouse or keyboard input."""
    import time
    if timelines:
        # Passive branch: no firmware counters, ping, input or source inspection.
        return {'timelines': list(input_timelines.recent), 'write_errors': input_timelines.write_errors}
    if not device.connected:
        return {"network": False}
    try:
        if getattr(device, 'background_yielding', lambda: False)():
            timeline_event('health_yielded', outcome='not_enqueued')
            return {'network': device.connected, 'yielded': True}
        with command_source('health'):
            reply = await device.send("stats")
    except DeviceError as error:
        raise HTTPException(409, str(error)) from error
    counters = dict(part.split("=", 1) for part in (reply or "").split()[1:] if "=" in part)
    return {"network": device.connected, "counters": counters,
            "frame_age_s": round(time.time() - screen.updated, 2) if screen.updated else None,
            "action_timings": list(getattr(phone,"action_timings",())), "last_typing": getattr(phone,"last_typing_timing",None), "last_tap_timing":getattr(phone,"last_tap_timing",None), "last_pointer_check":getattr(phone,"last_pointer_check",None), "last_pointer_probe":getattr(phone,"last_pointer_probe",None), "transport": list(device.timings)}


@app.get("/api/performance")
def performance():
    """Current task's completed rounds; decision time includes actor retries and parsing."""
    rounds = agent.timings
    fields = ("decision_s", "action_s", "observe_s", "total_s")
    return {"running": agent.running, "rounds": rounds, "judge": getattr(getattr(agent,"client",None),"stats",None),
            "totals": {key: round(sum(row[key] for row in rounds), 4) for key in fields},
            "scope": "completed rounds, excluding initial planning and between-round error recovery"}


@app.post("/api/target")
async def select_target(choice: TargetChoice):
    check_manual()
    targets.select(choice.mode)
    phone._sync_profile()
    return {"ok": True, "target": phone.target_info(), "calibrated": phone.calibrated}


@app.get("/api/frame.jpg")
def frame():
    if not screen.live:
        raise HTTPException(404, "no live screen: start screen mirroring on the phone")
    return Response(screen.frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/screen-text")
async def screen_text():
    """Local OCR text+boxes; not a native accessibility tree or proof of focus."""
    import time
    if local_text is None:
        raise HTTPException(503, "Local text reader is not configured; use the screenshot")
    if not screen.live or not screen.frame:
        raise HTTPException(404, "No live mirrored frame")
    frame, updated = screen.frame, screen.updated
    try:
        result = await local_text.read(frame)
    except LocalTextError as exc:
        raise HTTPException(503, str(exc)) from exc
    result.update(frame_updated_at=updated, frame_age_s=round(time.time()-updated, 3))
    return result


@app.get("/api/steps/{index}.jpg")
def step_shot(index: int):
    if index not in agent.shots:
        raise HTTPException(404)
    return Response(agent.shots[index], media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/tap")
async def tap(p: Tap):
    target = phone.target_info() if hasattr(phone,"target_info") else {}
    precise = p.precise or (target or {}).get("device")=="ipad"
    return await act(lambda: phone.tap(p.x,p.y,aim=True) if precise else phone.tap(p.x,p.y))


@app.post("/api/motion-timing")
async def motion_timing(p: MotionTiming,request: Request):
    # The legacy bridge's documented tune command changes pacing until reboot.
    # Keep its known 10-unit step, never flash or send arbitrary protocol text.
    async def tune():
        # The active orientation may override the global default. Invalidate
        # its mapping against the cadence actually in use, not that default.
        pace=getattr(phone,'_pointer_ms',None)
        previous=pace() if callable(pace) else int(os.environ.get('HID_POINTER_MS','40'))
        from .phone import experimental_pointer_pace
        fast=p.milliseconds<40
        target=(phone.target_info() or {}) if hasattr(phone,'target_info') else {}
        binding=(target.get('calibration_key'),getattr(device,'links',0))
        if fast:
            from .live_voice import check_local
            check_local(request)
            if not experimental_pointer_pace(target,p.milliseconds):
                raise HTTPException(422,'Fast pointer cadence is an opt-in iPad portrait experiment')
            if not screen.live or not device.connected or not device.readiness()['stable'] or not phone.calibrated:
                raise HTTPException(409,'Fast pointer experiment requires live, stable and calibrated iPad')
        phone.forget_position()
        phone._pointer_tuned_link=None  # unknown tune delivery must force a fresh tune before any later motion
        await device.send(f"tune 10 {p.milliseconds}")
        if hasattr(phone,'target_info') and binding!=((phone.target_info() or {}).get('calibration_key'),getattr(device,'links',0)):
            raise HTTPException(409,'Device geometry or connection changed during tuning')
        if fast:
            overrides=json.loads(os.environ.get('HID_POINTER_PROFILE_MS','{}'))
            overrides[target['calibration_key']]=p.milliseconds
            os.environ['HID_POINTER_PROFILE_MS']=json.dumps(overrides)
        else:
            os.environ['HID_POINTER_MS']=str(p.milliseconds)
        if not fast and os.environ.get('HID_POINTER_PROFILE_MS'):
            overrides=json.loads(os.environ['HID_POINTER_PROFILE_MS'])
            key=(phone.target_info() or {}).get('calibration_key')
            if key in overrides:
                overrides[key]=p.milliseconds
                os.environ['HID_POINTER_PROFILE_MS']=json.dumps(overrides)
        phone._pointer_tuned_link=(getattr(device,'links',0),p.milliseconds)
        if previous!=p.milliseconds:
            phone.span=None
            phone._profile_key=None  # reload only a mapping measured at this cadence
    return await act(tune)


@app.post("/api/keyboard-timing")
async def keyboard_timing(p: KeyboardTiming,request: Request):
    from .live_voice import check_local
    check_local(request)
    async def tune():
        os.environ['HID_KEY_MS']=str(p.milliseconds)
        phone._typing_tuned_link=None
    await act(tune)
    return {'ok':True,'milliseconds':p.milliseconds,'effect':'applies before next ASCII input; no text sent'}


@app.post("/api/probe")
async def pointer_probe(p: PointerProbe):
    # Bounded relative movement only. No homing, clicking, keys or calibration
    # writes. This lets a human inspect real motion without a possibly bad map.
    if not screen.live or not device.connected:
        raise HTTPException(409,"Connect the device and live mirroring before pointer measurement")
    async def move():
        phone.forget_position()
        await phone._send("release")
        await phone._send(f"move {p.dx} {p.dy}")
    return await act(move)


@app.post("/api/long-press")
async def long_press(p: Point):
    return await act(lambda: phone.long_press(p.x,p.y))


@app.post("/api/swipe")
async def swipe(s: Swipe):
    return await act(lambda: phone.swipe(s.x1, s.y1, s.x2, s.y2))


@app.post("/api/scroll")
async def scroll(s: Scroll):
    return await act(lambda: phone.scroll(s.x, s.y, s.lines))


@app.post("/api/type")
async def type_text(t: KeyboardText):
    check_keyboard(t.text)
    return await act(lambda: phone.type(t.text))


@app.post("/api/key")
async def key(t: KeyboardText):
    check_keyboard(t.text, ascii_only=True)
    return await act(lambda: phone.key(t.text))


@app.post("/api/recover-home")
async def recover_home(request: Request):
    from .live_voice import check_local
    check_local(request)
    result=None
    async def recover():
        nonlocal result
        result=await phone.recover_home()
    await act(recover)
    return result


@app.post("/api/home")
async def home():
    return await act(phone.go_home)


@app.post("/api/back")
async def back():
    return await act(phone.go_back)


@app.post("/api/raw")
async def raw(t: Text):
    """Debug only: send one protocol line to the board as-is."""
    if not DEBUG:
        raise HTTPException(404)

    async def send():
        phone.forget_position()  # the server will not know where the pointer is, even if the board errs
        await device.send(t.text)
    return await act(send)


@app.post("/api/calibrate/start")
async def calibrate_start():
    """Calibrate the pointer. {"auto": true}: done by itself; otherwise the user clicks the pointer next."""
    result={}
    async def measure():
        result['auto']=await phone.start_calibration()
        result['span']=phone.span
    await act(measure)
    return {'ok':True,**result}


@app.post("/api/calibrate/finish")
async def calibrate_finish(p: Point):
    check_manual()
    if not 0 < p.x < 1 or not 0 < p.y < 1:
        raise HTTPException(422, "calibration pointer must be inside the screen")
    try:
        phone.finish_calibration(p.x, p.y)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "span": phone.span}


@app.post("/api/agent")
async def agent_start(t: Text):
    if camera_active_any():
        raise HTTPException(409, "camera_conversation_active")
    if exploration.busy or app_tools_busy():
        raise HTTPException(409, "exploration owns this device; stop it before another task")
    if manual_lock.locked():
        raise HTTPException(409, "phone is busy; wait for the current action")
    if not phone.calibrated:
        raise HTTPException(409, "calibrate the current device and orientation before starting")
    try:
        agent.start(t.text)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@app.post("/api/agent/stop")
async def agent_stop():
    """Emergency stop: cancel the agent and release the mouse button."""
    camera_end_all('emergency')  # Camera frames stop first, synchronously, before any await.
    agent.stop()  # Revoke ordinary HID stages before awaiting other controllers.
    demos=getattr(globals().get('voice_bridge'),'demos',None)
    if demos is not None:demos.stop()
    if globals().get('app_mission'):app_mission.revoke()
    if globals().get("app_tools"):await app_tools.stop()
    if globals().get('app_mission'):await app_mission.stop()
    await exploration.stop()
    pending=manual_task
    if pending is not None and pending is not asyncio.current_task() and not pending.done():
        manual_stop_tasks.add(pending)
        pending.cancel()
        await asyncio.wait({pending},timeout=.25)
    await asyncio.sleep(0.1)  # let a gesture in flight finish its own release
    # Emergency stop must stay available even while a direct action holds the lock.
    try:
        if not device.connected:
            return {"ok":True,"release_pending_reconnect":True}
        await phone.release_input()
    except (DeviceError, RuntimeError):
        # A cancelled in-flight command closes the old link; firmware releases
        # on reconnect. The agent is already stopped, so do not report failure.
        return {"ok": True, "release_pending_reconnect": True}
    return {"ok": True,"release_pending_reconnect":False}


@app.post("/api/agent/clear")
def agent_clear():
    """Forget the last run (steps, screenshots, plan) so the page starts clean."""
    try:
        if exploration.busy or app_tools_busy():
            raise HTTPException(409, "stop exploration before clearing task state")
        agent.clear()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


# Gemini Live voice delegates to the same calibrated ESP32 agent.
from .live_voice import VoiceBridge, router_for

async def voice_start(goal):
    return await agent_start(Text(text=goal))

voice_bridge = VoiceBridge(status, voice_start, agent_stop, exploration=exploration, memory=companion_memory,
                          executor_model=agent.actor)
voice_bridge.screen = screen
voice_bridge.agent = agent  # owner interjections (add_to_current_task) reach the running agent's next step
from .ipad_demo_flows import build_demos
voice_bridge.demos = build_demos(phone, screen, status, voice_bridge.task_memory, local_text)
from .camera_conversation import CameraConversation, LandscapeCameraDetector, enabled as camera_conversation_enabled
# Direct Camera app controls are available without a separate Live conversation.
voice_bridge.camera_detector = LandscapeCameraDetector(phone, screen)
# Optional legacy endpoint, never offered by normal voice.
# iPad landscape 1646x1080 only; templates come from CAMERA_TEMPLATE_DIR.
camera_conversation = (CameraConversation(screen, detector=LandscapeCameraDetector(phone, screen))
                       if camera_conversation_enabled() else None)
voice_bridge.camera = camera_conversation
app.include_router(router_for(voice_bridge))
from .app_tools import AppTools,router_for as app_tools_router
from .app_result_reader import AppResultReader
from .receipt_journal import ReceiptJournal
receipt_journal=ReceiptJournal(DATA_DIR/'task-receipts.sqlite3') if os.environ.get('APP_TOOL_RECEIPTS')=='1' else None
from .meta_model import meta_llm as hosted_meta_client
mission_writer=hosted_meta_client(engine='meta_hybrid',role='mission') if os.environ.get('META_MISSION_WRITER')=='1' else agent.actor
draft_writer=hosted_meta_client(engine='meta_hybrid',role='draft') if os.environ.get('META_DRAFT_WRITER')=='1' else agent.actor
app_tools=AppTools(voice_bridge,draft_writer,companion_memory,status,agent_stop,result_reader=AppResultReader(agent),journal=receipt_journal)
voice_bridge.app_tools=app_tools
app.include_router(app_tools_router(app_tools))
from .app_mission import AppMission,router_for as mission_router
app_mission=AppMission(app_tools,mission_writer,companion_memory,status,agent_stop) if os.environ.get('APP_MISSIONS')=='1' else None
app_tools.mission_enabled=app_mission is not None
app_tools.mission_model=mission_writer.model
voice_bridge.mission=app_mission
if app_mission:app.include_router(mission_router(app_mission,agent_stop))
companion=Companion(companion_memory,agent.actor,exploration,voice_bridge,agent_stop,app_tools=app_tools)
companion.mission=app_mission
app.include_router(companion_router(companion))

@app.get("/companion")
@app.get("/companion/")
def companion_page():
    return FileResponse(STATIC_DIR / "companion" / "index.html",headers={"Cache-Control":"no-store"})
