"""Compare adjacent periods using the dashboard's existing daily analysis counts."""
from datetime import date, timedelta
from fractions import Fraction

from src.errors import ValidationError
from src.models import (
    ReviewFilter, ReviewStatistics, Sentiment, SentimentAlertOptions,
    SentimentChangeResult, SentimentChangeStatus,
)


def detect_sentiment_change(
    statistics: ReviewStatistics, filters: ReviewFilter, options: SentimentAlertOptions,
    *, today: date,
) -> SentimentChangeResult:
    """Respect the supplied filters; never silently query outside their range.

    A date-to filter anchors historical comparisons; otherwise use today's UTC
    date. Denominators include only successfully analyzed reviews in each period.
    """
    recent_end = filters.date_to or today
    try:
        recent_start = recent_end - timedelta(days=options.days - 1)
        previous_end = recent_start - timedelta(days=1)
        previous_start = recent_start - timedelta(days=options.days)
    except OverflowError:
        raise ValidationError("종료 날짜 이전에 두 비교 기간을 확보할 수 없습니다.") from None

    def counts(start: date, end: date) -> tuple[int, int]:
        analyzed = negative = 0
        for day, sentiments in statistics.daily_sentiment_counts.items():
            if start <= day <= end:
                analyzed += sum(sentiments.get(sentiment, 0) for sentiment in Sentiment)
                negative += sentiments.get(Sentiment.NEGATIVE, 0)
        return analyzed, negative

    previous_analyzed, previous_negative = counts(previous_start, previous_end)
    recent_analyzed, recent_negative = counts(recent_start, recent_end)
    if filters.sentiment is not None:
        status = SentimentChangeStatus.SENTIMENT_FILTERED
    elif filters.date_from is not None and filters.date_from > previous_start:
        status = SentimentChangeStatus.INCOMPLETE_PERIOD
    elif min(previous_analyzed, recent_analyzed) < options.min_reviews:
        status = SentimentChangeStatus.INSUFFICIENT_DATA
    else:
        # Exact fractions prevent a 20%p rise being treated as 19.999999...%p.
        increase_pp = 100 * (Fraction(recent_negative, recent_analyzed)
                             - Fraction(previous_negative, previous_analyzed))
        status = (SentimentChangeStatus.WARNING
                  if increase_pp >= Fraction(str(options.threshold_pp))
                  else SentimentChangeStatus.NORMAL)
    return SentimentChangeResult(
        status=status, options=options,
        previous_start=previous_start, previous_end=previous_end,
        recent_start=recent_start, recent_end=recent_end,
        previous_analyzed=previous_analyzed, previous_negative=previous_negative,
        recent_analyzed=recent_analyzed, recent_negative=recent_negative,
    )


def format_sentiment_change(result: SentimentChangeResult) -> str:
    if result.status is SentimentChangeStatus.SENTIMENT_FILTERED:
        headline = "[판정 보류] 감정 필터가 적용되어 전체 분석 리뷰 기준 부정 비율을 비교할 수 없습니다."
    elif result.status is SentimentChangeStatus.INCOMPLETE_PERIOD:
        headline = (f"[판정 보류] 날짜 필터가 비교 기간을 일부 제외합니다. "
                    f"--date-from을 {result.previous_start} 이전 또는 같은 날짜로 지정하세요.")
    elif result.status is SentimentChangeStatus.INSUFFICIENT_DATA:
        headline = (f"[판정 보류] 분석 완료 리뷰가 부족합니다. "
                    f"각 기간에 최소 {result.options.min_reviews}건이 필요합니다.")
    else:
        before = 100 * result.previous_negative / result.previous_analyzed
        after = 100 * result.recent_negative / result.recent_analyzed
        label = ("[경고] 부정 리뷰 비율 급증" if result.status is SentimentChangeStatus.WARNING
                 else "[정상] 부정 리뷰 비율 상승이 경고 기준 미만입니다")
        headline = (f"{label}: {before:.1f}% → {after:.1f}% "
                    f"({after - before:+.1f}%p, 경고 기준 +{result.options.threshold_pp:g}%p)")
    return "\n".join([
        f"감정 변화 확인 (각 {result.options.days}일, 리뷰 작성일 기준)", headline,
        f"  직전: {result.previous_start} ~ {result.previous_end} "
        f"(분석 {result.previous_analyzed}건 / 부정 {result.previous_negative}건)",
        f"  최근: {result.recent_start} ~ {result.recent_end} "
        f"(분석 {result.recent_analyzed}건 / 부정 {result.recent_negative}건)",
    ])
