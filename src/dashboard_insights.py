"""Persistent, source-bound insight cache and a single background generation job."""

import hashlib
import os
import tempfile
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path

from src.errors import ValidationError
from src.web_dashboard import encode, load_insight_artifact, make_insight_artifact, source_hash


class GenerationConflict(Exception):
    """The displayed source changed, or another generation is running."""


def matches(artifact, details, filters):
    if artifact["result"].filters != filters:
        return False
    if artifact["review_ids"] != [d.review.id for d in details] or artifact["source_sha256"] != source_hash(details):
        return False
    sources = {d.review.id: d.review for d in details}
    return all(c.review_id in sources and c.quote in sources[c.review_id].review_text
               and g.product_name == sources[c.review_id].product_name
               for g in artifact["result"].evidence_groups for c in g.citations)


class InsightJobs:
    def __init__(self, database, directory, factory, limit=50):
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValidationError("인사이트 선택 한도는 1~2000이어야 합니다.")
        self.directory = Path(directory)
        self.namespace = str(Path(database).resolve())
        self.factory, self.limit = factory, limit
        self.lock = threading.Lock()
        self.jobs = OrderedDict()
        self.active = None

    def key(self, filters):
        return hashlib.sha256(encode([self.namespace, asdict(filters), self.limit])).hexdigest()

    def cached(self, filters):
        path = self.directory / (self.key(filters) + ".json")
        if not path.exists():
            return None, "missing"
        try:
            artifact = load_insight_artifact(path)
            if artifact["selection_limit"] != self.limit or artifact["result"].filters != filters:
                raise ValidationError("저장된 인사이트 조건이 다릅니다.")
            return artifact, "available"
        except ValidationError:
            return None, "invalid"

    def latest(self, filters):
        key = self.key(filters)
        with self.lock:
            for job in reversed(self.jobs.values()):
                if job["key"] == key:
                    return self.public(job)
        return None

    @staticmethod
    def public(job):
        return {k: job[k] for k in ("id", "status", "review_count", "error", "reused")}

    def get(self, job_id):
        with self.lock:
            return self.public(self.jobs[job_id])

    def start(self, details, filters):
        if not details:
            raise ValidationError("생성할 분석 완료 리뷰가 없습니다.")
        key, digest = self.key(filters), source_hash(details)
        with self.lock:
            if self.active:
                current = self.jobs[self.active]
                if current["key"] == key and current["digest"] == digest:
                    return self.public(current)
                raise GenerationConflict("다른 인사이트를 생성 중입니다. 완료 후 다시 시도하세요.")
            cached, _ = self.cached(filters)
            reused = bool(cached and matches(cached, details, filters))
            job = {"id": uuid.uuid4().hex, "key": key, "digest": digest,
                   "status": "succeeded" if reused else "running", "review_count": len(details),
                   "error": None, "reused": reused}
            self.jobs[job["id"]] = job
            while len(self.jobs) > 64:
                self.jobs.popitem(last=False)
            if not reused:
                self.active = job["id"]
                threading.Thread(target=self._run, args=(job, details, filters), daemon=True).start()
            return self.public(job)

    def _run(self, job, details, filters):
        path = None
        try:
            result = self.factory().extract_insights(details, filters)
            artifact = make_insight_artifact(details, result, self.limit)
            self.directory.mkdir(parents=True, exist_ok=True)
            # Readers see either the old complete result or the new complete result.
            fd, name = tempfile.mkstemp(prefix=".insight-", suffix=".tmp", dir=self.directory)
            path = Path(name)
            with os.fdopen(fd, "wb") as output:
                output.write(encode(artifact))
                output.flush()
                os.fsync(output.fileno())
            checked = load_insight_artifact(path)
            if not matches(checked, details, filters):
                raise ValidationError("인사이트 원문 검증에 실패했습니다.")
            path.replace(self.directory / (job["key"] + ".json"))
            with self.lock:
                job["status"] = "succeeded"
        except Exception:
            # Provider errors may include credentials or review text: never expose them.
            with self.lock:
                job["status"] = "failed"
                job["error"] = "생성 또는 저장에 실패했습니다. AI 설정·연결과 저장 경로를 확인한 뒤 재시도하세요."
        finally:
            try:
                if path is not None:
                    path.unlink(missing_ok=True)
            finally:
                with self.lock:
                    self.active = None
