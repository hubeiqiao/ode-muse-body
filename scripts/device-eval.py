#!/usr/bin/env python3
"""Explicit real-device acceptance harness. Default is read-only preflight."""
import argparse, json, time, hashlib
from datetime import datetime, timezone
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('device',choices=['ipad','iphone'])
parser.add_argument('--run',action='store_true',help='Run bounded navigation on the selected real device')
parser.add_argument('--output',type=Path,required=True)
a=parser.parse_args();a.output.mkdir(parents=True,exist_ok=True)
port={'ipad':8890,'iphone':8894}[a.device]
report={'device':a.device,'console_port':port,'at':datetime.now(timezone.utc).isoformat(),
        'scope':'Real API tasks, not microphone/ASR latency. Completion is model-verified; inspect saved pixels.',
        'mode':'real_device' if a.run else 'read_only_preflight','trials':[]}
with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=15,trust_env=False) as c:
    try:
        state=c.get('/api/status').raise_for_status().json()
        report['readiness']={k:state.get(k) for k in ['device','screen','calibrated','target','engine','airplay_name']}
        target=(state.get('target') or {}).get('device')
        ready=all(state.get(k) for k in ['device','screen','calibrated']) and target==a.device
        report['ready']=ready
        if a.run and (not ready or state['agent']['running']):
            report['result']='blocked_not_ready_or_busy'
        elif a.run:
            for i,goal in enumerate(['Open Calculator','Open Settings','Open Safari','scroll down the feed once']):
                # A bare scroll in Safari can be unsuitable. Skip instead of guessing a feed.
                if goal.startswith('scroll'):
                    report['trials'].append({'goal':goal,'result':'not_run','reason':'Requires a user-selected scrollable fixture, not arbitrary Safari content.'});continue
                c.post('/api/home').raise_for_status();time.sleep(.5)
                t=time.monotonic();c.post('/api/agent',json={'text':goal}).raise_for_status()
                while True:
                    state=c.get('/api/status').raise_for_status().json()
                    if state['agent']['task'] != goal:
                        raise ValueError('Another task replaced evaluation; no stop or further input sent')
                    if not state['agent']['running']:break
                    if time.monotonic()-t>40:
                        c.post('/api/agent/stop').raise_for_status();break
                    time.sleep(.15)
                job=c.get('/api/status').raise_for_status().json()['agent']
                jpg=c.get('/api/frame.jpg').raise_for_status().content
                name=f'{i+1:02d}.jpg';(a.output/name).write_bytes(jpg)
                trial={'goal':goal,'elapsed_s':round(time.monotonic()-t,3),
                       'result':job['steps'][-1]['action'] if job['steps'] else 'unknown',
                       'frame':name,'sha256':hashlib.sha256(jpg).hexdigest(),'steps':job['steps']}
                report['trials'].append(trial)
                if trial['result']!='done':break
            report['result']='finished_bounded_navigation'
        else:report['result']='preflight_only'
    except (httpx.HTTPError,ValueError) as e:
        report.update(ready=False,result='service_unavailable',error=type(e).__name__)
(a.output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:report[k] for k in ['device','ready','result']},ensure_ascii=False))
