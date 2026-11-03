#!/usr/bin/env python3
"""Bounded AppTools scenario runner; no retry path and no server startup."""
import argparse
import json
import math
import re
import secrets
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

HERE = Path(__file__).resolve().parent
NEVER = {'send', 'post', 'like', 'follow', 'DM'}
FINAL = {'completed', 'needs_review', 'awaiting_feedback', 'failed', 'stopped',
         'interrupted', 'input_uncertain', 'expired', 'busy', 'not_ready', 'not_found'}
SAFE_SPECIAL = {'public.read_results', 'grok.read_action', 'grok.preview_reply',
                # Read-only scenario tools (scenario_tools.py): never send, like, follow or open DMs.
                'instagram.profile_status', 'discord.status', 'bilibili.notifications', 'youtube.notifications',
                'tiktok.notifications', 'douyin.notifications', 'muse.threads_bot_read'}


class EvalError(RuntimeError):
    pass


class EvalPreflightError(EvalError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EvalError('Redirect refused; dispatch receipt is uncertain')


class HTTP:
    def __init__(self, base):
        parsed = urlsplit(base)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise EvalError('Base must be a local HTTP origin')
        self.base = base.rstrip('/')
        # No proxy, redirect or retry, especially for an uncertain POST.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, *, timeout=25):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise EvalError('Response exceeds receipt limit')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise EvalError('Expected a JSON object')
        return value


def load_scenarios(directory=HERE / 'scenarios'):
    items = [json.loads(path.read_text()) for path in sorted(Path(directory).glob('*.json'))]
    names = [item.get('name') for item in items]
    if not items or len(set(names)) != len(names):
        raise EvalError('Missing scenarios or duplicate names')
    return items


def validate_args(value, schema):
    """Validate the simple object/string contracts used by these scenarios."""
    types = {'object': dict, 'string': str, 'boolean': bool, 'integer': int, 'number': (int, float)}
    kind = schema.get('type')
    if kind not in types or not isinstance(value, types[kind]) or (kind in ('integer', 'number') and isinstance(value, bool)):
        raise EvalError('Arguments do not match catalog types')
    if 'const' in schema and value != schema['const'] or 'enum' in schema and value not in schema['enum']:
        raise EvalError('Arguments do not match catalog choices')
    if kind == 'object':
        properties = schema.get('properties', {})
        if not set(schema.get('required', [])) <= value.keys() or set(value) - properties.keys():
            raise EvalError('Missing or extra tool arguments')
        for key, child in value.items():
            validate_args(child, properties[key])
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 10000):
            raise EvalError('Argument length violates catalog')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise EvalError('Argument pattern violates catalog')
    elif kind in ('integer', 'number'):
        if not math.isfinite(value) or not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
            raise EvalError('Argument range violates catalog')


