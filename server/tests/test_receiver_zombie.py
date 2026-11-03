"""Synthetic receiver error replay.

No AirPlay, device, service ports or model APIs are used here.
"""
import io
import logging
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from hidagent import receiver
from hidagent.mirror_stream import PREFIX, decode, record
from hidagent.screen import Screen

NTP = '*** ERROR: raop_ntp error sending request. Error 65:No route to host'
FEEDBACK = '*** ERROR:  {} seconds since last client feedback request (expected every two seconds); client may be offline'
INCIDENT = [NTP] * 8 + [FEEDBACK.format(20)]
FRAME = b'\xff\xd8test-frame\xff\xd9'


@pytest.fixture
def clock(monkeypatch):
    import hidagent.screen as module
    now = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: now[0])
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    return now


def connected(monkeypatch, **options):
    screen = Screen(name='iPad Agent', **options)
    screen._proc = Mock(pid=12345, poll=Mock(return_value=None))
    monkeypatch.setattr(screen, '_clients', lambda: 1)  # Stale TCP remains established in the incident.
    return screen


def replay(screen, lines):
    # Before the fix stdout is inherited, so none of these lines affect health.
    handle = getattr(screen, '_handle_uxplay_line', lambda line: None)
    for line in lines:
        handle(line)


def test_incident_revokes_stale_tcp_session_and_cached_frame(monkeypatch, clock, caplog):
    screen = connected(monkeypatch)
    screen.frame, screen.updated = FRAME, clock[0] - 3480
    assert screen.receiver_health()['peer_connected'] is True
    assert screen.live  # Reproduces the old false-positive despite a 58 minute old frame.
    with caplog.at_level(logging.INFO):
        replay(screen, INCIDENT)
    assert screen.receiver_health()['peer_connected'] is False
    assert not screen.live and screen.frame is None
    assert screen.mirror_health()['state'] == 'source_offline'
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


@pytest.mark.parametrize('reset,expected', [(None, '15'), ('0', '0'), ('27', '27')])
def test_launch_reset_and_stdout_capture(monkeypatch, clock, reset, expected):
    if reset is None:
        monkeypatch.delenv('UXPLAY_RESET_S', raising=False)
    else:
        monkeypatch.setenv('UXPLAY_RESET_S', reset)
    child = Mock(pid=12345, stdout=io.StringIO(''))
    spawn = Mock(return_value=child)
    monkeypatch.setattr('hidagent.screen.subprocess.Popen', spawn)
    screen = Screen()
    screen._launch('fake sink')
    argv = spawn.call_args.args[0]
    assert argv[argv.index('-reset') + 1] == expected
    assert spawn.call_args.kwargs['stdout'] == subprocess.PIPE
    assert spawn.call_args.kwargs['stderr'] == subprocess.STDOUT
    screen._log_thread.join(timeout=1)
    assert not screen._log_thread.is_alive()


def test_launched_stdout_is_actually_parsed_and_drained(monkeypatch, clock):
    child = Mock(pid=12345, poll=Mock(return_value=None), stdout=io.StringIO('\n'.join(INCIDENT)))
    monkeypatch.setattr('hidagent.screen.subprocess.Popen', Mock(return_value=child))
    screen = Screen()
    monkeypatch.setattr(screen, '_clients', lambda: 1)
    screen._launch('fake sink')
    screen._log_thread.join(timeout=1)
    assert not screen._log_thread.is_alive()
    assert screen.receiver_health()['peer_connected'] is False
    assert child.stdout.closed


def test_feedback_threshold_does_not_confuse_still_frames_with_offline(monkeypatch, clock):
    screen = connected(monkeypatch)
    screen.frame, screen.updated = FRAME, clock[0] - 3600
    replay(screen, [FEEDBACK.format(n) for n in range(3, 20)])
    assert screen.live and screen.receiver_health()['peer_connected'] is True
    replay(screen, [FEEDBACK.format(20)])
    assert not screen.live and screen.receiver_health()['peer_connected'] is False


def test_ntp_burst_alone_detects_zombie_and_collapses_30000_lines(monkeypatch, clock, caplog):
    screen = connected(monkeypatch)
    with caplog.at_level(logging.INFO):
        replay(screen, [NTP] * 30000)
        clock[0] += 60
        replay(screen, [NTP])
    assert screen.receiver_health()['peer_connected'] is False
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert len(caplog.records) <= 4
    assert any('29999' in r.message and 'suppressed' in r.message for r in caplog.records)


def test_ntp_burst_does_not_revoke_recent_frames(monkeypatch, clock):
    screen = connected(monkeypatch)
    screen.frame, screen.updated = FRAME, clock[0]
    replay(screen, [NTP] * 10)
    assert screen.receiver_health()['peer_connected'] is True
    clock[0] += 11
    replay(screen, [NTP] * 5)
    assert screen.receiver_health()['peer_connected'] is False


def test_sparse_ntp_errors_are_not_a_burst(monkeypatch, clock):
    screen = connected(monkeypatch)
    for _ in range(10):
        replay(screen, [NTP])
        clock[0] += 11
    assert screen.receiver_health()['peer_connected'] is True
    replay(screen, [NTP] * 5)
    assert screen.receiver_health()['peer_connected'] is False


