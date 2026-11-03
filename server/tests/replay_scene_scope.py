"""Read saved pre/post pairs in place; never invent pre-verdict history.

Run from server with PYTHONPATH=$PWD and the evidence directory as argument.
This replays the scene gate, not the model verdict, device input or app success.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from hidagent.jev_agent import scene_changed
from hidagent.scene_scope import verdict_scene_changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    runs = [json.loads(line) for line in (args.evidence/'runs.jsonl').read_text().splitlines()]
    for run in runs:
        if run['app'] != 'TikTok':
            continue
        paths = [args.evidence/'frames'/f'{run["label"]}-{suffix}.jpg' for suffix in ('pre', 'post')]
        if not all(p.is_file() for p in paths):
            print(json.dumps({'label': run['label'], 'result': 'frame_pair_missing'}))
            continue
        before, after = [p.read_bytes() for p in paths]
        # Each run has only one pre and one post image. Neither a later post
        # nor a different run may stand in for two fresh pre-verdict frames.
        agent = SimpleNamespace(steps=[], shots={})
        scoped = verdict_scene_changed(agent, before, after, {'action': 'done'},
                                       scene_changed, foreground=True, workflow='keyboard')
        print(json.dumps({
            'label': run['label'], 'start': run['start'],
            'files': [p.name for p in paths],
            'sha256': [hashlib.sha256(frame).hexdigest() for frame in (before, after)],
            'sizes': [Image.open(io.BytesIO(frame)).size for frame in (before, after)],
            'before_decision': 'wait' if scene_changed(before, after) else 'keep_verdict',
            'after_decision': 'wait' if scoped else 'keep_verdict',
            'scope': 'pair_only_no_pre_verdict_motion_history',
            'limitation': 'Home-to-app transition' if run['start'] == 'A' else 'No three-frame pre-verdict sequence',
        }))


if __name__ == '__main__':
    main()
