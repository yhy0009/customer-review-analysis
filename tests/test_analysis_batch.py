"""Batch state transitions and request dispatch without SQLite or network access."""

import contextlib
import copy
import io
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

from src.ai_provider import AnalysisProvider, NonRetryableAIError, ProviderResponse
from src.analysis_service import AnalysisService
from src.analyzer import BatchReviewAnalyzer
from src.cli import main
from src.errors import AIProviderError, ConfigError, StorageError, ValidationError
from src.handlers import build_analyze_handler
from src.models import (
    AnalysisOptions, AnalysisResult, AnalyzeRequest, AnalyzeTarget, CleanReview,
    ReviewDetail, Sentiment,
)
from src.services import ReviewAnalyzer


class FakeRepository:
    """Only the persistence operations used by the analyze use case."""

    def __init__(self, reviews):
        self.reviews = {r.id: copy.deepcopy(r) for r in reviews}
        self.analyses = {}
        self.failures = {}
        self.saved = []

    def get_review(self, review_id):
        if review_id not in self.reviews:
            return None
        return ReviewDetail(copy.deepcopy(self.reviews[review_id]), self.analyses.get(review_id))

    def save_analysis(self, result):
        self.analyses[result.review_id] = result
        self.failures.pop(result.review_id, None)
        self.saved.append(result.review_id)

    def mark_analysis_failed(self, review_id, error_message):
        self.analyses.pop(review_id, None)
        self.failures[review_id] = error_message

    def fetch_clean_reviews(self, filters=None, *, limit=None):
        return copy.deepcopy(list(self.reviews.values())[:limit])

    def fetch_unanalyzed_reviews(self, *, limit=None):
        return [r for r in self.fetch_clean_reviews() if r.id not in self.analyses][:limit]


