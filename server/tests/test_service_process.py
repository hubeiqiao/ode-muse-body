import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

path = Path(__file__).resolve().parents[2] / 'scripts/service-process.py'
spec = importlib.util.spec_from_file_location('service_process', path)
service = importlib.util.module_from_spec(spec); spec.loader.exec_module(service)

EXE = '/project/server/.venv/bin/python'

def probe(tmp_path, pid='77', owners='', command='', error=False, cwd='/project/server'):
    file=tmp_path/'server.pid';file.write_text(pid)
    calls=[]
    def run(args, **kwargs):
        calls.append(args)
        if error:return SimpleNamespace(returncode=2,stdout='',stderr='permission denied')
        value=('n'+cwd if '-d' in args else owners) if args[0].endswith('lsof') else command
        return SimpleNamespace(returncode=0 if value else 1,stdout=value,stderr='')
    return service.inspect_service(file, EXE, 'hidagent', 8890, run),calls

def test_recycled_pid_is_absent_without_killing(tmp_path):
    state,calls=probe(tmp_path, command='/bin/sleep 900')
    assert state=='absent'
    assert all('kill' not in c[0] for c in calls)

def test_listener_and_exact_pid_identity_required(tmp_path):
    assert probe(tmp_path,owners='77',command=EXE+' -u -m hidagent')[0]=='running'
    assert probe(tmp_path,owners='88',command=EXE+' -u -m hidagent')[0]=='conflict'

def test_other_profile_module_or_checkout_not_adopted(tmp_path):
    for command in [EXE+' -u -m hidagent.receiver', '/other/python -u -m hidagent', '/bin/echo '+EXE+' -u -m hidagent']:
        assert probe(tmp_path,owners='77',command=command)[0]=='conflict'

def test_no_listener_but_owned_process_does_not_duplicate(tmp_path):
    assert probe(tmp_path,command=EXE+' -u -m hidagent')[0]=='starting_or_unhealthy'

def test_malformed_and_stale_pid_with_live_port_refuses(tmp_path):
    for pid in ['-1','1','not-a-pid','77\n88']:
        assert probe(tmp_path,pid=pid,owners='88')[0]=='conflict'

def test_multiple_owners_not_adopted(tmp_path):
    assert probe(tmp_path,owners='77\n88',command=EXE+' -u -m hidagent')[0]=='conflict'

def test_ownership_failure_does_not_mean_absent(tmp_path):
    with pytest.raises(RuntimeError):probe(tmp_path,error=True)

def test_no_shell_execution_of_process_text():
    assert not service.matches_service(EXE+' -u -m hidagent; touch /tmp/bad',EXE,'hidagent')
    assert not service.matches_service('malformed "',EXE,'hidagent')

def test_same_interpreter_wrong_checkout_rejected(tmp_path):
    assert probe(tmp_path,owners='77',command=EXE+' -u -m hidagent',cwd='/other/server')[0]=='conflict'