def validate_scenario(scenario, catalog):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,59}', scenario.get('name', '')):
        raise EvalError('Invalid scenario name')
    if not NEVER <= set(scenario.get('never', [])):
        raise EvalError('Scenario must forbid send/post/like/follow/DM')
    if scenario.get('mode') not in ('read_only', 'preview_only', 'manual'):
        raise EvalError('Only read-only or preview-only scenarios are permitted')
    if type(scenario.get('manual')) is not bool or (scenario['mode'] == 'manual' and not scenario['manual']):
        raise EvalError('Manual scenario needs an explicit manual flag')
    if scenario['manual'] and not scenario.get('manual_reason'):
        raise EvalError('Manual scenario needs a reason')
    if scenario.get('start_condition', {}).get('surface') not in ('Home', 'in-app'):
        raise EvalError('Start condition must be Home or in-app')
    if type(scenario.get('max_seconds')) is not int or not 1 <= scenario['max_seconds'] <= 1800:
        raise EvalError('Invalid scenario deadline')
    success = scenario.get('success', {})
    if not success.get('receipt_status') or not set(success['receipt_status']) <= {'completed', 'needs_review', 'awaiting_feedback'}:
        raise EvalError('Success needs explicit terminal receipt statuses')
    tools = {tool['name']: tool for tool in catalog['tools']}
    steps = scenario.get('steps')
    if not isinstance(steps, list) or not steps and not scenario['manual']:
        raise EvalError('Runnable scenario needs steps')
    for step in steps:
        name = step.get('tool_name', '')
        if name == 'mission.run':
            if not catalog.get('missions', {}).get('enabled'):
                raise EvalError('Goal API is not enabled')
            # Freeze a reviewed public goal instead of accepting arbitrary goals
            # from scenario data. The mission executor enforces its skill allowlist.
            if scenario['name'] != 'yolo_goal' or step.get('args') != {
                    'goal': PUBLIC_GOAL, 'seconds': 180, 'max_steps': 3}:
                raise EvalError('Only the bounded read-only public goal is permitted')
            continue
        if name not in tools or not (name.endswith(('.open', '.search')) or name in SAFE_SPECIAL):
            raise EvalError('Tool is unregistered or outside the read-only allowlist')
        if name == 'grok.preview_reply' and (scenario['mode'] != 'preview_only' or not scenario['manual']):
            raise EvalError('Preview requires a fresh human-reviewed target; sample cards must never dispatch')
        validate_args(step.get('args', {}), tools[name]['parameters'])


PUBLIC_GOAL = ('Search public YouTube for English speaking practice and read checked public search results. '
               'Read-only public research; return checked quotes without creating content or engaging with accounts.')


def ready(state):
    if not all(state.get(key) is True for key in ('device', 'screen', 'calibrated')):
        raise EvalError('Live calibrated device is required')
    if state.get('controller', {}).get('stable') is not True:
        raise EvalError('Stable controller is required')
    if state.get('target', {}).get('orientation') not in ('portrait', 'landscape'):
        raise EvalError('Known orientation is required')
    if state.get('target', {}).get('live_frame') is False:
        raise EvalError('Live screen frame is required')
    activity = state.get('control_activity', {})
    if (state.get('agent', {}).get('running') is not False
            or state.get('exploration', {}).get('busy') or activity.get('app_tool_running')
            or activity.get('manual_running') or activity.get('voice_sessions', 0)):
        raise EvalError('Console must be idle, including voice, manual and goal owners')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def safe_metrics(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ('total_s', 'first_feedback_s', 'hid_actions', 'waits')
              if number(value.get(key))}
    if value.get('first_feedback_s') is None:
        result['first_feedback_s'] = None
    model = value.get('model_calls', {})
    if isinstance(model, dict):
        result['model_calls'] = {key: model[key] for key in ('count', 'total_s') if number(model.get(key))}
        for key in ('jev', 'muse', 'other'):
            child = model.get(key)
            if isinstance(child, dict):
                result['model_calls'][key] = {k: child[k] for k in ('count', 'total_s') if number(child.get(k))}
    if value.get('launch_method') in ('spotlight_first', 'home_icon', 'shortcut', 'foreground_reuse'):
        result['launch_method'] = value['launch_method']
    if value.get('instrumentation') == 'unavailable':
        result['instrumentation'] = 'unavailable'
    return result


def safe_receipt(receipt, request_id, tool_name):
    # Store a receipt projection, never source excerpts, dictated text or frames.
    result = {'request_id': request_id, 'tool_name': tool_name,
              'status': receipt.get('status') if receipt.get('status') in FINAL | {'in_progress', 'running'} else 'unknown',
              'metrics': safe_metrics(receipt.get('metrics'))}
    for key in ('running', 'terminal', 'historical', 'history_write_pending', 'receipt_persisted'):
        if type(receipt.get(key)) is bool:
            result[key] = receipt[key]
    flags = receipt.get('result', {})
    if isinstance(flags, dict):
        result['result'] = {key: flags[key] for key in ('published', 'acknowledged', 'never_retry_typing', 'input_was_sent')
                            if type(flags.get(key)) is bool}
    return result


