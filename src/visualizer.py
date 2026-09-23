"""ReviewStatistics를 하나의 통합 대시보드 PNG로 시각화한다.

이 모듈은 services.ReviewVisualizer Protocol을 구조적으로 구현한다.

책임(INTERFACE_BOUNDARY_SPEC 기준):
- storage.get_statistics()가 계산한 ReviewStatistics를 소비만 한다.
  집계를 자체적으로 다시 계산하지 않는다(§9.2).
- 결과는 list[OutputArtifact]로 반환하며 화면에 직접 출력하지 않는다(§9.3).
- 출력 디렉터리는 없으면 생성하고, 기존 파일은 force 없이는 덮어쓰지 않는다(§13).
- 자동 파일명은 UTC YYYYMMDD_HHMMSS 규칙을 따르고, 절대경로를 반환한다(§13).
- 실패 시 OutputError를 발생시킨다.
- API 키/리뷰 전문/PII는 로그에 남기지 않는다(§12).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import matplotlib

# CLI/오프라인 환경에서 화면(DISPLAY) 없이 PNG를 생성하기 위한 headless 백엔드.
# pyplot을 import 하기 전에 지정해야 한다.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager, rcParams  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from src.config import get_logger  # noqa: E402
from src.errors import OutputError  # noqa: E402
from src.models import (  # noqa: E402
    KeywordCount,
    OutputArtifact,
    OutputKind,
    ReviewStatistics,
    Sentiment,
)

logger = get_logger("visualizer")


# --- 감정 색상(발산형 팔레트: 두 극 + 중립 회색 중간값) ----------------------
# dataviz 팔레트 검증(validate_palette.js) 통과:
#   양극 분리(CVD/정상시야/대비/명도) 전부 PASS.
#   각 감정은 항상 한글 라벨과 함께 표기하므로 색상 단독 식별이 아니다.
SENTIMENT_COLORS: Dict[Sentiment, str] = {
    Sentiment.POSITIVE: "#0b6fd1",
    Sentiment.NEUTRAL: "#a7adb3",
    Sentiment.NEGATIVE: "#d93a2b",
}

SENTIMENT_LABELS: Dict[Sentiment, str] = {
    Sentiment.POSITIVE: "긍정",
    Sentiment.NEUTRAL: "중립",
    Sentiment.NEGATIVE: "부정",
}

# 감정 표기 순서(긍정 → 중립 → 부정)
SENTIMENT_ORDER: Sequence[Sentiment] = (
    Sentiment.POSITIVE,
    Sentiment.NEUTRAL,
    Sentiment.NEGATIVE,
)

# 텍스트/보조 요소 색상(값·라벨·축은 시리즈 색이 아닌 잉크 토큰을 쓴다)
INK_PRIMARY = "#1a1a1a"
INK_SECONDARY = "#55585e"
INK_MUTED = "#8a8f98"
GRID_COLOR = "#e6e6e3"
SURFACE_COLOR = "#ffffff"

# 키워드 패널에 표시할 긍정/부정 키워드 최대 개수
_MAX_KEYWORDS_PER_SIDE = 6

# 한글 폰트 후보(우선순위 순). macOS 기본 폰트를 앞에 둔다.
_KOREAN_FONT_CANDIDATES = (
    "AppleGothic",
    "Apple SD Gothic Neo",
    "NanumGothic",
    "Nanum Gothic",
    "Malgun Gothic",
    "NanumBarunGothic",
)

_DEFAULT_DPI = 150


class DashboardVisualizer:
    """ReviewStatistics를 2×2 통합 대시보드 PNG로 렌더링한다."""

    def generate_dashboard(
        self,
        statistics: ReviewStatistics,
        output: Path,
        *,
        font_family: str,
        dpi: int,
        force: bool = False,
    ) -> List[OutputArtifact]:
        """통합 대시보드 PNG 한 장을 생성한다.

        Args:
            statistics: storage.get_statistics()가 계산한 통계.
            output: 출력 대상. 디렉터리이면 자동 파일명을 붙이고,
                ``.png`` 파일 경로이면 그대로 사용한다.
            font_family: 사용할 폰트명(빈 문자열이면 한글 폰트 자동 탐색).
            dpi: 저장 해상도.
            force: True이면 기존 파일을 덮어쓴다.

        Returns:
            생성된 아티팩트 한 개를 담은 리스트.

        Raises:
            OutputError: 디렉터리 생성/파일 저장 실패,
                또는 force 없이 기존 파일과 충돌할 때.
        """
        target = self._resolve_target(output, force=force)
        resolved_dpi = self._resolve_dpi(dpi)
        chosen_font = self._configure_fonts(font_family)

        logger.info(
            "대시보드 생성 시작: total=%d analyzed=%d dpi=%d font=%s",
            statistics.total_reviews,
            statistics.analyzed_reviews,
            resolved_dpi,
            chosen_font or "(fallback)",
        )

        fig = self._build_figure(statistics)
        try:
            fig.savefig(
                target,
                dpi=resolved_dpi,
                format="png",
                facecolor=SURFACE_COLOR,
                bbox_inches="tight",
            )
        except OSError as exc:
            raise OutputError(
                f"대시보드 파일을 저장할 수 없습니다: {target}"
            ) from exc
        finally:
            plt.close(fig)

        absolute = target.resolve()
        logger.info("대시보드 생성 완료: %s", absolute)

        return [
            OutputArtifact(
                kind=OutputKind.CHART,
                path=absolute,
                format="png",
            )
        ]

    # -- 경로/설정 처리 ------------------------------------------------------

    def _resolve_target(self, output: Path, *, force: bool) -> Path:
        """출력 대상 파일 경로를 확정하고 디렉터리를 준비한다."""
        output = Path(output)

        if output.suffix.lower() == ".png":
            directory = output.parent
            target = output
        else:
            # 디렉터리로 간주하고 UTC 타임스탬프 파일명을 자동 생성한다.
            directory = output
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            target = directory / f"dashboard_{timestamp}.png"

        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputError(
                f"출력 디렉터리를 만들 수 없습니다: {directory}"
            ) from exc

        if target.exists() and not force:
            raise OutputError(
                f"출력 파일이 이미 존재합니다(--force 필요): {target}"
            )

        return target

    def _resolve_dpi(self, dpi: int) -> int:
        """dpi 값을 검증하고 유효하지 않으면 기본값으로 대체한다."""
        try:
            value = int(dpi)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            logger.warning("잘못된 dpi 값(%r), 기본값 %d 사용", dpi, _DEFAULT_DPI)
            return _DEFAULT_DPI
        return value

    def _configure_fonts(self, font_family: str) -> Optional[str]:
        """한글이 깨지지 않도록 폰트를 설정하고 선택된 폰트명을 반환한다."""
        available = {font.name for font in font_manager.fontManager.ttflist}

        candidates: List[str] = []
        if font_family and font_family.strip():
            candidates.append(font_family.strip())
        candidates.extend(_KOREAN_FONT_CANDIDATES)

        chosen = next((name for name in candidates if name in available), None)
        if chosen:
            rcParams["font.family"] = chosen
        else:
            logger.warning(
                "한글 폰트를 찾지 못했습니다. 한글이 깨질 수 있습니다."
            )

        # 마이너스 기호가 유니코드 문자로 렌더되며 깨지는 문제 방지
        rcParams["axes.unicode_minus"] = False
        return chosen

    # -- 도형 구성 ----------------------------------------------------------

    def _build_figure(self, statistics: ReviewStatistics):
        """2×2 격자 대시보드 Figure를 만든다."""
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.patch.set_facecolor(SURFACE_COLOR)

        self._plot_sentiment_distribution(axes[0][0], statistics)
        self._plot_daily_trend(axes[0][1], statistics)
        self._plot_rating_matrix(axes[1][0], statistics)
        self._plot_keywords(axes[1][1], statistics)

        fig.suptitle(
            "고객 리뷰 분석 대시보드",
            fontsize=18,
            fontweight="bold",
            color=INK_PRIMARY,
        )

        summary = self._summary_line(statistics)
        fig.text(
            0.5,
            0.945,
            summary,
            ha="center",
            va="top",
            fontsize=11,
            color=INK_SECONDARY,
        )

        # 감정 색상 공통 범례(2개 이상 시리즈이므로 항상 표시)
        handles = [
            Patch(facecolor=SENTIMENT_COLORS[sent], label=SENTIMENT_LABELS[sent])
            for sent in SENTIMENT_ORDER
        ]
        fig.legend(
            handles=handles,
            loc="lower center",
            ncol=3,
            frameon=False,
            fontsize=11,
            bbox_to_anchor=(0.5, 0.005),
        )

        fig.tight_layout(rect=(0, 0.04, 1, 0.93))
        return fig

    def _summary_line(self, statistics: ReviewStatistics) -> str:
        parts = [
            f"총 리뷰 {statistics.total_reviews:,}건",
            f"분석 완료 {statistics.analyzed_reviews:,}건",
            f"미분석 {statistics.unanalyzed_reviews:,}건",
        ]
        if statistics.failed_reviews:
            parts.append(f"분석 실패 {statistics.failed_reviews:,}건")
        if statistics.average_rating is not None:
            parts.append(f"평균 평점 {statistics.average_rating:.2f}")
        return "   ·   ".join(parts)

    # -- 개별 패널 ----------------------------------------------------------

    def _plot_sentiment_distribution(
        self, ax, statistics: ReviewStatistics
    ) -> None:
        """① 감정 분포(막대)."""
        ax.set_title("감정 분포", fontsize=13, color=INK_PRIMARY, loc="left")

        counts = statistics.sentiment_counts or {}
        values = [int(counts.get(sent, 0)) for sent in SENTIMENT_ORDER]

        if sum(values) == 0:
            self._no_data(ax)
            return

        labels = [SENTIMENT_LABELS[sent] for sent in SENTIMENT_ORDER]
        colors = [SENTIMENT_COLORS[sent] for sent in SENTIMENT_ORDER]
        ratios = statistics.sentiment_ratios or {}

        bars = ax.bar(labels, values, color=colors, width=0.6)
        self._style_axes(ax)
        ax.set_ylabel("리뷰 수", fontsize=10, color=INK_SECONDARY)

        top = max(values)
        ax.set_ylim(0, top * 1.18)
        for sent, bar, value in zip(SENTIMENT_ORDER, bars, values):
            ratio = ratios.get(sent)
            label = f"{value:,}"
            if ratio is not None:
                label += f"\n({ratio * 100:.0f}%)"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + top * 0.02,
                label,
                ha="center",
                va="bottom",
                fontsize=10,
                color=INK_SECONDARY,
            )

    def _plot_daily_trend(self, ax, statistics: ReviewStatistics) -> None:
        """② 일자별 감정 추이(선)."""
        ax.set_title(
            "일자별 감정 추이", fontsize=13, color=INK_PRIMARY, loc="left"
        )

        daily = statistics.daily_sentiment_counts or {}
        if not daily:
            self._no_data(ax)
            return

        dates = sorted(daily.keys())
        for sent in SENTIMENT_ORDER:
            series = [int(daily[day].get(sent, 0)) for day in dates]
            if sum(series) == 0:
                continue
            ax.plot(
                dates,
                series,
                color=SENTIMENT_COLORS[sent],
                linewidth=2,
                marker="o",
                markersize=4,
                label=SENTIMENT_LABELS[sent],
            )

        self._style_axes(ax)
        ax.set_ylabel("리뷰 수", fontsize=10, color=INK_SECONDARY)
        ax.set_ylim(bottom=0)
        ax.tick_params(axis="x", labelrotation=30)
        for label in ax.get_xticklabels():
            label.set_horizontalalignment("right")

    def _plot_rating_matrix(self, ax, statistics: ReviewStatistics) -> None:
        """③ 평점별 감정 분포(누적 막대)."""
        ax.set_title(
            "평점별 감정 분포", fontsize=13, color=INK_PRIMARY, loc="left"
        )

        matrix = statistics.rating_sentiment_matrix or {}
        ratings = [r for r in range(1, 6) if r in matrix]
        if not ratings or all(
            sum(matrix[r].get(s, 0) for s in SENTIMENT_ORDER) == 0
            for r in ratings
        ):
            self._no_data(ax)
            return

        x_labels = [f"{r}점" for r in ratings]
        bottoms = [0.0] * len(ratings)
        for sent in SENTIMENT_ORDER:
            heights = [int(matrix[r].get(sent, 0)) for r in ratings]
            ax.bar(
                x_labels,
                heights,
                bottom=bottoms,
                color=SENTIMENT_COLORS[sent],
                width=0.6,
                label=SENTIMENT_LABELS[sent],
                edgecolor=SURFACE_COLOR,  # 세그먼트 사이 2px 간격 효과
                linewidth=1.5,
            )
            bottoms = [b + h for b, h in zip(bottoms, heights)]

        self._style_axes(ax)
        ax.set_ylabel("리뷰 수", fontsize=10, color=INK_SECONDARY)
        ax.set_ylim(bottom=0)

    def _plot_keywords(self, ax, statistics: ReviewStatistics) -> None:
        """④ 긍정/부정 상위 키워드(수평 막대)."""
        ax.set_title(
            "상위 키워드(긍정/부정)", fontsize=13, color=INK_PRIMARY, loc="left"
        )

        positive = self._top_keywords(statistics.top_positive_keywords)
        negative = self._top_keywords(statistics.top_negative_keywords)

        if not positive and not negative:
            self._no_data(ax)
            return

        # 부정(아래) → 긍정(위) 순으로 쌓아 한 축에 함께 표시한다.
        entries: List[tuple[str, int, str]] = []
        for kw in reversed(negative):
            entries.append(
                (f"[부정] {kw.keyword}", kw.count, SENTIMENT_COLORS[Sentiment.NEGATIVE])
            )
        for kw in reversed(positive):
            entries.append(
                (f"[긍정] {kw.keyword}", kw.count, SENTIMENT_COLORS[Sentiment.POSITIVE])
            )

        labels = [e[0] for e in entries]
        values = [e[1] for e in entries]
        colors = [e[2] for e in entries]

        positions = range(len(entries))
        ax.barh(list(positions), values, color=colors, height=0.65)
        ax.set_yticks(list(positions))
        ax.set_yticklabels(labels, fontsize=9, color=INK_SECONDARY)

        self._style_axes(ax, grid_axis="x")
        ax.set_xlabel("언급 수", fontsize=10, color=INK_SECONDARY)

        if values:
            span = max(values)
            ax.set_xlim(0, span * 1.15)
            for pos, value in zip(positions, values):
                ax.text(
                    value + span * 0.01,
                    pos,
                    f"{value:,}",
                    va="center",
                    ha="left",
                    fontsize=9,
                    color=INK_SECONDARY,
                )

    def _top_keywords(
        self, keywords: Sequence[KeywordCount]
    ) -> List[KeywordCount]:
        """언급 수 기준 상위 키워드를 잘라 반환한다."""
        ranked = sorted(
            (kw for kw in (keywords or []) if kw.count > 0),
            key=lambda kw: kw.count,
            reverse=True,
        )
        return ranked[:_MAX_KEYWORDS_PER_SIDE]

    # -- 공통 스타일 --------------------------------------------------------

    def _style_axes(self, ax, *, grid_axis: str = "y") -> None:
        """축/격자를 눈에 띄지 않게 정돈한다."""
        ax.set_facecolor(SURFACE_COLOR)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID_COLOR)
        ax.tick_params(colors=INK_SECONDARY, labelsize=10)
        ax.grid(axis=grid_axis, color=GRID_COLOR, linewidth=0.8, alpha=0.9)
        ax.set_axisbelow(True)

    def _no_data(self, ax) -> None:
        """데이터가 없는 패널에 안내 문구를 표시한다."""
        ax.text(
            0.5,
            0.5,
            "데이터 없음",
            ha="center",
            va="center",
            fontsize=12,
            color=INK_MUTED,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)


def generate_dashboard(
    statistics: ReviewStatistics,
    output: Path,
    *,
    font_family: str,
    dpi: int,
    force: bool = False,
) -> List[OutputArtifact]:
    """모듈 수준 편의 함수(ReviewVisualizer Protocol과 동일 시그니처)."""
    return DashboardVisualizer().generate_dashboard(
        statistics,
        output,
        font_family=font_family,
        dpi=dpi,
        force=force,
    )


__all__ = ["DashboardVisualizer", "generate_dashboard"]
