"""Report content and publication contracts, including real SQLite consumption."""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.errors import OutputError, ValidationError
from src.insight_extractor import AIInsightExtractor
from src.insight_service import InsightService
from src.models import (
    AnalysisOptions, AnalysisResult, CleanReview, DuplicatePolicy, ExtractRequest,
    InsightResult, KeywordCount, OutputKind, RawReview, ReportFormat, ReviewFilter,
    ReviewStatistics, Sentiment,
)
from src.reporter import FileReportGenerator
from src.services import ReportGenerator
from src.storage import SQLiteReviewRepository


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2026, 9, 13, 1, 2, 3, tzinfo=timezone.utc)
        self.reporter = FileReportGenerator(clock=lambda: self.now)
        self.stats = ReviewStatistics(
            total_reviews=5, analyzed_reviews=3, unanalyzed_reviews=1, failed_reviews=1,
            average_rating=3.4,
            sentiment_counts={Sentiment.POSITIVE: 2, Sentiment.NEGATIVE: 1},
            sentiment_ratios={Sentiment.POSITIVE: 2/3, Sentiment.NEGATIVE: 1/3},
            daily_sentiment_counts={date(2026, 9, 2): {Sentiment.NEGATIVE: 1},
                                    date(2026, 9, 1): {Sentiment.POSITIVE: 2}},
            rating_sentiment_matrix={1: {Sentiment.NEGATIVE: 1}, 5: {Sentiment.POSITIVE: 2}},
            top_positive_keywords=[KeywordCount("음질", 2), KeywordCount("배송", 1)],
            top_negative_keywords=[KeywordCount("포장", 1)],
        )
        self.insight = InsightResult(
            filters=ReviewFilter(sentiment=Sentiment.NEGATIVE, product_name="테스트 이어폰",
                                 date_from=date(2026, 9, 1), rating_min=1),
            review_count=1, generated_at=self.now,
            summary="포장 손상에 대한 불만이 있습니다.", issues=["포장 손상"],
            improvement_suggestions=["출고 전 포장 검수를 권장합니다."],
            negative_keywords=[KeywordCount("포장", 1)],
        )

    def generate(self, fmt=ReportFormat.MARKDOWN, **kwargs):
        artifact = self.reporter.generate_report(self.stats, self.insight, self.root / ("report." + fmt.value),
                                                report_format=fmt, **kwargs)
        return artifact, artifact.path.read_text(encoding="utf-8")

    def assert_no_temporary(self):
        self.assertEqual(list(self.root.rglob(".review-report-*")), [])

    def test_protocol_utf8_artifact_and_no_console_output(self):
        self.assertIsInstance(self.reporter, ReportGenerator)
        for fmt in ReportFormat:
            with self.subTest(fmt=fmt), contextlib.redirect_stdout(io.StringIO()) as stdout:
                artifact, content = self.generate(fmt)
                self.assertEqual(artifact.kind, OutputKind.REPORT)
                self.assertEqual(artifact.format, fmt.value)
                self.assertTrue(artifact.path.is_absolute())
                self.assertFalse(artifact.path.read_bytes().startswith(b"\xef\xbb\xbf"))
                self.assertIn("고객 리뷰 종합 리포트", content)
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(content.endswith("\n"))
        self.assert_no_temporary()

    def test_markdown_has_coverage_denominators_and_all_aggregates(self):
        _, text = self.generate()
        for value in ("| 정제 리뷰 | 5건 |", "| 미분석 (실패 제외) | 1건 |",
                      "| 분석 완료율 (정제 리뷰 기준) | 60.0% |",
                      "| 분석 실패율 (정제 리뷰 기준) | 20.0% |", "| 평균 별점 | 3.40/5 |",
                      "| 긍정 | 2건 | 66.7% |", "| 중립 | 0건 | 0.0% |",
                      "| 1 | 0건 | 0건 | 1건 |", "| 5 | 2건 | 0건 | 0건 |",
                      "| 1 | 음질 | 2건 |", "2026-09-13T01:02:03Z"):
            self.assertIn(value, text)
        self.assertLess(text.index("| 2026-09-01 |"), text.index("| 2026-09-02 |"))

    def test_txt_remains_readable_without_markdown_tables(self):
        _, text = self.generate(ReportFormat.TXT)
        self.assertFalse(text.startswith("#"))
        self.assertNotIn("| ---", text)
        self.assertIn("분석 완료율 (정제 리뷰 기준) / 60.0%", text)
        self.assertIn("긍정 / 2건 / 66.7%", text)
        self.assertIn("- 포장 손상", text)

    def test_insight_scope_is_separate_and_inputs_unchanged(self):
        before = copy.deepcopy((self.stats, self.insight))
        _, text = self.generate()
        self.assertIn("실제 추출 대상: 분석 완료 리뷰 1건", text)
        self.assertIn("감정=부정", self.generate(ReportFormat.TXT)[1])
        self.assertIn("대상 범위는 서로 다를 수 있습니다", text)
        self.assertIn("추출 대상 부정 TOP 10", text)
        self.assertEqual((self.stats, self.insight), before)

    def test_missing_and_empty_insights_never_invent_a_summary(self):
        for insight in (None, InsightResult(ReviewFilter(), 0, self.now, summary="stale invented narrative")):
            with self.subTest(insight=insight):
                artifact = self.reporter.generate_report(self.stats, insight, self.root / "optional.md",
                                                         report_format=ReportFormat.MARKDOWN, force=True)
                text = artifact.path.read_text()
                self.assertIn("AI 인사이트가 제공되지 않았습니다" if insight is None else "AI 요약을 생성하지 않았습니다", text)
                self.assertNotIn("stale invented narrative", text)

    def test_empty_statistics_do_not_divide_by_zero(self):
        self.stats = ReviewStatistics(0, 0, 0, 0)
        self.insight = None
        _, text = self.generate()
        self.assertIn("| 평균 별점 | N/A |", text)
        self.assertIn("| 분석 완료율 (정제 리뷰 기준) | 0.0% |", text)
        self.assertIn("통계 대상 정제 리뷰가 없습니다", text)
        self.assertIn("집계된 키워드가 없습니다", text)

    def test_top_n_preserves_repository_ranking(self):
        self.reporter = FileReportGenerator(top_n=1)
        _, text = self.generate()
        self.assertIn("통계 대상 TOP 1 키워드", text)
        self.assertIn("| 1 | 음질 | 2건 |", text)
        self.assertNotIn("| 2 | 배송 |", text)

    def test_external_text_cannot_add_markdown_structure_or_html(self):
        malicious = '배송|불만\n## 새 제목 <img src="https://example.test/x"> ![link](https://example.test) `code`\x1b'
        self.stats.top_positive_keywords = [KeywordCount(malicious, 1)]
        self.insight.summary = malicious
        self.insight.filters.product_name = malicious
        self.insight.issues = [malicious]
        _, text = self.generate()
        self.assertNotIn('\n## 새 제목', text)
        self.assertNotIn('<img', text)
        self.assertNotIn('![link](', text)
        self.assertNotIn('\x1b', text)
        self.assertIn('배송\\|불만', text)
        self.assertIn('&lt;img', text)
        _, plain = self.generate(ReportFormat.TXT)
        self.assertIn('![link](https://example.test)', plain)
        self.assertNotIn('\x1b', plain)

    def test_directory_name_and_relative_path_use_utc_and_project_root(self):
        with patch("src.config.PROJECT_ROOT", self.root):
            artifact = self.reporter.generate_report(self.stats, None, Path("nested/reports"),
                                                     report_format=ReportFormat.TXT)
        self.assertEqual(artifact.path, self.root / "nested/reports/report_20260913_010203.txt")
        directory = self.root / "reports.v1"
        directory.mkdir()
        artifact = self.reporter.generate_report(self.stats, None, directory, report_format=ReportFormat.MARKDOWN)
        self.assertEqual(artifact.path.name, "report_20260913_010203.md")

    def test_no_overwrite_then_force_replaces_complete_file(self):
        path = self.root / "report.md"
        path.write_text("original")
        with self.assertRaises(OutputError):
            self.generate()
        self.assertEqual(path.read_text(), "original")
        self.generate(force=True)
        self.assertIn("고객 리뷰", path.read_text())
        self.assert_no_temporary()

    def test_failed_write_and_replace_preserve_original_and_clean_temporary(self):
        path = self.root / "report.md"
        path.write_text("original")
        for operation in ("pathlib.Path.write_text", "src.reporter.os.replace"):
            with self.subTest(operation=operation):
                with patch(operation, side_effect=OSError("sensitive-content")):
                    with self.assertRaises(OutputError) as error:
                        self.generate(force=True)
                self.assertNotIn("sensitive-content", str(error.exception))
                self.assertEqual(path.read_text(), "original")
                self.assert_no_temporary()

    def test_concurrent_creator_is_not_overwritten(self):
        link = os.link
        def race(source, target):
            target.write_text("another writer")
            return link(source, target)
        with patch("src.reporter.os.link", side_effect=race):
            with self.assertRaises(OutputError):
                self.generate()
        self.assertEqual((self.root / "report.md").read_text(), "another writer")
        self.assert_no_temporary()

    def test_symlinks_and_broken_links_are_rejected_even_with_force(self):
        original = self.root / "original.md"
        original.write_text("original")
        for name, destination in (("link.md", original), ("broken.md", self.root / "absent"),
                                  ("dirlink", self.root)):
            with self.subTest(name=name):
                target = self.root / name
                target.symlink_to(destination)
                with self.assertRaises(OutputError):
                    self.reporter.generate_report(self.stats, None, target, report_format=ReportFormat.MARKDOWN, force=True)
        self.assertEqual(original.read_text(), "original")

    def test_invalid_options_fail_before_creating_files(self):
        for top_n in (0, -1, True, 1.5):
            with self.assertRaises(ValidationError):
                FileReportGenerator(top_n=top_n)
        for options in ({"report_format": "md"}, {"force": 1}, {"output": "report.md"},
                        {"output": self.root / "report.txt"}, {"statistics": None}, {"insight": []}):
            params = dict(statistics=self.stats, insight=self.insight, output=self.root / "report.md",
                          report_format=ReportFormat.MARKDOWN)
            params.update(options)
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.reporter.generate_report(**params)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_sqlite_statistics_and_extract_result_feed_report_directly(self):
        with SQLiteReviewRepository(self.root / "reviews.db") as repo:
            repo.save_raw_reviews([RawReview(source_review_id=str(i)) for i in (1, 2, 3)], DuplicatePolicy.SKIP)
            repo.save_clean_reviews([CleanReview(id=i, product_name="이어폰", rating=i,
                review_date=date(2026, 9, i), review_text="포장이 손상됐어요.", cleaned_at=self.now)
                for i in (1, 2, 3)], DuplicatePolicy.SKIP)
            repo.save_analysis(AnalysisResult(review_id=1, sentiment=Sentiment.NEGATIVE, confidence=.9,
                analyzed_at=self.now, provider="fake", model="test", keywords=["포장"]))
            repo.mark_analysis_failed(2, "실패")
            provider = Mock(spec=AnalysisProvider)
            provider.complete.return_value = ProviderResponse(json.dumps({"summary": "포장 불만이 있습니다.",
                "issues": ["포장 손상"], "improvement_suggestions": ["포장 보강을 권장합니다."]}), "test")
            extractor = AIInsightExtractor(AnalysisOptions(provider="fake", model="test", timeout_seconds=5,
                                                          max_retries=0), provider)
            insight = InsightService(repo, extractor, snapshot=repo.read_snapshot).extract_insights(
                ExtractRequest(ReviewFilter(sentiment=Sentiment.NEGATIVE)))
            stats = repo.get_statistics()
            for fmt in ReportFormat:
                artifact = self.reporter.generate_report(stats, insight, self.root, report_format=fmt)
                text = artifact.path.read_text()
                self.assertIn("33.3%", text)
                self.assertIn("포장 손상", text)
                self.assertIn("실제 추출 대상: 분석 완료 리뷰 1건", text)
            self.assertEqual(provider.complete.call_count, 1)
            self.assertEqual(repo.get_statistics(), stats)


if __name__ == "__main__":
    unittest.main()
