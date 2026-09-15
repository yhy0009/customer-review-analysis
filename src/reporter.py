"""UTF-8 TXT/Markdown reports consuming the shared statistics and insight DTOs."""

from __future__ import annotations

import html
import os
import string
import tempfile
import unicodedata
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from src.config import resolve_project_path
from src.errors import OutputError, ValidationError
from src.models import (
    InsightResult, KeywordCount, OutputArtifact, OutputKind, ReportFormat,
    ReviewFilter, ReviewStatistics, Sentiment,
)


_LABELS = {Sentiment.POSITIVE: "긍정", Sentiment.NEUTRAL: "중립", Sentiment.NEGATIVE: "부정"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: str, markdown: bool) -> str:
    # Keep external prose within a single paragraph/table cell. It cannot add
    # Markdown headings, links, images, HTML, or terminal control characters.
    value = " ".join(value.split())
    value = "".join(c for c in value if unicodedata.category(c) != "Cc")
    if markdown:
        value = "".join("\\" + c if c in string.punctuation and c not in "&<>" else c for c in value)
        value = html.escape(value, quote=False)
    return value


def _filter_text(filters: ReviewFilter) -> str:
    parts = []
    if filters.sentiment is not None:
        parts.append(f"감정={_LABELS[filters.sentiment]}")
    if filters.date_from is not None or filters.date_to is not None:
        parts.append(f"기간={filters.date_from or '시작 제한 없음'} ~ {filters.date_to or '종료 제한 없음'}")
    if filters.product_name is not None:
        parts.append(f"제품명 포함={filters.product_name}")
    if filters.rating is not None:
        parts.append(f"별점={filters.rating}")
    if filters.rating_min is not None:
        parts.append(f"최소 별점={filters.rating_min}")
    return ", ".join(parts) if parts else "추가 필터 없음"


