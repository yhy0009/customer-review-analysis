"""Grounding and omission regressions independent of live model quality."""
import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

from src.ai_provider import ProviderResponse
from src.errors import AIProviderError, ValidationError
from src.insight_evidence import check_coverage, parse_evidence
from src.insight_extractor import AIInsightExtractor
from src.models import AnalysisOptions, ReviewFilter, Sentiment
from tests.test_insight_extractor import detail


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.reviews = [{"review_text": "잡음이 심하고 고객센터도 답이 없어요.", "sentiment": "negative"},
                        {"review_text": "음질은 좋지만 착용감은 불편해요.", "sentiment": "neutral"}]
        self.evidence = [{"review_number": 1, "complaints": [
            {"label": "잡음", "quote": "잡음이 심하고"},
            {"label": "고객센터 무응답", "quote": "고객센터도 답이 없어요."}], "praises": []},
            {"review_number": 2, "complaints": [{"label": "착용 불편", "quote": "착용감은 불편해요."}],
             "praises": [{"label": "음질", "quote": "음질은 좋지만"}]}]
        self.narrative = {"issues": ["잡음·착용 불편", "고객센터 무응답"],
                          "improvement_suggestions": [],
                          "summary": "음질에 대한 긍정 경험과 잡음·착용·응대 불편이 함께 있습니다."}

    def test_all_reviews_with_multiple_and_neutral_complaints(self):
        result = parse_evidence(json.dumps({"reviews": self.evidence}), self.reviews)
        self.assertEqual(result, self.evidence)
        check_coverage(self.narrative, result)

    def test_missing_reordered_duplicate_or_empty_negative_review_is_rejected(self):
        for rows in (self.evidence[:1], self.evidence[::-1], [self.evidence[0]] * 2,
                     [dict(self.evidence[0], complaints=[]), self.evidence[1]],
                     [dict(self.evidence[0], review_number=True), self.evidence[1]]):
            with self.subTest(rows=rows), self.assertRaises(AIProviderError):
                parse_evidence(json.dumps({"reviews": rows}), self.reviews)

    def test_invented_or_other_review_quote_is_rejected(self):
        for quote in ("무선 연결 불량", "음질은 좋지만"):
            rows = copy.deepcopy(self.evidence)
            rows[0]["complaints"][0]["quote"] = quote
            with self.subTest(quote=quote), self.assertRaises(AIProviderError):
                parse_evidence(json.dumps({"reviews": rows}), self.reviews)

    def test_duplicate_json_keys_and_extra_fields_are_rejected(self):
        for content in ('{"reviews":[],"reviews":[]}', json.dumps({"reviews": self.evidence, "extra": 1})):
            with self.assertRaises(AIProviderError):
                parse_evidence(content, self.reviews)

    def test_compressed_narrative_must_keep_every_complaint_and_some_praise(self):
        for field, value in (("issues", ["잡음·착용 불편"]), ("issues", ["고객센터 무응답"]),
                             ("summary", "문제가 있는 제품입니다.")):
            with self.subTest(field=field, value=value), self.assertRaises(AIProviderError):
                check_coverage(dict(self.narrative, **{field: value}), self.evidence)

    def test_positive_only_and_factual_inputs_cannot_gain_issues_or_suggestions(self):
        for praises in ([], [{"label": "음질", "quote": "음질은 좋지만"}]):
            evidence = [{"review_number": 1, "complaints": [], "praises": praises}]
            narrative = {"summary": "음질에 만족합니다." if praises else "구성품을 설명한 리뷰입니다.",
                         "issues": [], "improvement_suggestions": []}
            check_coverage(narrative, evidence)
            for field in ("issues", "improvement_suggestions"):
                with self.assertRaises(AIProviderError):
                    check_coverage(dict(narrative, **{field: ["배터리 개선"]}), evidence)

    def test_missing_final_complaint_retries_only_narrative(self):
        provider = Mock()
        bad = dict(self.narrative, issues=["잡음·착용 불편"])
        provider.complete.side_effect = [ProviderResponse(json.dumps({"reviews": self.evidence}), "fake"),
                                        ProviderResponse(json.dumps(bad), "fake"),
                                        ProviderResponse(json.dumps(self.narrative), "fake")]
        options = AnalysisOptions(provider="fake", model="fake", timeout_seconds=5, max_retries=1)
        reviews = [detail(1, Sentiment.NEGATIVE, review_text=self.reviews[0]["review_text"]),
                   detail(2, Sentiment.NEUTRAL, review_text=self.reviews[1]["review_text"])]
        sleep = Mock()
        result = AIInsightExtractor(options, provider, sleep=sleep).extract_insights(reviews, ReviewFilter())
        self.assertIn("고객센터 무응답", result.issues)
        self.assertEqual(provider.complete.call_count, 3)
        self.assertEqual(provider.complete.call_args_list[1].args, provider.complete.call_args_list[2].args)
        sleep.assert_called_once_with(1.0)

    def test_evidence_too_large_fails_explicitly_before_narrative(self):
        findings = [{"label": str(i).zfill(20), "quote": "잡음"} for i in range(13)]
        provider = Mock()
        provider.complete.return_value = ProviderResponse(json.dumps({"reviews": [
            {"review_number": 1, "complaints": findings, "praises": []}]}), "fake")
        options = AnalysisOptions(provider="fake", model="fake", timeout_seconds=5, max_retries=0)
        with self.assertRaisesRegex(ValidationError, "limit"):
            AIInsightExtractor(options, provider).extract_insights(
                [detail(1, Sentiment.NEGATIVE, review_text="잡음")], ReviewFilter())
        self.assertEqual(provider.complete.call_count, 1)

    def test_augmented_input_budget_is_checked_before_second_request(self):
        provider = Mock()
        provider.complete.return_value = ProviderResponse(json.dumps({"reviews": [self.evidence[0]]}), "fake")
        options = AnalysisOptions(provider="fake", model="fake", timeout_seconds=5, max_retries=0)
        review = detail(1, Sentiment.NEGATIVE, review_text=self.reviews[0]["review_text"])
        body = {"review_count": 1, "positive_keywords": [], "negative_keywords": [], "reviews": [
            {"product_name": review.review.product_name, "rating": review.review.rating,
             "review_text": review.review.review_text, "sentiment": "negative"}]}
        budget = len(json.dumps(body, ensure_ascii=False))
        with self.assertRaisesRegex(ValidationError, "근거를 포함한"):
            AIInsightExtractor(options, provider, max_input_chars=budget).extract_insights([review], ReviewFilter())
        self.assertEqual(provider.complete.call_count, 1)

    def test_unlisted_technical_fix_is_rejected_even_when_all_issues_are_covered(self):
        provider = Mock()
        provider.complete.side_effect = [ProviderResponse(json.dumps({"reviews": self.evidence}), "fake"),
            ProviderResponse(json.dumps(dict(self.narrative, improvement_suggestions=["무선 펌웨어를 교체하세요."])), "fake")]
        options = AnalysisOptions(provider="fake", model="fake", timeout_seconds=5, max_retries=0)
        reviews = [detail(1, Sentiment.NEGATIVE, review_text=self.reviews[0]["review_text"]),
                   detail(2, Sentiment.NEUTRAL, review_text=self.reviews[1]["review_text"])]
        with self.assertRaises(AIProviderError):
            AIInsightExtractor(options, provider).extract_insights(reviews, ReviewFilter())
        self.assertEqual(provider.complete.call_count, 2)
