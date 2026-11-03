"""Read-only API/static wiring, no phone or cloud connections."""
import importlib
from types import SimpleNamespace
from unittest.mock import Mock

def test_main_status_includes_passive_exploration_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA',str(tmp_path))
    api=importlib.import_module('hidagent.app')
    monkeypatch.setattr(api,'phone',SimpleNamespace(calibrated=False,target_info=lambda:{'aspect':.5}))
    monkeypatch.setattr(api,'screen',SimpleNamespace(live=False,aspect=.5))
    monkeypatch.setattr(api,'device',SimpleNamespace(connected=False,readiness=lambda:{'stable':False,'connected':False}))
    monkeypatch.setattr(api,'ENGINE','llm')
    monkeypatch.setattr(api,'agent',SimpleNamespace(running=False,task='',steps=[],trace=SimpleNamespace(dir=None),planner=SimpleNamespace(model='fake'),actor=SimpleNamespace(model='fake')))
    snapshot=Mock(return_value={'available':False,'state':'idle','busy':False})
    monkeypatch.setattr(api,'exploration',SimpleNamespace(status=snapshot))
    assert api.status()['exploration']=={'available':False,'state':'idle','busy':False}
    snapshot.assert_called_once_with()

def test_browser_entry_is_versioned_and_default_controls_are_inert(tmp_path,monkeypatch):
    monkeypatch.setenv('HIDAGENT_DATA',str(tmp_path))
    api=importlib.import_module('hidagent.app')
    html=api.index().body.decode()
    assert '/static/exploration.js?v='+api.ui_revision() in html
    assert 'id="exploration-panel" hidden' in html
    assert 'id="exploration-start" disabled' in html
    assert 'id="exploration-stop" hidden' in html
