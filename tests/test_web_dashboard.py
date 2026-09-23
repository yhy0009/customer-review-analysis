"""Real SQLite, snapshot consistency, HTTP boundaries and source-bound insights."""

import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.serve_dashboard import seed_demo
from scripts.prepare_dashboard_insight import main as prepare_insight
from src.config import DEFAULT_CONFIG
from src.dashboard_server import create_server
from src.errors import StorageError, ValidationError
from src.models import DuplicatePolicy, ReviewFilter, Sentiment
from src.sqlite_repository import SQLiteReviewRepository
from src.web_dashboard import DashboardData, filters_from_dict, load_insight_artifact


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database, self.insight = seed_demo(self.root)
        self.data = DashboardData(self.database, insight_path=self.insight, demo=True)

    def test_read_only_queries_do_not_change_database_or_allow_writes(self):
        before = self.database.read_bytes()
        with SQLiteReviewRepository(self.database, read_only=True) as repository:
            self.assertEqual(repository.get_statistics().total_reviews, 18)
            with self.assertRaises(StorageError):
                repository.mark_analysis_failed(1, "never write")
        self.assertEqual(before, self.database.read_bytes())
        missing = self.root / "missing" / "database.db"
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(missing, read_only=True)
        self.assertFalse(missing.parent.exists())

    def test_read_only_rejects_unregistered_schema_without_migration(self):
        import sqlite3
        path = self.root / "old.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE legacy(value TEXT)")
        before = path.read_bytes()
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(path, read_only=True)
        self.assertEqual(before, path.read_bytes())

    def test_snapshot_returns_real_counts_pagination_and_no_private_source_fields(self):
        snapshot = self.data.create_snapshot(ReviewFilter())
        body = snapshot.response
        self.assertEqual(body["statistics"]["total_reviews"], 18)
        self.assertEqual(body["page"]["total_pages"], 2)
        self.assertEqual(len(body["page"]["items"]), 10)
        self.assertEqual(body["insight_status"], "available")
        self.assertEqual(len(body["insight"]["evidence_groups"]), 25)
        self.assertEqual(body["page"]["items"][0]["id"], 18)
        self.assertNotIn("source_review_id", json.dumps(body))
        self.assertNotIn("source_file", json.dumps(body))
        self.assertEqual(len(self.data.create_snapshot(ReviewFilter(), 2).response["page"]["items"]), 8)

    def test_filtered_statistics_do_not_reuse_other_scope_insights(self):
        result = self.data.create_snapshot(ReviewFilter(sentiment=Sentiment.NEGATIVE)).response
        self.assertEqual(result["statistics"]["total_reviews"], 6)
        self.assertEqual(result["insight_status"], "scope_mismatch")
        self.assertIsNone(result["insight"])
        empty = self.data.create_snapshot(ReviewFilter(product_name="없는 제품")).response
        self.assertEqual(empty["statistics"]["total_reviews"], 0)
        self.assertEqual(empty["page"]["items"], [])

    def test_changed_analysis_and_source_are_rejected_but_previous_snapshot_is_stable(self):
        previous = self.data.create_snapshot(ReviewFilter())
        with SQLiteReviewRepository(self.database) as repository:
            review = repository.get_review(1).review
            repository.save_clean_reviews([replace(review, review_text="수정된 리뷰")], DuplicatePolicy.UPSERT)
        current = self.data.create_snapshot(ReviewFilter()).response
        self.assertEqual(current["insight_status"], "stale")
        self.assertIsNone(current["insight"])
        self.assertEqual(previous.response["statistics"]["analyzed_reviews"], 18)
        self.assertEqual(previous.reviews[1]["review_text"], review.review_text)
        self.assertEqual(current["statistics"]["analyzed_reviews"], 17)

    def test_missing_insight_still_supports_statistics(self):
        body = DashboardData(self.database).create_snapshot(ReviewFilter()).response
        self.assertEqual(body["insight_status"], "missing")
        self.assertIsNone(body["insight"])
        self.assertEqual(body["statistics"]["analyzed_reviews"], 18)

    def test_explicit_preparation_creates_usable_source_bound_artifact_without_db_writes(self):
        output = self.root / "prepared.json"
        before = self.database.read_bytes()
        result = load_insight_artifact(self.insight)["result"]
        arguments = ["prepare", "--live", "--database", str(self.database), "--output", str(output)]
        with patch("sys.argv", arguments), patch("scripts.prepare_dashboard_insight.load_env_file"), \
                patch("scripts.prepare_dashboard_insight.load_config", return_value=DEFAULT_CONFIG), \
                patch("src.insight_extractor.AIInsightExtractor") as extractor, redirect_stdout(io.StringIO()):
            extractor.return_value.extract_insights.return_value = result
            self.assertEqual(prepare_insight(), 0)
            details, filters = extractor.return_value.extract_insights.call_args.args
            self.assertEqual(len(details), 18)
            self.assertEqual(filters, ReviewFilter())
        self.assertEqual(self.database.read_bytes(), before)
        response = DashboardData(self.database, insight_path=output).create_snapshot(ReviewFilter()).response
        self.assertEqual(response["insight_status"], "available")
        with patch("sys.argv", arguments), patch("src.insight_extractor.AIInsightExtractor") as extractor, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            prepare_insight()
        self.assertEqual(error.exception.code, 2)
        extractor.assert_not_called()

    def test_expiry_and_capacity_bound_retained_snapshots(self):
        data = DashboardData(self.database, capacity=1)
        first = data.create_snapshot(ReviewFilter())
        second = data.create_snapshot(ReviewFilter())
        with self.assertRaises(KeyError):
            data.get_snapshot(first.response["snapshot_id"])
        with patch("src.web_dashboard.time.monotonic", return_value=second.created + 901):
            with self.assertRaises(KeyError):
                data.get_snapshot(second.response["snapshot_id"])

    def test_artifact_shape_and_citation_source_are_validated(self):
        source = json.loads(self.insight.read_text())
        for change in ({"schema_version": True}, {"selection_limit": 0}, {"review_ids": [1, 1]},
                       {"source_sha256": "wrong"}):
            self.insight.write_text(json.dumps(dict(source, **change)))
            with self.assertRaises(ValidationError):
                load_insight_artifact(self.insight)
        self.insight.write_text(json.dumps(source))
        source["insight"]["evidence_groups"][0]["citations"][0]["quote"] = "원문에 없는 인용"
        self.insight.write_text(json.dumps(source))
        self.assertEqual(self.data.create_snapshot(ReviewFilter()).response["insight_status"], "stale")

    def test_bad_filters_and_pages_fail(self):
        for values in ({"date_from": "2026-99-01"}, {"sentiment": "bad"}, {"unknown": "x"},
                       {"rating": "99"}, {"date_from": "2026-09-10", "date_to": "2026-09-01"}):
            with self.assertRaises(ValidationError):
                filters_from_dict(values)
        for page in (0, -1, True, 1.5):
            with self.assertRaises(ValidationError):
                self.data.create_snapshot(ReviewFilter(), page)


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database, cls.insight = seed_demo(Path(cls.directory.name))
        cls.data = DashboardData(cls.database, insight_path=cls.insight, demo=True)
        cls.server = create_server(cls.data, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, path, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_static_allowlist_does_not_expose_workspace_and_sets_csp(self):
        for path in ("/", "/app.js", "/styles.css", "/utils.js", "/favicon.svg"):
            code, headers, body = self.request(path)
            self.assertEqual(code, 200)
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
            self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/.env", "/src/config.py", "/../.env", "/%2e%2e/.env"):
            self.assertEqual(self.request(path)[0], 404)

    def test_loopback_host_origin_and_write_boundaries(self):
        self.assertEqual(self.request("/api/snapshot", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", method="POST")[0], 405)
        self.assertEqual(self.request("/api/snapshot?sentiment=positive&sentiment=negative")[0], 400)
        self.assertEqual(self.request("/api/snapshot?page=0")[0], 400)

    def test_chart_report_and_review_share_snapshot_without_modifying_database(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        code, _, body = self.request("/api/snapshot")
        self.assertEqual(code, 200)
        result = json.loads(body)
        token = result["snapshot_id"]
        code, _, review = self.request(f"/api/review/{token}/1")
        self.assertEqual(code, 200)
        self.assertIn("review_text", json.loads(review))
        code, headers, chart = self.request(result["chart_url"])
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(chart.startswith(b"\x89PNG"))
        self.assertEqual(self.request(result["chart_url"])[2], chart)
        for fmt, path in result["report_urls"].items():
            code, headers, report = self.request(path)
            self.assertEqual(code, 200)
            self.assertIn(f"review-report.{fmt}", headers["Content-Disposition"])
            self.assertIn("18건", report.decode())
            self.assertIn("근거 주제 25", report.decode())
        self.assertEqual(self.request("/api/chart/expired")[0], 410)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_empty_scope_and_unavailable_insight_report(self):
        code, _, body = self.request("/api/snapshot?product_name=no-matching-product")
        self.assertEqual(code, 200)
        result = json.loads(body)
        self.assertEqual(result["statistics"]["total_reviews"], 0)
        self.assertEqual(self.request(result["chart_url"])[0], 200)
        report = self.request(result["report_urls"]["md"])[2].decode()
        self.assertIn("AI 인사이트가 제공되지 않았습니다", report)

    def test_rating_filters_keep_reviews_statistics_and_reports_in_the_same_scope(self):
        for query, accepts in (("rating=1", lambda value: value == 1),
                               ("rating_min=4", lambda value: value >= 4)):
            with self.subTest(query=query):
                code, _, body = self.request('/api/snapshot?' + query)
                self.assertEqual(code, 200)
                result = json.loads(body)
                self.assertTrue(result['page']['items'])
                self.assertTrue(all(accepts(row['rating']) for row in result['page']['items']))
                self.assertEqual(result['statistics']['total_reviews'], result['page']['total_items'])
                self.assertTrue(accepts(result['statistics']['average_rating']))
                self.assertEqual(result['insight_status'], 'scope_mismatch')
                report = self.request(result['report_urls']['md'])[2].decode()
                self.assertIn(f"{result['page']['total_items']}건", report)
                self.assertIn('AI 인사이트가 제공되지 않았습니다', report)
        self.assertEqual(self.request('/api/snapshot?rating=1&rating_min=4')[0], 400)
