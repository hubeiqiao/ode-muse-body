#!/usr/bin/env python3
"""Offline OCR/template replay. Never read trace runtime, credentials or APIs."""
import argparse
import asyncio
from collections import Counter, defaultdict
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
from hidagent import app_query_text as current
from hidagent import app_controls
from hidagent.local_text import LocalTextReader

HERE = Path(__file__).resolve().parent
LITERAL = {'verify query before search submission', 'verify actual query with current-run input continuity'}
RESULTS = 'verify public search results'
LOCATE = {'locate public search entry', 'locate editable app search'}
TARGET = {'device': 'ipad', 'orientation': 'landscape'}


class RecordedReader:
    def __init__(self, reader):
        self.reader, self.attempts = reader, []

    async def read(self, frame, *, mode):
        try:
            value = await self.reader.read(frame, mode=mode)
            self.attempts.append({'size': list(Image.open(io.BytesIO(frame)).size), **value})
            return value
        except Exception as exc:
            self.attempts.append({'error': type(exc).__name__ + ': ' + str(exc)})
            raise


def baseline():
    source = subprocess.check_output(['git', 'show', '27d2f340e8161532d0ea735371d8673fb69f8c91:server/hidagent/app_query_text.py'], cwd=ROOT, text=True)
    module = types.ModuleType('hidagent._query_baseline')
    module.__package__ = 'hidagent'
    exec(compile(source, '<baseline-app-query-text>', 'exec'), module.__dict__)
    return module


def miss_reason(attempts, query, hit):
    if hit:
        return '命中'
    if not attempts or 'error' in attempts[-1]:
        return '原生 OCR 错误或未读取'
    rows = attempts[-1]['rows']
    if not rows:
        return '未识别查询文字'
    if len(rows) != 1:
        return '查询区域存在多行或额外文字'
    if not current._landscape_query_matches(rows[0]['text'], query):
        if rows[0]['text'].startswith('Q ') and current._landscape_query_matches(rows[0]['text'][2:], query):
            return '放大镜被识别成查询前的 Q'
        return '完整文字不符，保留缺字或残留的拒绝'
    return '文字框越界或贴近裁剪边界'


async def observe_pair(old, reader, frame, goal):
    outputs = {}
    for name, module in [('baseline', old), ('local', current)]:
        recorded = RecordedReader(reader)
        agent = types.SimpleNamespace(phone=types.SimpleNamespace(target_info=lambda: TARGET), query_text_reader=recorded)
        hit = await module.observe_landscape_query(agent, goal, frame)
        outputs[name] = {'hit': bool(hit), 'attempts': recorded.attempts,
                         'reason': miss_reason(recorded.attempts, goal['query'], hit)}
    recorded = RecordedReader(reader)
    agent = types.SimpleNamespace(phone=types.SimpleNamespace(target_info=lambda: TARGET), query_text_reader=recorded)
    label_hit = await current.observe_landscape_query(agent, goal, frame, results=True)
    anchors = app_controls.landscape_results_control(frame, goal['app'], TARGET)
    loaded = await current._landscape_loaded_results(agent, goal['app'], frame) if anchors else False
    outputs['results'] = {'anchors': anchors, 'loaded_body': loaded,
                          'hit': bool(anchors and loaded and label_hit),
                          'body_attempts': recorded.attempts}
    return outputs


