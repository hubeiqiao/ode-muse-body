#!/usr/bin/env python3
"""Explicit, read-only probe: sends only the supplied screenshot to official TypeSafe."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from hidagent.decision import DecisionUnavailable, Jev, read_key_file

async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--key-file', help='Optional local dotenv reference; otherwise use JEV_KEY_FILE')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    client = Jev(read_key_file(args.key_file)) if args.key_file else Jev.from_env()
    try:
        result = {"accepted": True, **await client.locate(args.image.read_bytes(), args.target)}
    except DecisionUnavailable as error:
        result = {"accepted": False, "reason": str(error), "decision": error.result}
    finally:
        await client.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if not result["accepted"]:
        raise SystemExit(2)

if __name__ == '__main__':
    asyncio.run(main())
