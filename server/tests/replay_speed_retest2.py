"""Offline native OCR replay. No device, server, model client, or network."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

from hidagent import app_query_text as current
from hidagent.local_text import LocalTextReader

ROOT = Path(__file__).parent
FRAMES = ROOT / 'fixtures/speed_retest2'


async def replay(module, command):
    rows = []
    reader = LocalTextReader([command, '--serve'], timeout=30)
    try:
        for case in json.loads((FRAMES / 'cases.json').read_text()):
            logs = []
            frame = (FRAMES / case['results_frame']).read_bytes()
            a = NS(phone=NS(target_info=lambda: {'device': 'ipad', 'orientation': 'landscape'}),
                   screen=NS(frame=frame), steps=case['steps'], query_text_reader=reader,
                   trace=NS(write=lambda *args, **kw: logs.append(kw)))
            goal = {'app': case['app'], 'query': case['query']}
            literal = await module.observe_landscape_query(a, goal, frame, results=True)
            context = module._query_input(a, goal, submitted=True) is not None
            verified = await module.verify_landscape_results(a, goal, frame)
            rows.append(dict(label=case['label'], literal=bool(literal), input_context=context,
                             verified_results=bool(verified), reasons=[x['reason'] for x in logs if 'reason' in x]))
        # Saved real residual-text frame: query contains futurehabits instead
        # of future. Neither caret handling nor confusables may hide the tail.
        frame = (ROOT / 'local_evidence_frames/r07-tt-A-post.jpg').read_bytes()
        a.screen.frame = frame
        rejected = await module.observe_landscape_query(a, {
            'app': 'TikTok', 'query': 'ielts speaking part 3 public libraries future'}, frame)
        return dict(results_frames=rows, literal_hits=sum(r['literal'] for r in rows),
                    results_hits=sum(r['verified_results'] for r in rows), total=len(rows),
                    futurehabits_rejected=rejected is None,
                    query_phase_frames='unavailable: raw records name frames but evidence package omits their pixels')
    finally:
        await reader.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ocr-bin', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    os.environ['LANDSCAPE_QUERY_OCR'] = '1'
    os.environ['LANDSCAPE_RESULTS_LOCAL'] = '1'
    spec = importlib.util.spec_from_file_location('hidagent._speed_retest2_baseline', args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    print(json.dumps({'before': await replay(baseline, args.ocr_bin),
                      'after': await replay(current, args.ocr_bin)}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
