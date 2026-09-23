"""Console insight scope, missing content, and untrusted text presentation."""

import contextlib
import copy
import io
import unittest
from datetime import date, datetime, timezone

from src.models import InsightResult, KeywordCount, ReviewFilter, Sentiment
from src.query_output import format_insight_result


NOW = datetime(2026, 9, 14, 4, 5, 6, tzinfo=timezone.utc)


def insight_fixture(**changes):
    values = dict(
        filters=ReviewFilter(), review_count=3, generated_at=NOW,
        positive_keywords=[KeywordCount("음질 🎧", 2), KeywordCount("배송", 1)],
        negative_keywords=[KeywordCount("배터리", 1)],
        summary="음질에 만족하지만 배터리 개선이 필요합니다.",
        issues=["배터리 사용 시간이 짧습니다."],
        improvement_suggestions=["배터리 용량을 검토하세요."],
    )
    values.update(changes)
    return InsightResult(**values)


class InsightOutputTests(unittest.TestCase):
    def test_output_identifies_actual_analyzed_scope_and_keyword_denominator(self):
        result = format_insight_result(insight_fixture())

        for expected in (
            "인사이트 생성 시각 (UTC): 2026-09-14T04:05:06Z",
            "실제 추출 대상: 분석 완료 리뷰 3건",
            "필터: 추가 필터 없음",
            "키워드 빈도는 해당 키워드를 포함한 리뷰 수입니다.",
            "긍정 키워드:\n  - 음질 🎧: 2건\n  - 배송: 1건",
            "부정 키워드:\n  - 배터리: 1건",
            "요약:\n  음질에 만족하지만 배터리 개선이 필요합니다.",
            "주요 이슈:\n  - 배터리 사용 시간이 짧습니다.",
            "개선 제안:\n  - 배터리 용량을 검토하세요.",
        ):
            self.assertIn(expected, result)
        self.assertNotIn("전체 리뷰", result)

    def test_combined_filters_show_dates_sentiment_product_and_minimum_rating(self):
        filters = ReviewFilter(
            sentiment=Sentiment.NEGATIVE, date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 14), product_name="이어폰 🎧", rating_min=2,
        )

        result = format_insight_result(insight_fixture(filters=filters))

        for expected in ("감정=부정", "기간=2026-09-01 ~ 2026-09-14",
                         "제품명 포함=이어폰 🎧", "최소 별점=2/5"):
            self.assertIn(expected, result)

    def test_open_ended_dates_exact_rating_and_other_sentiments(self):
        cases = (
            (ReviewFilter(date_from=date(2026, 9, 1), rating=4,
                          sentiment=Sentiment.POSITIVE),
             ("기간=2026-09-01 ~ 종료 제한 없음", "별점=4/5", "감정=긍정")),
            (ReviewFilter(date_to=date(2026, 9, 14), sentiment=Sentiment.NEUTRAL),
             ("기간=시작 제한 없음 ~ 2026-09-14", "감정=중립")),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                result = format_insight_result(insight_fixture(filters=filters))
                for text in expected:
                    self.assertIn(text, result)

    def test_zero_review_result_suppresses_stale_ai_content_and_keyword_counts(self):
        insight = insight_fixture(review_count=0, filters=ReviewFilter(product_name="없는 제품"))

        result = format_insight_result(insight)

        self.assertIn("실제 추출 대상: 분석 완료 리뷰 0건", result)
        self.assertIn("제품명 포함=없는 제품", result)
        self.assertIn("조건에 맞는 분석 완료 리뷰가 없어 AI 요약을 생성하지 않았습니다.", result)
        self.assertNotIn(insight.summary, result)
        self.assertNotIn(insight.issues[0], result)
        self.assertNotIn(insight.improvement_suggestions[0], result)
        self.assertNotIn("긍정 키워드", result)
        self.assertNotIn("음질 🎧", result)

    def test_missing_content_and_control_only_prose_have_readable_fallbacks(self):
        insight = insight_fixture(
            positive_keywords=[], negative_keywords=[], summary="\x1b[31m\x00\x1b[0m",
            issues=["\x1b]0;숨김\x07"], improvement_suggestions=[],
        )

        result = format_insight_result(insight)

        self.assertIn("긍정 키워드:\n  집계된 키워드가 없습니다.", result)
        self.assertIn("부정 키워드:\n  집계된 키워드가 없습니다.", result)
        self.assertIn("요약:\n  요약이 없습니다.", result)
        self.assertIn("주요 이슈:\n  제공된 항목이 없습니다.", result)
        self.assertIn("개선 제안:\n  제공된 항목이 없습니다.", result)

    def test_all_external_text_is_terminal_safe_without_losing_unicode(self):
        insight = insight_fixture(
            filters=ReviewFilter(product_name="제품\x1b]0;창 제목\x07\n 🎧"),
            positive_keywords=[KeywordCount("음질\x1b[2J\t만족 👩‍💻", 2)],
            negative_keywords=[KeywordCount("배터리\x9b2J\x7f", 1)],
            summary="\x1b[31m한글\x1b[0m\r\n"
                    "\x1b]8;;https://example.test\x1b\\링크\x1b]8;;\x1b\\ 🎧\x00",
            issues=["잡음\x1bP숨김 내용\x1b\\\n발생\x08"],
            improvement_suggestions=["개선\x9d0;C1 제목\x9c\x07\t검토 👩‍💻"],
        )

        result = format_insight_result(insight)

        for expected in ("제품명 포함=제품 🎧", "음질 만족 👩‍💻: 2건", "배터리: 1건",
                         "요약:\n  한글\n  링크 🎧", "  - 잡음 발생", "  - 개선 검토 👩‍💻"):
            self.assertIn(expected, result)
        for unexpected in ("\x1b", "\x00", "\x07", "\x08", "\x7f", "\x9b", "\x9c", "\x9d",
                           "\t", "\r", "창 제목", "숨김 내용", "C1 제목", "https://"):
            self.assertNotIn(unexpected, result)

    def test_formatter_does_not_print_or_modify_input(self):
        insight = insight_fixture()
        original = copy.deepcopy(insight)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            result = format_insight_result(insight)

        self.assertIsInstance(result, str)
        self.assertTrue(result)
        self.assertEqual(stream.getvalue(), "")
        self.assertEqual(insight, original)


if __name__ == "__main__":
    unittest.main()
