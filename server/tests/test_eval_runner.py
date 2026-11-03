"""Runner contract tests use a fake transport, never a listening server."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runner_module():
    spec = importlib.util.spec_from_file_location('offline_eval_runner', ROOT / 'tools/eval/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHTTP:
    def __init__(self, *, running=False, orientation='portrait', timeout=False, uncertain=False):
        self.calls = []
        self.running, self.orientation = running, orientation
        self.timeout, self.uncertain = timeout, uncertain
        self.request_id = None
    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body))
        if path == '/api/status':
            return {'device': True, 'screen': True, 'calibrated': True,
                    'controller': {'stable': True}, 'target': {'orientation': self.orientation},
                    'agent': {'running': self.running}, 'control_activity': {}, 'exploration': {}}
        if path == '/api/app-tools/catalog':
            return json.loads((ROOT / 'tools/eval/fixtures/catalog.json').read_text())
        if method == 'POST':
            self.request_id = body['request_id']
            if self.timeout:
                raise TimeoutError('Uncertain dispatch')
            return self.receipt(True)
        return self.receipt(False)
    def receipt(self, running):
        return {'request_id': self.request_id, 'server_instance': 'a' * 32,
                'status': 'in_progress' if running else 'input_uncertain' if self.uncertain else 'completed',
                'running': running, 'terminal': not running,
                'metrics': {'total_s': 1.2, 'model_calls': {'count': 1, 'total_s': .5}},
                'result': {'body': 'PRIVATE', 'screenshot': 'PRIVATE', 'typed_text': 'PRIVATE'}}


def test_scenario_files_validate_offline_and_catalog_matches_registered_tools(runner_module):
    from hidagent.app_tools import TOOLS
    catalog = json.loads((ROOT / 'tools/eval/fixtures/catalog.json').read_text())
    assert {tool['name'] for tool in catalog['tools']} == set(TOOLS)
    scenarios = runner_module.load_scenarios()
    assert {s['name'] for s in scenarios} == {
        'photo', 'grok_read_action', 'grok_preview_reply', 'muse_threads_read',
        'rednote_search', 'reddit_chrome_search', 'instagram_profile_status',
        'discord_status', 'bilibili_notifications', 'youtube_notifications',
        'tiktok_notifications', 'douyin_notifications', 'yolo_goal'}
    for scenario in scenarios:
        runner_module.validate_scenario(scenario, catalog)
    assert next(s for s in scenarios if s['name'] == 'photo')['manual']


def scenario(module, name='grok_read_action'):
    return next(s for s in module.load_scenarios() if s['name'] == name)


@pytest.mark.parametrize('running,orientation', [(True, 'portrait'), (False, None), (False, 'unknown')])
def test_live_run_refuses_busy_or_unknown_orientation_before_dispatch(runner_module, running, orientation):
    api = FakeHTTP(running=running, orientation=orientation)
    with pytest.raises(runner_module.EvalError):
        runner_module.Runner(api).run([scenario(runner_module)], repeat=1)
    assert all(method == 'GET' for method, _, _ in api.calls)


def test_dispatch_once_poll_only_unique_ids_and_sanitize_results(runner_module):
    api = FakeHTTP()
    rows = runner_module.Runner(api, sleep=lambda _: None).run([scenario(runner_module)], repeat=2)
    posts = [body for method, _, body in api.calls if method == 'POST']
    assert len(posts) == 2 and len({p['request_id'] for p in posts}) == 2
    assert all(p['request_id'].startswith('a' * 32 + '_') for p in posts)
    assert all(row['success'] for row in rows)
    assert 'PRIVATE' not in json.dumps(rows)
    assert all(row['receipts'][0]['metrics']['total_s'] == 1.2 for row in rows)
    assert 'P50' in runner_module.summary_table(rows)


@pytest.mark.parametrize('timeout,uncertain', [(True, False), (False, True)])
def test_uncertain_dispatch_or_result_halts_repeats_without_resubmitting(runner_module, timeout, uncertain):
    api = FakeHTTP(timeout=timeout, uncertain=uncertain)
    rows = runner_module.Runner(api, sleep=lambda _: None).run([scenario(runner_module)], repeat=3)
    assert len([c for c in api.calls if c[0] == 'POST']) == 1
    assert len(rows) == 1 and rows[0]['halt'] and not rows[0]['success']


def test_manual_scenarios_and_dry_run_never_dispatch(runner_module, tmp_path):
    api = FakeHTTP()
    rows = runner_module.Runner(api).run([scenario(runner_module, 'photo')], repeat=2)
    assert all(row['status'] == 'manual' for row in rows)
    assert not api.calls
    assert runner_module.main(['--all', '--dry-run', '--catalog-fixture',
        str(ROOT / 'tools/eval/fixtures/catalog.json')]) == 0


def test_validation_rejects_unregistered_or_side_effect_tools(runner_module):
    catalog = FakeHTTP().request('GET', '/api/app-tools/catalog')
    for name in ('missing.read', 'keyboard.type_focused', 'grok.copy_reply', 'x.prepare_draft'):
        data = copy.deepcopy(scenario(runner_module))
        data['steps'][0]['tool_name'] = name
        with pytest.raises(runner_module.EvalError):
            runner_module.validate_scenario(data, catalog)
    data = copy.deepcopy(scenario(runner_module))
    data['never'] = []
    with pytest.raises(runner_module.EvalError):
        runner_module.validate_scenario(data, catalog)


def test_cli_records_jsonl_on_uncertain_dispatch(runner_module, tmp_path, monkeypatch):
    api = FakeHTTP(timeout=True)
    monkeypatch.setattr(runner_module, 'HTTP', lambda *a, **kw: api)
    output = tmp_path / 'results.jsonl'
    assert runner_module.main(['--scenario', 'grok_read_action', '--repeat', '3', '--output', str(output)]) == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]['status'] == 'dispatch_uncertain'


@pytest.mark.parametrize('problem', ['published', 'nonterminal', 'wrong_id', 'poll_error', 'poll_timeout', 'busy_between_steps', 'not_persisted'])
def test_bad_receipts_and_changed_readiness_halt_and_keep_evidence(runner_module, problem):
    clock = [0.0]
    class BadHTTP(FakeHTTP):
        def request(self, method, path, body=None, **kwargs):
            if path.startswith('/api/app-tools/status'):
                if problem == 'poll_error':
                    raise OSError('private response')
                if problem == 'poll_timeout':
                    clock[0] = 500
            value = super().request(method, path, body, **kwargs)
            if path == '/api/status' and problem == 'busy_between_steps' and len(self.calls) > 4:
                value['agent']['running'] = True
            if method == 'GET' and path.startswith('/api/app-tools/status'):
                if problem == 'published': value['result']['published'] = True
                if problem == 'nonterminal': value['terminal'] = False
                if problem == 'wrong_id': value['request_id'] = 'old_request'
                if problem == 'not_persisted': value['receipt_persisted'] = False
            return value
    api = BadHTTP()
    selected = scenario(runner_module, 'rednote_search' if problem == 'busy_between_steps' else 'grok_read_action')
    rows = runner_module.Runner(api, clock=lambda: clock[0], sleep=lambda _: None).run([selected], repeat=2)
    assert len(rows) == 1 and rows[0]['halt'] and not rows[0]['success']
    assert len([call for call in api.calls if call[0] == 'POST']) == 1
    assert rows[0]['receipts']


def test_mission_goal_does_not_trigger_executor_draft_permission(runner_module):
    goal = scenario(runner_module, 'yolo_goal')['steps'][0]['args']['goal'].lower()
    assert not any(word in goal for word in ('draft', 'post', 'caption', 'recommend', 'idea'))


def test_missing_model_metrics_are_unknown_instead_of_claimed_zero(runner_module):
    class NoMetrics(FakeHTTP):
        def receipt(self, running):
            value = super().receipt(running)
            value.pop('metrics')
            return value
    rows = runner_module.Runner(NoMetrics(), sleep=lambda _: None).run([scenario(runner_module)])
    assert rows[0]['metrics']['model_calls'] is None
    assert runner_module.summary_table(rows).splitlines()[-1].endswith('| -')


def test_goal_uses_existing_mission_api_once_and_polls_only(runner_module):
    class GoalHTTP(FakeHTTP):
        def receipt(self, running):
            value = super().receipt(running)
            if not running:
                value['status'] = 'awaiting_feedback'
            return value
    api = GoalHTTP()
    rows = runner_module.Runner(api, sleep=lambda _: None).run([scenario(runner_module, 'yolo_goal')])
    assert rows[0]['success']
    assert len([c for c in api.calls if c[0] == 'POST']) == 1
    assert next(c for c in api.calls if c[0] == 'POST')[1] == '/api/mission/start'
    assert any(path == '/api/mission/status' for _, path, _ in api.calls)


@pytest.mark.parametrize('dry_run',[False,True])
def test_stability_all_skips_proposed_manual_tools(runner_module,tmp_path,monkeypatch,capsys,dry_run):
 data=runner_module.load_scenarios()
 for item in data:
  if item['name'] in {'bilibili_notifications','discord_status','douyin_notifications','instagram_profile_status','muse_threads_read','tiktok_notifications','youtube_notifications'}:
   item.update(manual=True,manual_reason='Proposed launch requires review')
  if item['manual']:
   item['steps']=[{'tool_name':'unregistered.open','args':{}}]
 monkeypatch.setattr(runner_module,'load_scenarios',lambda:data)
 class AllHTTP(FakeHTTP):
  def receipt(self,running):
   result=super().receipt(running)
   if not running and self.calls[-1][1]=='/api/mission/status':result['status']='awaiting_feedback'
   return result
 api=AllHTTP()
 monkeypatch.setattr(runner_module,'HTTP',lambda *a,**kw:api)
 original=runner_module.validate_scenario
 validated=[]
 def validate(item,catalog):
  validated.append(item['name']);return original(item,catalog)
 monkeypatch.setattr(runner_module,'validate_scenario',validate)
 args=['--all','--output',str(tmp_path/'results.jsonl')]
 if dry_run:args.append('--dry-run')
 assert runner_module.main(args)==0
 assert set(validated)=={s['name'] for s in data if not s['manual']}
 output=capsys.readouterr().out
 assert all(s['name'] in output for s in data if s['manual'])
 assert 'manual' in output
 if dry_run:assert all(c[0]=='GET' for c in api.calls)
 else:
  rows=[json.loads(line) for line in (tmp_path/'results.jsonl').read_text().splitlines()]
  assert all(r['status']=='manual' for r in rows if r['scenario'] in {s['name'] for s in data if s['manual']})


@pytest.mark.parametrize('flag', [[], ['--keep-going']])
def test_all_continues_after_terminal_failure_and_exits_nonzero(runner_module, tmp_path, monkeypatch, flag):
    class FailedHTTP(FakeHTTP):
        def receipt(self, running):
            value=super().receipt(running)
            if not running: value['status']='failed'
            return value
    api=FailedHTTP(); monkeypatch.setattr(runner_module,'HTTP',lambda *a,**kw:api)
    output=tmp_path/'all.jsonl'
    assert runner_module.main(['--all','--output',str(output),*flag]) == 1
    rows=[json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows)==len(runner_module.load_scenarios())
    assert len([c for c in api.calls if c[0]=='POST'])==sum(not s['manual'] for s in runner_module.load_scenarios())


def test_keep_going_records_preflight_exception_and_continues(runner_module):
    api=FakeHTTP(running=True)
    rows=runner_module.Runner(api).run([scenario(runner_module),scenario(runner_module,'photo')],keep_going=True)
    assert [r['status'] for r in rows]==['preflight_failed','manual']
    assert not any(c[0]=='POST' for c in api.calls)


def test_keep_going_never_repeats_uncertain_scenario(runner_module):
    api=FakeHTTP(timeout=True)
    rows=runner_module.Runner(api).run([scenario(runner_module),scenario(runner_module,'photo')],repeat=3,keep_going=True)
    assert len([c for c in api.calls if c[0]=='POST'])==1
    assert len(rows)==4 and rows[0]['status']=='dispatch_uncertain'


def test_keep_going_does_not_mislabel_unexpected_execution_error_as_preflight(runner_module, monkeypatch):
    runner=runner_module.Runner(FakeHTTP())
    monkeypatch.setattr(runner,'execute',lambda *args: (_ for _ in ()).throw(RuntimeError('PRIVATE')))
    rows=runner.run([scenario(runner_module)],keep_going=True)
    assert rows[0]['status']=='execution_error'
    assert 'PRIVATE' not in json.dumps(rows)


def test_preflight_failure_summary_is_not_manual(runner_module):
    rows=runner_module.Runner(FakeHTTP(running=True)).run([scenario(runner_module)],keep_going=True)
    assert 'preflight_failed' in runner_module.summary_table(rows)
    assert '| manual |' not in runner_module.summary_table(rows)


def test_cli_all_can_explicitly_disable_keep_going(runner_module,tmp_path,monkeypatch):
    monkeypatch.setattr(runner_module,'HTTP',lambda *a,**kw:FakeHTTP(uncertain=True))
    output=tmp_path/'one.jsonl'
    assert runner_module.main(['--all','--no-keep-going','--output',str(output)])==1
    assert len(output.read_text().splitlines())==1
