"""Replay saved public frames in place; never connect to a device or model.

Native OCR failures and ideal, manually annotated mock rows are separate.
Run from server with PYTHONPATH=$PWD and the existing Python environment.
"""
import asyncio
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import types
from unittest.mock import AsyncMock

from pytest import MonkeyPatch
from hidagent import app_query_text as ocr
from hidagent import app_search
from hidagent.local_text import LocalTextReader, LocalTextError
from test_landscape_query_ocr import agent, observation, row

BASE = Path('runtime/local-evidence')
SPEED = BASE.parent / 'speed-ab-20261030'
ROOT = Path(__file__).resolve().parents[2]
BINARY = str(ROOT / 'work/native-probe/vision-ocr')
OUT = ROOT / 'evidence/landscape-ocr'
COMMIT = '041cdcc073fe39e18c44c66967773f28a82ab172'


def ground_truth(name, runs):
    """Checked saved header strips, not model receipts or OCR-derived labels.

    Unassociated Home/navigation frames are tested against a sentinel query,
    so empty means no requested query rather than no text anywhere on screen.
    """
    label = name.removesuffix('-post.jpg').removesuffix('-pre.jpg')
    current = next((r for r in runs if r['label'] == label), None)
    if current is not None and not label.startswith('r'):
        if name.endswith('-post.jpg') and current.get('status') == 'completed':
            return current['query']
        previous = [r for r in runs[:runs.index(current)]
                    if r['app'] == current['app'] and r.get('status') == 'completed']
        return previous[-1]['query'] if previous else ''
    if name.endswith('-post.jpg') and name[:3] in ('r01', 'r02', 'r05', 'r06', 'r09', 'r10'):
        return next(r['query'] for r in runs if name == r['label']+'-post.jpg')
    previous = {'r02-yt-B-pre.jpg': 'r01-yt-A', 'r06-rn-B-pre.jpg': 'r05-rn-A',
                'r10-yt-B-pre.jpg': 'r09-yt-A'}
    if name in previous:
        return next(r['query'] for r in runs if r['label'] == previous[name])
    if name in ('r11-rn-A-post.jpg', 'r12-rn-A-post.jpg'):
        return 'ielts speaking part 2 describe a helpful neighboureducation system discussion'
    return ''  # Home or TikTok feed, no search-field value.


class RecordedNativeRead:
    """Record the actual production read, including rows rejected by policy."""
    def __init__(self, reader):
        self.reader = reader
        self.observation = None
        self.error = None
        self.attempts = []

    async def read(self, frame, *, mode):
        try:
            self.observation = await self.reader.read(frame, mode=mode)
            self.attempts.append(dict(observation=self.observation,
                                      crop_sha256=hashlib.sha256(frame).hexdigest()))
            return self.observation
        except LocalTextError as exc:
            self.error = f'{exc}; cause={exc.__cause__!r}'
            self.attempts.append(dict(error=self.error, crop_sha256=hashlib.sha256(frame).hexdigest()))
            raise