class Runner:
    def __init__(self, api, *, clock=time.monotonic, sleep=time.sleep):
        self.api, self.clock, self.sleep = api, clock, sleep

    def run(self, scenarios, repeat=1, emit=None, keep_going=False):
        if type(repeat) is not int or not 1 <= repeat <= 100:
            raise EvalError('Repeat must be between 1 and 100')
        rows = []
        for scenario in scenarios:
            for attempt in range(1, repeat + 1):
                try:
                    row = self.execute(scenario, attempt)
                except Exception as error:
                    if not keep_going:raise
                    # Preserve unexpected failures too, without claiming they
                    # preceded dispatch or retaining private exception bodies.
                    row={'scenario':scenario['name'],'attempt':attempt,'success':False,
                         'status':'preflight_failed' if isinstance(error,EvalPreflightError) else 'execution_error',
                         'halt':True,'error_type':type(error).__name__,
                         'receipts':[],'metrics':{},'elapsed_s':None,'visible_check':'not_requested'}
                rows.append(row)
                if emit:
                    emit(row)
                if row['halt']:
                    if not keep_going:return rows
                    # Advance to a DIFFERENT scenario, never repeat failed or
                    # uncertain input. execute performs a fresh idle precheck.
                    break
        return rows

    def execute(self, scenario, attempt):
        row = {'scenario': scenario['name'], 'attempt': attempt, 'success': False,
               'status': 'manual' if scenario['manual'] else 'pending', 'halt': False,
               'receipts': [], 'metrics': {}, 'elapsed_s': None,
               'visible_check': 'manual_required' if scenario.get('success', {}).get('visible_check_note') else 'not_requested'}
        if scenario['manual']:
            return row
        try:
            ready(self.api.request('GET', '/api/status'))
            catalog = self.api.request('GET', '/api/app-tools/catalog')
            validate_scenario(scenario, catalog)
            instance = catalog.get('server_instance', '')
            if not re.fullmatch(r'[a-f0-9]{32}', instance):
                raise EvalError('Missing fresh server instance')
        except Exception as error:
            raise EvalPreflightError('Preflight unavailable: '+type(error).__name__) from None
        started = self.clock()
        deadline = started + scenario['max_seconds']
        for step in scenario['steps']:
            if self.clock() >= deadline:
                row.update(status='timed_out', halt=True)
                break
            # A fresh idle check before each operation. Never repair device state.
            try:
                ready(self.api.request('GET', '/api/status'))
            except Exception:
                row.update(status='preflight_changed', halt=True)
                break
            rid = instance + '_eval_' + secrets.token_hex(12)
            mission = step['tool_name'] == 'mission.run'
            body = {'server_instance': instance, 'request_id': rid}
            if mission:
                body.update(step['args'])
            else:
                body.update(tool_name=step['tool_name'], args=step['args'])
            try:
                receipt = self.api.request('POST', '/api/mission/start' if mission else '/api/app-tools/call',
                                           body, timeout=max(.01, min(25, deadline - self.clock())))
            except Exception:
                row.update(status='dispatch_uncertain', halt=True)
                row['receipts'].append({'request_id': rid, 'tool_name': step['tool_name'], 'status': 'dispatch_uncertain'})
                break
            while True:
                if self.clock() >= deadline:
                    row.update(status='timed_out', halt=True)
                    break
                if receipt.get('request_id') != rid or receipt.get('server_instance') != instance or receipt.get('historical'):
                    row.update(status='receipt_mismatch', halt=True)
                    break
                flags=receipt.get('result',{})
                if (receipt.get('history_error') or receipt.get('receipt_persisted') is False or not isinstance(flags,dict)
                        or any(flags.get(key) for key in ('never_retry_typing','published','acknowledged'))):
                    row.update(status='receipt_uncertain', halt=True)
                    break
                if not receipt.get('running') and not receipt.get('history_write_pending'):
                    row['status'] = receipt.get('status') if receipt.get('status') in FINAL else 'invalid_receipt'
                    if receipt.get('terminal') is not True or row['status'] not in scenario['success']['receipt_status']:
                        row['halt'] = True
                    break
                self.sleep(min(.25, max(0, deadline - self.clock())))
                path = '/api/mission/status' if mission else '/api/app-tools/status?' + urlencode({'request_id': rid})
                try:
                    receipt = self.api.request('GET', path, timeout=max(.01, min(10, deadline - self.clock())))
                except Exception:
                    row.update(status='poll_uncertain', halt=True)
                    break
            row['receipts'].append(safe_receipt(receipt, rid, step['tool_name']))
            if row['halt']:
                break
        row['elapsed_s'] = round(self.clock() - started, 3)
        row['success'] = not row['halt'] and row['status'] in scenario['success']['receipt_status']
        metrics = [r.get('metrics', {}) for r in row['receipts']]
        measured = bool(metrics) and all('count' in m.get('model_calls', {})
            and m.get('instrumentation') != 'unavailable' for m in metrics)
        row['metrics'] = {'total_s': row['elapsed_s'],
            'model_calls': sum(m['model_calls']['count'] for m in metrics) if measured else None,
            'model_s': round(sum(m.get('model_calls', {}).get('total_s', 0) for m in metrics), 3) if measured else None}
        return row


