"""Explicit opt-in Meta model client; never reuses another provider's key."""
from contextvars import ContextVar
import os
import base64
import json
import logging
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit
from .llm import LLM

log = logging.getLogger(__name__)


def _error_excerpt(response, payload):
    """Bounded diagnostic body, excluding echoed requests, credentials and images."""
    try:
        body = response.json()
        error = body.get('error', body) if isinstance(body, dict) else {}
        if isinstance(error, dict):
            body = {k: error[k] for k in ('message', 'type', 'code', 'param')
                    if isinstance(error.get(k), (str, int))}
        else:
            body = {'message': error} if isinstance(error, str) else {}
        text = json.dumps(body, ensure_ascii=True)
    except ValueError:
        text = response.text
    # Providers can echo credentials or input into an error message. Strip the
    # actual credential and all supplied prompt/image strings before truncating.
    authorization = response.request.headers.get('authorization', '')
    for value in (authorization, authorization.removeprefix('Bearer ')):
        if value:
            text = text.replace(value, '[redacted]')
    for message in payload.get('messages', []):
        content = message.get('content', '')
        parts = [{'text': content}] if isinstance(content, str) else content
        for part in parts:
            value = part.get('text') or part.get('image_url', {}).get('url')
            if value:
                text = text.replace(value, '[request omitted]').replace(json.dumps(value)[1:-1], '[request omitted]')
    text = re.sub(r'(?i)\bBearer\s+[^\s"\\]+|\bsk-[\w-]+|data:image/[^\s"\\]+', '[redacted]', text)
    text = re.sub(r'(?i)(api[_-]?key|token|password|secret)([\s"\x27:=\\]+)[^\s",}\\]+', r'\1=[redacted]', text)
    return json.dumps(text[:600], ensure_ascii=True) + (' [truncated]' if len(text) > 600 else '')


class LocalMetaLLM(LLM):
    """Use Ollama's explicit thinking switch and constrained JSON locally."""
    supports_native_json = True

    def __init__(self,base,model):
        super().__init__(base,model,'',token_param='max_tokens',trust_env=False)
        self.native_url=base.rstrip('/')[:-3]+'/api/chat'

    async def chat(self,system,text,image=None,max_tokens=600,temperature=0,json_mode=False,json_schema=None):
        from .frame_privacy import check_camera_frame
        check_camera_frame(image)  # Meta never receives camera frames; inert without a camera session.
        content={'role':'user','content':text}
        if image:content['images']=[base64.b64encode(image).decode()]
        body={'model':self.model,'stream':False,'think':False,'keep_alive':'10m',
              'options':{'num_predict':max_tokens,'num_ctx':4096,'temperature':temperature,'top_p':.95,'top_k':64},
              'messages':[{'role':'system','content':system},content]}
        if json_schema is not None:body['format']=json_schema
        elif json_mode:body['format']='json'
        response=await self._http.post(self.native_url,json=body)
        response.raise_for_status()
        data=response.json()
        # Numeric runtime measurements only; never persist requests or keys.
        self.last_metrics={k:data[k] for k in ('total_duration','load_duration',
            'prompt_eval_count','prompt_eval_duration','eval_count','eval_duration')
            if isinstance(data.get(k),(int,float)) and not isinstance(data.get(k),bool)}
        if data.get('error'):raise RuntimeError('Local Meta model failed; no provider was substituted')
        content=data.get('message',{}).get('content','').strip()
        # This runtime emits its own end-of-turn marker after valid JSON.
        # Remove only an exact terminal transport marker, never interior text.
        if content.endswith('<|eot|>'):content=content[:-7].rstrip()
        return content