async def frame_replay(*, base=BASE, command=None, out=OUT, timeout=30):
    runs = [json.loads(line) for line in (base/'runs.jsonl').read_text().splitlines()]
    paths = sorted((base/'frames').glob('*.jpg'))
    if not paths:
        raise ValueError(f'No baseline frames in {base}')
    out.mkdir(parents=True, exist_ok=True)
    native = LocalTextReader(command if command is not None else [BINARY, '--serve'], timeout=timeout)
    outputs = []
    try:
        with MonkeyPatch.context() as patch:
            patch.setenv('LANDSCAPE_QUERY_OCR', '1')
            for path in paths:
                label = path.name.removesuffix('-post.jpg').removesuffix('-pre.jpg')
                run = next((r for r in runs if r['label'] == label), None)
                if run is None and 'recover' in path.name:
                    run = next((r for r in runs if r['label'].startswith(path.name[:3])), None)
                if run is None:
                    run = dict(app='YouTube', query='offline negative sentinel query')
                goal = {'app': run['app'], 'query': run['query']}
                frame = path.read_bytes()
                truth = ground_truth(path.name, runs)
                a = agent(patch)
                a.screen.frame = frame
                # Exactly the production crop/read path, with a recording
                # wrapper around the persistent real worker, not mock rows.
                recorded = RecordedNativeRead(native)
                a.query_text_reader = recorded
                native_result = await ocr.observe_landscape_query(a, goal, frame)
                native_observation, native_error = recorded.observation, recorded.error
                # Ideal query-only recognizer, confidence/box are mocked. This
                # does not estimate real OCR accuracy or adjacent-control recall.
                mock_rows = [row(truth)] if truth else []
                a.query_text_reader = types.SimpleNamespace(read=AsyncMock(return_value=observation(mock_rows)))
                mock_result = await ocr.observe_landscape_query(a, goal, frame)
                exact = bool(truth and ocr._normalized_query(truth) == ocr._normalized_query(goal['query']))
                native_text = [r['text'] for r in native_observation['rows']] if native_observation else []
                print(json.dumps(dict(frame=path.name, ground_truth=truth, native_text=native_text,
                    native_error=native_error, native_local_accept=bool(native_result)), ensure_ascii=False), flush=True)
                outputs.append(dict(frame=str(path.relative_to(base)), frame_sha256=hashlib.sha256(frame).hexdigest(),
                    app=goal['app'], requested_query=goal['query'], ground_truth=truth, ground_truth_exact=exact,
                    native_observation=native_observation, native_text=native_text, native_error=native_error,
                    native_attempts=recorded.attempts, caret_masked=bool(native_result and native_result['caret_masked']),
                    native_local_accept=bool(native_result), mock_rows=mock_rows,
                    mock_local_accept=bool(mock_result), mock_false_accept=bool(mock_result) and not exact))
    finally:
        await native.close()
    (out/'frames.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in outputs))
    summary = dict(frames=len(outputs), exact_ground_truth=sum(r['ground_truth_exact'] for r in outputs),
        native_successful_observations=sum(r['native_observation'] is not None for r in outputs),
        native_accepts=sum(r['native_local_accept'] for r in outputs),
        native_caret_masked_accepts=sum(r['caret_masked'] for r in outputs),
        native_false_accepts=sum(r['native_local_accept'] and not r['ground_truth_exact'] for r in outputs),
        mock_accepts=sum(r['mock_local_accept'] for r in outputs),
        mock_false_accepts=sum(r['mock_false_accept'] for r in outputs),
        limitations='Native rows are actual local --serve output; failures are recorded per frame. Query-only mock rows are human annotations, not OCR measurements. Confidence is not an accuracy estimate.')
    (out/'offline-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    assert summary['mock_false_accepts'] == summary['native_false_accepts'] == 0, 'Replay has false accepts'
    if not summary['native_successful_observations']:
        raise RuntimeError('No successful native OCR observations; inspect frames.jsonl and run with macOS Vision access.')
    return summary


def flag_off_parity():
    source = subprocess.run(['git', 'show', COMMIT+':server/hidagent/app_search.py'], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout
    baseline = types.ModuleType('hidagent._baseline_app_search')
    baseline.__package__ = 'hidagent'
    exec(compile(source, '<base-commit-app-search>', 'exec'), baseline.__dict__)
    cases = []
    for flag in ('unset', '0'):
        for app in ('YouTube', 'TikTok', 'rednote', 'Bilibili', 'Instagram'):
            for continuity in ('0', '1'):
                for submitted in (False, True):
                    with MonkeyPatch.context() as patch:
                        before, after = agent(patch, submitted=submitted), agent(patch, submitted=submitted)
                        if flag == 'unset': patch.delenv('LANDSCAPE_QUERY_OCR')
                        else: patch.setenv('LANDSCAPE_QUERY_OCR', flag)
                        patch.setenv('HID_SEARCH_INPUT_CONTINUITY', continuity)
                        goal = {'app': app, 'query': 'IELTS speaking'}
                        for a in (before, after):
                            a.client.evaluate.return_value['answers']['literal'] = {'noul': 1}
                            a.client.evaluate.return_value['answers']['editing_mode'] = {'choice': 'query_editor', 'confidence': 1}
                        old = asyncio.run(baseline.next_search_actions(before, goal))
                        new = asyncio.run(app_search.next_search_actions(after, goal))
                        assert old == new
                        assert before.client.evaluate.call_args_list == after.client.evaluate.call_args_list
                        assert before.trace.write.call_args_list == after.trace.write.call_args_list
                        before.query_text_reader.read.assert_not_called()
                        after.query_text_reader.read.assert_not_called()
                        assert not hasattr(after, '_landscape_results_proof')
                        cases.append(dict(flag=flag, app=app, input_continuity=continuity,
                                          phase='results' if submitted else 'literal', byte_identical=True))
    (OUT/'flag-off-parity.json').write_text(json.dumps(cases, indent=2)+'\n')
    return len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, action='append', help='Repeat for multiple datasets; defaults to baseline and speed A/B.')
    parser.add_argument('--binary', default=os.environ.get('LOCAL_OCR_BIN', BINARY))
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--timeout', type=float, default=30, help='Offline native read timeout in seconds; production timeout is unchanged.')
    args = parser.parse_args()
    OUT = args.out
    OUT.mkdir(parents=True, exist_ok=True)
    failed = False
    for base in args.base or [BASE, SPEED]:
        try:
            summary = asyncio.run(frame_replay(base=base, command=[args.binary, '--serve'], out=OUT/base.name, timeout=args.timeout))
            print(json.dumps(summary, indent=2))
        except (RuntimeError, AssertionError) as exc:
            print(f'{base.name}: VALIDATION FAILED: {exc}', flush=True)
            failed = True
    print('flag_off_byte_identical_cases:', flag_off_parity())
    raise SystemExit(1 if failed else 0)
