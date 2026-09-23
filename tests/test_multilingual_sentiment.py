"""Transport and persistence contracts; fake responses do not measure AI quality."""
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.analyzer import BatchReviewAnalyzer, PROMPT_VERSION, SingleReviewAnalyzer
from src.errors import AIProviderError, ConfigError
from src.evaluation import load_dataset
from src.models import AnalysisOptions, CleanReview, DuplicatePolicy, RawReview, Sentiment
from src.sqlite_repository import SQLiteReviewRepository


DATASET = Path(__file__).resolve().parents[1] / 'evaluation/reviews.multilingual.v1.json'


class MultilingualSentimentTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_dataset(DATASET)['cases']
        self.provider = Mock(spec=AnalysisProvider)
        self.options = AnalysisOptions(provider='fake', model='fixture', timeout_seconds=5, max_retries=0)
        self.now = datetime.now(timezone.utc)

    def review(self, case, review_id=1):
        return CleanReview(review_id, case['product_name'], date(2026, 9, 1), case['rating'], case['review_text'], self.now)

    def reply(self, sentiment='positive'):
        return ProviderResponse(json.dumps({'sentiment': sentiment, 'confidence': .8,
            'summary': '원문에 근거한 테스트용 한국어 요약입니다.', 'keywords': ['음질', ' 연결 ', '음질']}, ensure_ascii=False), 'fixture')

    def test_all_languages_reach_provider_without_translation_or_label_metadata(self):
        analyzer = SingleReviewAnalyzer(self.provider)
        # This validates original-text transport and schema parsing, not predicted labels.
        self.provider.complete.return_value = self.reply()
        for case in self.cases:
            with self.subTest(case=case['id']):
                review = self.review(case)
                result = analyzer.analyze_review(review, self.options)
                messages, schema, _ = self.provider.complete.call_args.args
                self.assertEqual([message['role'] for message in messages], ['system', 'user'])
                self.assertEqual(json.loads(messages[1]['content']), {
                    'product_name': case['product_name'], 'rating': case['rating'], 'review_text': case['review_text'],
                })
                self.assertNotIn(review.review_text, messages[0]['content'])
                self.assertEqual(set(schema['properties']), {'sentiment', 'confidence', 'summary', 'keywords'})
                self.assertEqual(result.prompt_version, 'review-sentiment-v2-multilingual')
                self.assertEqual(result.keywords, ['음질', '연결'])
                self.assertEqual(review.review_text, case['review_text'])

    def test_translated_or_unknown_sentiment_labels_are_rejected(self):
        review = self.review(next(case for case in self.cases if case['language'] == 'en'))
        for label in ('긍정', 'POSITIVE', 'happy'):
            self.provider.complete.return_value = self.reply(label)
            with self.subTest(label=label), self.assertRaises(AIProviderError):
                SingleReviewAnalyzer(self.provider).analyze_review(review, self.options)

    def test_explicit_old_prompt_is_rejected_before_any_provider_request(self):
        with self.assertRaises(ConfigError):
            SingleReviewAnalyzer(self.provider).analyze_review(self.review(self.cases[0]), replace(self.options, prompt_version='review-sentiment-v1'))
        self.provider.complete.assert_not_called()
        self.provider.complete.return_value = self.reply()
        result = SingleReviewAnalyzer(self.provider).analyze_review(self.review(self.cases[0]), replace(self.options, prompt_version=PROMPT_VERSION))
        self.assertEqual(result.prompt_version, PROMPT_VERSION)

    def test_existing_v1_result_stays_until_explicit_force(self):
        with tempfile.TemporaryDirectory() as directory, SQLiteReviewRepository(Path(directory) / 'reviews.db') as repo:
            case = next(case for case in self.cases if case['id'] == 'en-n2')
            review = self.review(case)
            repo.save_raw_reviews([RawReview(source_review_id='english')], DuplicatePolicy.SKIP)
            repo.save_clean_reviews([review], DuplicatePolicy.SKIP)
            self.provider.complete.return_value = self.reply('negative')
            legacy = replace(SingleReviewAnalyzer(self.provider).analyze_review(review, self.options), prompt_version='review-sentiment-v1')
            repo.save_analysis(legacy)
            self.provider.reset_mock()
            analyzer = BatchReviewAnalyzer(repo, self.provider)
            batch = analyzer.analyze_reviews([review], self.options)
            self.assertEqual((batch.succeeded, batch.skipped), (0, 1))
            self.provider.complete.assert_not_called()
            self.assertEqual(repo.get_review(review.id).analysis.prompt_version, 'review-sentiment-v1')
            batch = analyzer.analyze_reviews([review], self.options, force=True)
            self.assertEqual(batch.succeeded, 1)
            self.provider.complete.assert_called_once()
            self.assertEqual(repo.get_review(review.id).analysis.prompt_version, PROMPT_VERSION)
            self.assertEqual(repo.get_review(review.id).review.review_text, case['review_text'])
            self.assertEqual(repo.get_statistics().sentiment_counts[Sentiment.NEGATIVE], 1)
