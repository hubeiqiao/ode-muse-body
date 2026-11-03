"""Opt-in exploration controller. No background browsing, models or device IO.

The adapter must supply a real input-release callback and independently verified
pixel evidence. This controller cannot establish visual truth by itself.
"""
import asyncio
from dataclasses import dataclass
import json
import ipaddress
import math
import re
import time
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4


class RoundRejected(RuntimeError):
    pass


class InputDeliveryUncertain(RuntimeError):
    """A device adapter reports possible partial input; never replay it."""


PUBLIC_SOURCE_APPS = frozenset({'X', 'YouTube', 'Reddit', 'Threads', 'Instagram', 'TikTok', 'rednote', 'Wikipedia'})


def source_reference(url, app, title='', attribution=''):
    """Source identity only, never navigation permission or an inferred permalink."""
    if any(not isinstance(v, str) or len(v)>240 or any(ord(c)<32 for c in v)
           for v in (app, title, attribution)):
        raise RoundRejected('A readable app and visible public title or author are required')
    if app == 'Safari':
        try:
            if not isinstance(url, str) or len(url)>1200 or '\\' in url or any(c.isspace() or ord(c)<32 for c in url):raise ValueError()
            parsed = urlsplit(url)
            host = parsed.hostname or ''
            if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}', host)
                    or host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid', '.example'))
                    or any(word in unquote(parsed.path).lower() for word in ('login', 'account', 'messages', 'checkout', 'settings', 'edit'))):raise ValueError()
            try:ipaddress.ip_address(host)
            except ValueError:pass
            else:raise ValueError()
        except (ValueError, TypeError):
            raise RoundRejected('Safari was visible, but its complete public HTTPS URL could not be verified') from None
        return url
    if app not in PUBLIC_SOURCE_APPS or not (title.strip() or attribution.strip()) or url:
        raise RoundRejected('A public app and visible title or author/handle are required; no permalink may be invented')
    return app+' · '+(attribution.strip() or title.strip())


def public_url(value):
    if (not isinstance(value,str) or len(value)>1200 or '\\' in value
            or any(ord(c)<32 or ord(c)==127 for c in value)):
        raise RoundRejected('Invalid public URL')
    try:
        parsed=urlsplit(value)
        if (parsed.scheme!='https' or parsed.hostname not in
            {'en.wikipedia.org','www.nasa.gov','science.nasa.gov','apod.nasa.gov'}
            or parsed.username or parsed.password or parsed.port not in (None,443)
            or parsed.fragment or any(c.isspace() for c in value)):
            raise ValueError()
        path=unquote(parsed.path).lower()
        if '%' in path:raise ValueError()  # Reject nested escaping, not decode-and-guess.
        if any(word in path for word in ('login','logout','account','subscribe','donate','checkout','edit','preferences')):
            raise ValueError()
        if parsed.hostname=='en.wikipedia.org':
            if not path.startswith('/wiki/') or not path.removeprefix('/wiki/'):
                raise ValueError()
            article=path.removeprefix('/wiki/')
            if ':' in article and article!='special:search':raise ValueError()
            if parsed.query:
                query=parse_qs(parsed.query,strict_parsing=True)
                if article!='special:search' or set(query)!={'search'} or len(query['search'])!=1:
                    raise ValueError()
        elif parsed.query:
            raise ValueError()
    except (ValueError,TypeError):
        raise RoundRejected('Only allowlisted public reading/search URLs are permitted') from None
    return value


@dataclass(frozen=True)
class ActionTicket:
    round_id: str
    device: str
    ticket_id: str
    kind: str
    payload_json: str

    @property
    def payload(self):
        return json.loads(self.payload_json)


