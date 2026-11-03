"""Offline pre-demo report. No socket, board probe, process or model invocation.

Use --snapshot with an exported status JSON, or omit it for exact setup fixes.
The normal voice ipad_demo(action='preflight') checks current in-process facts.
"""
import argparse
import json
from pathlib import Path
from .ipad_demo_flows import preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, help='Saved status JSON, never a URL')
    args = parser.parse_args(argv)
    state = {}
    if args.snapshot:
        state = json.loads(args.snapshot.read_text())
        if not isinstance(state, dict): parser.error('Snapshot must be a JSON object')
    report = preflight(state)
    report.update(snapshot_only=True, live_device_checked=False,
                  instruction='Saved status is not live proof. Run the voice preflight on the configured iPad console before a demo.')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
