"""Generation lifecycle, persistent cache and HTTP write boundaries without live AI."""

import http.client
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from scripts.serve_dashboard import seed_demo
from src.dashboard_insights import GenerationConflict
from src.dashboard_server import create_server
from src.errors import ValidationError
from src.models import DuplicatePolicy, InsightCitation, InsightEvidenceGroup, InsightResult, ReviewFilter, Sentiment
from src.web_dashboard import DashboardData
from src.sqlite_repository import SQLiteReviewRepository


def fake_insight(details, filters):
    first = details[0].review
    return InsightResult(filters=filters, review_count=len(details), generated_at=datetime.now(timezone.utc),
                         summary="테스트 생성 요약", evidence_groups=[InsightEvidenceGroup(
                             product_name=first.product_name, kind="praises", label="테스트 근거",
                             citations=[InsightCitation(review_id=first.id, label="테스트 근거", quote=first.review_text)])])


class InsightJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database, _ = seed_demo(self.root)
        self.cache = self.root / "cache"
        self.extractor = Mock()
        self.extractor.extract_insights.side_effect = fake_insight
        self.factory = Mock(return_value=self.extractor)
        self.data = DashboardData(self.database, cache_dir=self.cache, extractor_factory=self.factory)

    def snapshot(self, filters=None):
        return self.data.create_snapshot(filters or ReviewFilter()).response

    def wait_job(self, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = self.data.jobs.get(job["id"])
            if current["status"] != "running" and self.data.jobs.active is None:
                return current
            time.sleep(.01)
        self.fail("generation did not finish")

    def generate(self, filters=None):
        return self.wait_job(self.data.start_generation(self.snapshot(filters)["snapshot_id"]))

    def test_persistent_cache_and_multiple_filters_reuse_without_ai_or_db_writes(self):
        before = self.database.read_bytes()
        initial = self.snapshot()
        self.factory.assert_not_called()
        self.assertEqual(initial["generation"]["review_count"], 18)
        self.assertEqual(self.generate()["status"], "succeeded")
        negative = ReviewFilter(sentiment=Sentiment.NEGATIVE)
        self.assertEqual(self.generate(negative)["review_count"], 6)
        self.assertEqual(len(list(self.cache.glob("*.json"))), 2)
        restarted = DashboardData(self.database, cache_dir=self.cache)
        for filters, count in ((ReviewFilter(), 18), (negative, 6)):
            result = restarted.create_snapshot(filters).response
            self.assertEqual(result["insight_status"], "available")
            self.assertEqual(result["insight"]["review_count"], count)
            self.assertFalse(result["generation"]["enabled"])
        self.assertTrue(self.generate()["reused"])
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.database.read_bytes(), before)

    def test_duplicate_clicks_join_job_and_other_scope_is_busy(self):
        gate = threading.Event()
        self.extractor.extract_insights.side_effect = lambda rows, filters: (gate.wait(3), fake_insight(rows, filters))[1]
        all_rows = self.snapshot()
        negative = self.snapshot(ReviewFilter(sentiment=Sentiment.NEGATIVE))
        first = self.data.start_generation(all_rows["snapshot_id"])
        try:
            self.assertEqual(first["id"], self.data.start_generation(all_rows["snapshot_id"])["id"])
            self.assertEqual(self.snapshot()["generation"]["job"]["status"], "running")
            with self.assertRaises(GenerationConflict):
                self.data.start_generation(negative["snapshot_id"])
        finally:
            gate.set()
        self.assertEqual(self.wait_job(first)["status"], "succeeded")
        self.factory.assert_called_once()

    def test_failure_is_sanitized_and_retry_is_explicit(self):
        self.extractor.extract_insights.side_effect = RuntimeError("secret-key and private review")
        job = self.generate()
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("secret", json.dumps(job))
        self.snapshot()
        self.assertEqual(self.factory.call_count, 1)
        self.extractor.extract_insights.side_effect = fake_insight
        self.assertEqual(self.generate()["status"], "succeeded")
        self.assertEqual(self.factory.call_count, 2)

    def change_source(self):
        with SQLiteReviewRepository(self.database) as repository:
            detail = repository.get_review(1)
            repository.save_clean_reviews([replace(detail.review, review_text="변경한 원문")], DuplicatePolicy.UPSERT)

    def test_changed_source_rejects_old_click_and_invalidates_cached_result(self):
        self.generate()
        old = self.snapshot()
        self.change_source()
        self.assertEqual(self.snapshot()["insight_status"], "stale")
        with self.assertRaises(GenerationConflict):
            self.data.start_generation(old["snapshot_id"])
        self.assertEqual(self.factory.call_count, 1)
        self.assertEqual(self.generate()["review_count"], 17)
        self.assertEqual(self.snapshot()["insight_status"], "available")

    def test_source_change_during_generation_does_not_expose_stale_insight(self):
        gate = threading.Event()
        self.extractor.extract_insights.side_effect = lambda rows, filters: (gate.wait(3), fake_insight(rows, filters))[1]
        job = self.data.start_generation(self.snapshot()["snapshot_id"])
        try:
            self.change_source()
        finally:
            gate.set()
        self.assertEqual(self.wait_job(job)["status"], "succeeded")
        self.assertEqual(self.snapshot()["insight_status"], "stale")

    def test_empty_expired_and_disabled_generation_never_call_ai(self):
        empty = self.snapshot(ReviewFilter(product_name="없는 제품"))
        with self.assertRaises(ValidationError):
            self.data.start_generation(empty["snapshot_id"])
        with self.assertRaises(KeyError):
            self.data.start_generation("expired")
        with self.assertRaises(ValidationError):
            DashboardData(self.database).start_generation(empty["snapshot_id"])
        self.factory.assert_not_called()

    def test_corrupt_cache_is_repairable_and_wrong_citations_are_not_saved(self):
        self.cache.mkdir()
        path = self.cache / (self.data.jobs.key(ReviewFilter()) + ".json")
        path.write_text("broken JSON")
        self.assertEqual(self.snapshot()["insight_status"], "invalid")
        self.extractor.extract_insights.side_effect = lambda rows, filters: replace(fake_insight(rows, filters), filters=ReviewFilter(product_name="wrong"))
        self.assertEqual(self.generate()["status"], "failed")
        self.assertEqual(path.read_text(), "broken JSON")
        self.extractor.extract_insights.side_effect = fake_insight
        self.assertEqual(self.generate()["status"], "succeeded")
        self.assertEqual(self.snapshot()["insight_status"], "available")
        self.assertEqual(list(self.cache.glob("*.tmp")), [])

    def test_cache_selection_limit_and_database_are_separate(self):
        self.generate()
        other = DashboardData(self.database, cache_dir=self.cache, insight_limit=5)
        self.assertEqual(other.create_snapshot(ReviewFilter()).response["insight_status"], "missing")
        other_path = self.root / "other.sqlite"
        other_path.write_bytes(self.database.read_bytes())
        other = DashboardData(other_path, cache_dir=self.cache)
        self.assertEqual(other.create_snapshot(ReviewFilter()).response["insight_status"], "missing")

    def test_save_failure_releases_worker_for_retry(self):
        self.cache.write_text("not a directory")
        self.assertEqual(self.generate()["status"], "failed")
        self.assertIsNone(self.data.jobs.active)
        self.cache.unlink()
        self.assertEqual(self.generate()["status"], "succeeded")

    def test_http_start_poll_report_citation_and_request_boundaries(self):
        server = create_server(self.data, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(path, payload=None, overrides=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}",
                       "X-Dashboard-Token": self.data.generation_token}
            headers.update(overrides or {})
            connection.request("POST" if payload is not None else "GET", path,
                               None if payload is None else json.dumps(payload), headers)
            response = connection.getresponse()
            code, body = response.status, response.read()
            connection.close()
            return code, body

        initial = json.loads(request("/api/snapshot")[1])
        payload = {"snapshot_id": initial["snapshot_id"]}
        for headers in ({"Origin": "https://foreign.example"}, {"Origin": ""},
                        {"X-Dashboard-Token": "invalid"}, {"Host": "foreign.example"}):
            self.assertEqual(request("/api/insight-jobs", payload, headers)[0], 403)
        self.assertEqual(request("/api/insight-jobs", payload, {"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(request("/api/insight-jobs", {"filters": {}})[0], 400)
        self.assertEqual(request("/api/insight-jobs", {"snapshot_id": "expired"})[0], 410)
        self.factory.assert_not_called()
        code, body = request("/api/insight-jobs", payload)
        self.assertEqual(code, 202)
        job = self.wait_job(json.loads(body))
        self.assertEqual(json.loads(request(f"/api/insight-jobs/{job['id']}")[1])["status"], "succeeded")
        current = json.loads(request("/api/snapshot")[1])
        self.assertEqual(current["insight_status"], "available")
        self.assertIn("테스트 생성 요약", request(current["report_urls"]["md"])[1].decode())
        token = current["snapshot_id"]
        self.assertEqual(json.loads(request(f"/api/review/{token}/1")[1])["id"], 1)
        # The old snapshot's report remains unchanged after generation completes.
        self.assertNotIn("테스트 생성 요약", request(initial["report_urls"]["md"])[1].decode())


if __name__ == "__main__":
    unittest.main()