class ExplorationRound:
    """One human-started round; a stopped/expired round has no resume method."""
    def __init__(self,device,goal,release,*,seconds=90,max_actions=8):
        if device not in ('iphone:8894','ipad:8890'):
            raise RoundRejected('Explicit isolated device binding required')
        if not isinstance(goal,str) or not 1<=len(goal.strip())<=1000:
            raise RoundRejected('A human goal is required')
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=90:
            raise RoundRejected('Round deadline must be within 90 seconds')
        if type(max_actions) is not int or not 1<=max_actions<=12 or not callable(release):
            raise RoundRejected('Bounded action count and release callback required')
        self.id,self.device,self.goal=uuid4().hex,device,goal
        self.seconds,self.max_actions,self.release=seconds,max_actions,release
        self.state='idle';self.finished_at=None;self.action_count=0;self.read_count=0
        self.observation_count=0;self.max_observations=30
        self._pending=self._inflight=self._watchdog=self._active_ticket=None
        self._evidence=[];self.release_pending=False

    @property
    def evidence(self):
        return tuple(dict(item) for item in self._evidence)

    async def start(self):
        if self.state!='idle':raise RoundRejected('A fresh human-started round is required')
        self.started=time.monotonic();self.deadline=self.started+self.seconds
        self.state='running';self._watchdog=asyncio.create_task(self._expire())
        return self

    async def _expire(self):
        await asyncio.sleep(max(0,self.deadline-time.monotonic()))
        await self._halt('expired')

    def _active(self):
        if self.state!='running' or time.monotonic()>=self.deadline:
            raise RoundRejected('Round is inactive; no old plan may continue')

    async def _halt(self,state):
        if self.state!='running':return
        self.state=state;self._pending=self._active_ticket=None
        tasks=[t for t in (self._inflight,self._watchdog) if t and t is not asyncio.current_task() and not t.done()]
        for task in tasks:task.cancel()
        if tasks:
            _,pending=await asyncio.wait(tasks,timeout=.25)
            self.release_pending=bool(pending)
        try:
            await asyncio.wait_for(self.release(),timeout=.5)
        except (Exception,asyncio.CancelledError):
            self.release_pending=True
        finally:
            self.finished_at=time.monotonic()

    async def stop(self):
        await self._halt('stopped')

    async def grant(self,kind,payload=None):
        self._active();payload={} if payload is None else payload
        if type(payload) is not dict:raise RoundRejected('Action payload must be an object')
        if self._pending is not None or self._inflight is not None:
            raise RoundRejected('Only one outstanding action is permitted')
        if kind=='navigate' and set(payload)=={'url'}:
            public_url(payload['url'])
        elif kind=='scroll' and set(payload)=={'direction'} and payload['direction'] in ('up','down'):
            pass  # Exactly one gesture; no continuous mode.
        elif kind=='observe' and payload=={}:
            pass
        elif kind=='browse' and set(payload)=={'query'} and isinstance(payload['query'],str) and re.fullmatch(r'[A-Za-z][A-Za-z-]{2,11}',payload['query']):
            pass  # Internal adapter: one bounded, independently judged public-search agent run.
        else:raise RoundRejected('Only public navigation, one scroll or observation is permitted')
        if (kind=='observe' and self.read_count>=9) or (kind!='observe' and self.action_count>=self.max_actions):
            await self._halt('action_limit')
            raise RoundRejected('Round action/read limit reached')
        if kind=='observe':self.read_count+=1
        elif kind!='browse':self.action_count+=1
        ticket=ActionTicket(self.id,self.device,uuid4().hex,kind,json.dumps(payload,sort_keys=True))
        self._pending=ticket
        return ticket

    def consume_browse_input(self):
        """Charge actual executor inputs, never the browse container or waits."""
        self._active()
        if not self._active_ticket or self._active_ticket.kind != 'browse':
            raise RoundRejected('A current browse lease is required for input')
        if self.action_count >= self.max_actions:
            raise RoundRejected('Exploration navigation limit reached')
        self.action_count += 1  # Consume before delivery; uncertain input is never replayed.

    async def execute(self,ticket,operation):
        self._active()
        if ticket!=self._pending or not isinstance(ticket,ActionTicket):
            raise RoundRejected('Stale, altered or already consumed action ticket')
        self._pending=None
        self._active_ticket=ticket
        try:
            self._inflight=asyncio.create_task(operation(ticket.kind,ticket.payload))
            return await self._inflight
        except InputDeliveryUncertain:
            await self._halt('input_uncertain');raise
        except asyncio.CancelledError:
            await self._halt('stopped');raise
        except Exception:
            await self._halt('failed');raise
        finally:self._inflight=self._active_ticket=None

    def check_action(self,ticket):
        """The future device adapter must call this before EVERY HID chunk."""
        self._active()
        if ticket!=self._active_ticket:
            raise RoundRejected('Action lease revoked or belongs to another round/device')

    def add_verified_evidence(self,*,round_id,device,url,frame_sha256,quote,claim,confidence,verifier,observed_at,source_app=None,source_title='',source_attribution=''):
        """Internal adapter input, NOT a model-callable authorization interface."""
        self._active()
        reference = source_reference(url,source_app,source_title,source_attribution) if source_app is not None else public_url(url)
        if (round_id!=self.id or device!=self.device
            or not isinstance(frame_sha256,str) or not re.fullmatch('[0-9a-f]{64}',frame_sha256)
            or type(confidence) not in (int,float) or not .99<=confidence<=1
            or verifier not in ('jev-latest','jev-1.14.0','claude-haiku-5-5','muse-spark-1.3')
            or type(observed_at) not in (int,float) or not self.started<=observed_at<=time.monotonic()
            or not isinstance(quote,str) or not 1<=len(quote)<=2000
            or not isinstance(claim,str) or not 1<=len(claim)<=600):
            raise RoundRejected('Fresh bound source evidence and independent verification required')
        item={'evidence_id':uuid4().hex,'url':url,'frame_sha256':frame_sha256,'quote':quote,
              'claim':claim,'verifier':verifier,'observed_at':observed_at}
        if source_app is not None:
            item.update(source=reference,source_app=source_app,source_title=source_title,source_attribution=source_attribution)
        self._evidence.append(item);return dict(item)

    async def finish(self):
        self._active()
        if not self.evidence or self._pending is not None or self._inflight is not None:
            raise RoundRejected('A discovery needs verified source evidence and no pending actions')
        await self._halt('completed')
        return {'state':self.state,'release_pending':self.release_pending,'evidence':[dict(e) for e in self.evidence]}
