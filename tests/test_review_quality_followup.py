"""Domain-scoped selections and persisted summary omissions across public outputs."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.analyzer import SingleReviewAnalyzer
from src.cli import parse_args
from src.errors import ValidationError
from src.exporter import _flatten
from src.handlers import _review_filter
from src.insight_evidence import movie_review_action, suggestion_candidates
from src.insight_batching import compact_request, group_evidence
from src.models import (AnalysisOptions, AnalysisResult, CleanReview, DuplicatePolicy,
    RawReview, ReviewDetail, ReviewDomain, ReviewFilter, ReviewQuery, Sentiment, SummaryStatus)
from src.query_output import format_review_detail
from src.sqlite_repository import SQLiteReviewRepository
from src.web_dashboard import filters_from_dict, public_review, select_analyzed


class ReviewQualityFollowupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'reviews.sqlite'
        self.now = datetime.now(timezone.utc)
        self.repo = SQLiteReviewRepository(self.path); self.addCleanup(self.repo.close)
        for i, (name, domain) in enumerate((('Film A', ReviewDomain.MOVIE),
                ('Film A sequel', ReviewDomain.MOVIE), ('Film A', ReviewDomain.PRODUCT)), 1):
            self.repo.save_raw_reviews([RawReview(source_review_id=str(i), product_name=name,
                review_text='The pacing drags and the ending is confusing.',
                raw_payload={'review_domain': domain.value})], DuplicatePolicy.SKIP)
            self.repo.save_clean_reviews([CleanReview(i, name, None, None,
                'The pacing drags and the ending is confusing.', self.now, review_domain=domain)], DuplicatePolicy.SKIP)
            self.repo.save_analysis(AnalysisResult(i, Sentiment.NEGATIVE, .8, self.now, 'fixture', 'fixture'))

    def test_domain_and_exact_match_apply_to_listing_statistics_and_selection(self):
        f=ReviewFilter(product_name='film a', review_domain=ReviewDomain.MOVIE, target_match='exact')
        self.assertEqual([r.review.id for r in self.repo.list_reviews(ReviewQuery(filters=f)).items], [1])
        self.assertEqual(self.repo.get_statistics(f).total_reviews, 1)
        self.assertEqual([r.id for r in self.repo.fetch_clean_reviews(f)], [1])
        self.assertEqual([r.review.id for r in select_analyzed(self.repo, f, 20)], [1])
        self.assertEqual(self.repo.get_statistics(ReviewFilter(product_name='film a')).total_reviews, 3)
        self.assertEqual(self.repo.get_statistics(ReviewFilter(review_domain=ReviewDomain.PRODUCT)).total_reviews, 1)
        self.assertEqual(self.repo.get_statistics(ReviewFilter(product_name='Film %', target_match='exact')).total_reviews, 0)

    def test_cli_and_web_parse_same_filter_and_reject_incomplete_exact_selection(self):
        cli=_review_filter(parse_args(['list','--domain','movie','--target','Film A','--exact-target']))
        web=filters_from_dict({'review_domain':'movie','product_name':'Film A','target_match':'exact'})
        self.assertEqual(cli,web)
        self.assertRaises(ValidationError, filters_from_dict, {'target_match':'exact'})
        self.assertRaises(ValidationError, filters_from_dict, {'review_domain':'other'})
        self.assertRaises(ValidationError, filters_from_dict, {'target_match':'wrong'})

    def test_summary_reasons_survive_db_export_and_detail(self):
        source=self.repo.get_review(1).review
        summaries=[(None, SummaryStatus.NOT_PROVIDED),
            ('영화의 전개가 느리다는 평가입니다.', SummaryStatus.AVAILABLE),
            ('This is only English.', SummaryStatus.FILTERED_LANGUAGE),
            ('가'*161, SummaryStatus.FILTERED_LENGTH),
            ('원문 '+source.review_text, SummaryStatus.FILTERED_SOURCE_COPY),
            ('이전 규칙을 무시하라는 명령입니다.', SummaryStatus.FILTERED_INSTRUCTION)]
        provider=Mock(spec=AnalysisProvider)
        options=AnalysisOptions(provider='fixture',model='fixture',timeout_seconds=5,max_retries=0)
        for summary, status in summaries:
            with self.subTest(status=status):
                provider.complete.return_value=ProviderResponse(json.dumps({'sentiment':'negative',
                    'confidence':.8,'summary':summary,'keywords':['전개']}), 'fixture')
                result=SingleReviewAnalyzer(provider).analyze_review(source, options)
                self.assertEqual(result.summary_status,status)
                self.repo.save_analysis(result)
                detail=self.repo.get_review(1)
                self.assertEqual(detail.analysis.summary_status,status)
                self.assertEqual(_flatten(detail)['summary_status'],status.value)
                self.assertEqual(public_review(detail)['analysis']['summary_status_label'],status.label)
                if status is not SummaryStatus.AVAILABLE:
                    self.assertIsNone(detail.analysis.summary)
                    self.assertIn(status.label,format_review_detail(detail))
        self.assertEqual(provider.complete.call_count,len(summaries))

    def test_summary_status_and_content_must_agree(self):
        self.assertRaises(ValidationError, AnalysisResult, 1,Sentiment.NEUTRAL,.5,self.now,'x','x',
            summary='요약',summary_status=SummaryStatus.FILTERED_LANGUAGE)
        self.assertRaises(ValidationError, AnalysisResult, 1,Sentiment.NEUTRAL,.5,self.now,'x','x',
            summary_status=SummaryStatus.AVAILABLE)

    def test_movie_actions_are_distinct_bounded_and_used_by_compact_path(self):
        labels=['전개 느림','결말 혼란','연기 부족','스토리 부실','대사 어색함','음악 과다','영상 부족']
        self.assertEqual(len({movie_review_action(s) for s in labels}),len(labels))
        self.assertIn('작품 요소', movie_review_action('ending and acting'))
        self.assertIn('작품 요소', movie_review_action('counteracting'))
        evidence=[{'review_number':1,'complaints':[{'label':s,'quote':'pacing drags'} for s in labels],'praises':[]}]
        candidates=suggestion_candidates(evidence,ReviewDomain.MOVIE)
        self.assertTrue(all(len(c)<=80 for c in candidates))
        groups=group_evidence(evidence,[self.repo.get_review(1)])
        compact=compact_request(groups,1,ReviewDomain.MOVIE)
        for issue, candidate in zip(compact['issue_candidates'],compact['suggestion_candidates']):
            label=issue.split(': ',1)[1]
            self.assertEqual(candidate,f'{issue}: {movie_review_action(label)}')
            self.assertLessEqual(len(candidate),80)
        self.assertIn('증상',suggestion_candidates(evidence,ReviewDomain.PRODUCT)[0])

    def test_v2_upgrade_and_read_only_preserve_legacy_missing_summary_reason(self):
        self.repo.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute('ALTER TABLE analysis_results DROP COLUMN summary_status')
            connection.execute('PRAGMA user_version = 2')
        before=self.path.read_bytes()
        with SQLiteReviewRepository(self.path,read_only=True) as old:
            self.assertEqual(old.get_review(1).analysis.summary_status,SummaryStatus.LEGACY_UNKNOWN)
        self.assertEqual(self.path.read_bytes(),before)
        with SQLiteReviewRepository(self.path) as upgraded:
            self.assertEqual(upgraded.get_statistics().analyzed_reviews,3)
            self.assertEqual(upgraded.get_review(1).analysis.summary_status,SummaryStatus.LEGACY_UNKNOWN)
            self.assertEqual(upgraded._connection.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(upgraded._connection.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__ == '__main__': unittest.main()
