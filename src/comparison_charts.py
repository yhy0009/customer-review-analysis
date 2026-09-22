"""Paged comparison PNGs; imported only for an explicit --chart request."""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from src.comparison import ComparisonRequest, ComparisonResult
from src.comparison_output import display_name
from src.models import Sentiment


PAGE_SIZE = 20
COLORS = ("#0b6fd1", "#a7adb3", "#d93a2b")
LABELS = ("긍정", "중립", "부정")


def render_comparison_charts(
    result: ComparisonResult, request: ComparisonRequest, stage: Path, stem: str, *,
    font_family: str, dpi: int,
) -> list[Path]:
    available = {font.name for font in font_manager.fontManager.ttflist}
    candidates = (font_family, "AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic")
    font = next((candidate for candidate in candidates if candidate in available), "DejaVu Sans")
    files = []
    pages = max(1, (len(result.groups) + PAGE_SIZE - 1) // PAGE_SIZE)
    with matplotlib.rc_context({"font.family": font, "axes.unicode_minus": False, "text.usetex": False, "text.parse_math": False}):
        for page in range(pages):
            groups = result.groups[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
            fig, axes = plt.subplots(1, 3, figsize=(16, max(4.8, len(groups) * .58 + 2.5)), sharey=True)
            try:
                dimension = "제품" if request.group_by == "product" else "카테고리"
                fig.suptitle(f"{dimension}별 리뷰 비교 ({page + 1}/{pages})", fontsize=18, fontweight="bold")
                labels = []
                for group in groups:
                    name = display_name(group.label)
                    if len(name) > 60:
                        name = name[:59] + "…"
                    labels.append(textwrap.fill(name, 23))
                y = list(range(len(groups)))
                axes[0].set_yticks(y, labels)
                axes[0].barh(y, [group.statistics.total_reviews for group in groups], color="#526c85")
                axes[0].set_title("정제 리뷰 수")
                axes[0].set_xlim(0, max([group.statistics.total_reviews for group in groups] + [1]) * 1.3)
                axes[1].barh(y, [group.statistics.average_rating or 0 for group in groups], color="#526c85")
                axes[1].set_title("평균 별점 (정제 리뷰 기준)")
                axes[1].set_xlim(0, 5.7)
                left = [0.0] * len(groups)
                for sentiment, color, label in zip(Sentiment, COLORS, LABELS):
                    values = [group.statistics.sentiment_counts.get(sentiment, 0) / group.statistics.analyzed_reviews * 100
                              if group.statistics.analyzed_reviews else 0 for group in groups]
                    axes[2].barh(y, values, left=left, color=color, label=label)
                    for index, value in enumerate(values):
                        if value >= 12:
                            axes[2].text(left[index] + value / 2, index, f"{value:.0f}%", ha="center", va="center",
                                         color="black" if sentiment is Sentiment.NEUTRAL else "white", fontsize=9)
                    left = [start + value for start, value in zip(left, values)]
                axes[2].set_title("감정 비율 (분석 완료 기준)")
                axes[2].set_xlim(0, 130)
                axes[2].set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
                axes[2].legend(loc="upper center", bbox_to_anchor=(.5, -.09), ncol=3, frameon=False)
                for index, group in enumerate(groups):
                    stats = group.statistics
                    axes[0].text(stats.total_reviews, index, f" {stats.total_reviews}", va="center")
                    axes[1].text(stats.average_rating or 0, index,
                                 f" {stats.average_rating:.2f}" if stats.average_rating is not None else " N/A", va="center")
                    annotation = "N/A" if not stats.analyzed_reviews else f"n={stats.analyzed_reviews}"
                    if stats.analyzed_reviews < request.min_reviews:
                        annotation += " *"
                    axes[2].text(102, index, annotation, va="center", fontsize=9)
                for ax in axes:
                    ax.grid(axis="x", alpha=.18)
                    ax.set_axisbelow(True)
                    for spine in ("top", "right"):
                        ax.spines[spine].set_visible(False)
                axes[0].invert_yaxis()
                if not groups:
                    axes[0].text(.5, .5, "조건에 맞는 정제 리뷰가 없습니다", transform=axes[0].transAxes, ha="center")
                context = f"기간 {request.filters.date_from or '전체'} ~ {request.filters.date_to or '전체'}"
                if request.category:
                    context += f" | 카테고리: {display_name(request.category)}"
                if request.filters.rating_min is not None:
                    context += f" | 최소 별점 {request.filters.rating_min}"
                context = textwrap.fill(context, 110)
                fig.text(.02, .02, f"{context}\n* 분석 {request.min_reviews}건 미만: 참고용 | 미분석·실패 리뷰는 감정 비율에서 제외", fontsize=10)
                fig.tight_layout(rect=(0, .11, 1, .94), w_pad=2)
                target = stage / f"{stem}_{page + 1:02d}.png"
                fig.savefig(target, dpi=dpi, facecolor="white")
                files.append(target)
            finally:
                plt.close(fig)
    return files
