"""Offline quality gate: 0 pass, 1 fail/pending, 2 invalid input. No API calls."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.errors import AppError
from src.quality_gate import assess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New JSON file; existing files are protected")
    args = parser.parse_args()
    try:
        result = assess(args.dataset, args.evaluation, args.policy,
                        review_path=args.review, baseline_path=args.baseline)
        # Exclusive creation also protects an input accidentally passed as output.
        with args.output.open("x", encoding="utf-8") as target:
            target.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": result["status"], "scope": result["scope"],
                          "narrative_review": result["narrative_review"]}))
        return 0 if result["status"] == "pass" else 1
    except (AppError, OSError) as exc:
        print(f"품질 판정 실패: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
