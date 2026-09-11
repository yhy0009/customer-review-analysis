"""Readable query output for empty data, mixed statuses, and Unicode content."""

import contextlib
import io
import unittest
from datetime import date, datetime, timezone

from src.models import (
    AnalysisResult, CleanReview, KeywordCount, Page, ReviewDetail,
    ReviewStatistics, Sentiment,
)
from src.query_output import (
    format_review_detail, format_review_list, format_statistics,
)


NOW = datetime(2026, 9, 11, 4, 5, 6, tzinfo=timezone.utc)


def detail_fixture(*, analyzed=True, text="음질이 좋아요 🎧\n배송도 빨라요."):
    review = CleanReview(
        id=17, source_review_id="source-017", product_name="이어폰 🎧",
        review_date=date(2026, 9, 10), rating=5,
        review_text=text, cleaned_at=NOW,
    )
    analysis = AnalysisResult(
        review_id=17, sentiment=Sentiment.POSITIVE, confidence=.925,
        analyzed_at=NOW, provider="test-provider", model="test-model",
        summary="음질과 배송에 만족합니다.", keywords=["음질", "배송"],
        prompt_version="v1",
    ) if analyzed else None
    return ReviewDetail(review, analysis)


class QueryOutputTests(unittest.TestCase):
    def test_list_has_page_context_and_compact_unicode_content(self):
        detail = detail_fixture(text="음질이 좋아요 🎧\n" + "만족" * 80)
        page = Page([detail], page=2, size=1, total_items=3, total_pages=3)

        result = format_review_list(page)

        for expected in ("총 3건", "페이지 2/3", "페이지 크기 1건", "ID 17",
                         "2026-09-10", "별점 5/5", "이어폰 🎧", "감정: 긍정",
                         "신뢰도: 92.5%", "음질이 좋아요 🎧 만족"):
            self.assertIn(expected, result)
        self.assertTrue(result.endswith("…"))
        self.assertNotIn("만족" * 80, result)
        self.assertIn("\n", detail.review.review_text)

    def test_empty_list_and_page_beyond_range_are_distinguished(self):
        empty = format_review_list(Page([], 1, 20, 0, 0))
        beyond = format_review_list(Page([], 3, 20, 25, 2))

        self.assertIn("총 0건", empty)
        self.assertIn("페이지 없음", empty)
        self.assertIn("조회된 정제 리뷰가 없습니다.", empty)
        self.assertIn("총 25건", beyond)
        self.assertIn("페이지 3/2", beyond)
        self.assertIn("요청한 페이지에 리뷰가 없습니다.", beyond)

    def test_detail_preserves_full_body_and_analysis_metadata(self):
        detail = detail_fixture(text="첫 줄 👩‍💻\n" + "긴 리뷰 본문" * 50)

        result = format_review_detail(detail)

        for expected in (detail.review.review_text, "리뷰 ID: 17", "source-017",
                         "정제 시각 (UTC): 2026-09-11T04:05:06Z",
                         "음질과 배송에 만족합니다.", "키워드: 음질, 배송",
                         "제공자: test-provider", "모델: test-model",
                         "분석 시각 (UTC): 2026-09-11T04:05:06Z",
                         "프롬프트 버전: v1"):
            self.assertIn(expected, result)
        self.assertNotIn("…", result)

    def test_missing_analysis_does_not_claim_failed_or_never_analyzed(self):
        detail = detail_fixture(analyzed=False)
        outputs = (
            format_review_detail(detail),
            format_review_list(Page([detail], 1, 20, 1, 1)),
        )
        for result in outputs:
            self.assertIn("분석 결과 없음", result)
            self.assertNotIn("분석 실패", result)
            self.assertNotIn("신뢰도:", result)

    def test_optional_summary_keywords_and_source_id_have_readable_fallbacks(self):
        detail = detail_fixture()
        detail.review.source_review_id = None
        detail.analysis.summary = None
        detail.analysis.keywords = []
        detail.analysis.prompt_version = None

        result = format_review_detail(detail)

        self.assertIn("원본 리뷰 ID: 없음", result)
        self.assertIn("요약:\n없음", result)
        self.assertIn("키워드: 없음", result)
        self.assertNotIn("프롬프트 버전:", result)

    def test_terminal_controls_removed_without_losing_text_or_emoji(self):
        detail = detail_fixture(
            text="\x1b[31m한글\x1b[0m\n"
                 "\x1b]8;;https://example.test\x1b\\링크\x1b]8;;\x1b\\"
                 "\x07\x00\x9b2J 👩‍💻"
        )
        detail.review.product_name = "제품\x1b]0;창 제목\x07 🎧"
        detail.analysis.summary = "요약\x1bP숨김 내용\x1b\\"
        detail.analysis.keywords = ["음질\x1b[2J"]
        detail.analysis.provider = "제공자\x08"
        detail.analysis.model = "모델\x7f"
        detail.analysis.prompt_version = "v1\x1b[0m"

        result = format_review_detail(detail)
        listing = format_review_list(Page([detail], 1, 20, 1, 1))

        self.assertIn("한글\n링크 👩‍💻", result)
        self.assertIn("제품 🎧", result)
        for output in (result, listing):
            for unexpected in ("\x1b", "\x07", "\x00", "\x08", "\x7f", "\x9b",
                               "https://", "창 제목", "숨김 내용"):
                self.assertNotIn(unexpected, output)

    def test_stats_shows_disjoint_status_counts_and_analyzed_denominator(self):
        stats = ReviewStatistics(
            total_reviews=8, analyzed_reviews=4, unanalyzed_reviews=3, failed_reviews=1,
            average_rating=4.125,
            sentiment_counts={Sentiment.POSITIVE: 3, Sentiment.NEGATIVE: 1},
            sentiment_ratios={Sentiment.POSITIVE: .75, Sentiment.NEGATIVE: .25},
            top_positive_keywords=[KeywordCount("음질", 3)],
            top_negative_keywords=[KeywordCount("배터리", 1)],
        )

        result = format_statistics(stats)

        for expected in ("정제 리뷰 수: 8건", "분석 완료: 4건", "미분석 (실패 제외): 3건",
                         "분석 실패: 1건", "분석 완료율: 50.0% (정제 리뷰 수 기준)",
                         "평균 별점: 4.12/5", "감정 분포 (분석 완료 4건 기준)",
                         "긍정: 3건 (75.0%)", "중립: 0건 (0.0%)", "부정: 1건 (25.0%)",
                         "긍정 TOP 키워드: 음질 (3건)", "부정 TOP 키워드: 배터리 (1건)"):
            self.assertIn(expected, result)

    def test_empty_statistics_avoid_division_by_zero_and_meaningless_average(self):
        result = format_statistics(ReviewStatistics(0, 0, 0, 0))

        self.assertIn("분석 완료율: 0.0%", result)
        self.assertIn("평균 별점: N/A", result)
        self.assertIn("감정 분포 (분석 완료 0건 기준)", result)
        for label in ("긍정", "중립", "부정"):
            self.assertIn(f"{label}: 0건 (0.0%)", result)

    def test_formatters_return_text_without_printing(self):
        detail = detail_fixture()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            outputs = (
                format_review_list(Page([detail], 1, 20, 1, 1)),
                format_review_detail(detail),
                format_statistics(ReviewStatistics(0, 0, 0, 0)),
            )
        self.assertTrue(all(isinstance(result, str) and result for result in outputs))
        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
