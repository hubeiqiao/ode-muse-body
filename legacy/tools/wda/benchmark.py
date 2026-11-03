#!/usr/bin/env python3
"""Explicit mini-only WDA public search benchmark. Does not start a server.

Fresh output directory and intent reservation before every dispatch; a process
crash or uncertain input must never cause an automatic replay. Reference timings
are owner-supplied historical context, not matched measurements from this run.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'server'))
from hidagent.wda_driver import WDAClient, WDAError
from hidagent.wda_search import APPS, search

QUERIES = {
    'youtube': 'ielts speaking local parks',
    'tiktok': 'ielts speaking favourite season',
    'xiaohongshu': 'ielts speaking learning vocabulary',
    'instagram': 'ielts speaking daily practice',
    'linkedin': 'ielts speaking language teaching',
    'x': 'ielts speaking study routine',
    'reddit': 'ielts speaking clear pronunciation',
    'app_store': 'IELTS speaking',
    'google_maps': 'Ottawa public library',
    'douyin': 'ielts speaking simple sentences',
    'bilibili': 'ielts speaking exam preparation',
}
NAMES = {'youtube': 'YouTube', 'tiktok': 'TikTok', 'xiaohongshu': 'rednote', 'instagram': 'Instagram',
         'linkedin': 'LinkedIn', 'x': 'X', 'reddit': 'Reddit', 'app_store': 'App Store',
         'google_maps': 'Google Maps', 'douyin': 'Douyin', 'bilibili': 'Bilibili'}

REDNOTE_EXCLUSION = 'rednote excluded: security restriction, account abnormal; owner must set ENABLE_REDNOTE_BENCHMARK=1 to re-enable'


def excluded(app):
    return app == 'xiaohongshu' and os.environ.get('ENABLE_REDNOTE_BENCHMARK') != '1'


def write_json(path, value):
    # Atomic result updates. The separate exclusive intent file is never removed.
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w') as file:
        json.dump(value, file, indent=2, ensure_ascii=False)
        file.write('\n')
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def write_report(out):
    lines = ['# WDA iPad 公共搜索实测', '',
             '仅 `verified` 代表树中看到查询相关说明及已加载结果条目。合成测试耗时不计为真机结果。', '',
             '对照为 Joe 提供的历史概数：iPad ESP32 ~60 s，Android ~8.7 s。不是本轮同条件对照。', '',
             '| App | WDA 状态 | WDA 总耗时 s | ESP32 参考 s | Android 参考 s |',
             '| --- | --- | ---: | ---: | ---: |']
    for key in APPS:
        path = out / key / 'result.json'
        record = json.loads(path.read_text()) if path.is_file() else {}
        state = REDNOTE_EXCLUSION if excluded(key) else record.get('status', '未测')
        elapsed = record.get('total_s', 'N/A')
        lines.append(f'| {NAMES[key]} | {state} | {elapsed} | ~60 | ~8.7 |')
    lines += ['', '每项 `intent.json` 在首次请求前写入；存在即禁止重放。`result.json` 含查询、结果、分项耗时。',
              '输入回执不确定时停止整批。未配对或型号不符时不启动搜索。截图和树验证不能相互冒充。', '']
    (out / 'README.zh-CN.md').write_text('\n'.join(lines))


async def run_case(out, app, query, *, factory=None):
    if excluded(app):
        print(REDNOTE_EXCLUSION, flush=True)
        return {'app': app, 'status': 'excluded', 'reason': REDNOTE_EXCLUSION}
    directory = out / app
    directory.mkdir(parents=True, exist_ok=True)
    # Never overwrite or redispatch even if the earlier process crashed before result.json.
    with (directory / 'intent.json').open('x') as file:
        json.dump({'app': app, 'query': query, 'created_at': time.time(), 'never_replay': True}, file)
        file.flush()
        os.fsync(file.fileno())
    started, client, timings = time.perf_counter(), None, []
    record = {'app': app, 'query': query, 'driver': 'wda', 'status': 'started'}
    def log(row):
        timings.append(row)
    try:
        client = await (factory or WDAClient.from_env)(log=log)
        result = await search(client, app, query)
        record.update(status='verified' if result.get('verified') else 'failed', result=result)
        return record
    except asyncio.CancelledError:
        record.update(status='input_uncertain', never_retry_typing=True, reason='Benchmark cancelled; never replay')
        raise
    except WDAError as exc:
        record.update(status='input_uncertain' if exc.delivery_uncertain else 'failed',
                      never_retry_typing=exc.delivery_uncertain, reason=str(exc))
        if exc.delivery_uncertain:
            raise
        return record
    except Exception as exc:
        # Unexpected errors after dispatch are conservative, never evidence of non-delivery.
        record.update(status='input_uncertain', never_retry_typing=True, reason=type(exc).__name__)
        raise WDAError('Unexpected benchmark failure; stop batch without replay', delivery_uncertain=True) from exc
    finally:
        record.update(total_s=round(time.perf_counter() - started, 6), timings=timings)
        write_json(directory / 'result.json', record)
        write_report(out)
        if client is not None:
            await client.http.aclose()
        print(json.dumps({k: record[k] for k in ('app', 'status', 'total_s')}), flush=True)


async def run_batch(out, apps, run_id):
    out.mkdir(parents=True, exist_ok=False)
    selected = [app for app in apps if not excluded(app)]
    if excluded('xiaohongshu'):
        print(REDNOTE_EXCLUSION, flush=True)
    write_json(out / 'batch.json', {'run_id': run_id, 'apps': selected,
                                  'excluded': [app for app in apps if excluded(app)], 'never_replay': True})
    write_report(out)
    for app in selected:
        # New meaningful queries relative to basic_ops_benchmark.CASES. Tracking
        # IDs belong in evidence, not the literal query (especially Maps/Store).
        await run_case(out, app, QUERIES[app])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New evidence directory; existing directory refuses replay')
    parser.add_argument('--app', action='append', choices=list(APPS))
    args = parser.parse_args()
    try:
        asyncio.run(run_batch(args.out.resolve(), args.app or list(APPS), uuid.uuid4().hex[:8]))
    except (WDAError, OSError) as exc:
        print(f'Benchmark stopped: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
