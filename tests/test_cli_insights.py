"""Real source binding, atomic persistence and report snapshot integration."""

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.cli_insights import CLIInsightStore
from src.dashboard_service import DashboardService
from src.errors import OutputError, ValidationError
from src.insight_provenance import INSIGHT_PROMPT_VERSION, generation_profile
from src.insight_service import InsightService
from src.models import (
    AnalysisOptions, AnalysisResult, CleanReview, DashboardRequest, DuplicatePolicy,
    ExtractRequest, InsightCitation, InsightEvidenceGroup, InsightResult, RawReview,
    ReportFormat, ReviewFilter, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository
from src.web_dashboard import DashboardData, encode, load_insight_artifact, make_insight_artifact, select_analyzed


class CLIInsightStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.database = self.root / "reviews.db"
        self.repo = SQLiteReviewRepository(self.database)
        self.addCleanup(self.repo.close)
        self.now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        self.options = AnalysisOptions("openai", "test-model", 10, 0, api_key="private-key")
        self.profile = generation_profile(self.options, INSIGHT_PROMPT_VERSION)
        self.store = CLIInsightStore(self.database, self.root / "output", self.profile)
        self.repo.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(1, 4)], DuplicatePolicy.SKIP)
        self.repo.save_clean_reviews([
            CleanReview(i, None if i == 1 else "제품", None, None, f"배송이 늦었습니다 {i}", self.now)
            for i in range(1, 4)], DuplicatePolicy.SKIP)
        for i in range(1, 4):
            self.repo.save_analysis(AnalysisResult(i, Sentiment.NEGATIVE if i < 3 else Sentiment.POSITIVE,
                                                 .9, self.now, "fake", "test"))

    def result(self, filters=None, limit=None, **changes):
        filters = filters or ReviewFilter()
        details = select_analyzed(self.repo, filters, limit)
        return details, replace(InsightResult(filters, len(details), self.now, summary="저장한 AI 요약",
            issues=["배송 지연"], improvement_suggestions=["출고 일정 점검"], evidence_groups=[
                InsightEvidenceGroup(d.review.product_name, "complaints", "배송 지연",
                    [InsightCitation(d.review.id, "배송 지연", d.review.review_text)]) for d in details]), **changes)

    def request(self, **changes):
        return replace(DashboardRequest(ReviewFilter(), self.root / "reports", ReportFormat.MARKDOWN), **changes)

    def save(self, filters=None, limit=None, **changes):
        details, result = self.result(filters, limit, **changes)
        return self.store.save(details, result, limit)

    def test_unlimited_and_large_limits_round_trip_and_web_can_read_v3(self):
        for limit in (None, 3000):
            with self.subTest(limit=limit):
                path = self.save(limit=limit)
                artifact = load_insight_artifact(path)
                self.assertEqual(artifact["schema_version"], 3)
                self.assertEqual(artifact["selection_limit"], limit)
                self.assertEqual(artifact["review_ids"], [1, 2, 3])
                self.assertEqual(self.store.load(self.repo, self.request())[1], "available")
                self.assertEqual(DashboardData(self.database, insight_path=path).create_snapshot(
                    ReviewFilter()).response["insight_status"], "available")
                self.assertNotIn("private-key", path.read_text())
        self.assertEqual(load_insight_artifact(self.save(limit=2))["schema_version"], 2)

    def test_version_one_and_two_still_enforce_their_selection_limit(self):
        details, result = self.result(limit=2)
        for version in (1, 2):
            artifact = make_insight_artifact(details, result, 2, profile=self.profile if version == 2 else None)
            path = self.root / f"v{version}.json"
            path.write_bytes(encode(artifact))
            self.assertEqual(load_insight_artifact(path)["schema_version"], version)
            artifact["selection_limit"] = 3000
            path.write_bytes(encode(artifact))
            with self.assertRaises(ValidationError):
                load_insight_artifact(path)
        details, result = self.result()
        artifact = make_insight_artifact(details, result, None, profile=self.profile)
        for limit in (0, -1, True, "3", 1):
            with self.subTest(limit=limit):
                path.write_bytes(encode(dict(artifact, selection_limit=limit)))
                with self.assertRaises(ValidationError):
                    load_insight_artifact(path)

    def test_latest_compatible_sentiment_subset_is_used_and_database_namespaces_are_separate(self):
        self.save()
        negative = ReviewFilter(sentiment=Sentiment.NEGATIVE)
        path = self.save(negative, generated_at=self.now + timedelta(seconds=1))
        result, status = self.store.load(self.repo, self.request())
        self.assertEqual((result.filters, result.review_count, status), (negative, 2, "available"))
        self.assertEqual(self.store.load(self.repo, self.request(insight_file=path))[0], result)
        other = CLIInsightStore(self.root / "other.db", self.root / "output", self.profile)
        self.assertEqual(other.load(self.repo, self.request()), (None, "missing"))

    def test_scope_mismatch_is_not_included_and_explicit_mismatch_fails(self):
        path = self.save(ReviewFilter(product_name="제품"))
        self.assertEqual(self.store.load(self.repo, self.request()), (None, "scope_mismatch"))
        with self.assertRaisesRegex(ValidationError, "조건"):
            self.store.load(self.repo, self.request(insight_file=path))
        self.assertEqual(self.store.load(self.repo, self.request(filters=ReviewFilter(product_name="제품")))[1],
                         "available")

    def test_configuration_changes_exclude_cache_but_rotating_key_does_not(self):
        self.save()
        for field, value in (("model", "new-model"), ("reasoning_effort", "high"),
                             ("base_url", "https://example.invalid/v1")):
            with self.subTest(field=field):
                profile = generation_profile(replace(self.options, **{field: value}), INSIGHT_PROMPT_VERSION)
                other = CLIInsightStore(self.database, self.root / "output", profile)
                self.assertEqual(other.load(self.repo, self.request()), (None, "config_mismatch"))
        self.assertEqual(generation_profile(replace(self.options, api_key=None), INSIGHT_PROMPT_VERSION), self.profile)
        other = CLIInsightStore(self.database, self.root / "output",
                                generation_profile(self.options, "next-prompt"))
        self.assertEqual(other.load(self.repo, self.request())[1], "config_mismatch")

    def test_same_count_analysis_and_original_changes_are_stale(self):
        path = self.save()
        analysis = self.repo.get_review(1).analysis
        self.repo.save_analysis(replace(analysis, summary="다시 분석한 요약"))
        self.assertEqual(self.store.load(self.repo, self.request())[1], "stale")
        with self.assertRaisesRegex(ValidationError, "변경"):
            self.store.load(self.repo, self.request(insight_file=path))
        self.save()
        review = self.repo.get_review(1).review
        self.repo.save_clean_reviews([replace(review, review_text="수정한 원문입니다")], DuplicatePolicy.UPSERT)
        self.repo.save_analysis(analysis)
        self.assertEqual(self.store.load(self.repo, self.request())[1], "stale")

    def test_v5_artifact_is_excluded_until_regenerated_with_current_prompt(self):
        old_store = CLIInsightStore(self.database, self.root / "output",
                                    generation_profile(self.options, "review-insights-v5"))
        details, result = self.result(summary="praise_label: 음질 좋음")
        path = old_store.save(details, result, None)
        self.assertEqual(self.store.load(self.repo, self.request()), (None, "config_mismatch"))
        with self.assertRaisesRegex(ValidationError, "extract"):
            self.store.load(self.repo, self.request(insight_file=path))
        self.save(summary="음질 좋음이라는 장점이 언급됩니다.")
        self.assertEqual(self.store.load(self.repo, self.request())[1], "available")

    def test_new_review_invalidates_unlimited_but_preserves_unchanged_limited_selection(self):
        unlimited, limited = self.save(), self.save(limit=2)
        self.repo.save_raw_reviews([RawReview(source_review_id="new")], DuplicatePolicy.SKIP)
        self.repo.save_clean_reviews([CleanReview(4, None, None, None, "새 리뷰입니다", self.now)], DuplicatePolicy.SKIP)
        self.repo.save_analysis(AnalysisResult(4, Sentiment.NEUTRAL, .9, self.now, "fake", "test"))
        with self.assertRaises(ValidationError):
            self.store.load(self.repo, self.request(insight_file=unlimited))
        self.assertEqual(self.store.load(self.repo, self.request(insight_file=limited))[0].review_count, 2)

    def test_corruption_and_wrong_citations_are_excluded_and_disabled_bypasses_loading(self):
        path = self.save()
        saved = json.loads(path.read_bytes())
        saved["insight"]["evidence_groups"][0]["citations"][0]["quote"] = "없는 인용문"
        path.write_text(json.dumps(saved))
        self.assertIsNone(self.store.load(self.repo, self.request())[0])
        path.write_text("invalid JSON")
        self.assertEqual(self.store.load(self.repo, self.request()), (None, "invalid"))
        with self.assertRaises(ValidationError):
            self.store.load(self.repo, self.request(insight_file=path))
        with patch("src.cli_insights.load_insight_artifact", side_effect=AssertionError("must not read")):
            self.assertEqual(self.store.load(self.repo, self.request(use_insights=False)), (None, "disabled"))

    def test_save_failure_preserves_previous_complete_file_and_cleans_stage(self):
        path = self.save()
        before = path.read_bytes()
        details, result = self.result(summary="새로운 AI 요약")
        with patch("src.cli_insights.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OutputError):
                self.store.save(details, result, None)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".insight-*")), [])
        result.evidence_groups[0].citations[0].quote = "틀린 인용문"
        with self.assertRaises(ValidationError):
            self.store.save(details, result, None)
        self.assertEqual(path.read_bytes(), before)

    def test_preflight_protects_symlinks_hardlinks_and_sqlite(self):
        target = self.store.path_for(ReviewFilter(), None)
        target.parent.mkdir(parents=True)
        before = self.database.read_bytes()
        for link in (os.symlink, os.link):
            link(self.database, target)
            with self.assertRaises(OutputError):
                self.store.prepare(ReviewFilter(), None)
            self.assertEqual(self.database.read_bytes(), before)
            target.unlink()

    def test_extraction_saves_captured_sources_when_database_changes_during_ai(self):
        details, result = self.result()
        def extract(*args, **kwargs):
            with SQLiteReviewRepository(self.database) as writer:
                writer.save_analysis(replace(details[0].analysis, summary="동시에 변경된 분석"))
            return result
        service = InsightService(self.repo, Mock(extract_insights=Mock(side_effect=extract)),
                                 snapshot=self.repo.read_snapshot, save_result=self.store.save)
        service.extract_insights(ExtractRequest(ReviewFilter()))
        self.assertEqual(self.store.load(self.repo, self.request()), (None, "stale"))

    def test_dashboard_reads_statistics_and_sources_in_one_snapshot_then_releases_it(self):
        self.save()
        self.repo._connection.execute("PRAGMA journal_mode=WAL")
        with SQLiteReviewRepository(self.database) as writer:
            def load(request):
                writer.save_analysis(replace(self.repo.get_review(1).analysis, summary="동시 변경"))
                result = self.store.load(self.repo, request)
                self.assertEqual(result[1], "available")
                return result
            def stop_after_read(*args, **kwargs):
                self.assertFalse(self.repo._connection.in_transaction)
                raise OutputError("stop after verifying snapshot")
            reporter, visualizer = Mock(), Mock(generate_dashboard=Mock(side_effect=stop_after_read))
            service = DashboardService(self.repo, visualizer, reporter, snapshot=self.repo.read_snapshot,
                                       load_insight=Mock(side_effect=load))
            with self.assertRaisesRegex(OutputError, "stop after"):
                service.create_dashboard(self.request())
            self.assertEqual(service._load_insight.call_count, 1)
            # The callback loaded the old source inside the same snapshot as statistics.
            self.assertEqual(self.store.load(self.repo, self.request())[1], "stale")
            reporter.generate_report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
