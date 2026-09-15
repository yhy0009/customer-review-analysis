"""Offline, explicit-policy quality gates over immutable evaluation artifacts."""

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from src.errors import ValidationError
from src.evaluation import classification_metrics, load_dataset, _unique_object


RUBRIC = ("grounding", "numbers", "issues", "suggestions", "scope", "injection")
METRICS = ("accuracy_all", "macro_f1", "valid_ratio")
CHECKS = {"all_analyses_saved", "repeat_skips_without_api", "insight_generated",
          "same_scope_keyword_counts", "reports_written", "stored_analysis_unchanged"}


def read_json(path):
    try:
        raw = Path(path).read_bytes()
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError())), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValidationError("품질 판정 입력 파일 또는 JSON 형식이 올바르지 않습니다.") from None


def number(value, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValidationError("품질 기준 수치의 범위가 올바르지 않습니다.")
    return value


def validate_policy(policy, dataset):
    if not isinstance(policy, dict) or set(policy) != {
        "version", "dataset_sha256", "minimums", "max_analysis_p95_seconds",
        "max_regression", "require_human_review",
    }:
        raise ValidationError("품질 기준 필드를 확인하세요.")
    if not isinstance(policy["version"], str) or not policy["version"].strip():
        raise ValidationError("품질 기준 버전이 필요합니다.")
    if policy["dataset_sha256"] != dataset["sha256"]:
        raise ValidationError("품질 기준과 데이터셋 해시가 다릅니다.")
    if type(policy["require_human_review"]) is not bool:
        raise ValidationError("사람 검토 요구 여부는 bool이어야 합니다.")
    if not isinstance(policy["minimums"], dict) or set(policy["minimums"]) != set(METRICS):
        raise ValidationError("분류 기준 세 항목을 모두 지정하세요.")
    for value in policy["minimums"].values():
        number(value)
    number(policy["max_analysis_p95_seconds"], 0, 86400)
    if policy["max_regression"] is not None:
        if not isinstance(policy["max_regression"], dict) or set(policy["max_regression"]) != set(METRICS):
            raise ValidationError("회귀 허용량 세 항목을 모두 지정하세요.")
        for value in policy["max_regression"].values():
            number(value)


def evaluation_metrics(data, dataset):
    """Recompute metrics from rows; do not trust cached scores in the artifact."""
    try:
        if data["dataset_sha256"] != dataset["sha256"] or len(data["rows"]) != len(dataset["cases"]):
            raise ValueError
        for row, case in zip(data["rows"], dataset["cases"]):
            if any(row[k] != value for k, value in case.items()):
                raise ValueError
            if row["status"] not in ("ok", "error", "not_run"):
                raise ValueError
            if row["status"] == "ok":
                if row["predicted"] not in ("positive", "neutral", "negative"):
                    raise ValueError
            elif row["predicted"] is not None:
                raise ValueError
        durations = []
        for call in data["calls"]:
            elapsed = number(call["elapsed_seconds"], 0, 86400)
            if call["stage"] == "analysis":
                durations.append(elapsed)
        # Evaluation disables retries and makes exactly one call per attempted row.
        if len(durations) != sum(r["status"] != "not_run" for r in data["rows"]):
            raise ValueError
        if data.get("max_retries") != 0:
            raise ValueError
        metrics = classification_metrics(data["rows"])
        metrics["valid_ratio"] = metrics["valid"] / metrics["total"]
        durations.sort()
        metrics["analysis_p95_seconds"] = durations[math.ceil(.95 * len(durations)) - 1] if durations else None
        return metrics
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValidationError("데이터셋과 일치하는 평가 행·호출 기록이 필요합니다.") from None


def review_status(review, evaluation_sha, data):
    if review is None:
        return "pending"
    try:
        if set(review) != {"evaluation_sha256", "reviewer", "reviewer_kind", "reviewed_at",
                           "labels_reviewed", "rubric"}:
            raise ValueError
        if review["evaluation_sha256"] != evaluation_sha:
            raise ValueError
        if not isinstance(review["reviewer"], str) or not review["reviewer"].strip():
            raise ValueError
        if review["reviewer_kind"] not in ("human", "assistant") or type(review["labels_reviewed"]) is not bool:
            raise ValueError
        when = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError
        if set(review["rubric"]) != set(RUBRIC):
            raise ValueError
        ids = {r["id"] for r in data["rows"] if r["status"] == "ok"}
        verdicts = []
        for name, item in review["rubric"].items():
            if set(item) != {"verdict", "case_ids", "note"}:
                raise ValueError
            verdict = item["verdict"]
            if verdict not in ("pass", "partial", "fail", "not_applicable"):
                raise ValueError
            if verdict == "not_applicable" and name != "numbers":
                raise ValueError
            if not isinstance(item["case_ids"], list) or any(type(i) is not str for i in item["case_ids"]):
                raise ValueError
            if not set(item["case_ids"]) <= ids or len(set(item["case_ids"])) != len(item["case_ids"]):
                raise ValueError
            if verdict != "not_applicable" and not item["case_ids"]:
                raise ValueError
            if not isinstance(item["note"], str) or not item["note"].strip():
                raise ValueError
            verdicts.append(verdict)
        if review["reviewer_kind"] != "human":
            return "pending"
        if any(v in ("fail", "partial") for v in verdicts):
            return "fail"
        return "pass" if review["labels_reviewed"] else "pending"
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValidationError("검토 기록의 해시·검토자·근거 ID·평가 항목을 확인하세요.") from None


def assess(dataset_path, evaluation_path, policy_path, *, review_path=None, baseline_path=None):
    dataset = load_dataset(Path(dataset_path))
    data, evaluation_sha = read_json(evaluation_path)
    policy, policy_sha = read_json(policy_path)
    validate_policy(policy, dataset)
    metrics = evaluation_metrics(data, dataset)
    results = []

    def add(name, passed, actual, required):
        results.append(dict(name=name, status="pass" if passed else "fail", actual=actual, required=required))

    checks = data.get("checks", {})
    structural = (data.get("status") == "completed" and isinstance(checks, dict)
                  and CHECKS <= set(checks) and all(v is True for v in checks.values()))
    add("structure", structural, structural, True)
    for name, minimum in policy["minimums"].items():
        add(name, metrics[name] is not None and metrics[name] >= minimum, metrics[name], minimum)
    p95 = metrics["analysis_p95_seconds"]
    add("analysis_p95_seconds", p95 is not None and p95 <= policy["max_analysis_p95_seconds"],
        p95, policy["max_analysis_p95_seconds"])
    baseline_sha = None
    if policy["max_regression"] is not None:
        if baseline_path is None:
            raise ValidationError("회귀 기준이 있으면 baseline 평가 파일이 필요합니다.")
        baseline, baseline_sha = read_json(baseline_path)
        previous = evaluation_metrics(baseline, dataset)
        if baseline.get("mode") != data.get("mode"):
            raise ValidationError("실제 API 평가와 주입 평가를 회귀 비교할 수 없습니다.")
        if baseline.get("status") != "completed":
            raise ValidationError("완료된 baseline 평가가 필요합니다.")
        for name, maximum in policy["max_regression"].items():
            drop = previous[name] - metrics[name]
            add("regression_" + name, drop <= maximum + 1e-12, drop, maximum)
    elif baseline_path is not None:
        raise ValidationError("baseline을 사용하려면 max_regression을 지정하세요.")
    review, review_sha = read_json(review_path) if review_path else (None, None)
    narrative = review_status(review, evaluation_sha, data)
    if policy["require_human_review"]:
        results.append(dict(name="human_review", status=narrative, actual=narrative, required="pass"))
    statuses = {r["status"] for r in results}
    status = "fail" if "fail" in statuses else "pending" if "pending" in statuses else "pass"
    return dict(status=status, policy_version=policy["version"], policy_sha256=policy_sha,
                dataset_sha256=dataset["sha256"], evaluation_sha256=evaluation_sha,
                baseline_sha256=baseline_sha, review_sha256=review_sha, checks=results,
                metrics=metrics, narrative_review=narrative,
                scope="classification_and_human_review" if policy["require_human_review"] else "automated_checks_only")
