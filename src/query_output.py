"""Pure text presentation for the CLI's list, show, and stats commands."""

import re
import unicodedata
from datetime import datetime

from src.models import Page, ReviewDetail, ReviewStatistics, Sentiment


_SENTIMENT_LABELS = {
    Sentiment.POSITIVE: "긍정",
    Sentiment.NEUTRAL: "중립",
    Sentiment.NEGATIVE: "부정",
}
# Strip OSC (including terminal hyperlinks), string controls, CSI and ESC commands
# before removing remaining control characters. Keep ordinary Unicode and emoji.
_TERMINAL_SEQUENCE = re.compile(
    r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)"
    r"|(?:\x1b[P^_X]|[\x90\x98\x9e\x9f])[\s\S]*?(?:\x1b\\|\x9c|$)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|\x1b[ -/]*[@-~]"
)


def _safe_text(value: str) -> str:
    text = _TERMINAL_SEQUENCE.sub("", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return "".join(
        character
        for character in text
        if character == "\n" or unicodedata.category(character) != "Cc"
    )


def _single_line(value: str, limit: int = 0) -> str:
    text = " ".join(_safe_text(value).split())
    if limit and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _utc_text(value: datetime) -> str:
    # All timestamps in the shared models have already been validated as UTC.
    return value.isoformat().replace("+00:00", "Z")


def format_review_list(page: Page[ReviewDetail]) -> str:
    """Render compact review cards without fixed-width Unicode table alignment."""
    pagination = f"페이지 {page.page}/{page.total_pages}" if page.total_pages else "페이지 없음"
    lines = [f"총 {page.total_items}건 | {pagination} | 페이지 크기 {page.size}건"]
    if not page.items:
        lines.append(
            "조회된 정제 리뷰가 없습니다."
            if page.total_items == 0
            else "요청한 페이지에 리뷰가 없습니다."
        )
        return "\n".join(lines)

    for detail in page.items:
        review = detail.review
        lines.extend([
            "",
            f"ID {review.id} | {review.review_date.isoformat()}"
            f" | 별점 {review.rating}/5 | {_single_line(review.product_name, 40)}",
        ])
        if detail.analysis is None:
            lines.append("  분석 결과 없음")
        else:
            analysis = detail.analysis
            lines.append(
                f"  감정: {_SENTIMENT_LABELS[analysis.sentiment]}"
                f" | 신뢰도: {analysis.confidence:.1%}"
            )
        lines.append(f"  {_single_line(review.review_text, 100)}")
    return "\n".join(lines)


def format_review_detail(detail: ReviewDetail) -> str:
    """Show the full cleaned text and available analysis metadata."""
    review = detail.review
    lines = [
        f"리뷰 ID: {review.id}",
        f"원본 리뷰 ID: {_single_line(review.source_review_id) if review.source_review_id else '없음'}",
        f"제품명: {_single_line(review.product_name)}",
        f"작성일: {review.review_date.isoformat()}",
        f"별점: {review.rating}/5",
        f"정제 시각 (UTC): {_utc_text(review.cleaned_at)}",
        "",
        "리뷰 본문 (정제):",
        _safe_text(review.review_text),
        "",
    ]
    analysis = detail.analysis
    if analysis is None:
        # ReviewDetail does not carry processing status, so this includes both
        # never-analyzed reviews and failed analyses without claiming either.
        lines.append("분석 결과 없음")
    else:
        lines.extend([
            f"감정: {_SENTIMENT_LABELS[analysis.sentiment]}",
            f"신뢰도: {analysis.confidence:.1%}",
            "요약:",
            _safe_text(analysis.summary) if analysis.summary else "없음",
            "키워드: " + (
                ", ".join(_single_line(keyword) for keyword in analysis.keywords)
                if analysis.keywords else "없음"
            ),
            f"제공자: {_single_line(analysis.provider)}",
            f"모델: {_single_line(analysis.model)}",
            f"분석 시각 (UTC): {_utc_text(analysis.analyzed_at)}",
        ])
        if analysis.prompt_version:
            lines.append(f"프롬프트 버전: {_single_line(analysis.prompt_version)}")
    return "\n".join(lines)


def format_statistics(stats: ReviewStatistics) -> str:
    """Present repository aggregates with explicit coverage and ratio denominators."""
    coverage = stats.analyzed_reviews / stats.total_reviews if stats.total_reviews else 0
    average = f"{stats.average_rating:.2f}/5" if stats.average_rating is not None else "N/A"
    lines = [
        f"정제 리뷰 수: {stats.total_reviews}건",
        f"분석 완료: {stats.analyzed_reviews}건",
        f"미분석 (실패 제외): {stats.unanalyzed_reviews}건",
        f"분석 실패: {stats.failed_reviews}건",
        f"분석 완료율: {coverage:.1%} (정제 리뷰 수 기준)",
        f"평균 별점: {average}",
        "",
        f"감정 분포 (분석 완료 {stats.analyzed_reviews}건 기준)",
    ]
    for sentiment, label in _SENTIMENT_LABELS.items():
        count = stats.sentiment_counts.get(sentiment, 0)
        ratio = stats.sentiment_ratios.get(sentiment, 0.0) if stats.analyzed_reviews else 0.0
        lines.append(f"  {label}: {count}건 ({ratio:.1%})")

    for label, keywords in (
        ("긍정 TOP 키워드", stats.top_positive_keywords),
        ("부정 TOP 키워드", stats.top_negative_keywords),
    ):
        if keywords:
            lines.append(label + ": " + ", ".join(
                f"{_single_line(item.keyword)} ({item.count}건)" for item in keywords
            ))
    return "\n".join(lines)
