"""Exercise the validator's real subprocess protocol without macOS Vision."""
import asyncio
import json
import sys

import pytest

import validate_landscape_ocr_offline as validator
from test_landscape_query_ocr import frame


def test_default_worker_belongs_to_this_worktree():
    assert validator.BINARY == str(validator.ROOT / 'work/native-probe/vision-ocr')


def baseline(tmp_path):
    base = tmp_path / 'baseline'
    (base / 'frames').mkdir(parents=True)
    (base / 'runs.jsonl').write_text(json.dumps({
        'label': 'r01-yt-A', 'app': 'YouTube', 'query': 'ielts speaking'}) + '\n')
    (base / 'frames/r01-yt-A-post.jpg').write_bytes(frame())
    return base


def test_native_replay_uses_serve_fast_protocol_and_reports_text(tmp_path, capsys):
    worker = tmp_path / 'worker.py'
    worker.write_text('''import sys, json, base64, io
from PIL import Image
assert sys.argv[1:] == ['--serve']
for line in sys.stdin:
    request = json.loads(line)
    assert request['mode'] == 'fast'
    assert Image.open(io.BytesIO(base64.b64decode(request['image_base64']))).size == (3292, 196)
    print(json.dumps({'source': 'local_vision_ocr', 'is_accessibility_tree': False,
        'mode': 'fast', 'rows': [{'text': 'lelts speaking', 'confidence': .5,
        'box': {'left': .05, 'top': .25, 'right': .4, 'bottom': .65}}]}), flush=True)
''')
    out = tmp_path / 'out'
    summary = asyncio.run(validator.frame_replay(
        base=baseline(tmp_path), command=[sys.executable, str(worker), '--serve'], out=out, timeout=30))
    assert summary['native_successful_observations'] == 1
    assert summary['native_accepts'] == 1
    record = json.loads((out / 'frames.jsonl').read_text())
    assert record['native_text'] == ['lelts speaking']
    assert record['ground_truth'] == 'ielts speaking'
    assert record['native_observation']['cache_hit'] is False
    assert record['mock_rows'][0]['confidence'] == .5
    printed = capsys.readouterr().out
    assert 'lelts speaking' in printed and 'ielts speaking' in printed


def test_native_failure_writes_evidence_and_fails_validation(tmp_path):
    worker = tmp_path / 'bad-worker.py'
    worker.write_text('import sys\nfor line in sys.stdin: print(\'{"error":"local OCR failed"}\', flush=True)\n')
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError, match='No successful native OCR observations'):
        asyncio.run(validator.frame_replay(
            base=baseline(tmp_path), command=[sys.executable, str(worker), '--serve'], out=out))
    assert json.loads((out / 'offline-summary.json').read_text())['native_successful_observations'] == 0


def test_empty_baseline_is_an_error(tmp_path):
    base = baseline(tmp_path)
    (base / 'frames/r01-yt-A-post.jpg').unlink()
    with pytest.raises(ValueError, match='No baseline frames'):
        asyncio.run(validator.frame_replay(base=base, out=tmp_path / 'out'))


def test_speed_ab_annotations_do_not_assume_rejected_run_typed_its_query():
    runs = [dict(label='b1-rn-B', app='rednote', query='music and hobbies tips', status='completed'),
            dict(label='b2-rn-B', app='rednote', query='a new query', status='not_ready'),
            dict(label='b3-rn-B', app='rednote', query='fluency daily routine', status='completed')]
    assert validator.ground_truth('b2-rn-B-post.jpg', runs) == 'music and hobbies tips'
    assert validator.ground_truth('b3-rn-B-pre.jpg', runs) == 'music and hobbies tips'
    assert validator.ground_truth('b3-rn-B-post.jpg', runs) == 'fluency daily routine'


def test_speed_replay_includes_unrelated_frames_as_negative_controls(tmp_path):
    base = baseline(tmp_path)
    (base/'runs.jsonl').write_text(json.dumps(dict(label='a1-yt-B', app='YouTube', query='ielts speaking', status='completed'))+'\n')
    (base/'frames/r01-yt-A-post.jpg').rename(base/'frames/a1-yt-B-post.jpg')
    (base/'frames/final-home.jpg').write_bytes(frame(typed=False))
    worker = tmp_path/'worker.py'
    worker.write_text('''import sys,json
for line in sys.stdin:
 print(json.dumps({'source':'local_vision_ocr','is_accessibility_tree':False,'mode':'fast','rows':[]}),flush=True)
''')
    summary = asyncio.run(validator.frame_replay(base=base, command=[sys.executable, str(worker), '--serve'], out=tmp_path/'out'))
    assert summary['frames'] == 2
    assert summary['exact_ground_truth'] == 1
    assert summary['native_false_accepts'] == summary['mock_false_accepts'] == 0


def test_native_false_accept_is_a_validation_failure(tmp_path):
    base = baseline(tmp_path)
    # A pre frame is annotated empty. A recognizer hallucinating the requested
    # literal must not produce a successful validation report.
    (base/'frames/r01-yt-A-post.jpg').rename(base/'frames/r01-yt-A-pre.jpg')
    worker = tmp_path/'worker.py'
    worker.write_text('''import sys,json
for line in sys.stdin:
 print(json.dumps({'source':'local_vision_ocr','is_accessibility_tree':False,'mode':'fast','rows':[{'text':'ielts speaking','confidence':.5,'box':{'left':.05,'top':.25,'right':.4,'bottom':.65}}]}),flush=True)
''')
    with pytest.raises(AssertionError, match='false accepts'):
        asyncio.run(validator.frame_replay(base=base, command=[sys.executable, str(worker), '--serve'], out=tmp_path/'out'))
    assert json.loads((tmp_path/'out/offline-summary.json').read_text())['native_false_accepts'] == 1
