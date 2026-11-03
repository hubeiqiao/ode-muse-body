#!/usr/bin/env python3
"""Adapted from basic-ops-baseline-20261030/scripts/bo_run.py and bo_queries.txt.

Based on ESP32 iPhone Agent by Henry Yuan (repository MIT LICENSE).
Runs registered app-search programs only, never low-level key/tap/home APIs.
The program owns launch/navigation. Start surface is not asserted to be Home.
APP_SEARCH_KEYBOARD_ROUTES must be set in the SERVER process too; the runner
checks the catalog before dispatch. No server, receiver or device is started.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

from eval.run import HTTP


# Same eleven app/query pairs as the supplied basic-ops baseline.
CASES = [
    ('b01-yt', 'YouTube', 'youtube.search', 'ielts speaking part 1 kites and windy weather'),
    ('b02-tt', 'TikTok', 'tiktok.search', 'ielts speaking part 2 describe a noisy neighbour'),
    ('b03-rn', 'rednote', 'xiaohongshu.search', 'ielts speaking part 1 handwriting versus typing'),
    ('b04-x', 'X', 'x.search', 'ielts speaking part 3 remote work balance'),
    ('b05-ig', 'Instagram', 'instagram.search', 'ielts speaking part 1 morning routines'),
    ('b06-rd', 'Reddit', 'reddit.search', 'ielts speaking pauses'),
    ('b07-bl', 'Bilibili', 'bilibili.search', 'ielts speaking part 2 describe a river'),
    ('b08-dy', 'Douyin', 'douyin.search', 'ielts speaking part 1 street food snacks'),
    ('b09-as', 'AppStore', 'app_store.search', 'IELTS speaking app'),
    ('b10-gm', 'GoogleMaps', 'google_maps.search', 'library near me'),
    ('b11-li', 'LinkedIn', 'linkedin.search', 'ielts speaking examiner feedback'),
]

REDNOTE_EXCLUSION = 'rednote excluded: security restriction, account abnormal; owner must set ENABLE_REDNOTE_BENCHMARK=1 to re-enable'


def excluded(case):
    return case[2] == 'xiaohongshu.search' and os.environ.get('ENABLE_REDNOTE_BENCHMARK') != '1'


def route_report(steps, timelines):
    route_rows = [row for row in steps if row.get('route_used') and
                  (row.get('kind') == 'app-search-route' or row.get('action') not in ('wait', 'done'))]
    routes = list(dict.fromkeys(row['route_used'] for row in route_rows))
    actual = [row.get('pointer_probe_sent') for row in route_rows if row.get('phase')]
    # Include launch probes too. A search-entry route is not a claim that
    # Spotlight/Home/application launch had zero pointer input.
    actual += [event['pointer_probe_sent'] for timeline in timelines for event in timeline.get('events', [])
               if 'pointer_probe_sent' in event]
    return dict(route_used=route_rows[-1]['route_used'] if route_rows else 'unknown',
                routes_attempted=routes,
                pointer_probe_sent=True if True in actual else
                None if not actual or None in actual else False)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def run_case(http, case, data_dir, out, *, expected_keyboard, timeout=300,
             sleep=time.sleep, monotonic=time.monotonic):
    label, app, tool, query = case
    if excluded(case):
        print(REDNOTE_EXCLUSION, flush=True)
        return dict(app=app, status='excluded', reason=REDNOTE_EXCLUSION)
    raw = Path(out) / 'raw' / (label + '.json')
    if raw.exists():
        raise RuntimeError('Existing benchmark receipt; use a new output directory, never replay')
    catalog = http.request('GET', '/api/app-tools/catalog')
    config = catalog.get('app_search_keyboard_routes', {})
    if config.get('enabled') is not expected_keyboard:
        raise RuntimeError('APP_SEARCH_KEYBOARD_ROUTES differs from the server process; no dispatch')
    status = http.request('GET', '/api/status')
    if not (status.get('controller', {}).get('stable') and status.get('calibrated')
            and not status.get('agent', {}).get('running')):
        raise RuntimeError('Program is not ready; no dispatch')
    traces = Path(data_dir) / 'traces'
    before = set(traces.iterdir()) if traces.exists() else set()
    timeline_path = traces / 'input-timelines.jsonl'
    timeline_count = len(rows(timeline_path))
    instance = catalog['server_instance']
    rid = f'{instance}_bo-{label}-{uuid.uuid4().hex[:6]}'
    started, wall = monotonic(), time.time()
    # One registered program invocation. Uncertain dispatch is never retried.
    call = http.request('POST', '/api/app-tools/call', dict(
        server_instance=instance, request_id=rid, tool_name=tool, args={'query': query}))
    receipt = call
    while receipt.get('status') in ('running', 'queued', 'in_progress', 'accepted', 'pending'):
        if monotonic() - started >= timeout:
            raise TimeoutError(f'Uncertain completion for {rid}; stop batch, never redispatch')
        sleep(1)
        receipt = http.request('GET', '/api/app-tools/status?request_id=' + rid)
    elapsed = round(monotonic() - started, 2)
    new = sorted(p for p in traces.iterdir() if p.is_dir() and p not in before) if traces.exists() else []
    steps = [row for directory in new for row in rows(directory / 'steps.jsonl')]
    timelines = rows(timeline_path)[timeline_count:]
    report = route_report(steps, timelines)
    record = dict(label=label, app=app, tool=tool, query=query, request_id=rid,
                  start='program_owned_unverified_surface', wall_start=wall, total_s=elapsed,
                  call_response=call, status=receipt, trace_dirs=[p.name for p in new],
                  steps=steps, timelines=timelines, **report)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(record, ensure_ascii=False, indent=1) + '\n')
    print(f'{app} total_s={elapsed} status={receipt.get("status")} '
          f'route_used={report["route_used"]} pointer_probe_sent={report["pointer_probe_sent"]}', flush=True)
    result = receipt.get('result') or {}
    uncertain = result.get('never_retry_typing') or (result.get('receipt') or {}).get('never_retry_typing')
    # Keep known failures in the matrix, without retrying that app/query.
    # An uncertain receipt or explicit Stop ends the batch, including later apps.
    if uncertain or receipt.get('status') not in ('completed', 'needs_review', 'awaiting_feedback', 'failed', 'not_ready'):
        raise RuntimeError(f'Uncertain or stopped program ({rid}); receipt saved, stop batch without replay')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:8890')
    parser.add_argument('--data-dir', required=True, type=Path, help='Data directory of the isolated program under test')
    parser.add_argument('--out', required=True, type=Path, help='New benchmark output directory')
    parser.add_argument('--app', action='append', choices=[case[1] for case in CASES])
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    http = HTTP(args.api)
    if os.environ.get('ENABLE_REDNOTE_BENCHMARK') != '1':
        print(REDNOTE_EXCLUSION, flush=True)
    cases = [case for case in CASES if (args.app is None or case[1] in args.app) and not excluded(case)]
    for case in cases:
        run_case(http, case, args.data_dir, args.out,
                 expected_keyboard=os.environ.get('APP_SEARCH_KEYBOARD_ROUTES', '0') == '1', timeout=args.timeout)


if __name__ == '__main__':
    main()
