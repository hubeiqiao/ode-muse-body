"""Read saved frames in place and replay the real matcher, without device/API access.

Run from server: PYTHONPATH=$PWD python tests/replay_landscape_search_controls.py
  --evidence-root /path/to/saved/evidence --output /tmp/search-replay.json
Full frames, partial frames and reconstructed fixtures are reported separately.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from hidagent import app_controls
from test_landscape_search_controls import FIXTURES, TARGET, fixture_picture, frame_bytes

HERE = Path(__file__).resolve().parent / "landscape_search_evidence"


def replay(evidence_root):
    manifest = json.loads((HERE / "manifest.json").read_text())
    results = {"full_frames": [], "partial_frames": [], "reconstructed_fixtures": []}
    failures = []
    for record in manifest["frames"] + manifest["partial_frames"]:
        path = evidence_root / record["file"]
        frame = path.read_bytes()
        if hashlib.sha256(frame).hexdigest() != record["sha256"]:
            raise ValueError(f"Evidence hash changed: {record['file']}")
        observed = {}
        for app, _, control, _, _ in FIXTURES:
            hit = app_controls.public_search_control(frame, app, TARGET, control=control)
            if hit:
                observed[f"{app}/{control}"] = [hit["x"], hit["y"]]
        expected = record.get("expected", {})
        with Image.open(path) as picture:
            size = list(picture.size)
        row = {"file": record["file"], "size": size,
               "expected": expected, "observed": observed}
        section = "full_frames" if "screen" in record else "partial_frames"
        results[section].append(row)
        if observed != expected:
            failures.append(row)
    for record in manifest["reconstructed_closed_header_fixtures"]:
        case = next(case for case in FIXTURES
                    if case[0] == record["app"] and case[2] == record["control"])
        picture = fixture_picture(case)
        with Image.open(evidence_root / record["strip"]) as strip:
            assert strip.size == (1646, 220)
            picture.paste(strip, (0, 0))
        hit = app_controls.public_search_control(
            frame_bytes(picture), case[0], TARGET, control=case[2])
        ok = bool(hit and (hit["x"], hit["y"]) == case[3])
        row = {**record, "expected": list(case[3]), "matched": ok}
        results["reconstructed_fixtures"].append(row)
        if not ok:
            failures.append(row)
    summary = []
    for app, _, control, center, _ in FIXTURES:
        key = f"{app}/{control}"
        positive = [row for row in results["full_frames"] if key in row["expected"]]
        negative = [row for row in results["full_frames"] if key not in row["expected"]]
        summary.append({"app": app, "control": control, "center": list(center),
                        "positive_matches": sum(key in row["observed"] for row in positive),
                        "positive_frames": len(positive),
                        "false_matches": sum(key in row["observed"] for row in negative),
                        "negative_frames": len(negative)})
    return {"summary": summary, **results, "failures": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.evidence_root)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    print(f"Full frames: {len(result['full_frames'])}; partial frames: {len(result['partial_frames'])}; "
          f"reconstructed fixtures: {len(result['reconstructed_fixtures'])}; mismatches: {len(result['failures'])}")
    return int(bool(result["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
