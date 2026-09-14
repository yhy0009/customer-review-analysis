"""Compare archived v2 with current evidence-based insight generation."""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_provider import provider_for
from src.config import load_config, load_env_file, resolve_project_path
from src.errors import AppError, ValidationError
from src.evaluation import _MeasuredProvider, _save, _unique_object, load_dataset
from src.insight_extractor import AIInsightExtractor, INSIGHT_SCHEMA, PROMPT_VERSION, SYSTEM_PROMPT, _keywords, _parse_response
from src.insight_evidence import EVIDENCE_PROMPT
from src.models import AnalysisOptions, AnalysisResult, CleanReview, InsightResult, ReviewDetail, ReviewFilter, Sentiment


ARCHIVED_PROMPT = Path(__file__).resolve().parents[1] / "evaluation/prompts/review-insights-v2.txt"


def compare(dataset_path, saved_path, output, options, *, provider=None, case_ids=None, current_only=False):
    """Three calls at most: archived v2 once, current evidence + narrative twice.

    Only accepts complete successful evaluation rows matching the frozen dataset.
    No DB is opened, no classification is rerun and no source file is overwritten.
    """
    dataset = load_dataset(dataset_path)
    try:
        raw = saved_path.read_bytes()
        saved = json.loads(raw, object_pairs_hook=_unique_object)
        if saved["dataset_sha256"] != dataset["sha256"] or saved["status"] != "completed":
            raise ValueError
        if len(saved["rows"]) != len(dataset["cases"]):
            raise ValueError
        now = datetime.now(timezone.utc)
        details = []
        for i, (case, row) in enumerate(zip(dataset["cases"], saved["rows"]), 1):
            if any(row[k] != v for k, v in case.items()) or row["status"] != "ok":
                raise ValueError
            details.append(ReviewDetail(
                CleanReview(id=i, product_name=case["product_name"], rating=case["rating"],
                            review_text=case["review_text"], review_date=date(2026, 9, 1), cleaned_at=now),
                AnalysisResult(review_id=i, sentiment=Sentiment(row["predicted"]),
                               confidence=row["confidence"], keywords=row["keywords"],
                               analyzed_at=now, provider=saved["provider"], model=row["model"]),
            ))
        prompts = {"review-insights-v2": ARCHIVED_PROMPT.read_text(encoding="utf-8"),
                   PROMPT_VERSION: SYSTEM_PROMPT}
        if current_only:
            prompts = {PROMPT_VERSION: SYSTEM_PROMPT}
        selected_ids = [c["id"] for c in dataset["cases"]]
        if case_ids is not None:
            if not case_ids or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= set(selected_ids):
                raise ValueError
            details = [d for c, d in zip(dataset["cases"], details) if c["id"] in case_ids]
            selected_ids = [c for c in selected_ids if c in case_ids]
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise ValidationError("고정 데이터와 일치하는 성공한 평가 결과가 필요합니다.") from None
    options = replace(options, max_retries=0, prompt_version=None)
    measured = _MeasuredProvider(provider if provider is not None else provider_for(options, max_completion_tokens=8192))
    output.mkdir(parents=True, exist_ok=False)
    result = {"dataset_sha256": dataset["sha256"], "source_sha256": hashlib.sha256(raw).hexdigest(),
              "case_ids": selected_ids,
              "provider": options.provider, "requested_model": options.model,
              "mode": "live" if provider is None else "injected", "max_retries": 0,
              "timeout_seconds": options.timeout_seconds, "reasoning_effort": options.reasoning_effort,
              "started_at": now.isoformat(), "narrative_review": "pending", "runs": [],
              "calls": measured.calls}
    checkpoint = output / "comparison.json"
    _save(checkpoint, result)
    for version, prompt in prompts.items():
        entry = {"prompt_version": version, "system_prompt": prompt, "status": "running"}
        result["runs"].append(entry)

        class ReplayProvider:
            def complete(self, messages, schema, call_options):
                # Compare the original source input, before the new evidence pass.
                entry.setdefault("input_sha256", hashlib.sha256(messages[1]["content"].encode()).hexdigest())
                return measured.complete(messages, schema, call_options)

        try:
            if version == PROMPT_VERSION:
                entry["evidence_prompt"] = EVIDENCE_PROMPT
                insight = AIInsightExtractor(options, ReplayProvider()).extract_insights(details, ReviewFilter())
            else:
                positive = _keywords(details, Sentiment.POSITIVE)
                negative = _keywords(details, Sentiment.NEGATIVE)
                content = json.dumps({"review_count": len(details),
                    "positive_keywords": [asdict(k) for k in positive],
                    "negative_keywords": [asdict(k) for k in negative],
                    "reviews": [{"product_name": d.review.product_name, "rating": d.review.rating,
                        "review_text": d.review.review_text, "sentiment": d.analysis.sentiment.value} for d in details]}, ensure_ascii=False)
                response = ReplayProvider().complete([{"role": "system", "content": prompt},
                    {"role": "user", "content": content}], INSIGHT_SCHEMA, options)
                insight = InsightResult(ReviewFilter(), len(details), datetime.now(timezone.utc),
                    positive_keywords=positive, negative_keywords=negative, **_parse_response(response.content))
            entry.update(status="ok", insight=asdict(insight))
        except AppError as exc:
            entry.update(status="error", error_type=type(exc).__name__)
        except KeyboardInterrupt:
            entry.update(status="interrupted")
            raise
        finally:
            _save(checkpoint, result)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["same_input"] = None if current_only else len({r.get("input_sha256") for r in result["runs"]}) == 1
    result["completed"] = all(r["status"] == "ok" for r in result["runs"]) and result["same_input"] is not False
    _save(checkpoint, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--saved-evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; contains comparison.json")
    parser.add_argument("--case-id", action="append", help="Select specific frozen case IDs; repeat for multiple cases")
    parser.add_argument("--timeout-seconds", type=int, help="Explicit per-request timeout for this evaluation only")
    parser.add_argument("--current-only", action="store_true", help="Replay current pipeline only; at most two requests")
    args = parser.parse_args()
    try:
        load_env_file()
        options = AnalysisOptions(**load_config("config/config.json")["ai"])
        if args.timeout_seconds is not None:
            options = replace(options, timeout_seconds=args.timeout_seconds)
        result = compare(resolve_project_path(args.dataset), resolve_project_path(args.saved_evaluation),
                         resolve_project_path(args.output), options,
                         case_ids=args.case_id, current_only=args.current_only)
        print(json.dumps({"completed": result["completed"], "same_input": result["same_input"],
                          "calls": len(result["calls"]), "narrative_review": result["narrative_review"]}))
        return 0 if result["completed"] else 1
    except (AppError, OSError) as exc:
        print(f"비교 실행 실패: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
