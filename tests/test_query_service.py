"""Query service boundary tests: preserve requests, results, and storage errors."""

import contextlib
import io
import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock

from src.errors import StorageError
from src.models import (
    CleanReview, ListRequest, Page, ReviewDetail, ReviewFilter, ReviewQuery,
    ReviewStatistics, Sentiment, ShowRequest, SortField, SortOrder, StatsRequest,
)
from src.query_service import QueryService
from src.storage import ReviewRepository


class QueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock(spec=ReviewRepository)
        self.service = QueryService(self.repository)

    def test_list_preserves_combined_filters_pagination_and_order(self):
        query = ReviewQuery(
            filters=ReviewFilter(
                sentiment=Sentiment.NEGATIVE,
                product_name="이어폰_%",
                date_from=date(2026, 9, 1),
                date_to=date(2026, 9, 11),
                rating_min=2,
            ),
            page=3, size=2, sort=SortField.DATE, order=SortOrder.ASC,
        )
        page = Page([], 3, 2, 3, 2)
        self.repository.list_reviews.return_value = page

        self.assertIs(self.service.list_reviews(ListRequest(query)), page)
        self.repository.list_reviews.assert_called_once_with(query)
        self.assertEqual(query.filters.product_name, "이어폰_%")
        self.assertEqual(query.page, 3)
        self.repository.get_statistics.assert_not_called()

    def test_show_preserves_detail_and_missing_result_without_output(self):
        review = CleanReview(
            id=7, product_name="이어폰", review_date=date(2026, 9, 11),
            rating=4, review_text="서비스에서 출력하면 안 되는 본문",
            cleaned_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )
        detail = ReviewDetail(review)
        self.repository.get_review.side_effect = [detail, None]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertIs(self.service.show_review(ShowRequest(7)), detail)
            self.assertIsNone(self.service.show_review(ShowRequest(999)))

        self.assertEqual(output.getvalue(), "")
        self.assertEqual([call.args for call in self.repository.get_review.call_args_list],
                         [(7,), (999,)])

    def test_stats_returns_shared_aggregate_without_loading_reviews(self):
        filters = ReviewFilter(product_name="키보드", rating=5)
        stats = ReviewStatistics(3, 1, 1, 1)
        self.repository.get_statistics.return_value = stats

        self.assertIs(self.service.get_statistics(StatsRequest(filters)), stats)
        self.repository.get_statistics.assert_called_once_with(filters)
        self.repository.fetch_clean_reviews.assert_not_called()
        self.repository.list_reviews.assert_not_called()

    def test_storage_errors_reach_cli_adapter_unchanged(self):
        cases = (
            ("list_reviews", "list_reviews", ListRequest(ReviewQuery())),
            ("show_review", "get_review", ShowRequest(7)),
            ("get_statistics", "get_statistics", StatsRequest(ReviewFilter())),
        )
        for method, repository_method, request in cases:
            with self.subTest(method=method):
                error = StorageError("저장소를 읽을 수 없습니다.")
                getattr(self.repository, repository_method).side_effect = error
                with self.assertRaises(StorageError) as caught:
                    getattr(self.service, method)(request)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
