"""Offline artifact contents, embedded images and escaping of external text."""
import base64
import unittest
from datetime import date, datetime, timezone

from src.errors import OutputError, ValidationError
from src.html_dashboard import render_dashboard_html
from src.models import (
    InsightCitation, InsightEvidenceGroup, InsightResult, KeywordCount,
    ReviewFilter, ReviewStatistics, Sentiment, SentimentAlertOptions,
)
from src.sentiment_alerts import detect_sentiment_change
from tests.html_fixtures import DashboardHTML, PNG


class HtmlDashboardTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 1, 2, 3, tzinfo=timezone.utc)
        self.filters = ReviewFilter(product_name='테스트 이어폰', date_from=date(2026, 9, 1),
                                    date_to=date(2026, 9, 22), rating_min=2)
        self.stats = ReviewStatistics(20, 10, 8, 2, average_rating=3.4,
            sentiment_counts={Sentiment.POSITIVE: 5, Sentiment.NEGATIVE: 5},
            sentiment_ratios={Sentiment.POSITIVE: .5, Sentiment.NEGATIVE: .5},
            daily_sentiment_counts={date(2026, 9, 22): {Sentiment.POSITIVE: 1, Sentiment.NEGATIVE: 4},
                                    date(2026, 9, 9): {Sentiment.POSITIVE: 4, Sentiment.NEGATIVE: 1}},
            rating_sentiment_matrix={2: {Sentiment.NEGATIVE: 5}, 5: {Sentiment.POSITIVE: 5}},
            top_positive_keywords=[KeywordCount('음질', 3)], top_negative_keywords=[KeywordCount('배송 지연', 2)])

    def render(self, **kwargs):
        return render_dashboard_html(self.stats, kwargs.pop('charts', [PNG]),
            filters=self.filters, generated_at=kwargs.pop('generated_at', self.now), **kwargs)

    def test_single_file_embeds_identical_png_without_external_assets_or_scripts(self):
        document = DashboardHTML(self.render())
        self.assertEqual(len(document.images), 1)
        self.assertEqual(base64.b64decode(document.images[0].split(',', 1)[1], validate=True), PNG)
        self.assertTrue(document.images[0].startswith('data:image/png;base64,'))
        self.assertTrue(any(tag == 'style' for tag, _ in document.elements))
        for tag, attrs in document.elements:
            self.assertNotIn(tag, ('script', 'link', 'iframe', 'object', 'embed', 'base', 'form'))
            self.assertFalse(any(key.lower().startswith('on') for key in attrs))
            if 'src' in attrs:
                self.assertTrue(attrs['src'].startswith('data:image/png;base64,'))
            if 'href' in attrs:
                self.assertTrue(attrs['href'].startswith('#'))
        policy = [attrs['content'] for tag, attrs in document.elements
                  if tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy'][0]
        self.assertIn("default-src 'none'", policy)
        self.assertIn('img-src data:', policy)

    def test_statistics_filter_metadata_and_date_order_are_visible(self):
        content = self.render()
        text = ' '.join(DashboardHTML(content).text)
        for value in ('테스트 이어폰', '2026-09-01', '2026-09-22', '최소 별점: 2',
                      '2026-09-22T01:02:03Z', '3.40', '50.0%', '미분석 (실패 제외) 8건',
                      '분석 실패 2건', '음질', '배송 지연', '2점', '5점'):
            self.assertIn(value, text)
        dates = content.split('<h2>날짜별 감정 분포</h2>', 1)[1]
        self.assertLess(dates.index('2026-09-09'), dates.index('2026-09-22'))

    def test_product_and_keyword_markup_is_visible_text_without_active_elements(self):
        malicious = '\"><script>alert(1)</script><img src="https://invalid.test/x" onerror="alert(2)">&가나다'
        self.filters.product_name = malicious
        self.stats.top_negative_keywords = [KeywordCount(malicious, 1)]
        document = DashboardHTML(self.render())
        self.assertIn(malicious, ' '.join(document.text))
        self.assertEqual(len(document.images), 1)
        self.assertNotIn('script', [tag for tag, _ in document.elements])
        self.assertFalse(any('onerror' in attrs for _, attrs in document.elements))

    def test_empty_statistics_show_unavailable_average_and_empty_states(self):
        self.stats = ReviewStatistics(0, 0, 0, 0)
        text = ' '.join(DashboardHTML(self.render()).text)
        for value in ('조건에 맞는 정제 리뷰가 없습니다', 'N/A', '분석 완료율 0.0%',
                      '집계된 키워드가 없습니다', '날짜별 집계가 없습니다'):
            self.assertIn(value, text)
        self.assertNotIn('nan', text)

    def test_saved_insight_scope_summary_and_citations_are_visible_and_escaped(self):
        malicious = '<script>alert(1)</script><img src="https://invalid.test/x">'
        insight = InsightResult(ReviewFilter(product_name=malicious, sentiment=Sentiment.NEGATIVE), 1, self.now,
            summary=malicious, issues=[malicious], improvement_suggestions=[malicious],
            positive_keywords=[KeywordCount(malicious, 1)], negative_keywords=[KeywordCount(malicious, 1)],
            evidence_groups=[InsightEvidenceGroup(None, 'complaints', malicious,
                [InsightCitation(7, malicious, malicious)]),
                InsightEvidenceGroup(malicious, 'praises', malicious, [InsightCitation(7, malicious, malicious)])])
        document = DashboardHTML(self.render(insight=insight))
        text = ' '.join(document.text)
        for value in (malicious, '제품명 없음', '리뷰 7', '분석 완료 리뷰 1건', '부정', '개선 제안',
                      '2026-09-22T01:02:03Z', '대상 범위는 서로 다를 수 있습니다'):
            self.assertIn(value, text)
        self.assertEqual(len(document.images), 1)
        self.assertNotIn('script', [tag for tag, _ in document.elements])
        self.assertFalse(any(key.lower().startswith('on') for _, attrs in document.elements for key in attrs))
        self.assertTrue(any(attrs.get('href') == '#insight' for _, attrs in document.elements))

    def test_all_evidence_is_kept_when_summary_only_covers_top_complaints(self):
        insight = InsightResult(ReviewFilter(), 2, self.now, summary='선택된 주제 요약',
            summary_scope='top_complaints', evidence_groups=[
                InsightEvidenceGroup('제품 A', 'complaints', '배터리', [
                    InsightCitation(7, '배터리', '첫 번째 인용\n두 번째 줄'),
                    InsightCitation(7, '배터리', '같은 리뷰의 다른 인용')]),
                InsightEvidenceGroup('제품 B', 'praises', '좋은 음질', [
                    InsightCitation(8, '좋은 음질', '요약에 없는 제품의 원문')])])
        document = DashboardHTML(self.render(insight=insight))
        text = ' '.join(document.text)
        for value in ('2개 주제', '주요 불편 최대 3개', '첫 번째 인용\n두 번째 줄',
                      '같은 리뷰의 다른 인용', '요약에 없는 제품의 원문', '리뷰 8'):
            self.assertIn(value, text)
        counts = self.render(insight=insight).count('<span class="insight-count">1건</span>')
        self.assertEqual(counts, 2)  # Count unique reviews, not quote fragments.
        self.assertEqual(len([tag for tag, _ in document.elements if tag == 'blockquote']), 3)

    def test_zero_review_insight_suppresses_stale_optional_content(self):
        stale = '오래된 결과는 표시하면 안 됨'
        insight = InsightResult(ReviewFilter(), 0, self.now, summary=stale, issues=[stale],
            improvement_suggestions=[stale], positive_keywords=[KeywordCount(stale, 1)],
            evidence_groups=[InsightEvidenceGroup(stale, 'complaints', stale, [InsightCitation(7, stale, stale)])])
        text = ' '.join(DashboardHTML(self.render(insight=insight)).text)
        self.assertNotIn(stale, text)
        self.assertIn('조건에 맞는 분석 완료 리뷰가 없어 AI 요약을 생성하지 않았습니다.', text)
        self.assertIn('분석 완료 리뷰 0건', text)

    def test_sentiment_warning_is_included_with_its_periods_and_counts(self):
        result = detect_sentiment_change(self.stats, self.filters, SentimentAlertOptions(), today=self.now.date())
        document = DashboardHTML(self.render(sentiment_change=result))
        self.assertIn('panel alert warning', [attrs.get('class') for _, attrs in document.elements])
        for value in ('[경고]', '20.0% → 80.0%', '+60.0%p', '2026-09-09 ~ 2026-09-15', '부정 4건'):
            self.assertIn(value, ' '.join(document.text))

    def test_optional_alert_section_is_absent_when_alerts_are_disabled(self):
        self.assertFalse(any(attrs.get('aria-label') == '감정 변화 알림'
                             for _, attrs in DashboardHTML(self.render()).elements))

    def test_multiple_charts_are_each_embedded(self):
        document = DashboardHTML(self.render(charts=[PNG, PNG]))
        self.assertEqual(len(document.images), 2)
        self.assertTrue(all(base64.b64decode(image.split(',', 1)[1]) == PNG for image in document.images))

    def test_invalid_or_missing_png_is_an_output_error(self):
        for charts in ([], [b'not png'], ['not bytes']):
            with self.subTest(charts=charts), self.assertRaises(OutputError):
                self.render(charts=charts)

    def test_timestamp_requires_a_timezone(self):
        with self.assertRaises(ValidationError):
            self.render(generated_at=datetime(2026, 9, 22))


if __name__ == '__main__':
    unittest.main()