class FileReportGenerator:
    """ReportGenerator implementation without repository, AI, or console I/O.

    An existing directory or a suffixless path receives an automatic UTC name.
    A .txt/.md path names a file explicitly. Complete files are published atomically.
    """

    def __init__(self, *, top_n: int = 10, clock: Callable[[], datetime] = _utc_now) -> None:
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
            raise ValidationError("top_n은 양의 정수여야 합니다.")
        self.top_n = top_n
        self._clock = clock

    def generate_report(
        self, statistics: ReviewStatistics, insight: InsightResult | None, output: Path, *,
        report_format: ReportFormat, force: bool = False,
    ) -> OutputArtifact:
        if not isinstance(statistics, ReviewStatistics):
            raise ValidationError("statistics는 ReviewStatistics여야 합니다.")
        if insight is not None and not isinstance(insight, InsightResult):
            raise ValidationError("insight는 InsightResult 또는 None이어야 합니다.")
        if not isinstance(report_format, ReportFormat):
            raise ValidationError("report_format은 ReportFormat이어야 합니다.")
        if not isinstance(force, bool) or not isinstance(output, Path):
            raise ValidationError("force는 bool, output은 Path여야 합니다.")
        generated_at = self._clock()
        if not isinstance(generated_at, datetime) or generated_at.utcoffset() != timezone.utc.utcoffset(None):
            raise ValidationError("리포트 생성 시각은 UTC datetime이어야 합니다.")
        try:
            content = self._render(statistics, insight, report_format, generated_at)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValidationError("리포트 입력 데이터의 형식이 올바르지 않습니다.") from None
        suffix = "." + report_format.value
        temporary = None
        try:
            target = resolve_project_path(output.parent) / output.name
            if target.is_symlink():
                raise OutputError("심볼릭 링크에는 리포트를 생성할 수 없습니다.")
            if target.is_dir() or not target.suffix:
                target = resolve_project_path(target) / f"report_{generated_at:%Y%m%d_%H%M%S}{suffix}"
            elif target.suffix.lower() != suffix:
                raise ValidationError("출력 파일 확장자가 리포트 형식과 일치하지 않습니다.")
            if target.is_symlink():
                raise OutputError("심볼릭 링크에는 리포트를 생성할 수 없습니다.")
            if target.exists() and not force:
                raise OutputError("출력 파일이 이미 있습니다. 덮어쓰려면 --force를 지정하세요.")
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".review-report-", suffix=suffix, dir=target.parent)
            temporary = Path(name)
            os.close(descriptor)
            temporary.write_text(content, encoding="utf-8")
            if target.is_symlink():
                raise OutputError("심볼릭 링크에는 리포트를 생성할 수 없습니다.")
            if force:
                os.replace(temporary, target)
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    raise OutputError("출력 파일이 이미 있습니다. 덮어쓰려면 --force를 지정하세요.") from None
            return OutputArtifact(OutputKind.REPORT, target, report_format.value)
        except (OutputError, ValidationError):
            raise
        except Exception:
            raise OutputError("리포트 파일을 생성할 수 없습니다.") from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _render(
        self, stats: ReviewStatistics, insight: InsightResult | None,
        report_format: ReportFormat, generated_at: datetime,
    ) -> str:
        markdown = report_format is ReportFormat.MARKDOWN
        lines = ["# 고객 리뷰 종합 리포트" if markdown else "고객 리뷰 종합 리포트", "",
                 f"리포트 생성 시각 (UTC): {generated_at.isoformat().replace('+00:00', 'Z')}"]

        def heading(title: str) -> None:
            lines.extend(["", "## " + title if markdown else title, ""])

        def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
            if markdown:
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                lines.extend("| " + " | ".join(row) + " |" for row in rows)
            else:
                lines.append(" / ".join(headers))
                lines.extend(" / ".join(row) for row in rows)

        def keywords(title: str, values: Sequence[KeywordCount]) -> None:
            lines.extend(["", "### " + title if markdown else title, ""])
            if not values:
                lines.append("집계된 키워드가 없습니다.")
            else:
                table(["순위", "키워드", "리뷰 수"], [
                    [str(i), _text(k.keyword, markdown), f"{k.count}건"]
                    for i, k in enumerate(values[:self.top_n], 1)
                ])

        heading("처리 현황 및 품질 지표")
        coverage = stats.analyzed_reviews / stats.total_reviews if stats.total_reviews else 0
        failure_rate = stats.failed_reviews / stats.total_reviews if stats.total_reviews else 0
        average = f"{stats.average_rating:.2f}/5" if stats.average_rating is not None else "N/A"
        table(["항목", "값"], [
            ["정제 리뷰", f"{stats.total_reviews}건"], ["분석 완료", f"{stats.analyzed_reviews}건"],
            ["미분석 (실패 제외)", f"{stats.unanalyzed_reviews}건"], ["분석 실패", f"{stats.failed_reviews}건"],
            ["분석 완료율 (정제 리뷰 기준)", f"{coverage:.1%}"],
            ["분석 실패율 (정제 리뷰 기준)", f"{failure_rate:.1%}"], ["평균 별점", average],
        ])
        lines.extend(["", "완료율과 실패율은 처리 현황 지표이며, 감정 분류의 정확도 측정값이 아닙니다."])
        if not stats.total_reviews:
            lines.append("통계 대상 정제 리뷰가 없습니다.")

        heading("감정 분포")
        lines.extend([f"분석 완료 {stats.analyzed_reviews}건 기준", ""])
        table(["감정", "리뷰 수", "비율"], [
            [label, f"{stats.sentiment_counts.get(s, 0)}건", f"{stats.sentiment_ratios.get(s, 0.0):.1%}"]
            for s, label in _LABELS.items()
        ])
        heading("날짜별 감정 분포")
        if stats.daily_sentiment_counts:
            table(["리뷰 작성일", *_LABELS.values()], [
                [day.isoformat(), *(f"{counts.get(s, 0)}건" for s in _LABELS)]
                for day, counts in sorted(stats.daily_sentiment_counts.items())
            ])
        else:
            lines.append("분석 완료 리뷰의 날짜별 집계가 없습니다.")
        heading("별점별 감정 분포")
        table(["별점", *_LABELS.values()], [
            [str(rating), *(f"{stats.rating_sentiment_matrix.get(rating, {}).get(s, 0)}건" for s in _LABELS)]
            for rating in range(1, 6)
        ])
        heading(f"통계 대상 TOP {self.top_n} 키워드")
        lines.append("빈도는 해당 키워드를 포함한 리뷰 수입니다.")
        lines.append("키워드는 리뷰 전체의 감정별로 묶습니다. 긍정 리뷰의 키워드에도 불편한 점이 포함될 수 있습니다.")
        keywords("긍정 리뷰의 키워드", stats.top_positive_keywords)
        keywords("부정 리뷰의 키워드", stats.top_negative_keywords)

        heading("AI 인사이트")
        if insight is None:
            lines.append("AI 인사이트가 제공되지 않았습니다.")
        else:
            lines.extend([
                "- 추출 조건: " + _text(_filter_text(insight.filters), markdown),
                f"- 실제 추출 대상: 분석 완료 리뷰 {insight.review_count}건",
                "- 인사이트 생성 시각 (UTC): " + insight.generated_at.isoformat().replace("+00:00", "Z"),
                "",
                "통계와 인사이트의 대상 범위는 서로 다를 수 있습니다. 아래 내용은 추출 대상에 한정됩니다.",
                "",
            ])
            if insight.review_count == 0:
                lines.append("조건에 맞는 분석 완료 리뷰가 없어 AI 요약을 생성하지 않았습니다.")
            else:
                lines.append(_text(insight.summary, markdown) if insight.summary.strip() else "요약이 없습니다.")
                if insight.summary_scope == "top_complaints":
                    lines.append("요약 범위: 리뷰 수 기준 주요 불편 최대 3개와 장점 최대 1개. 전체 근거는 아래 목록에 보존됩니다.")
                for title, items in (("주요 이슈", insight.issues), ("개선 제안", insight.improvement_suggestions)):
                    lines.extend(["", "### " + title if markdown else title, ""])
                    lines.extend(["- " + _text(item, markdown) for item in items] if items else ["제공된 항목이 없습니다."])
                keywords(f"추출 대상 긍정 리뷰의 키워드 TOP {self.top_n}", insight.positive_keywords)
                keywords(f"추출 대상 부정 리뷰의 키워드 TOP {self.top_n}", insight.negative_keywords)
                lines.extend(["", "개선 제안은 AI가 생성한 검토 항목이며 효과가 검증된 결론은 아닙니다."])
                if insight.evidence_groups:
                    heading("전체 인사이트 근거")
                    lines.append("제품별 주제입니다. 같은 리뷰의 중복 언급은 리뷰 수에 한 번만 포함합니다. 인용 존재는 의미적 정확성 보장이 아닙니다.")
                    for number, group in enumerate(insight.evidence_groups, 1):
                        kind = "불편" if group.kind == "complaints" else "장점"
                        lines.extend(["", f"- 근거 주제 {number}: " + _text(
                            f"{group.product_name} / {kind} / {group.label} ({group.review_count}건)", markdown)])
                        for citation in group.citations:
                            lines.append(f"  - 리뷰 {citation.review_id}: " + _text(
                                f"{citation.label} — {citation.quote}", markdown))
        return "\n".join(lines) + "\n"


__all__ = ["FileReportGenerator"]
