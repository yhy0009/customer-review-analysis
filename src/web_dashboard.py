"""Versioned browser data and source-bound insight artifacts. No AI/network I/O."""

import hashlib
import json
import threading
import time
import uuid
import secrets
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path

from src.errors import ValidationError
from src.insight_provenance import public_profile, validate_profile
from src.models import (
    InsightCitation, InsightEvidenceGroup, InsightResult, KeywordCount, ReviewFilter,
    ReviewQuery, Sentiment, SortField, SortOrder,
)
from src.sqlite_repository import SQLiteReviewRepository


# Bound retained export data for each of the dashboard's in-memory snapshots.
EXPORT_LIMIT = 2000


def export_selection(repository, filters, total):
    if total > EXPORT_LIMIT:
        return None
    selected = []
    for page in range(1, (total + 499) // 500 + 1):
        rows = repository.list_reviews(ReviewQuery(filters=filters, page=page, size=500,
                                                   sort=SortField.ID, order=SortOrder.ASC))
        # Preserve the shared exporter format without exposing external source IDs.
        selected.extend(replace(detail, review=replace(detail.review, source_review_id=None))
                        for detail in rows.items)
    if len(selected) != total:
        raise ValidationError("다운로드 대상 리뷰를 모두 조회하지 못했습니다.")
    return selected


def json_value(value):
    if isinstance(value, dict):
        return {str(k.value if hasattr(k, "value") else k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def encode(value):
    return json.dumps(json_value(value), ensure_ascii=False, allow_nan=False).encode("utf-8")


def filters_from_dict(values):
    if set(values) - {"product_name", "date_from", "date_to", "sentiment", "rating", "rating_min"}:
        raise ValidationError("지원하지 않는 필터입니다.")
    try:
        values = {k: v for k, v in values.items() if v not in (None, "")}
        if any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in values.values()):
            raise ValueError
        for name in ("date_from", "date_to"):
            if name in values:
                values[name] = date.fromisoformat(values[name])
        if "sentiment" in values:
            values["sentiment"] = Sentiment(values["sentiment"])
        for name in ("rating", "rating_min"):
            if name in values:
                values[name] = int(values[name])
        if "product_name" in values and len(values["product_name"]) > 200:
            raise ValueError
        return ReviewFilter(**values)
    except (ValueError, TypeError):
        raise ValidationError("필터의 날짜·감정·별점을 확인하세요.") from None


def select_analyzed(repository, filters, limit):
    selected, page = [], 1
    while True:
        result = repository.list_reviews(ReviewQuery(filters=filters, page=page, size=500,
                                                     sort=SortField.ID, order=SortOrder.ASC))
        for detail in result.items:
            if detail.analysis is not None:
                selected.append(detail)
                if len(selected) == limit:
                    return selected
        if page >= result.total_pages:
            return selected
        page += 1


def source_hash(details):
    return hashlib.sha256(encode([asdict(d) for d in details])).hexdigest()


def make_insight_artifact(details, insight, limit, *, profile=None):
    if type(limit) is not int or not 1 <= limit <= 2000 or len(details) != insight.review_count:
        raise ValidationError("인사이트 선택 범위가 올바르지 않습니다.")
    artifact = {"schema_version": 1, "source_sha256": source_hash(details),
            "selection_limit": limit, "review_ids": [d.review.id for d in details],
            "insight": json_value(asdict(insight))}
    if profile is not None:
        artifact.update(schema_version=2, generation_profile=validate_profile(profile))
    return artifact


def load_insight_artifact(path):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError
            data[key] = value
        return data
    try:
        if path.stat().st_size > 10_000_000:
            raise ValueError
        artifact = json.loads(path.read_bytes(), object_pairs_hook=unique)
        if not isinstance(artifact, dict):
            raise ValueError
        version = artifact.get("schema_version")
        if type(version) is not int or version not in (1, 2):
            raise ValueError
        fields = {"schema_version", "source_sha256", "selection_limit", "review_ids", "insight"}
        if set(artifact) != (fields | {"generation_profile"} if version == 2 else fields):
            raise ValueError
        if version == 2:
            artifact["generation_profile"] = validate_profile(artifact["generation_profile"])
        if type(artifact["selection_limit"]) is not int or not 1 <= artifact["selection_limit"] <= 2000:
            raise ValueError
        ids = artifact["review_ids"]
        if not isinstance(ids, list) or any(type(i) is not int or i < 1 for i in ids):
            raise ValueError
        if ids != sorted(set(ids)) or len(ids) > artifact["selection_limit"]:
            raise ValueError
        digest = artifact["source_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError
        data = dict(artifact["insight"])
        data["filters"] = filters_from_dict(data["filters"])
        data["generated_at"] = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
        for key in ("positive_keywords", "negative_keywords"):
            data[key] = [KeywordCount(**k) for k in data.get(key, [])]
        data["evidence_groups"] = [InsightEvidenceGroup(**dict(g,
            citations=[InsightCitation(**c) for c in g["citations"]])) for g in data.get("evidence_groups", [])]
        if type(data["review_count"]) is not int or data["review_count"] != len(ids):
            raise ValueError
        if not isinstance(data.get("summary", ""), str):
            raise ValueError
        for key in ("issues", "improvement_suggestions"):
            if not isinstance(data.get(key, []), list) or any(not isinstance(v, str) for v in data.get(key, [])):
                raise ValueError
        artifact["result"] = InsightResult(**data)
        return artifact
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ValidationError("대시보드 인사이트 파일 형식이 올바르지 않습니다.") from None


def public_review(detail):
    review, analysis = detail.review, detail.analysis
    return {"id": review.id, "product_name": review.product_name,
            "review_date": review.review_date.isoformat() if review.review_date is not None else None,
            "rating": review.rating, "review_text": review.review_text,
            "analysis": None if analysis is None else {
                "sentiment": analysis.sentiment.value, "confidence": analysis.confidence,
                "summary": analysis.summary, "keywords": analysis.keywords,
                "model": analysis.model, "analyzed_at": analysis.analyzed_at.isoformat()}}


@dataclass
class Snapshot:
    response: dict
    statistics: object
    insight: InsightResult | None
    reviews: dict
    created: float
    chart: bytes | None = None
    generation_source: list | None = None
    export_reviews: list | None = None


class DashboardData:
    def __init__(self, database, *, insight_path=None, demo=False, ttl=900, capacity=16,
                 cache_dir=None, extractor_factory=None, insight_limit=50, generation_profile=None):
        self.database = Path(database)
        self.insight_path = Path(insight_path) if insight_path else None
        self.demo, self.ttl, self.capacity = demo, ttl, capacity
        self.snapshots = OrderedDict()
        self.lock = threading.Lock()
        self.chart_lock = threading.Lock()
        self.jobs = None
        self.generation_token = secrets.token_urlsafe(32)
        if cache_dir is not None:
            from src.dashboard_insights import InsightJobs
            self.jobs = InsightJobs(database, cache_dir, extractor_factory, insight_limit, profile=generation_profile)
        elif extractor_factory is not None:
            raise ValidationError("인사이트 저장 경로가 필요합니다.")
        # Fail early without creating or migrating a DB.
        with SQLiteReviewRepository(self.database, read_only=True):
            pass
        if self.insight_path:
            load_insight_artifact(self.insight_path)

    def create_snapshot(self, filters, page=1):
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise ValidationError("페이지 번호를 확인하세요.")
        insight = None
        status = "missing"
        source = []
        # Load before querying; everything displayed is bound to this DB read snapshot.
        artifact = load_insight_artifact(self.insight_path) if self.insight_path else None
        if self.jobs:
            cached, cache_status = self.jobs.cached(filters)
            if cached:
                artifact = cached
            elif cache_status == "invalid":
                status = "invalid"
        generation_source = None
        with SQLiteReviewRepository(self.database, read_only=True) as repository:
            with repository.read_snapshot():
                stats = repository.get_statistics(filters)
                rows = repository.list_reviews(ReviewQuery(filters=filters, page=page, size=10,
                                                          sort=SortField.ID, order=SortOrder.DESC))
                export_reviews = export_selection(repository, filters, stats.total_reviews)
                if self.jobs:
                    generation_source = select_analyzed(repository, filters, self.jobs.limit)
                if artifact:
                    candidate = artifact["result"]
                    status = "scope_mismatch"
                    if candidate.filters == filters:
                        source = (generation_source if self.jobs and artifact["selection_limit"] == self.jobs.limit
                                  else select_analyzed(repository, filters, artifact["selection_limit"]))
                        status = "stale"
                        from src.dashboard_insights import matches
                        if matches(artifact, source, filters):
                            if self.jobs and not self.jobs.compatible(artifact):
                                status = "config_mismatch"
                            else:
                                insight, status = candidate, "available"
        by_id = {d.review.id: public_review(d) for d in [*source, *rows.items]}
        token = uuid.uuid4().hex
        response = {"schema_version": 1, "snapshot_id": token,
                    "generated_at": datetime.now(timezone.utc).isoformat(), "demo": self.demo,
                    "filters": json_value(asdict(filters)), "statistics": json_value(asdict(stats)),
                    "page": {"number": rows.page, "size": rows.size, "total_items": rows.total_items,
                             "total_pages": rows.total_pages, "items": [public_review(d) for d in rows.items]},
                    "insight_status": status, "insight": json_value(asdict(insight)) if insight else None,
                    "insight_provenance": public_profile(artifact.get("generation_profile")) if insight else None,
                    "chart_url": f"/api/chart/{token}",
                    "report_urls": {fmt: f"/api/report/{token}/{fmt}" for fmt in ("md", "txt")}}
        response["export"] = {
            "status": "available" if export_reviews is not None else "too_large",
            "row_count": stats.total_reviews, "limit": EXPORT_LIMIT,
            "urls": {fmt: f"/api/export/{token}/{fmt}" for fmt in ("csv", "jsonl")}
                    if export_reviews is not None else {},
        }
        response["generation"] = {"enabled": bool(self.jobs and self.jobs.factory),
                                  "profile": public_profile(self.jobs.profile) if self.jobs else None,
                                  "limit": self.jobs.limit if self.jobs else None,
                                  "review_count": len(generation_source or []),
                                  "token": self.generation_token if self.jobs and self.jobs.factory else None,
                                  "job": self.jobs.latest(filters) if self.jobs else None}
        snapshot = Snapshot(response, stats, insight, by_id, time.monotonic(),
                            generation_source=generation_source, export_reviews=export_reviews)
        with self.lock:
            self.snapshots[token] = snapshot
            while len(self.snapshots) > self.capacity:
                self.snapshots.popitem(last=False)
        return snapshot

    def get_snapshot(self, token):
        with self.lock:
            snapshot = self.snapshots.get(token)
            if snapshot is None or time.monotonic() - snapshot.created > self.ttl:
                self.snapshots.pop(token, None)
                raise KeyError("expired snapshot")
            return snapshot

    def start_generation(self, token):
        from src.dashboard_insights import GenerationConflict
        if not self.jobs or not self.jobs.factory:
            raise ValidationError("인사이트 생성이 활성화되지 않았습니다.")
        snapshot = self.get_snapshot(token)
        filters = filters_from_dict(snapshot.response["filters"])
        with SQLiteReviewRepository(self.database, read_only=True) as repository:
            with repository.read_snapshot():
                current = select_analyzed(repository, filters, self.jobs.limit)
        if source_hash(current) != source_hash(snapshot.generation_source):
            raise GenerationConflict("조회 이후 데이터가 변경됐습니다. 새로고침 후 생성하세요.")
        return self.jobs.start(snapshot.generation_source, filters)