def summary_table(rows):
    lines = ['Scenario | First-try success | P50 s | Max s | Model calls']
    for name in dict.fromkeys(row['scenario'] for row in rows):
        selected = [row for row in rows if row['scenario'] == name]
        measured = [row for row in selected if row['elapsed_s'] is not None]
        if not measured:
            statuses=', '.join(dict.fromkeys(row['status'] for row in selected))
            lines.append(f'{name} | {statuses} | - | - | -')
            continue
        times = [row['elapsed_s'] for row in measured]
        successes = sum(row['success'] for row in measured)
        values = [row['metrics'].get('model_calls') for row in measured]
        calls = sum(values) if all(value is not None for value in values) else '-'
        lines.append(f'{name} | {successes}/{len(measured)} | {statistics.median(times):.3f} | {max(times):.3f} | {calls}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--scenario')
    choice.add_argument('--all', action='store_true')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--keep-going', action=argparse.BooleanOptionalAction, default=None,
                        help='Continue with the next scenario after failure (default with --all); never repeat failed input')
    parser.add_argument('--base', default='http://127.0.0.1:8890')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--catalog-fixture', type=Path,
                        help='Offline dry-run only; never contacts the base URL')
    parser.add_argument('--output', type=Path, default=HERE / 'results' / ('eval-' + time.strftime('%Y%m%d-%H%M%S') + '.jsonl'))
    args = parser.parse_args(argv)
    try:
        scenarios = load_scenarios()
        if args.scenario:
            scenarios = [s for s in scenarios if s['name'] == args.scenario]
        if not scenarios or not 1 <= args.repeat <= 100:
            raise EvalError('Unknown scenario or invalid repeat')
        if args.catalog_fixture and not args.dry_run:
            raise EvalError('Fixture catalogs are only valid with --dry-run')
        if args.dry_run:
            catalog = (json.loads(args.catalog_fixture.read_text()) if args.catalog_fixture
                       else HTTP(args.base).request('GET', '/api/app-tools/catalog'))
            for scenario in scenarios:
                if scenario['manual']:
                    print(f"{scenario['name']}: manual (skipped)")
                    continue
                validate_scenario(scenario, catalog)
                print(f"{scenario['name']}: valid ({scenario['mode']})")
            return 0
        api = HTTP(args.base)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Append each outcome immediately, including uncertain dispatches.
        with args.output.open('a', encoding='utf-8') as output:
            def emit(row):
                output.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + '\n')
                output.flush()
            rows = Runner(api).run(scenarios, args.repeat, emit=emit,
                                   keep_going=args.all if args.keep_going is None else args.keep_going)
        print(summary_table(rows))
        return 1 if any(row['halt'] for row in rows) else 0
    except Exception as error:
        # Exception bodies from HTTP/model services may contain private content.
        print('Evaluation refused or interrupted: ' + (str(error) if isinstance(error, EvalError) else type(error).__name__))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
