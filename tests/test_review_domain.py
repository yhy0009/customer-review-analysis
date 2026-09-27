"""Movie metadata must survive import and cannot produce cross-title insights."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.analyzer import SingleReviewAnalyzer
from src.cleaner import clean_reviews
from src.collector import load_reviews
from src.errors import ValidationError
from src.insight_extractor import AIInsightExtractor
from src.models import (AnalysisOptions, AnalysisResult, CleanReview, CleaningOptions,
                        DuplicatePolicy, RawReview, ReviewDetail, ReviewDomain,
                        ReviewFilter, Sentiment)
from src.review_domain import review_domain_from_payload
from src.sqlite_repository import SQLiteReviewRepository


class ReviewDomainTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = datetime.now(timezone.utc)
        self.options = AnalysisOptions(provider="fake", model="fixture", timeout_seconds=5, max_retries=0)

    def detail(self, id_, domain, title):
        review = CleanReview(id_, title, None, None, "영화 전개가 지루합니다.", self.now,
                             review_domain=domain)
        analysis = AnalysisResult(id_, Sentiment.NEGATIVE, .8, self.now, "fake", "fixture")
        return ReviewDetail(review, analysis)

    def test_movie_title_import_round_trip_and_analysis_context(self):
        path = self.root / "movie.csv"
        path.write_text("review_id,movie_title,review_text\nM1,영화 A,전개가 지루했습니다.\n", encoding="utf-8")
        raw = load_reviews(path)
        self.assertEqual(raw[0].product_name, "영화 A")
        self.assertEqual(raw[0].raw_payload["review_domain"], "movie")
        with SQLiteReviewRepository(self.root / "reviews.sqlite") as repository:
            repository.save_raw_reviews(raw, DuplicatePolicy.SKIP)
            cleaned = clean_reviews(repository.fetch_raw_reviews(), CleaningOptions(DuplicatePolicy.SKIP, 3))
            self.assertEqual(cleaned.succeeded, 1)
            self.assertEqual(cleaned.reviews[0].review_domain, ReviewDomain.MOVIE)
            repository.save_clean_reviews(cleaned.reviews, DuplicatePolicy.SKIP)
            stored = repository.get_review(1).review
            self.assertEqual(stored.review_domain, ReviewDomain.MOVIE)
            provider = Mock(spec=AnalysisProvider)
            provider.complete.return_value = ProviderResponse(json.dumps({
                "sentiment": "negative", "confidence": .8,
                "summary": "전개가 지루하다는 평가입니다.", "keywords": ["전개"],
            }), "fixture")
            SingleReviewAnalyzer(provider).analyze_review(stored, self.options)
            body = json.loads(provider.complete.call_args.args[0][1]["content"])
            self.assertEqual((body["review_domain"], body["product_name"]), ("movie", "영화 A"))

    def test_unknown_or_conflicting_domain_is_rejected(self):
        self.assertRaises(ValueError, review_domain_from_payload,
                          {"category": "영화", "review_domain": "product"})
        raw = RawReview(id=1, review_text="리뷰 원문", raw_payload={"review_domain": "other"})
        result = clean_reviews([raw], CleaningOptions(DuplicatePolicy.SKIP, 3))
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.errors[0].code, "INVALID_REVIEW_DOMAIN")

    def test_movie_insight_requires_one_named_title_before_provider_call(self):
        provider = Mock(spec=AnalysisProvider)
        extractor = AIInsightExtractor(self.options, provider)
        for reviews in ([self.detail(1, ReviewDomain.MOVIE, None)],
                        [self.detail(1, ReviewDomain.MOVIE, "영화 A"),
                         self.detail(2, ReviewDomain.MOVIE, "영화 B")],
                        [self.detail(1, ReviewDomain.MOVIE, "영화 A"),
                         self.detail(2, ReviewDomain.PRODUCT, "이어폰")]):
            with self.assertRaises(ValidationError):
                extractor.extract_insights(reviews, ReviewFilter())
        provider.complete.assert_not_called()

    def test_named_movie_uses_movie_review_suggestions(self):
        provider = Mock(spec=AnalysisProvider)
        def complete(messages, schema, options):
            body = json.loads(messages[1]["content"])
            self.assertEqual(body["review_domain"], "movie")
            if "reviews" in schema["properties"]:
                self.assertIn("영화의 서사", messages[0]["content"])
                return ProviderResponse(json.dumps({"reviews": [{"review_number": 1,
                    "complaints": [{"label": "전개 지루함", "quote": "전개가 지루합니다"}],
                    "praises": []}]}, ensure_ascii=False), "fixture")
            candidate = body["suggestion_candidates"][0]
            return ProviderResponse(json.dumps({"issues": ["전개 지루함"],
                "improvement_suggestions": [candidate], "summary": "전개 지루함이 지적됐습니다."},
                ensure_ascii=False), "fixture")
        provider.complete.side_effect = complete
        result = AIInsightExtractor(self.options, provider).extract_insights(
            [self.detail(1, ReviewDomain.MOVIE, "영화 A")], ReviewFilter())
        self.assertEqual(result.review_count, 1)
        self.assertIn("전개 속도", result.improvement_suggestions[0])
        self.assertNotIn("증상", result.improvement_suggestions[0])


if __name__ == "__main__":
    unittest.main()
