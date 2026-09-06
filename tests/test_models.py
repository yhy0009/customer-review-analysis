"""Contract tests for shared boundary models."""

import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.errors import AppError, ConfigError, ValidationError
from src.models import (
    AnalysisResult,
    BatchOperationResult,
    CleanBatchResult,
    CleanReview,
    DuplicatePolicy,
    ItemError,
    OutputArtifact,
    OutputKind,
    Page,
    ProcessingStatus,
    RawReview,
    ReviewFilter,
    Sentiment,
)


UTC_NOW = datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc)


class EnumContractTests(unittest.TestCase):
    def test_serialized_enum_values_are_stable(self) -> None:
        self.assertEqual(Sentiment.POSITIVE.value, "positive")
        self.assertEqual(DuplicatePolicy.UPSERT.value, "upsert")
        self.assertEqual(ProcessingStatus.ANALYSIS_FAILED.value, "ANALYSIS_FAILED")

    def test_config_error_remains_in_application_error_hierarchy(self) -> None:
        self.assertTrue(issubclass(ConfigError, AppError))


class ReviewModelTests(unittest.TestCase):
    def test_raw_review_accepts_unvalidated_values(self) -> None:
        review = RawReview(review_date="not-a-date", rating="A+", review_text=None)

        self.assertEqual(review.review_date, "not-a-date")
        self.assertEqual(review.rating, "A+")
        self.assertIsNone(review.review_text)

    def test_raw_payload_defaults_are_not_shared(self) -> None:
        first = RawReview()
        second = RawReview()

        first.raw_payload["source"] = "first"

        self.assertEqual(second.raw_payload, {})

    def test_clean_review_enforces_rating_and_utc_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            CleanReview(1, "상품", date(2026, 9, 6), 6, "좋아요", UTC_NOW)

        non_utc = datetime(2026, 9, 6, tzinfo=timezone(timedelta(hours=9)))
        with self.assertRaises(ValidationError):
            CleanReview(1, "상품", date(2026, 9, 6), 5, "좋아요", non_utc)

    def test_analysis_enforces_confidence_range(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisResult(
                review_id=1,
                sentiment=Sentiment.POSITIVE,
                confidence=1.1,
                analyzed_at=UTC_NOW,
                provider="test",
                model="fake",
            )

    def test_analysis_keyword_defaults_are_not_shared(self) -> None:
        first = AnalysisResult(
            1, Sentiment.POSITIVE, 0.9, UTC_NOW, "test", "fake"
        )
        second = AnalysisResult(
            2, Sentiment.NEUTRAL, 0.5, UTC_NOW, "test", "fake"
        )

        first.keywords.append("배송")

        self.assertEqual(second.keywords, [])


class QueryAndResultTests(unittest.TestCase):
    def test_filter_rejects_reversed_dates_and_conflicting_ratings(self) -> None:
        with self.assertRaises(ValidationError):
            ReviewFilter(date_from=date(2026, 9, 7), date_to=date(2026, 9, 6))

        with self.assertRaises(ValidationError):
            ReviewFilter(rating=5, rating_min=3)

        with self.assertRaises(ValidationError):
            ReviewFilter(date_from=UTC_NOW)

    def test_page_validates_total_pages(self) -> None:
        page = Page(items=["a"], page=1, size=2, total_items=3, total_pages=2)
        self.assertEqual(page.total_pages, 2)

        with self.assertRaises(ValidationError):
            Page(items=[], page=1, size=2, total_items=3, total_pages=1)

    def test_batch_counts_and_error_count_are_validated(self) -> None:
        partial = BatchOperationResult(
            processed=2,
            succeeded=1,
            failed=1,
            errors=[ItemError("2", "FAILED", "실패")],
        )
        self.assertTrue(partial.is_partial_failure)

        with self.assertRaises(ValidationError):
            BatchOperationResult(processed=2, succeeded=1)

        with self.assertRaises(ValidationError):
            CleanBatchResult(processed=1, succeeded=1)

    def test_batch_error_defaults_are_not_shared(self) -> None:
        first = BatchOperationResult(processed=0, succeeded=0)
        second = BatchOperationResult(processed=0, succeeded=0)

        first.errors.append(ItemError("1", "IGNORED", "테스트"))

        self.assertEqual(second.errors, [])

    def test_output_artifact_requires_absolute_path(self) -> None:
        with self.assertRaises(ValidationError):
            OutputArtifact(OutputKind.REPORT, Path("output/report.md"), "md")


if __name__ == "__main__":
    unittest.main()
