"""Opt-in offline calibration, never collected by pytest.

Reads evidence images IN PLACE; emits scalar JSON only. No images or text OCR.
Usage: PYTHONPATH=$PWD python tests/measure_spotlight_frames.py /path/to/evidence/frames
"""
import json
import sys
from pathlib import Path
from hidagent.spotlight_launch import capsule_edge_score, local_check


def measure(folder):
    rows = []
    typed = {'d-tt-02-typed.jpg', 'd-yt-02-typed.jpg', 'd-rn-02-typed-cropped.jpg'}
    for path in sorted(Path(folder).glob('*.jpg')):
        frame = path.read_bytes()
        opened = 'spotlight' in path.name or path.name in typed
        group = 'open_typed' if path.name in typed else 'open_capsule' if opened else 'closed_reference'
        score = capsule_edge_score(frame)
        check = local_check(frame)
        rows.append({'name': path.name, 'group': group, 'score': round(score, 4),
                     'open_margin': round(abs(score)-12, 4),
                     'closed_margin': round(4-abs(score), 4), 'runtime_state': check['state']})
    summary = []
    for group in ('open_capsule', 'open_typed', 'closed_reference'):
        values = [r for r in rows if r['group'] == group]
        summary.append({'group': group, 'count': len(values),
                        **{key+'_range': [min(r[key] for r in values), max(r[key] for r in values)]
                           for key in ('score', 'open_margin', 'closed_margin')},
                        'states': {state: sum(r['runtime_state'] == state for r in values)
                                   for state in ('open', 'closed', 'unknown')}})
    return {'thresholds': {'open_abs_min': 12, 'closed_abs_max': 4},
            'summary': summary, 'rows': rows}


if __name__ == '__main__':
    print(json.dumps(measure(sys.argv[1]), indent=2))
