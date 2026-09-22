"""Read-only product/category comparison contracts and ordering.

Category is optional source metadata already persisted in raw_payload. It is
normalized only for this view; existing review identities and AI inputs stay intact.
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.errors import ValidationError
from src.models import ReviewFilter, ReviewStatistics, Sentiment


def normalize_group_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def category_from_payload(payload: object) -> str | None:
    """Accept one scalar category, rejecting contradictory aliases explicitly."""
    if not isinstance(payload, dict):
        raise ValueError("카테고리 원본이 올바른 객체가 아닙니다.")
    aliases = {"category", "productcategory", "카테고리", "상품분류"}
    names = set()
    for key, value in payload.items():
        key = normalize_group_name(str(key)).casefold().replace(" ", "").replace("_", "").replace("-", "")
        if key not in aliases or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("카테고리는 문자열 또는 숫자여야 합니다.")
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("카테고리 숫자가 유효하지 않습니다.")
            if value.is_integer():
                value = int(value)
        name = normalize_group_name(str(value))
        if name:
            names.add(name)
    if len(names) > 1:
        raise ValueError("카테고리 열의 값이 서로 다릅니다. 하나의 분류로 정리하세요.")
    return next(iter(names), None)


@dataclass(slots=True)
class ComparisonRequest:
    group_by: str = "product"
    filters: ReviewFilter = field(default_factory=ReviewFilter)
    names: tuple[str, ...] = ()
    category: str | None = None
    sort: str = "name"
    order: str = "asc"
    min_reviews: int = 5
    output: Path | None = None
    chart: bool = False
    force: bool = False

    def __post_init__(self) -> None:
        if self.group_by not in ("product", "category"):
            raise ValidationError("비교 기준은 product 또는 category여야 합니다.")
        if not isinstance(self.filters, ReviewFilter) or self.filters.sentiment is not None:
            raise ValidationError("감정 비율 비교에는 감정 필터를 사용할 수 없습니다.")
        if self.filters.product_name is not None or self.filters.rating is not None:
            raise ValidationError("제품 선택은 names, 별점 조건은 rating_min을 사용하세요.")
        if not isinstance(self.names, (tuple, list)) or any(
            not isinstance(name, str) or not normalize_group_name(name) for name in self.names
        ):
            raise ValidationError("비교할 이름은 비어 있지 않은 문자열이어야 합니다.")
        self.names = tuple(dict.fromkeys(normalize_group_name(name) for name in self.names))
        if self.category is not None:
            if not isinstance(self.category, str) or not normalize_group_name(self.category):
                raise ValidationError("카테고리 필터는 비어 있을 수 없습니다.")
            self.category = normalize_group_name(self.category)
        if self.sort not in ("name", "reviews", "rating", "negative") or self.order not in ("asc", "desc"):
            raise ValidationError("비교 정렬 기준 또는 방향이 올바르지 않습니다.")
        if type(self.min_reviews) is not int or self.min_reviews < 1:
            raise ValidationError("최소 분석 표본 수는 1 이상의 정수여야 합니다.")
        if self.output is not None and not isinstance(self.output, Path):
            raise ValidationError("출력 디렉터리는 Path여야 합니다.")
        if type(self.chart) is not bool or type(self.force) is not bool:
            raise ValidationError("chart와 force는 boolean이어야 합니다.")
        if self.output is None and (self.chart or self.force):
            raise ValidationError("--chart와 --force는 --output과 함께 사용하세요.")


@dataclass(slots=True)
class ComparisonGroup:
    name: str | None
    product_count: int
    statistics: ReviewStatistics

    @property
    def label(self) -> str:
        return self.name if self.name is not None else "[카테고리 없음]"

    @property
    def negative_ratio(self) -> float | None:
        stats = self.statistics
        return stats.sentiment_counts.get(Sentiment.NEGATIVE, 0) / stats.analyzed_reviews if stats.analyzed_reviews else None


@dataclass(slots=True)
class ComparisonResult:
    groups: list[ComparisonGroup]
    missing_names: tuple[str, ...] = ()
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ComparisonRepository(Protocol):
    def get_comparison_groups(
        self, filters: ReviewFilter, *, group_by: str, category: str | None = None,
    ) -> list[ComparisonGroup]: ...


class ComparisonService:
    def __init__(self, repository: ComparisonRepository) -> None:
        self.repository = repository

    def compare(self, request: ComparisonRequest) -> ComparisonResult:
        groups = self.repository.get_comparison_groups(
            request.filters, group_by=request.group_by, category=request.category,
        )
        available = {group.name for group in groups}
        missing = tuple(name for name in request.names if name not in available)
        if request.names:
            groups = [group for group in groups if group.name in request.names]
        # Stable tie-break by name. Unknown values stay last in either direction.
        groups.sort(key=lambda group: (group.name is None, group.name or ""))
        if request.sort == "name":
            named = [group for group in groups if group.name is not None]
            if request.order == "desc":
                named.reverse()
            groups = named + [group for group in groups if group.name is None]
        else:
            def metric(group: ComparisonGroup) -> float | int | None:
                if request.sort == "reviews":
                    return group.statistics.total_reviews
                if request.sort == "rating":
                    return group.statistics.average_rating
                return group.negative_ratio
            known = [group for group in groups if metric(group) is not None]
            unknown = [group for group in groups if metric(group) is None]
            known.sort(key=metric, reverse=request.order == "desc")
            groups = known + unknown
        return ComparisonResult(groups, missing)
