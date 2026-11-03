"""One real phone task; record wall time, actual frames and model receipt separately."""
import argparse,json,time,urllib.request
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('name');p.add_argument('goal');p.add_argument('--timeout',type=float,default=90)
p.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[2]/'runtime/iphone/evaluations')
a=p.parse_args()
out=a.out
out.mkdir(exist_ok=True)
base='http://127.0.0.1:8894/api/'
def req(path,body=None):
 request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=20) as r:return r.read()
status=json.loads(req('status'))
assert status['device'] and status['screen'] and status['calibrated'] and not status['agent']['running']
assert status.get('target',{}).get('device')=='iphone', 'Evaluation is scoped to iPhone only'
(out/(a.name+'-before.jpg')).write_bytes(req('frame.jpg'))
started=time.perf_counter();req('agent',{'text':a.goal})
deadline=started+a.timeout
while True:
 state=json.loads(req('status'))
 if not state['agent']['running']:break
 if time.perf_counter()>deadline:
  req('agent/stop',{});state=json.loads(req('status'));break
 time.sleep(.15)
elapsed=time.perf_counter()-started
(out/(a.name+'-after.jpg')).write_bytes(req('frame.jpg'))
result={'name':a.name,'goal':a.goal,'wall_s':round(elapsed,4),
 'model_reported_done':bool(state['agent']['steps'] and state['agent']['steps'][-1]['action']=='done'),
 'independent_verification':'pending','task':state['agent'],'performance':json.loads(req('performance')),
 'health':json.loads(req('device-health'))}
(out/(a.name+'.json')).write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('task','health','performance')}),flush=True)
print(json.dumps({'last_steps':state['agent']['steps'][-2:],'totals':result['performance']['totals']}),flush=True)