async def run(args):
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines()]
    os.environ['LANDSCAPE_QUERY_OCR'] = '1'
    old = baseline()
    reader = LocalTextReader([args.binary, '--serve'], timeout=30)
    records, supplemental = [], []
    try:
        for row in rows:
            if row['purpose'] not in LITERAL | {RESULTS}:
                continue
            # Resolve ONLY under explicitly supplied safe copies. Original
            # runtime paths in dataset.jsonl are provenance, never read targets.
            path = args.frames / (row['frame_sha256'] + '.jpg')
            record = {'id': row['id'], 'run': row['run'], 'purpose': row['purpose'],
                      'sha256': row['frame_sha256'], 'goal': {k: row['state'].get(k) for k in ('app', 'query')}}
            if not path.is_file():
                record.update(available=False, reason='安全副本缺失，未读取正式 runtime')
            else:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != row['frame_sha256']:
                    raise ValueError('Frame SHA mismatch')
                record.update(available=True, **await observe_pair(old, reader, data, record['goal']))
            records.append(record)
        goals = json.loads((HERE/'data/saved-goals.json').read_text())
        for path in sorted((ROOT/'server/tests/local_evidence_frames').glob('*.jpg')):
            g = next(g for g in goals if path.name.startswith(g['label']))
            goal = {k:g[k] for k in ('app','query')}
            supplemental.append({'frame':path.name,'goal':goal,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                 **await observe_pair(old,reader,path.read_bytes(),goal)})
    finally:
        await reader.close()
    # Cache requires full state + questions + SHA, not a loose purpose/SHA key.
    # Literal OCR still has a fresh owner/focus model gate. Its hit by itself
    # avoids ZERO confirmed Muse calls. Never count it as an entire judge skip.
    by_id = {r['id']:r for r in records}
    traces, seen = {}, set()
    for row in rows:
        t = traces.setdefault(row['run'], dict(calls=0, avoided_muse=0, cache_avoided=0,
                    results_avoided=0, skip_jev=0, saved_s=0.0, missing_literal=0, missing_results=0))
        t['calls'] += 1
        key = (row['run'],row['task_index'],row['frame_sha256'],json.dumps([row['state'],row['questions']],sort_keys=True))
        cached = key in seen
        seen.add(key)
        r = by_id.get(row['id'])
        local_results = r and r.get('available') and row['purpose']==RESULTS and r['results']['hit']
        if cached or local_results:
            t['cache_avoided' if cached else 'results_avoided'] += 1
            t['avoided_muse'] += bool(row.get('fallback_used'))
            t['saved_s'] += row.get('seconds') or 0
        elif row['purpose'] in LOCATE:
            t['skip_jev'] += 1
            t['saved_s'] += row.get('primary_s') or 0
        if r and not r['available']:
            t['missing_results' if row['purpose']==RESULTS else 'missing_literal'] += 1
    for t in traces.values():
        t['saved_s'] = round(t['saved_s'],3)
    summary = {'records':len(rows),'traces':len(traces),'literal_frames':sum(r['purpose'] in LITERAL for r in records),
        'results_frames':sum(r['purpose']==RESULTS for r in records),'available':sum(r['available'] for r in records),
        'missing':sum(not r['available'] for r in records), 'trace_estimates':traces,
        'limitations': ['Missing frames are unknown, not OCR misses.',
            'Original task steps/shots absent: observe_landscape_query recall only; not historical verify_landscape_literal eligibility.',
            'Cache estimates use reconstructed state/questions, so omitted historical context may reduce real cache hits.',
            'Local literal hit retains a fresh owner/focus model check, counted as zero confirmed Muse savings.',
            'Results skips assume same-task acknowledged submit; original per-step receipts are not in this dataset.',
            'Seconds are recorded call-duration estimates before new local OCR cost; no device or model timing was measured.']}
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'ocr-records.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    (args.out/'supplemental.json').write_text(json.dumps(supplemental,ensure_ascii=False,indent=2)+'\n')
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# 本地证据离线测量','','缺失帧不算 OCR miss；没有读取正式 runtime。原始 steps/shots 缺失，不能重建历史 verify_landscape_literal 门控。','',
        '| 检查 | 总帧 | 可读安全副本 | 基线 OCR 命中 | 新 OCR 命中 |','|---|---:|---:|---:|---:|']
    for purpose,name in [(LITERAL,'字面查询'),({RESULTS},'结果查询')]:
        group=[r for r in records if r['purpose'] in purpose];available=[r for r in group if r['available']]
        before = sum(r['baseline']['hit'] for r in available) if available else '不可测'
        after = sum(r['local']['hit'] for r in available) if available else '不可测'
        lines.append(f'| {name} | {len(group)} | {len(available)} | {before} | {after} |')
    lines += ['', '| 保存帧 | 真实标注 | 基线 | 新查询 OCR | 双信号含结果正文 | 基线原因 | 新原因 |','|---|---|---:|---:|---:|---|---|']
    for r in supplemental:
        truth='额外 habits 残留，必须拒绝' if r['frame'].startswith('r07') else '已看图的完整查询与结果'
        lines.append(f'| {r["frame"]} | {truth} | {int(r["baseline"]["hit"])} | {int(r["local"]["hit"])} | {int(r["results"]["hit"])} | {r["baseline"]["reason"]} | {r["local"]["reason"]} |')
    lines += ['', '| trace | judge 调用 | 避免 Muse 估计 | 缓存省调用 | 本地结果省调用 | Skip-Jev | 估计省秒 | 缺字面帧 | 缺结果帧 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for run,t in traces.items():
        lines.append('| '+run+' | '+' | '.join(str(t[k]) for k in ('calls','avoided_muse','cache_avoided','results_avoided','skip_jev','saved_s','missing_literal','missing_results'))+' |')
    lines += ['', '秒数使用旧 trace 的实际 judge 耗时，尚未扣除新 OCR 成本；属于静态估计。字面 OCR 命中后仍检查当前 owner/focus，不宣称直接省一次 Muse。','',
              '| 逐帧 ID | 状态或 miss 原因 |','|---|---|']
    lines += [f'| {r["id"]} | {r["local"]["reason"] if r["available"] else r["reason"]} |' for r in records]
    (args.out/'report.zh-CN.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='trace_estimates'},ensure_ascii=False))
    print(json.dumps({'avoided_muse_estimate':sum(t['avoided_muse'] for t in traces.values()),
        'skip_jev':sum(t['skip_jev'] for t in traces.values()),'saved_s_before_ocr':round(sum(t['saved_s'] for t in traces.values()),3)}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,default=HERE/'data/dataset.jsonl')
    p.add_argument('--frames',type=Path,default=HERE/'frames')
    p.add_argument('--binary',default=os.environ.get('LOCAL_OCR_BIN', 'vision-ocr'))
    p.add_argument('--out',type=Path,default=HERE/'evidence/replay')
    args=p.parse_args()
    forbidden=ROOT / 'runtime'
    if args.frames.resolve().is_relative_to(forbidden) or args.dataset.resolve().is_relative_to(forbidden):
        p.error('Formal runtime is forbidden; provide safe saved copies')
    asyncio.run(run(args))

if __name__=='__main__':main()