class MetaCloudLLM(LLM):
    """Explicit hosted reasoning profile; caller budgets include reasoning tokens."""
    supports_native_json = True
    def __init__(self,model,key,effort='minimal',minimum_tokens=2048):
        if model not in ('muse-spark-1.3','muse-wonder-1.0') or effort not in ('minimal','low','medium','high','xhigh','max'):
            raise RuntimeError('Unsupported hosted Meta profile; no provider substituted')
        super().__init__('https://api.meta.ai/v1',model,key,extra={'reasoning_effort':effort},token_param='max_completion_tokens',trust_env=False)
        self.minimum_tokens=minimum_tokens;self.effort=effort;self.provider='meta'
        self._request_effort=ContextVar('meta_request_effort',default=None)
    async def chat(self,system,text,image=None,max_tokens=600,temperature=0,enforce_token_budget=False,response_format=None,json_schema=None,reasoning_effort=None):
        if json_schema is not None:
            if response_format is not None:raise ValueError('Choose exactly one Meta structured response contract')
            response_format={'type':'json_schema','json_schema':{'name':'PublicVisualCheck','strict':True,'schema':json_schema}}
        if reasoning_effort is not None and reasoning_effort not in ('minimal','low','medium','high','xhigh','max'):
            raise ValueError('Unsupported request-local Meta reasoning effort')
        token=self._request_effort.set(reasoning_effort)
        try:return await super().chat(system,text,image,max_tokens=max(max_tokens,self.minimum_tokens),temperature=temperature,enforce_token_budget=True,response_format=response_format)
        finally:self._request_effort.reset(token)
    async def _post(self,payload):
        effort=self._request_effort.get()
        payload={**payload,'reasoning_effort':effort} if effort is not None else payload
        response=await super()._post(payload)
        if 400 <= response.status_code < 500 and response.request.url.host == 'api.meta.ai':
            log.warning('Meta API 4xx status=%s body=%s', response.status_code, _error_excerpt(response, payload))
        return response


def _cloud_credentials():
    path=os.environ.get('META_CREDENTIALS_FILE')
    if not path:return os.environ.get('META_API_KEY') or os.environ.get('MODEL_API_KEY'),os.environ.get('META_MODEL','muse-spark-1.3')
    # This is an explicit local secret destination, never a shell source or API input.
    try:
        values={}
        for line in Path(path).read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):continue
            key,sep,value=line.partition('=')
            if not sep or key.strip() not in ('META_API_KEY','META_MODEL'):raise ValueError()
            parts=shlex.split(value)
            if len(parts)!=1:raise ValueError()
            values[key.strip()]=parts[0]
        if not values.get('META_API_KEY'):raise ValueError()
        return values['META_API_KEY'],values.get('META_MODEL','muse-spark-1.3')
    except (OSError,ValueError):raise RuntimeError('Dedicated Meta credential file is missing or incomplete; no other provider key was used') from None


def meta_llm(engine=None,local_model=None,role=None):
    if (engine or os.environ.get('AGENT_ENGINE')) == 'meta_local_hybrid':
        base = os.environ.get('META_LOCAL_BASE_URL', 'http://127.0.0.1:11434/v1')
        url = urlsplit(base)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1') or url.path.rstrip('/') != '/v1'
                or url.username or url.password or url.query or url.fragment):
            raise RuntimeError('Meta local model endpoint must be a numeric loopback HTTP /v1 URL')
        model = local_model or os.environ.get('META_LOCAL_MODEL', 'muse-glimmer:30b')
        if ':cloud' in model:
            raise RuntimeError('Local Meta mode does not permit cloud model names')
        return LocalMetaLLM(base,model)
    if local_model is not None:raise RuntimeError('Local model override requires local Meta mode')
    key,model=_cloud_credentials()
    if not key:
        raise RuntimeError('meta_hybrid requires META_API_KEY or MODEL_API_KEY; no provider was substituted')
    if role not in (None,'mission','draft','reasoner'):raise RuntimeError('Unknown hosted Meta role')
    if role:
        model=os.environ.get('META_'+role.upper()+'_MODEL','muse-wonder-1.0' if role=='reasoner' else model)
    effort=os.environ.get('META_'+(role.upper()+'_' if role else '')+'EFFORT','low' if role=='reasoner' else 'minimal')
    return MetaCloudLLM(model,key,effort,4096 if model=='muse-wonder-1.0' else 2048)