class BatchAnalysisTests(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.reviews = [CleanReview(
            id=i, source_review_id=None, product_name="이어폰", rating=5,
            review_date=date(2026, 9, 1), review_text=f"리뷰 원문 {i}", cleaned_at=now,
        ) for i in (7, 12, 30)]
        self.repo = FakeRepository(self.reviews)
        self.provider = Mock(spec=AnalysisProvider)
        self.response = ProviderResponse(json.dumps({
            "sentiment": "positive", "confidence": 0.9,
            "summary": "만족합니다.", "keywords": ["음질"],
        }), "model-snapshot")
        self.provider.complete.return_value = self.response
        self.sleep = Mock()
        self.analyzer = BatchReviewAnalyzer(self.repo, self.provider, sleep=self.sleep)
        self.options = AnalysisOptions(
            provider="fake", model="fake", timeout_seconds=10, max_retries=2,
        )
        self.service = AnalysisService(self.repo, self.analyzer, self.options)

    def seed_analysis(self, review_id):
        result = AnalysisResult(
            review_id=review_id, sentiment=Sentiment.NEGATIVE, confidence=0.8,
            analyzed_at=datetime.now(timezone.utc), provider="old", model="old",
        )
        self.repo.analyses[review_id] = result
        return result

    def test_satisfies_protocol_and_saves_results_in_input_order(self):
        self.assertIsInstance(self.analyzer, ReviewAnalyzer)
        batch = self.analyzer.analyze_reviews(self.reviews, self.options)
        self.assertEqual((batch.processed, batch.succeeded, batch.failed, batch.skipped), (3, 3, 0, 0))
        self.assertEqual([r.review_id for r in batch.results], [7, 12, 30])
        self.assertEqual(self.repo.saved, [7, 12, 30])
        self.assertEqual(self.provider.complete.call_count, 3)
        self.sleep.assert_not_called()

    def test_empty_input_has_no_provider_or_repository_side_effects(self):
        with patch.object(self.repo, "get_review") as get_review:
            batch = self.analyzer.analyze_reviews([], self.options)
        self.assertEqual(batch.processed, 0)
        self.assertEqual(batch.results, [])
        get_review.assert_not_called()
        self.provider.complete.assert_not_called()
        self.assertEqual(self.repo.saved, [])

    def test_skips_existing_analysis_without_changing_metadata(self):
        original = self.seed_analysis(12)
        batch = self.analyzer.analyze_reviews(self.reviews, self.options)
        self.assertEqual((batch.succeeded, batch.skipped), (2, 1))
        self.assertIs(self.repo.analyses[12], original)
        self.assertEqual(self.repo.saved, [7, 30])
        self.assertEqual(self.provider.complete.call_count, 2)

    def test_force_overwrites_and_duplicate_ids_are_not_charged_twice(self):
        self.seed_analysis(7)
        batch = self.analyzer.analyze_reviews([self.reviews[0]] * 2, self.options, force=True)
        self.assertEqual((batch.processed, batch.succeeded, batch.skipped), (2, 1, 1))
        self.assertEqual(self.repo.analyses[7].model, "model-snapshot")
        self.provider.complete.assert_called_once()

    def test_retries_then_saves_once_and_clears_previous_failure(self):
        self.repo.failures[7] = "earlier failure"
        self.provider.complete.side_effect = [AIProviderError("secret"), self.response]
        batch = self.analyzer.analyze_reviews(self.reviews[:1], self.options)
        self.assertEqual((batch.processed, batch.succeeded, batch.failed), (1, 1, 0))
        self.assertEqual(self.provider.complete.call_count, 2)
        self.assertEqual(self.repo.saved, [7])
        self.assertNotIn(7, self.repo.failures)
        self.sleep.assert_called_once_with(1.0)

    def test_malformed_json_is_retried(self):
        self.provider.complete.side_effect = [ProviderResponse("not json", "model"), self.response]
        batch = self.analyzer.analyze_reviews(self.reviews[:1], self.options)
        self.assertEqual(batch.succeeded, 1)
        self.assertEqual(self.provider.complete.call_count, 2)

    def test_exhaustion_marks_failure_continues_and_does_not_leak_errors(self):
        self.provider.complete.side_effect = [self.response] + [AIProviderError("private-key raw-text")] * 3 + [self.response]
        with self.assertLogs("customer_review_analysis.analyzer", level="INFO") as logs:
            batch = self.analyzer.analyze_reviews(self.reviews, self.options)
        self.assertEqual((batch.processed, batch.succeeded, batch.failed), (3, 2, 1))
        self.assertEqual(self.provider.complete.call_count, 5)
        self.assertEqual(self.repo.saved, [7, 30])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1.0, 2.0])
        self.assertEqual(batch.errors[0].item_ref, "12")
        self.assertTrue(batch.errors[0].retryable)
        combined = str(logs.output) + str(batch.errors) + str(self.repo.failures)
        self.assertNotIn("private-key", combined)
        self.assertNotIn("raw-text", combined)
        self.assertNotIn(self.reviews[1].review_text, combined)

    def test_zero_retries_means_one_attempt(self):
        self.provider.complete.side_effect = AIProviderError("failed")
        batch = self.analyzer.analyze_reviews(self.reviews[:1], replace(self.options, max_retries=0))
        self.assertEqual(batch.failed, 1)
        self.provider.complete.assert_called_once()
        self.sleep.assert_not_called()

    def test_retry_delay_is_capped(self):
        self.provider.complete.side_effect = AIProviderError("failed")
        self.analyzer.analyze_reviews(self.reviews[:1], replace(self.options, max_retries=6))
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [1, 2, 4, 8, 16, 16])
        self.assertEqual(self.provider.complete.call_count, 7)

    def test_terminal_failure_skips_retries_and_removes_old_result_on_force(self):
        self.seed_analysis(7)
        self.provider.complete.side_effect = [NonRetryableAIError("private"), self.response]
        batch = self.analyzer.analyze_reviews(self.reviews[:2], self.options, force=True)
        self.assertEqual((batch.failed, batch.succeeded), (1, 1))
        self.assertFalse(batch.errors[0].retryable)
        self.assertNotIn(7, self.repo.analyses)
        self.assertIn(7, self.repo.failures)
        self.assertEqual(self.provider.complete.call_count, 2)
        self.sleep.assert_not_called()

    def test_duplicate_failed_review_is_not_retried_as_a_new_item(self):
        self.provider.complete.side_effect = NonRetryableAIError("failed")
        batch = self.analyzer.analyze_reviews([self.reviews[0]] * 2, self.options, force=True)
        self.assertEqual((batch.failed, batch.skipped), (1, 1))
        self.provider.complete.assert_called_once()

    def test_missing_and_stale_reviews_fail_without_provider_or_status_writes(self):
        reviews = [replace(self.reviews[0], id=999), replace(self.reviews[1], review_text="old")]
        batch = self.analyzer.analyze_reviews(reviews, self.options)
        self.assertEqual([e.code for e in batch.errors], ["REVIEW_NOT_FOUND", "STALE_REVIEW"])
        self.assertEqual(batch.failed, 2)
        self.assertEqual(self.repo.failures, {})
        self.provider.complete.assert_not_called()

    def test_invalid_input_is_rejected_before_processing_any_item(self):
        with self.assertRaises(ValidationError):
            self.analyzer.analyze_reviews([self.reviews[0], None], self.options)
        with self.assertRaises(ValidationError):
            self.analyzer.analyze_reviews(self.reviews, self.options, force="false")
        with self.assertRaises(ConfigError):
            self.analyzer.analyze_reviews(self.reviews, replace(self.options, prompt_version="bad"))
        self.provider.complete.assert_not_called()
        self.assertEqual(self.repo.saved, [])

    def test_configuration_errors_abort_without_failure_status_or_retry(self):
        self.provider.complete.side_effect = ConfigError("missing key")
        with self.assertRaises(ConfigError):
            self.analyzer.analyze_reviews(self.reviews, self.options)
        self.provider.complete.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(self.repo.failures, {})

    def test_storage_write_failure_is_not_retried_or_marked_as_ai_failure(self):
        save = self.repo.save_analysis

        def fail_second(result):
            if result.review_id == 12:
                raise StorageError("disk full")
            save(result)

        with patch.object(self.repo, "save_analysis", side_effect=fail_second):
            with self.assertRaises(StorageError):
                self.analyzer.analyze_reviews(self.reviews, self.options)
        self.assertEqual(self.repo.saved, [7])
        self.assertEqual(self.provider.complete.call_count, 2)
        self.assertEqual(self.repo.failures, {})
        self.sleep.assert_not_called()

    def test_storage_read_or_failure_status_error_aborts(self):
        with patch.object(self.repo, "get_review", side_effect=StorageError("closed")):
            with self.assertRaises(StorageError):
                self.analyzer.analyze_reviews(self.reviews, self.options)
        self.provider.complete.assert_not_called()
        self.provider.complete.side_effect = NonRetryableAIError("failed")
        with patch.object(self.repo, "mark_analysis_failed", side_effect=StorageError("disk full")):
            with self.assertRaises(StorageError):
                self.analyzer.analyze_reviews(self.reviews, self.options)
        self.provider.complete.assert_called_once()

    def test_keyboard_interrupt_preserves_existing_results_and_stops(self):
        self.provider.complete.side_effect = [self.response, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.analyzer.analyze_reviews(self.reviews, self.options)
        self.assertEqual(self.repo.saved, [7])
        self.assertEqual(self.repo.failures, {})

    def test_service_selects_unanalyzed_including_failed_with_limit(self):
        self.seed_analysis(7)
        self.repo.failures[12] = "earlier failure"
        result = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.UNANALYZED, limit=1))
        self.assertEqual([r.review_id for r in result.results], [12])
        self.assertNotIn(12, self.repo.failures)
        self.assertNotIn(30, self.repo.analyses)

    def test_service_all_limit_and_single_id_force(self):
        self.seed_analysis(7)
        batch = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.ALL, limit=2))
        self.assertEqual((batch.processed, batch.succeeded, batch.skipped), (2, 1, 1))
        batch = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.REVIEW_ID, review_id=7, force=True))
        self.assertEqual([r.review_id for r in batch.results], [7])

    def test_service_missing_id_errors_and_force_does_not_expand_unanalyzed(self):
        with self.assertRaises(ValidationError):
            self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.REVIEW_ID, review_id=999))
        self.seed_analysis(7)
        batch = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.UNANALYZED, force=True))
        self.assertEqual([r.review_id for r in batch.results], [12, 30])
        self.assertEqual(self.repo.analyses[7].model, "old")

    def run_cli(self, *arguments):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            # Config/logging initialization is unrelated and would write project logs.
            with patch("src.cli.load_env_file"), patch("src.cli.load_config", return_value={}), patch("src.cli.configure_logging"):
                code = main(["analyze", *arguments], handlers={
                    "analyze": build_analyze_handler(self.service.analyze_reviews),
                })
        return code, output.getvalue(), error.getvalue()

    def test_cli_success_partial_failure_and_error_exit_codes(self):
        code, output, _ = self.run_cli("--id", "7")
        self.assertEqual(code, 0)
        self.assertIn("processed=1 succeeded=1 skipped=0 failed=0", output)
        self.provider.complete.side_effect = NonRetryableAIError("secret")
        code, output, error = self.run_cli("--all")
        self.assertEqual(code, 1)
        self.assertIn("processed=3 succeeded=0 skipped=1 failed=2", output)
        self.assertNotIn("secret", output + error)
        code, _, _ = self.run_cli("--id", "999")
        self.assertEqual(code, 2)
        with patch.object(self.repo, "fetch_unanalyzed_reviews", side_effect=StorageError("db unavailable")):
            code, _, _ = self.run_cli("--unanalyzed")
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
