import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.comparison import ComparisonRequest, ComparisonService, category_from_payload
from src.errors import StorageError, ValidationError
from src.models import DuplicatePolicy, RawReview, ReviewFilter, Sentiment
from src.sqlite_repository import SQLiteReviewRepository
from tests.comparison_fixtures import seed_comparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'reviews.db'
        self.repo = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repo.close)
        seed_comparison(self.repo)
        self.service = ComparisonService(self.repo)

    def test_product_statistics_use_correct_denominators(self):
        result = self.service.compare(ComparisonRequest())
        self.assertEqual(sum(group.statistics.total_reviews for group in result.groups), 7)
        group = next(group for group in result.groups if group.name == '이어폰 A')
        stats = group.statistics
        self.assertEqual((stats.total_reviews, stats.analyzed_reviews, stats.unanalyzed_reviews, stats.failed_reviews), (4, 2, 1, 1))
        self.assertEqual(stats.average_rating, 2.5)
        self.assertEqual(group.negative_ratio, .5)
        self.assertEqual(group.product_count, 1)

    def test_categories_weight_reviews_and_retain_uncategorized(self):
        result = self.service.compare(ComparisonRequest(group_by='category'))
        groups = {group.name: group for group in result.groups}
        self.assertEqual(set(groups), {'전자', '도서', None})
        electronics = groups['전자']
        self.assertEqual(electronics.product_count, 2)
        self.assertEqual(electronics.statistics.average_rating, 2.8)
        self.assertAlmostEqual(electronics.negative_ratio, 1/3)
        self.assertIsNone(groups[None].negative_ratio)
        self.assertEqual(sum(group.statistics.total_reviews for group in groups.values()), 7)

    def test_exact_names_date_boundaries_and_category_filter(self):
        result = self.service.compare(ComparisonRequest(
            names=(' 이어폰   A ', '이어폰', '이어폰 A'), category=' 전자 ',
            filters=ReviewFilter(date_from=date(2026, 9, 2), date_to=date(2026, 9, 2)),
        ))
        self.assertEqual([group.name for group in result.groups], ['이어폰 A'])
        self.assertEqual(result.missing_names, ('이어폰',))
        self.assertEqual(result.groups[0].statistics.total_reviews, 1)
        self.assertEqual(result.groups[0].negative_ratio, 1)

    def test_rating_filter_and_requested_category_names(self):
        result = self.service.compare(ComparisonRequest(group_by='category', names=('전자', '없는분류'), filters=ReviewFilter(rating_min=4)))
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.groups[0].statistics.total_reviews, 2)
        self.assertEqual(result.groups[0].negative_ratio, 0)
        self.assertEqual(result.missing_names, ('없는분류',))

    def test_sorting_keeps_unknown_ratios_last_both_directions(self):
        for order, expected_first in [('asc', '이어폰 B'), ('desc', '이어폰 A')]:
            result = self.service.compare(ComparisonRequest(sort='negative', order=order))
            self.assertEqual(result.groups[0].name, expected_first)
            self.assertIsNone(result.groups[-1].negative_ratio)
        result = self.service.compare(ComparisonRequest(sort='reviews', order='desc'))
        self.assertEqual(result.groups[0].name, '이어폰 A')
        result = self.service.compare(ComparisonRequest(sort='rating', order='asc'))
        self.assertEqual(result.groups[0].name, '책')

    def test_empty_filters_and_missing_names_are_explicit(self):
        result = self.service.compare(ComparisonRequest(names=('없는 제품',)))
        self.assertEqual(result.groups, [])
        self.assertEqual(result.missing_names, ('없는 제품',))
        self.assertEqual(self.service.compare(ComparisonRequest(filters=ReviewFilter(date_from=date(2030, 1, 1)))).groups, [])

    def test_single_query_snapshot_and_existing_schema_unchanged(self):
        statements = []
        self.repo._connection.set_trace_callback(statements.append)
        self.service.compare(ComparisonRequest(group_by='category'))
        self.repo._connection.set_trace_callback(None)
        selects = [statement for statement in statements if statement.lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(selects), 1)
        self.assertEqual(self.repo._connection.execute('PRAGMA user_version').fetchone()[0], 2)
        with SQLiteReviewRepository(self.path, read_only=True) as readonly:
            self.assertEqual(len(ComparisonService(readonly).compare(ComparisonRequest()).groups), 4)

    def test_raw_upsert_follows_existing_clean_analysis_invalidation(self):
        self.repo.save_raw_reviews([RawReview(source_review_id='1', raw_payload={'category': '신규'})], DuplicatePolicy.UPSERT)
        result = self.service.compare(ComparisonRequest(group_by='category'))
        self.assertNotIn('신규', {group.name for group in result.groups})
        self.assertEqual(sum(group.statistics.total_reviews for group in result.groups), 6)

    def test_ambiguous_metadata_rejected_without_changing_product_comparison(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute('UPDATE raw_reviews SET raw_payload=? WHERE id=1', (json.dumps({'category': 'A', '카테고리': 'B'}),))
        with self.assertRaisesRegex(StorageError, 'ID=1'):
            self.service.compare(ComparisonRequest(group_by='category'))
        self.assertEqual(len(self.service.compare(ComparisonRequest()).groups), 4)

    def test_invalid_requests_reject_biased_sentiment_filter(self):
        cases = [dict(group_by='other'), dict(names=(' ',)), dict(names='A'), dict(category=' '),
                 dict(sort='other'), dict(order='other'), dict(min_reviews=0), dict(min_reviews=True),
                 dict(chart=True), dict(force=True), dict(filters=ReviewFilter(sentiment=Sentiment.NEGATIVE))]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                ComparisonRequest(**case)

    def test_category_aliases_normalization_and_invalid_values(self):
        for payload, expected in [({}, None), ({'category': None}, None), ({'category': ' '}, None),
                                  ({'Product-Category': ' Ａ  상품 '}, 'A 상품'),
                                  ({'category': 1.0}, '1'), ({'category': '전자', '카테고리': ' 전자 '}, '전자')]:
            self.assertEqual(category_from_payload(payload), expected)
        for payload in [[], {'category': []}, {'category': {}}, {'category': True}, {'category': float('inf')}]:
            with self.assertRaises(ValueError):
                category_from_payload(payload)
