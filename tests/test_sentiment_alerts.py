"""Adjacent-period alert decisions, boundary dates and readable CLI messages."""
import unittest
from datetime import date

from src.errors import ValidationError
from src.models import (
    ReviewFilter, ReviewStatistics, Sentiment, SentimentAlertOptions,
    SentimentChangeStatus,
)
from src.sentiment_alerts import detect_sentiment_change, format_sentiment_change


class SentimentAlertTests(unittest.TestCase):
    today = date(2026, 9, 22)

    def detect(self, previous=(1, 5), recent=(2, 5), *, filters=None, options=None, extra=None):
        previous_negative, previous_total = previous
        recent_negative, recent_total = recent
        daily = {
            date(2026, 9, 9): {Sentiment.NEGATIVE: previous_negative,
                              Sentiment.POSITIVE: previous_total - previous_negative},
            date(2026, 9, 22): {Sentiment.NEGATIVE: recent_negative,
                               Sentiment.NEUTRAL: recent_total - recent_negative},
        }
        daily.update(extra or {})
        # Unanalyzed and failed reviews must not dilute the daily denominators.
        stats = ReviewStatistics(100, 20, 70, 10, daily_sentiment_counts=daily)
        return detect_sentiment_change(stats, filters or ReviewFilter(),
                                       options or SentimentAlertOptions(), today=self.today)

    def test_exact_threshold_warns_using_analyzed_denominators(self):
        result = self.detect()
        self.assertEqual(result.status, SentimentChangeStatus.WARNING)
        self.assertEqual((result.previous_analyzed, result.recent_analyzed), (5, 5))
        text = format_sentiment_change(result)
        for value in ('[경고]', '20.0% → 40.0%', '+20.0%p', '분석 5건 / 부정 2건'):
            self.assertIn(value, text)

    def test_threshold_is_percentage_points_not_relative_percent_growth(self):
        result = self.detect(previous=(2, 10), recent=(3, 10))
        self.assertEqual(result.status, SentimentChangeStatus.NORMAL)
        self.assertIn('+10.0%p', format_sentiment_change(result))
        self.assertNotIn('[경고]', format_sentiment_change(result))

    def test_flat_or_falling_rates_are_not_warnings(self):
        for recent in ((1, 5), (0, 5)):
            with self.subTest(recent=recent):
                self.assertEqual(self.detect(recent=recent).status, SentimentChangeStatus.NORMAL)

    def test_zero_baseline_rate_is_valid_when_sample_is_large_enough(self):
        self.assertEqual(self.detect(previous=(0, 5), recent=(5, 5)).status,
                         SentimentChangeStatus.WARNING)

    def test_sample_shortage_is_not_reported_as_normal_or_warning(self):
        for previous, recent in (((0, 0), (5, 5)), ((0, 5), (0, 0)), ((0, 4), (5, 5)), ((0, 5), (4, 4))):
            with self.subTest(previous=previous, recent=recent):
                result = self.detect(previous=previous, recent=recent)
                self.assertEqual(result.status, SentimentChangeStatus.INSUFFICIENT_DATA)
                text = format_sentiment_change(result)
                self.assertIn('[판정 보류]', text)
                self.assertIn('최소 5건', text)
                self.assertNotIn('[정상]', text)
                self.assertNotIn('[경고]', text)

    def test_all_four_boundaries_are_included_and_other_dates_are_excluded(self):
        result = self.detect(extra={
            date(2026, 9, 8): {Sentiment.NEGATIVE: 999},
            date(2026, 9, 15): {Sentiment.NEGATIVE: 2},
            date(2026, 9, 16): {Sentiment.NEGATIVE: 3},
            date(2026, 9, 23): {Sentiment.NEGATIVE: 999},
        })
        self.assertEqual((result.previous_analyzed, result.previous_negative), (7, 3))
        self.assertEqual((result.recent_analyzed, result.recent_negative), (8, 5))
        self.assertEqual((result.previous_start, result.previous_end), (date(2026, 9, 9), date(2026, 9, 15)))
        self.assertEqual((result.recent_start, result.recent_end), (date(2026, 9, 16), date(2026, 9, 22)))

    def test_date_to_anchors_historical_comparison(self):
        result = self.detect(filters=ReviewFilter(date_to=date(2026, 9, 15)))
        self.assertEqual(result.recent_start, date(2026, 9, 9))
        self.assertEqual(result.recent_end, date(2026, 9, 15))
        self.assertEqual(result.recent_analyzed, 5)
        self.assertEqual(result.previous_analyzed, 0)

    def test_date_from_must_cover_both_whole_periods(self):
        result = self.detect(filters=ReviewFilter(date_from=date(2026, 9, 10)))
        self.assertEqual(result.status, SentimentChangeStatus.INCOMPLETE_PERIOD)
        self.assertIn('--date-from을 2026-09-09', format_sentiment_change(result))
        self.assertEqual(self.detect(filters=ReviewFilter(date_from=date(2026, 9, 9))).status,
                         SentimentChangeStatus.WARNING)

    def test_sentiment_filter_cannot_produce_a_misleading_ratio(self):
        result = self.detect(filters=ReviewFilter(sentiment=Sentiment.NEGATIVE))
        self.assertEqual(result.status, SentimentChangeStatus.SENTIMENT_FILTERED)
        self.assertIn('감정 필터', format_sentiment_change(result))

    def test_custom_days_threshold_and_minimum_across_leap_day(self):
        stats = ReviewStatistics(2, 2, 0, 0, daily_sentiment_counts={
            date(2024, 2, 29): {Sentiment.POSITIVE: 1},
            date(2024, 3, 1): {Sentiment.NEGATIVE: 1},
        })
        result = detect_sentiment_change(stats, ReviewFilter(),
            SentimentAlertOptions(days=1, threshold_pp=100, min_reviews=1), today=date(2024, 3, 1))
        self.assertEqual(result.status, SentimentChangeStatus.WARNING)
        self.assertEqual(result.previous_start, date(2024, 2, 29))
        self.assertEqual(result.recent_start, date(2024, 3, 1))

    def test_invalid_options_are_validation_errors(self):
        for values in ({'days': 0}, {'days': -1}, {'days': True}, {'days': 3651},
                       {'min_reviews': 0}, {'min_reviews': True},
                       {'threshold_pp': 0}, {'threshold_pp': 101}, {'threshold_pp': True},
                       {'threshold_pp': float('nan')}, {'threshold_pp': float('inf')}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                SentimentAlertOptions(**values)

    def test_calendar_underflow_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            self.detect(filters=ReviewFilter(date_to=date.min))


if __name__ == '__main__':
    unittest.main()
