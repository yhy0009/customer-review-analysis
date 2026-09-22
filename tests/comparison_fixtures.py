"""Small synthetic population with unequal group sizes and incomplete analysis."""
from datetime import date, datetime, timezone

from src.models import AnalysisResult, CleanReview, DuplicatePolicy, RawReview, Sentiment


NOW = datetime(2026, 9, 22, 1, 2, 3, tzinfo=timezone.utc)


def seed_comparison(repository):
    # name, rating, day, category payload, sentiment (None=unanalyzed, fail=failed)
    rows = [
        ('이어폰 A', 5, 1, {'category': '전자'}, 'positive'),
        ('이어폰 A', 1, 2, {'카테고리': ' 전자 '}, 'negative'),
        ('이어폰 A', 3, 3, {'Product_Category': '전자'}, None),
        ('이어폰 A', 1, 4, {'상품분류': '전자'}, 'fail'),
        ('이어폰 B', 4, 2, {'category': '전자'}, 'positive'),
        ('책', 2, 2, {'category': '도서'}, 'neutral'),
        ('분류 없는 제품', 5, 2, {}, None),
    ]
    repository.save_raw_reviews([
        RawReview(source_review_id=str(index), raw_payload=payload)
        for index, (_, _, _, payload, _) in enumerate(rows, 1)
    ], DuplicatePolicy.SKIP)
    repository.save_clean_reviews([
        CleanReview(id=index, product_name=name, rating=rating, review_date=date(2026, 9, day),
                    review_text='비교 분석용 합성 리뷰입니다', cleaned_at=NOW)
        for index, (name, rating, day, _, _) in enumerate(rows, 1)
    ], DuplicatePolicy.SKIP)
    for index, (_, _, _, _, sentiment) in enumerate(rows, 1):
        if sentiment == 'fail':
            repository.mark_analysis_failed(index, 'fixture failure')
        elif sentiment:
            repository.save_analysis(AnalysisResult(index, Sentiment(sentiment), .9, NOW, 'fake', 'test'))
    # Raw-only input must not enter any comparison denominator.
    repository.save_raw_reviews([RawReview(source_review_id='raw-only', raw_payload={'category': '미정제'})], DuplicatePolicy.SKIP)
