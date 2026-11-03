"""All supplied saved frames, native local OCR only, no device/model calls.

Post frames use saved current-run ACKs and independently located field boxes.
Pre/reset frames have no current focus authorization. No paired
fresh timestamps are available, so stills cannot prove blinking-caret focus.
"""
import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
import io

from hidagent.focus_local import verify_local_focus
from hidagent.local_text import LocalTextReader

SOURCES = ('focus-frames-20261030', 'speed-retest4-20261030',
           'speed-retest3-20261030', 'speed-retest2-20261030', 'tiktok-verify4-20261030')


async def replay(base, binary, output):
    reader = LocalTextReader([str(binary), '--serve'], timeout=30)
    records = []
    try:
        for source in SOURCES:
            root = base / source
            runs_file = root / 'runs.jsonl'
            runs = [json.loads(line) for line in runs_file.read_text().splitlines()] if runs_file.exists() else []
            paths = sorted((root / 'frames' if runs else root).glob('*.jpg'))
            for path in paths:
                run = next((r for r in runs if path.name.startswith(r['label']+'-')), None)
                app = run['app'] if run else ('TikTok' if source.startswith('focus-') else 'Unknown')
                frame = path.read_bytes()
                a = SimpleNamespace(screen=SimpleNamespace(frame=frame, updated=101.0),
                    phone=SimpleNamespace(target_info=lambda: {'device':'ipad', 'orientation':'landscape'}),
                    steps=[{'workflow':'app_search', 'phase':'app_ready', 'action':'wait', 'act_s':.1, 't':99},
                           {'workflow':'app_search', 'phase':'focus', 'action':'tap', 'act_s':.1, 't':100}],
                    query_text_reader=reader, trace=Mock())
                context_source = 'supplied_focus_evidence'
                field_source = None
                if runs:
                    a.steps = []
                    context_source = 'no_current_run_focus_context'
                    raw_path = root / 'raw' / (run['label']+'.json') if run else None
                    if raw_path and raw_path.exists() and path.name.endswith('-post.jpg'):
                        raw_steps = json.loads(raw_path.read_text()).get('steps', [])
                        a.steps = [s for s in raw_steps if s.get('kind') == 'step' and 'act_s' in s]
                        a.screen.updated = max((s.get('t', 0) for s in raw_steps), default=0)+.01
                        context_source = str(raw_path.relative_to(base))
                        # Reconstruct only the very same acknowledged field tap,
                        # using its preceding independent successful box verdict.
                        field = None
                        for step in raw_steps:
                            if step.get('purpose') == 'locate editable app search':
                                answers = step.get('answers', {})
                                target = answers.get('target', {})
                                field = (target.get('box') if answers.get('ready', {}).get('noul', 0) >= .99
                                    and answers.get('blocked', {}).get('noul', 1) <= .01
                                    and target.get('confidence', 0) >= .9 else None)
                            if (step.get('phase') == 'focus' and step.get('action') == 'tap'
                                    and 'act_s' in step and field
                                    and Image.open(io.BytesIO(frame)).size == (1646, 1080)):
                                step['_search_field_box'] = field
                                step['_search_field_size'] = [1646, 1080]
                                field_source = 'saved_independent_locate_verdict'
                proof = await verify_local_focus(a, {'app':app}, frame)
                traces = [call.kwargs for call in a.trace.write.call_args_list]
                record = {'source':source, 'frame':path.name, 'sha256':hashlib.sha256(frame).hexdigest(),
                    'app_context':app, 'context_source':context_source, 'field_source':field_source, 'detected':bool(proof),
                    'signal':proof['signal'] if proof else None,
                    'reason':None if proof else traces[-1]['reason']}
                records.append(record)
        output.write_text(''.join(json.dumps(r)+'\n' for r in records))
        summary = {source:dict(frames=sum(r['source']==source for r in records),
            detected=sum(r['source']==source and r['detected'] for r in records),
            misses=dict(Counter(r['reason'] for r in records if r['source']==source and not r['detected'])))
            for source in SOURCES}
        print(json.dumps(summary, indent=2))
        return records
    finally:
        await reader.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(replay(args.base, args.binary, args.output))
