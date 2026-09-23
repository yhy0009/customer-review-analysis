"""Evaluate fixed synthetic reviews; --live permits at most N+101 requests (100 evidence batches + summary), no retries."""

import argparse
from collections import Counter
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config, load_env_file, resolve_project_path
from src.errors import AppError
from src.evaluation import load_dataset, run_evaluation
from src.models import AnalysisOptions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="Validate fixtures without API/config/DB access")
    mode.add_argument("--live", action="store_true", help="Call the configured provider with the evaluation fixtures")
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/reviews.v1.json"))
    parser.add_argument("--output", type=Path, help="New directory for evaluation.json, isolated DB and reports")
    parser.add_argument("--timeout-seconds", type=int, help="Explicit per-request timeout for this evaluation only")
    args = parser.parse_args()
    if args.live and args.output is None:
        parser.error("--live에는 새 --output 디렉터리가 필요합니다.")
    try:
        dataset_path = resolve_project_path(args.dataset)
        if args.validate_only:
            data = load_dataset(dataset_path)
            print(json.dumps({"version": data["version"], "cases": len(data["cases"]), "sha256": data["sha256"],
                              "language_counts": dict(Counter(case.get("language", "unspecified") for case in data["cases"]))}))
            return 0
        load_env_file()
        options = AnalysisOptions(**load_config("config/config.json")["ai"])
        if args.timeout_seconds is not None:
            options = replace(options, timeout_seconds=args.timeout_seconds)
        result = run_evaluation(dataset_path, resolve_project_path(args.output), options,
                                progress=lambda message: print(message, flush=True))
        print(json.dumps({"status": result["status"], "metrics": result["metrics"], "latency": result["latency"],
                          "metrics_by_language": result["metrics_by_language"],
                          "structural_checks_passed": result["structural_checks_passed"],
                          "narrative_review": result["narrative_review"]}, ensure_ascii=False, indent=2))
        # Label mismatches and narrative review are quality results, not execution errors.
        return 0 if result["structural_checks_passed"] else 1
    except ImportError:
        print("평가 실행에 필요한 AI 패키지가 없습니다. requirements.txt의 의존성을 확인하세요.", file=sys.stderr)
        return 1
    except AppError as exc:
        print(f"평가 실행 실패: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
