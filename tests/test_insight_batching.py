"""Large-input coverage, stable provenance, bounded requests and consumer checks."""

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import ProviderResponse
from src.errors import AIProviderError, ValidationError
from src.insight_batching import fits_issue_budget, group_evidence, plan_batches
from src.insight_extractor import AIInsightExtractor
from src.models import AnalysisOptions, ReportFormat, ReviewFilter, ReviewStatistics, Sentiment
from src.query_output import format_insight_result
from src.reporter import FileReportGenerator
from tests.test_insight_extractor import detail


def reply(messages, schema, options):
    body = json.loads(messages[1]["content"])
    if "reviews" in schema["properties"]:
        rows = []
        for i, review in enumerate(body["reviews"], 1):
            kind = "praises" if review["sentiment"] == "positive" else "complaints"
            row = {"review_number": i, "complaints": [], "praises": []}
            if review["sentiment"] != "neutral":
                row[kind] = [{"label": "음질 좋음" if kind == "praises" else "배송 지연",
                              "quote": review["review_text"][:100]}]
            rows.append(row)
        payload = {"reviews": rows}
    else:
        payload = {"issues": body.get("issue_candidates", ["배송 지연"]),
                   "improvement_suggestions": body["suggestion_candidates"][:1],
                   "summary": body.get("praise_label") or "선택한 주요 불편 요약입니다."}
    return ProviderResponse(json.dumps(payload, ensure_ascii=False), "fixture")


