"""Explicit exploration API. Disabled until a real browser driver is supplied."""
import asyncio
import copy
import hashlib
import json
import re
import time
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from .exploration_round import ExplorationRound,RoundRejected,public_url
from .device import DeviceError
from .live_voice import check_local


class StartRound(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
    goal:str=Field(min_length=1,max_length=1000)


class StopRound(BaseModel):
    model_config=ConfigDict(extra='forbid')
    round_id:str=Field(pattern=r'^[0-9a-f]{32}$')


class ExplorationService:
    def __init__(self,readiness,other_busy,release,driver=None,*,seconds=90,max_actions=12,on_complete=None):
        self.readiness,self.other_busy,self.release,self.driver=readiness,other_busy,release,driver
        self.seconds,self.max_actions=seconds,max_actions
        self.on_complete=on_complete
        self._current=None;self._requests={};self._lock=asyncio.Lock()

    @property
    def busy(self):
        return bool(self._current and self._record_busy(self._current))

    def _record_busy(self,record):
        return record['round'].state=='running' or any(record.get(k) is not None and not record[k].done()
            for k in ('job','driver_job'))

    def _snapshot(self,record):
        r=record['round']
        return {'available':self.driver is not None,'round_id':r.id,'request_id':record['request_id'],
                'device':r.device,'state':r.state,'busy':self._record_busy(record),
                'goal':r.goal,'actions_used':r.action_count,'reads_used':r.read_count,'observations_used':r.observation_count,
                'elapsed_s':round((r.finished_at or time.monotonic())-r.started,3),'release_pending':r.release_pending,
                'memory_error':record.get('memory_error'),'reason':record.get('reason'),'proposal':copy.deepcopy(record['proposal']),'evidence':list(r.evidence),'error':record['error']}

    def status(self):
        if self._current:return self._snapshot(self._current)
        return {'available':self.driver is not None,'state':'idle','busy':False,
                'reason':None if self.driver else 'public_browser_adapter_not_configured'}

    async def start(self,request_id,goal):
        from .camera_conversation import active_any as camera_active_any
        if camera_active_any():raise HTTPException(409,'camera_conversation_active')
        if (not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',request_id)
            or not isinstance(goal,str) or not 1<=len(goal.strip())<=1000):
            raise HTTPException(422,'A bounded explicit goal and unique request_id are required')
        fingerprint=hashlib.sha256(json.dumps(goal,ensure_ascii=True).encode()).hexdigest()
        async with self._lock:
            old=self._requests.get(request_id)
            if old:
                if old['fingerprint']!=fingerprint:raise HTTPException(409,'Conflicting exploration request_id')
                return {**self._snapshot(old),'deduplicated':True}
            if self.driver is None:raise HTTPException(503,'Public browser adapter is not configured; no exploration was started')
            if self.busy or self.other_busy():raise HTTPException(409,'Device is busy; do not resubmit automatically')
            if len(self._requests)>=32:raise HTTPException(429,'Exploration history is full; no automatic replay or eviction')
            ready=self.readiness()
            if not all(ready.get(k) for k in ('device','screen','calibrated')):
                raise HTTPException(409,'Live device, screen and current calibration are required')
            try:
                r=await ExplorationRound(ready.get('binding'),goal,self.release,
                    seconds=self.seconds,max_actions=self.max_actions).start()
            except RoundRejected as exc:raise HTTPException(422,str(exc)) from None
            record={'round':r,'request_id':request_id,'fingerprint':fingerprint,'job':None,'driver_job':None,'proposal':None,'error':None}
            self._requests[request_id]=record;self._current=record
            record['job']=asyncio.create_task(self._drive(record))
            return self._snapshot(record)

    def publish_proposal(self,round_id,proposal):
        record=self._current
        if not record or record['round'].id!=round_id:raise RoundRejected('Proposal belongs to another round')
        record['round']._active()
        if (not isinstance(proposal,dict) or proposal.get('kind')!='unverified_exploration_proposal'
            or proposal.get('evidence')!=[]):raise RoundRejected('Only a clearly unverified proposal can be published')
        public_url(proposal.get('public_search_url'))
        if any(not isinstance(proposal.get(k),str) or not 1<=len(proposal[k])<=240 for k in ('topic','query','reason','author_model')):
            raise RoundRejected('Invalid proposal metadata')
        record['proposal']={k:proposal[k] for k in ('kind','topic','query','reason','author_model','public_search_url')}

    async def _drive(self,record):
        r=record['round']
        try:
            record['driver_job']=asyncio.create_task(self.driver(r,lambda p:self.publish_proposal(r.id,p)))
            record['driver_job'].add_done_callback(lambda task:None if task.cancelled() else task.exception())
            done,_=await asyncio.wait({record['driver_job']},timeout=max(0,r.deadline-time.monotonic()))
            if not done:
                await r._halt('expired');await self._cancel_driver(record);return
            await record['driver_job']
            if r.state=='running':await r.finish()
            if r.state=='completed' and self.on_complete:
                try:self.on_complete(r)
                except Exception:record['memory_error']='discovery_not_saved'
        except asyncio.CancelledError:
            await r.stop();await self._cancel_driver(record)
        except Exception as exc:
            record['reason']=(str(exc)[:240] if isinstance(exc,RoundRejected) else
                'ESP32 connection lost. Reconnect the board before a new request; no automatic replay.' if isinstance(exc,DeviceError) else None)
            record['error']=type(exc).__name__  # No raw provider exceptions, tokens or URLs.
            await r._halt('failed')

    async def _cancel_driver(self,record):
        job=record['driver_job']
        if job and not job.done():
            job.cancel();_,pending=await asyncio.wait({job},timeout=.25)
            if pending:record['round'].release_pending=True

    async def stop(self,round_id=None):
        record=self._current
        if not record:
            if round_id:raise HTTPException(404,'Exploration round not found')
            return self.status()
        if round_id and round_id!=record['round'].id:
            for old in self._requests.values():
                if old['round'].id==round_id:return self._snapshot(old)
            raise HTTPException(409,'Stop belongs to another round; current task was not interrupted')
        await record['round'].stop()
        job=record['job']
        if job and not job.done():
            job.cancel();_,pending=await asyncio.wait({job},timeout=.25)
            if pending:record['round'].release_pending=True
        return self._snapshot(record)


def router_for(service):
    router=APIRouter(prefix='/api/exploration')
    @router.get('/status')
    def status(request:Request):
        check_local(request);return service.status()
    @router.post('/start')
    async def start(body:StartRound,request:Request):
        check_local(request);return await service.start(body.request_id,body.goal)
    @router.post('/stop')
    async def stop(body:StopRound,request:Request):
        check_local(request);return await service.stop(body.round_id)
    return router
