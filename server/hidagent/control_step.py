"""One explicit manual action plus observation, never a replaying action queue.
Client-side pairing reduces caller round trips; it does not verify task success.
"""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class ControlStep(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
    expected_device:Literal['ipad','iphone']
    calibration_key:str=Field(min_length=1,max_length=100)
    action:Literal['tap','swipe','scroll','key','type']
    arguments:dict
    observe_seconds:float=Field(default=.5,ge=0,le=3,allow_inf_nan=False,strict=True)

    @model_validator(mode='after')
    def bounded_arguments(self):
        keys={'tap':{'x','y','precise'},'swipe':{'x1','y1','x2','y2'},'scroll':{'x','y','lines'},'key':{'text'},'type':{'text'}}[self.action]
        required=keys-{'precise'}
        if not required<=self.arguments.keys() or not self.arguments.keys()<=keys:raise ValueError('Exact action arguments required')
        for key,value in self.arguments.items():
            if key=='text':
                if not isinstance(value,str) or not 1<=len(value)<=500 or any(ord(c)<32 or c in '\u007f\u2028\u2029' for c in value):raise ValueError('One printable line required')
                if self.action=='key' and not value.isascii():raise ValueError('ASCII key combination required')
            elif key=='precise':
                if type(value)is not bool:raise ValueError('precise must be boolean')
            elif key=='lines':
                if type(value)is not int or not -100<=value<=100:raise ValueError('Invalid scroll amount')
            elif type(value) not in (int,float) or not 0<=value<=1:raise ValueError('Coordinates must be finite screen fractions')
        return self

def _ready(state,request):
    target=state.get('target',{})
    if (target.get('device')!=request.expected_device or target.get('calibration_key')!=request.calibration_key
        or not all(state.get(k) for k in ('device','screen','calibrated'))
        or not state.get('controller',{}).get('stable')
        or type(state.get('controller',{}).get('link_generation')) is not int
        or state['controller']['link_generation']<1
        or state.get('agent',{}).get('running') or any(state.get('control_activity',{}).values())
        or state.get('exploration',{}).get('busy')):raise ValueError('Device binding/readiness/ownership mismatch; no action dispatched')
    return {k:target.get(k) for k in ('device','orientation','width','height','calibration_key')},state['controller'].get('link_generation')

def run_step(client,request,receipt_path,screenshot_path):
    """Reserve a local attempt BEFORE HTTP mutation. Reusing its path never sends.
    Deduplication is scoped to this receipt path, not a server-wide request ID.
    No upstream model, clipboard read, raw board command, calibration or batching.
    The receipt stores no dictated text; caller owns the input request file.
    """
    import httpx
    request=ControlStep.model_validate(request)
    receipt_path=Path(receipt_path);screenshot_path=Path(screenshot_path)
    if receipt_path.resolve()==screenshot_path.resolve():raise ValueError('Receipt and screenshot paths must differ')
    # JSON receipt reservation is atomic across separate CLI invocations.
    signature=hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    receipt_path.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(receipt_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        saved=json.loads(receipt_path.read_text())
        if saved.get('request_id')!=request.request_id or saved.get('signature')!=signature:raise ValueError('Existing attempt has a different request; no action dispatched')
        return {**saved,'historical':True,'automatic_replay':False,'instruction':'Existing local attempt. Inspect receipt/device; never repeat it automatically.'}
    result={'request_id':request.request_id,'signature':signature,'action':request.action,'status':'reserved','action_attempted':False,'never_retry_automatically':True,'automatic_replay':False,'task_success_verified':False}
    with os.fdopen(fd,'w') as f:json.dump(result,f);f.flush();os.fsync(f.fileno())
    def save():
        # Keep the reserved inode present even if a write is interrupted.
        with receipt_path.open('w') as f:json.dump(result,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
    started=time.monotonic()
    try:
        if screenshot_path.exists():raise ValueError('Screenshot output already exists; no action dispatched')
        binding,link=_ready(client.status(),request)
        mission=client.request('GET','/api/mission/status').json()
        if mission.get('running'):raise ValueError('A goal owns the console; no action dispatched')
        result.update(status='dispatching',action_attempted=True,device_binding=binding,link_generation=link);save()
        mutation=time.monotonic()
        try:
            result['receipt']=client.action(request.action,**request.arguments)
            result['status']='executed_unverified'
        except httpx.HTTPError as exc:
            # Even 409 can follow an in-flight move. Do not infer not_sent or retry.
            result.update(status='delivery_unverified',error_type=type(exc).__name__)
            if isinstance(exc,httpx.HTTPStatusError):result['http_status']=exc.response.status_code
        result['action_s']=round(time.monotonic()-mutation,4);save()
        time.sleep(request.observe_seconds)
        observed=time.monotonic()
        try:
            state=client.status();target=state.get('target',{})
            same=(binding=={k:target.get(k) for k in binding} and state.get('controller',{}).get('link_generation')==link)
            result['observation_binding_matches']=same
            busy=bool(state.get('agent',{}).get('running') or any(state.get('control_activity',{}).values()) or state.get('exploration',{}).get('busy'))
            if not same or not state.get('screen') or busy:result['observation']='unavailable_or_binding_changed_or_busy'
            else:
                frame=client.screenshot()
                from .frame_privacy import known_excluded_overlay
                excluded=known_excluded_overlay(frame)
                if excluded:result.update(observation='withheld_known_private_overlay',excluded_layout=excluded)
                else:
                    screenshot_path.parent.mkdir(parents=True,exist_ok=True);screenshot_path.write_bytes(frame)
                    result.update(observation='actual_frame_unverified',frame_sha256=hashlib.sha256(frame).hexdigest(),screenshot_path=str(screenshot_path.resolve()))
            if request.action=='tap':
                health=client.request('GET','/api/device-health').json();timing=health.get('last_tap_timing',{})
                if timing.get('target')==[request.arguments['x'],request.arguments['y']]:result['tap_timing_candidate']=timing
                # Same target is not a unique server action ID. Never claim exact attribution.
        except (httpx.HTTPError,OSError,ValueError):result['observation_error']='Could not obtain a bound observation; never repeat the action'
        result['observation_s']=round(time.monotonic()-observed,4)
    except (ValueError,httpx.HTTPError) as exc:
        result.update(status='preflight_failed' if not result['action_attempted'] else 'delivery_unverified',error_type=type(exc).__name__)
    finally:
        result['elapsed_s']=round(time.monotonic()-started,4);save()
    return result