class BatchingTests(unittest.TestCase):
    def setUp(self):
        self.options = AnalysisOptions(provider="fake", model="fixture", timeout_seconds=5, max_retries=0)
        self.provider = Mock()
        self.provider.complete.side_effect = reply

    def test_one_hundred_long_reviews_keep_every_id_and_keyword_count(self):
        reviews = [detail(i, Sentiment.NEGATIVE, ["배송"], review_text="배송이 늦습니다. " * 80)
                   for i in range(1, 101)]
        before = copy.deepcopy(reviews)
        result = AIInsightExtractor(self.options, self.provider).extract_insights(reviews[::-1], ReviewFilter())
        self.assertEqual(result.review_count, 100)
        self.assertEqual(result.negative_keywords[0].count, 100)
        self.assertEqual(result.evidence_groups[0].review_count, 100)
        self.assertEqual([c.review_id for c in result.evidence_groups[0].citations], list(range(1, 101)))
        self.assertEqual(reviews, before)
        self.assertEqual(self.provider.complete.call_count, 6)
        for call in self.provider.complete.call_args_list:
            self.assertLessEqual(len(call.args[0][1]["content"]), 24000)
            self.assertNotIn('"review_id"', call.args[0][1]["content"])

    def test_char_budget_splits_even_below_batch_size(self):
        request = {"reviews": [{"review_text": "한" * 100}] * 5, "review_count": 5}
        batches = plan_batches(request, 300, 20)
        self.assertEqual([len(b["reviews"]) for b in batches], [2, 2, 1])
        self.assertEqual(sum(b["review_count"] for b in batches), 5)

    def test_issue_budget_accounts_for_separators_and_each_field_limit(self):
        self.assertTrue(fits_issue_budget([str(i).zfill(20) for i in range(9)]))
        self.assertFalse(fits_issue_budget([str(i).zfill(20) for i in range(10)]))

    def test_oversized_later_row_and_batch_cap_fail_before_any_api_call(self):
        for reviews, kwargs in (([detail(1), detail(2, review_text="가" * 25000)], {}),
                                ([detail(i) for i in range(1, 4)], {"batch_size": 1, "max_batches": 2})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                AIInsightExtractor(self.options, self.provider, **kwargs).extract_insights(reviews, ReviewFilter())
        self.provider.complete.assert_not_called()

    def test_middle_batch_retry_does_not_repeat_completed_batches(self):
        failed = False
        def flaky(messages, schema, options):
            nonlocal failed
            body = json.loads(messages[1]["content"])
            if "reviews" in body and body["reviews"][0]["review_text"] == "두번째" and not failed:
                failed = True
                raise AIProviderError("private")
            return reply(messages, schema, options)
        self.provider.complete.side_effect = flaky
        extractor = AIInsightExtractor(replace(self.options, max_retries=1), self.provider,
                                       batch_size=1, sleep=lambda _: None)
        result = extractor.extract_insights([detail(1, Sentiment.NEGATIVE, review_text="첫번째"),
            detail(2, Sentiment.NEGATIVE, review_text="두번째")], ReviewFilter())
        self.assertEqual(result.review_count, 2)
        calls = self.provider.complete.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1].args, calls[2].args)

    def test_batch_failure_aborts_without_partial_result_or_narrative(self):
        self.provider.complete.side_effect = [reply(
            [{}, {"content": json.dumps({"reviews": [{"sentiment": "negative", "review_text": "가"}]})}],
            {"properties": {"reviews": {}}}, self.options), AIProviderError("private")]
        with self.assertRaises(AIProviderError):
            AIInsightExtractor(self.options, self.provider, batch_size=1).extract_insights(
                [detail(1, Sentiment.NEGATIVE, review_text="가"), detail(2, Sentiment.NEGATIVE)], ReviewFilter())
        self.assertEqual(self.provider.complete.call_count, 2)

    def test_aliases_preserve_original_labels_and_never_merge_products_or_severity(self):
        selected = [detail(1, product_name="A"), detail(2, product_name="A"), detail(3, product_name="B")]
        rows = [{"complaints": [{"label": label, "quote": "배송"}], "praises": []}
                for label in ("배송 늦음", "배송 지연", "배송 지연")]
        rows[0]["complaints"].append({"label": "배송이 늦음", "quote": "늦음"})
        rows[1]["complaints"].append({"label": "심각한 배송 지연", "quote": "지연"})
        groups = group_evidence(rows, selected)
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0].review_count, 2)
        self.assertEqual(len(groups[0].citations), 3)
        self.assertEqual(groups[0].citations[0].label, "배송 늦음")

    def test_positive_and_factual_batches_do_not_invent_complaints(self):
        for sentiment in (Sentiment.POSITIVE, Sentiment.NEUTRAL):
            result = AIInsightExtractor(self.options, self.provider, batch_size=1).extract_insights(
                [detail(1, sentiment), detail(2, sentiment)], ReviewFilter())
            self.assertEqual(result.issues, [])
            self.assertEqual(result.improvement_suggestions, [])
            if sentiment is Sentiment.POSITIVE:
                self.assertIn("음질 좋음", result.summary)

    def test_invented_compact_issue_or_suggestion_is_rejected(self):
        for field in ("issues", "improvement_suggestions"):
            def bad(messages, schema, options):
                response = reply(messages, schema, options)
                if "reviews" not in schema["properties"]:
                    data = json.loads(response.content)
                    data[field] = ["배터리를 교체하세요"]
                    return ProviderResponse(json.dumps(data), "fake")
                return response
            self.provider.complete.side_effect = bad
            with self.subTest(field=field), self.assertRaises(AIProviderError):
                AIInsightExtractor(self.options, self.provider, batch_size=1).extract_insights(
                    [detail(1, Sentiment.NEGATIVE), detail(2, Sentiment.NEGATIVE)], ReviewFilter())

    def test_report_and_console_expose_all_topics_and_escape_quotes(self):
        result = AIInsightExtractor(self.options, self.provider, batch_size=1).extract_insights(
            [detail(i, Sentiment.NEGATIVE, product_name=str(i), review_text="<img> 배송 지연") for i in range(1, 6)],
            ReviewFilter())
        self.assertEqual(len(result.issues), 3)
        self.assertEqual(len(result.evidence_groups), 5)
        console = format_insight_result(result)
        self.assertIn("근거 주제 5", console)
        self.assertIn("요약 범위", console)
        stats = ReviewStatistics(total_reviews=5, analyzed_reviews=5, unanalyzed_reviews=0, failed_reviews=0)
        with tempfile.TemporaryDirectory() as directory:
            for fmt in ReportFormat:
                artifact = FileReportGenerator().generate_report(stats, result, Path(directory) / ("report." + fmt.value), report_format=fmt)
                content = artifact.path.read_text()
                self.assertIn("근거 주제 5", content)
                self.assertIn("리뷰 5", content)
                if fmt is ReportFormat.MARKDOWN:
                    self.assertNotIn("<img>", content)

    def test_invalid_options_rejected(self):
        for name in ("batch_size", "max_batches"):
            for value in (0, -1, True, 1.5):
                with self.assertRaises(ValidationError):
                    AIInsightExtractor(self.options, self.provider, **{name: value})