def test_new_frame_clears_offline_and_resets_burst(monkeypatch, clock):
    screen = connected(monkeypatch)
    replay(screen, [NTP] * 5)
    assert screen.receiver_health()['peer_connected'] is False
    chunks = iter([FRAME, b''])
    screen._consume(lambda: next(chunks))
    assert screen.live and screen.receiver_health()['peer_connected'] is True
    clock[0] += 10  # The old burst window has not expired at this boundary.
    replay(screen, [NTP] * 4)
    assert screen.receiver_health()['peer_connected'] is True
    replay(screen, [NTP])
    assert screen.receiver_health()['peer_connected'] is False


def test_offline_reason_crosses_receiver_health_protocol_and_diagnostics(monkeypatch, clock):
    source = connected(monkeypatch)
    replay(source, [FEEDBACK.format(20)])
    packets = []
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.sendall.side_effect = packets.append
    monkeypatch.setattr(receiver.time, 'sleep', Mock(side_effect=OSError('test feed complete')))
    receiver._feed(source, conn)
    follower = Screen(name='iPad Agent', stream='127.0.0.1:1')
    follower._sock = object()  # Local transport stays open while the source is dead.
    chunks = iter([b''.join(packets), b''])
    follower._consume(lambda: next(chunks), health_records=True)
    diagnostics = follower.mirror_health()
    assert diagnostics['state'] == 'source_offline'
    assert diagnostics['source_session_connected'] is False
    assert diagnostics['source_offline_reason'] == 'client_feedback_timeout'
    assert 're-select iPad Agent in Screen Mirroring' in diagnostics['hint']
    assert not follower.live


@pytest.mark.parametrize('enabled', [False, True])
def test_zombie_restart_is_opt_in_for_embedded_screen_and_limited_across_children(monkeypatch, clock, enabled):
    screen = connected(monkeypatch, restart_on_source_offline=enabled)
    first = screen._proc
    replay(screen, [FEEDBACK.format(20)] * 10)
    assert first.terminate.call_count == int(enabled)
    # The supervisor has a new child, but the cooldown belongs to the receiver.
    second = screen._proc = Mock(pid=12346, poll=Mock(return_value=None))
    clock[0] += 59
    replay(screen, [FEEDBACK.format(21)])
    second.terminate.assert_not_called()
    clock[0] += 1
    replay(screen, [FEEDBACK.format(22)] * 10)
    assert second.terminate.call_count == int(enabled)
    clock[0] += 60
    replay(screen, [FEEDBACK.format(23)])
    assert second.terminate.call_count == int(enabled)  # Never re-signal the same child.


@pytest.mark.parametrize('setting,enabled', [(None, True), ('0', False), ('1', True)])
def test_standalone_receiver_defaults_zombie_restart_on(monkeypatch, tmp_path, setting, enabled):
    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    if setting is None:
        monkeypatch.delenv('UXPLAY_ZOMBIE_RESTART', raising=False)
    else:
        monkeypatch.setenv('UXPLAY_ZOMBIE_RESTART', setting)
    screen = Mock(_proc=None)
    factory = Mock(return_value=screen)
    monkeypatch.setattr(receiver, 'Screen', factory)
    monkeypatch.setattr(receiver, 'serve', Mock())
    monkeypatch.setattr(receiver.signal, 'signal', Mock())
    receiver.main()
    assert factory.call_args.kwargs['restart_on_source_offline'] is enabled


def test_health_rejects_contradictory_offline_reason():
    health = dict(version=1, process_running=True, frame_ready=False, peer_connected=True,
                  source_epoch='test', source_frame_age_s=None, source_offline_reason='ntp_no_route')
    with pytest.raises(ValueError, match='offline'):
        decode(record(health)[len(PREFIX):])


def test_expired_receiver_health_does_not_report_source_connected(monkeypatch, clock):
    screen = Screen(stream='127.0.0.1:1')
    screen._sock = object()
    screen._receiver_health = dict(version=1, process_running=True, frame_ready=True,
                                   peer_connected=True, source_epoch='test', source_frame_age_s=0)
    screen._receiver_health_at = clock[0] - 10
    assert screen.mirror_health()['source_session_connected'] is False


def test_status_distinguishes_offline_source_from_open_local_transport(monkeypatch, clock, tmp_path):
    import importlib
    from types import SimpleNamespace

    monkeypatch.setenv('HIDAGENT_DATA', str(tmp_path))
    api = importlib.import_module('hidagent.app')
    source = connected(monkeypatch)
    replay(source, INCIDENT)
    follower = Screen(name='iPad Agent', stream='127.0.0.1:1')
    follower._sock = object()
    health = {**source.receiver_health(), 'frame_ready': False}
    chunks = iter([record(health), b''])
    follower._consume(lambda: next(chunks), health_records=True)
    monkeypatch.setattr(api, 'screen', follower)
    monkeypatch.setattr(api, 'phone', SimpleNamespace(calibrated=False, target_info=lambda: {'aspect': .5}))
    monkeypatch.setattr(api, 'device', SimpleNamespace(connected=False, readiness=lambda: {'stable': False}))
    monkeypatch.setattr(api, 'ENGINE', 'llm')
    monkeypatch.setattr(api, 'agent', SimpleNamespace(running=False, task='', steps=[],
        trace=SimpleNamespace(dir=None), planner=SimpleNamespace(model='fake'), actor=SimpleNamespace(model='fake')))
    result = api.status()  # Direct read-only call, no ASGI lifespan or HTTP listener.
    diagnostics = result['mirror_diagnostics']
    assert not result['screen']
    assert diagnostics['receiver_stream_connected'] is True
    assert diagnostics['source_session_connected'] is False
    assert diagnostics['state'] == 'source_offline'
    assert diagnostics['hint'] == 're-select iPad Agent in Screen Mirroring'
