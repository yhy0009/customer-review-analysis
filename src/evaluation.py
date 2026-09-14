"""Local development evaluation; labels never enter provider requests."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from src.ai_provider import AnalysisProvider, provider_for
from src.analysis_service import AnalysisService
from src.analyzer import BatchReviewAnalyzer, PROMPT_VERSION as ANALYSIS_PROMPT_VERSION
from src.errors import AIProviderError, AppError, OutputError, ValidationError
from src.insight_extractor import AIInsightExtractor, PROMPT_VERSION as INSIGHT_PROMPT_VERSION
from src.insight_service import InsightService
from src.insight_evidence import parse_evidence
from src.models import (
    AnalysisOptions, AnalyzeRequest, AnalyzeTarget, CleanReview, DuplicatePolicy,
    ExtractRequest, RawReview, ReportFormat, ReviewFilter, Sentiment,
)
from src.reporter import FileReportGenerator
from src.storage import SQLiteReviewRepository


_LABELS = [s.value for s in Sentiment]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def load_dataset(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        dataset = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(dataset, dict) or set(dataset) != {"version", "provenance", "cases"}:
            raise ValueError
        if any(not isinstance(dataset[k], str) or not dataset[k].strip() for k in ("version", "provenance")):
            raise ValueError
        cases = dataset["cases"]
        if not isinstance(cases, list) or not cases:
            raise ValueError
        ids = set()
        for case in cases:
            if not isinstance(case, dict) or set(case) != {
                "id", "category", "rating", "product_name", "review_text", "expected", "reason",
            }:
                raise ValueError
            for field in ("id", "category", "product_name", "review_text", "reason"):
                if not isinstance(case[field], str) or not case[field].strip():
                    raise ValueError
            if case["id"] in ids or case["expected"] not in _LABELS:
                raise ValueError
            ids.add(case["id"])
            if type(case["rating"]) is not int or not 1 <= case["rating"] <= 5:
                raise ValueError
        dataset["sha256"] = hashlib.sha256(raw).hexdigest()
        return dataset
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValidationError("평가 데이터의 파일 또는 형식이 올바르지 않습니다.") from None


def classification_metrics(rows: list[dict]) -> dict:
    """Errors and unattempted cases count as misses in overall accuracy/recall."""
    matrix = {s: {p: 0 for p in [*_LABELS, "error", "not_run"]} for s in _LABELS}
    for row in rows:
        matrix[row["expected"]][row["predicted"] or row["status"]] += 1
    correct = sum(matrix[s][s] for s in _LABELS)
    valid = sum(matrix[s][p] for s in _LABELS for p in _LABELS)
    per_class = {}
    for label in _LABELS:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted = sum(matrix[s][label] for s in _LABELS)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        per_class[label] = {"support": support, "precision": precision, "recall": recall,
                            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    return {"total": len(rows), "valid": valid, "correct": correct,
            "errors": sum(r["status"] == "error" for r in rows),
            "not_run": sum(r["status"] == "not_run" for r in rows),
            "accuracy_all": correct / len(rows) if rows else None,
            "accuracy_valid": correct / valid if valid else None,
            "macro_f1": sum(v["f1"] for v in per_class.values()) / len(_LABELS),
            "per_class": per_class, "confusion_matrix": matrix}


def _save(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    except OSError:
        raise OutputError("평가 결과 파일을 저장할 수 없습니다.") from None


def _statistics_json(statistics) -> dict:
    data = asdict(statistics)
    data["daily_sentiment_counts"] = {day.isoformat(): counts for day, counts in data["daily_sentiment_counts"].items()}
    return data


class _MeasuredProvider:
    def __init__(self, provider: AnalysisProvider):
        self.provider = provider
        self.calls = []

    def complete(self, messages, schema, options):
        started = time.monotonic()
        entry = {"stage": "analysis" if "sentiment" in schema["properties"] else (
                     "evidence" if "reviews" in schema["properties"] else "insight"),
                 "status": "error"}
        try:
            response = self.provider.complete(messages, schema, options)
            entry.update(status="response_received", model=response.model)
            if entry["stage"] == "evidence":
                try:
                    entry["evidence"] = parse_evidence(response.content, json.loads(messages[1]["content"])["reviews"])
                except AIProviderError:
                    entry["evidence_valid"] = False
                    # Evaluation uses synthetic fixtures. Preserve rejected model
                    # evidence for diagnosis, never transport exception bodies.
                    rejected = response.content
                    if options.api_key:
                        rejected = rejected.replace(options.api_key, "[redacted]")
                    entry["rejected_evidence_response"] = rejected
            return response
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 4)
            self.calls.append(entry)


def run_evaluation(
    dataset_path: Path, output: Path, options: AnalysisOptions, *,
    provider: AnalysisProvider | None = None, progress: Callable[[str], None] = lambda message: None,
) -> dict:
    """Create an exclusive run directory; persist only synthetic fixtures and safe metadata.

    At most N+2 provider calls: retries are disabled and skip verification makes no calls.
    Output directory must not exist, protecting both prior runs and application DBs.
    """
    dataset = load_dataset(dataset_path)
    options = replace(options, max_retries=0, prompt_version=None)
    measured = _MeasuredProvider(provider if provider is not None else provider_for(options, max_completion_tokens=8192))
    try:
        output = output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    except OSError:
        raise OutputError("평가 출력은 새 디렉터리여야 합니다.") from None
    rows = [dict(case, predicted=None, status="not_run") for case in dataset["cases"]]
    result = {"dataset_version": dataset["version"], "dataset_sha256": dataset["sha256"],
              "label_provenance": dataset["provenance"], "mode": "live" if provider is None else "injected",
              "provider": options.provider, "requested_model": options.model,
              "reasoning_effort": options.reasoning_effort, "timeout_seconds": options.timeout_seconds,
              "max_retries": 0, "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
              "insight_prompt_version": INSIGHT_PROMPT_VERSION,
              "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "rows": rows, "calls": measured.calls, "checks": {}, "narrative_review": "pending",
              "errors": []}
    checkpoint = output / "evaluation.json"
    _save(checkpoint, result)
    try:
        with SQLiteReviewRepository(output / "reviews.sqlite") as repository:
            now = datetime.now(timezone.utc)
            repository.save_raw_reviews([RawReview(source_review_id=c["id"], product_name=c["product_name"],
                rating=c["rating"], review_text=c["review_text"], review_date="2026-09-01")
                for c in rows], DuplicatePolicy.SKIP)
            repository.save_clean_reviews([CleanReview(id=i, source_review_id=c["id"],
                product_name=c["product_name"], rating=c["rating"], review_text=c["review_text"],
                review_date=date(2026, 9, 1), cleaned_at=now) for i, c in enumerate(rows, 1)], DuplicatePolicy.SKIP)
            analyzer = BatchReviewAnalyzer(repository, measured)
            service = AnalysisService(repository, analyzer, options)
            for i, row in enumerate(rows, 1):
                row["status"] = "error"  # An attempted but aborted request is not an unattempted row.
                batch = service.analyze_reviews(AnalyzeRequest(AnalyzeTarget.REVIEW_ID, review_id=i))
                detail = repository.get_review(i)
                row["status"] = "ok" if batch.succeeded == 1 else "error"
                if detail.analysis is not None:
                    analysis = detail.analysis
                    row.update(predicted=analysis.sentiment.value, summary=analysis.summary,
                               keywords=analysis.keywords, confidence=analysis.confidence, model=analysis.model)
                progress(f"분석 {i}/{len(rows)}: {row['status']}")
                _save(checkpoint, result)
            stats = repository.get_statistics()
            result["checks"]["all_analyses_saved"] = stats.analyzed_reviews == len(rows)
            # Re-running only successful rows verifies idempotence without retrying failed cases.
            successful = [repository.get_review(i).review for i, row in enumerate(rows, 1) if row["status"] == "ok"]
            call_count = len(measured.calls)
            skipped = analyzer.analyze_reviews(successful, options)
            result["checks"]["repeat_skips_without_api"] = skipped.skipped == len(successful) and len(measured.calls) == call_count
            before = [repository.get_review(i) for i in range(1, len(rows) + 1)]
            insight = None
            if stats.analyzed_reviews:
                progress("인사이트 요약 요청 중")
                try:
                    insight = InsightService(repository, AIInsightExtractor(options, measured),
                        snapshot=repository.read_snapshot).extract_insights(ExtractRequest(ReviewFilter()))
                except AIProviderError:
                    result["errors"].append({"stage": "insight", "type": "AIProviderError"})
            result["checks"]["insight_generated"] = insight is not None
            result["checks"]["same_scope_keyword_counts"] = insight is not None and (
                insight.review_count == stats.analyzed_reviews and insight.positive_keywords == stats.top_positive_keywords
                and insight.negative_keywords == stats.top_negative_keywords)
            result["insight"] = asdict(insight) if insight is not None else None
            result["statistics"] = _statistics_json(stats)
            paths = []
            for fmt in ReportFormat:
                artifact = FileReportGenerator().generate_report(stats, insight, output / ("report." + fmt.value), report_format=fmt)
                paths.append(artifact.path.name)
            result["reports"] = paths
            result["checks"]["reports_written"] = len(paths) == 2
            result["checks"]["stored_analysis_unchanged"] = before == [repository.get_review(i) for i in range(1, len(rows) + 1)]
            result["status"] = "completed"
    except (AppError, KeyboardInterrupt) as exc:
        result["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        result["errors"].append({"stage": "pipeline", "type": type(exc).__name__})
    finally:
        result["metrics"] = classification_metrics(rows)
        durations = sorted(c["elapsed_seconds"] for c in measured.calls if c["stage"] == "analysis")
        result["latency"] = {"analysis_requests": len(durations),
            "analysis_mean_seconds": sum(durations) / len(durations) if durations else None,
            "analysis_p95_seconds": durations[math.ceil(.95 * len(durations)) - 1] if durations else None}
        result["structural_checks_passed"] = result["status"] == "completed" and all(result["checks"].values())
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        _save(checkpoint, result)
    return result
