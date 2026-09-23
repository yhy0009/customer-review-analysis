"""Pure text presentation for CLI command results."""

import re
import unicodedata
from datetime import datetime

from src.models import (
    BatchOperationResult, DashboardResult, InsightResult, OutputKind,
    Page, ReviewDetail, ReviewStatistics, Sentiment,
)


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
            f"ID {review.id} | {review.review_date.isoformat() if review.review_date else '날짜 없음'}"
            f" | 별점 {str(review.rating) + '/5' if review.rating is not None else 'N/A'}"
            f" | {_single_line(review.product_name or '제품명 없음', 40)}",
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
        f"제품명: {_single_line(review.product_name or '제품명 없음')}",
        f"작성일: {review.review_date.isoformat() if review.review_date else '날짜 없음'}",
        f"별점: {str(review.rating) + '/5' if review.rating is not None else 'N/A'}",
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



def format_insight_result(result: InsightResult) -> str:
    """Render the selected analyzed-review scope and terminal-safe AI insights."""
    filters = result.filters
    conditions = []
    if filters.sentiment is not None:
        conditions.append(f"감정={_SENTIMENT_LABELS[filters.sentiment]}")
    if filters.date_from is not None or filters.date_to is not None:
        start = filters.date_from.isoformat() if filters.date_from else "시작 제한 없음"
        end = filters.date_to.isoformat() if filters.date_to else "종료 제한 없음"
        conditions.append(f"기간={start} ~ {end}")
    if filters.product_name is not None:
        conditions.append(f"제품명 포함={_single_line(filters.product_name) or '이름 없음'}")
    if filters.rating is not None:
        conditions.append(f"별점={filters.rating}/5")
    if filters.rating_min is not None:
        conditions.append(f"최소 별점={filters.rating_min}/5")

    lines = [
        f"인사이트 생성 시각 (UTC): {_utc_text(result.generated_at)}",
        f"실제 추출 대상: 분석 완료 리뷰 {result.review_count}건",
        "필터: " + (", ".join(conditions) if conditions else "추가 필터 없음"),
    ]
    if result.review_count == 0:
        # Ignore stale optional fields when there are no reviews behind them.
        lines.extend(["", "조건에 맞는 분석 완료 리뷰가 없어 AI 요약을 생성하지 않았습니다."])
        return "\n".join(lines)

    lines.extend(["", "키워드 빈도는 해당 키워드를 포함한 리뷰 수입니다."])
    if result.summary_scope == "top_complaints":
        lines.append("요약 범위: 리뷰 수 기준 주요 불편 최대 3개와 장점 최대 1개. 전체 근거는 아래 목록에 보존됩니다.")
    if result.evidence_groups:
        lines.append("전체 인사이트 근거:")
        for number, group in enumerate(result.evidence_groups, 1):
            kind = "불편" if group.kind == "complaints" else "장점"
            lines.append(f"  근거 주제 {number}: {_single_line(group.product_name or '제품명 없음')} / {kind} / "
                         f"{_single_line(group.label)} ({group.review_count}건)")
            for citation in group.citations:
                lines.append(f"    리뷰 {citation.review_id}: {_single_line(citation.label)} — {_single_line(citation.quote)}")
    for label, keywords in (
        ("긍정 키워드", result.positive_keywords),
        ("부정 키워드", result.negative_keywords),
    ):
        lines.append(label + ":")
        if keywords:
            lines.extend(
                f"  - {_single_line(item.keyword) or '이름 없음'}: {item.count}건"
                for item in keywords
            )
        else:
            lines.append("  집계된 키워드가 없습니다.")

    summary = _safe_text(result.summary).strip()
    lines.extend(["", "요약:"])
    if summary:
        lines.extend("  " + line for line in summary.splitlines())
    else:
        lines.append("  요약이 없습니다.")
    for title, items in (
        ("주요 이슈", result.issues),
        ("개선 제안", result.improvement_suggestions),
    ):
        lines.extend(["", title + ":"])
        visible_items = [text for item in items if (text := _single_line(item))]
        if visible_items:
            lines.extend("  - " + item for item in visible_items)
        else:
            lines.append("  제공된 항목이 없습니다.")
    return "\n".join(lines)


def format_batch_result(result: BatchOperationResult) -> str:
    """Show every outcome, including validation rejections, without review bodies."""
    lines = [
        f"processed={result.processed} succeeded={result.succeeded} "
        f"skipped={result.skipped} failed={result.failed} rejected={result.rejected}"
    ]
    for error in result.errors:
        item = _single_line(error.item_ref) if error.item_ref is not None else "-"
        lines.append(
            f"  [{_single_line(error.code)}] item={item}: {_single_line(error.message)}"
        )
    return "\n".join(lines)


def format_dashboard_result(result: DashboardResult) -> str:
    """Display returned artifact paths; an empty result does not claim creation."""
    labels = {
        OutputKind.CHART: "차트", OutputKind.REPORT: "리포트", OutputKind.EXPORT: "내보내기",
    }
    lines = [f"생성 파일: {len(result.artifacts)}개" if result.artifacts else "생성된 파일이 없습니다."]
    for artifact in result.artifacts:
        lines.append(
            f"  {labels[artifact.kind]} ({_single_line(artifact.format)}): "
            f"{_single_line(str(artifact.path))}"
        )
    if result.sentiment_change is not None:
        from src.sentiment_alerts import format_sentiment_change

        lines.extend(["", format_sentiment_change(result.sentiment_change)])
    return "\n".join(lines)
