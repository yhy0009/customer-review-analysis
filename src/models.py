"""Shared data contracts used at module boundaries.

The concrete feature modules intentionally depend on these small, standard-library
types instead of each other's implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypeVar

from src.errors import ValidationError


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class DuplicatePolicy(str, Enum):
    SKIP = "skip"
    UPSERT = "upsert"


class ProcessingStatus(str, Enum):
    RAW = "RAW"
    REJECTED = "REJECTED"
    CLEANED = "CLEANED"
    ANALYZED = "ANALYZED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"


class AnalyzeTarget(str, Enum):
    ALL = "all"
    REVIEW_ID = "review_id"
    UNANALYZED = "unanalyzed"


class SortField(str, Enum):
    ID = "id"
    DATE = "date"
    RATING = "rating"
    SENTIMENT = "sentiment"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class ExportFormat(str, Enum):
    CSV = "csv"
    JSONL = "jsonl"
    EXCEL = "excel"


class ReportFormat(str, Enum):
    TXT = "txt"
    MARKDOWN = "md"


class OutputKind(str, Enum):
    CHART = "chart"
    REPORT = "report"
    EXPORT = "export"


RawScalar = Optional[object]


def _require_positive_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field_name} must be a positive integer")


def _require_non_negative_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{field_name} must be a non-negative integer")


def _require_utc(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.utcoffset().total_seconds() != 0:
        raise ValidationError(f"{field_name} must use UTC")


@dataclass(slots=True)
class RawReview:
    """Review values as loaded, before cleaner validation and normalization."""

    source_review_id: RawScalar = None
    product_name: RawScalar = None
    review_date: RawScalar = None
    rating: RawScalar = None
    review_text: RawScalar = None
    source_file: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    # Assigned on repository reads; incoming IDs never override storage identity.
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.id is not None:
            _require_positive_integer(self.id, "id")


@dataclass(slots=True)
class CleanReview:
    id: int
    product_name: str
    review_date: date
    rating: int
    review_text: str
    cleaned_at: datetime
    source_review_id: Optional[str] = None

    def __post_init__(self) -> None:
        _require_positive_integer(self.id, "id")
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValidationError("product_name must be a non-empty string")
        if not isinstance(self.review_date, date) or isinstance(self.review_date, datetime):
            raise ValidationError("review_date must be a date")
        if isinstance(self.rating, bool) or not isinstance(self.rating, int):
            raise ValidationError("rating must be an integer")
        if not 1 <= self.rating <= 5:
            raise ValidationError("rating must be between 1 and 5")
        if not isinstance(self.review_text, str) or not self.review_text.strip():
            raise ValidationError("review_text must be a non-empty string")
        _require_utc(self.cleaned_at, "cleaned_at")


@dataclass(slots=True)
class AnalysisResult:
    review_id: int
    sentiment: Sentiment
    confidence: float
    analyzed_at: datetime
    provider: str
    model: str
    summary: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    prompt_version: Optional[str] = None

    def __post_init__(self) -> None:
        _require_positive_integer(self.review_id, "review_id")
        if not isinstance(self.sentiment, Sentiment):
            raise ValidationError("sentiment must be a Sentiment value")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise ValidationError("confidence must be numeric")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValidationError("confidence must be between 0.0 and 1.0")
        _require_utc(self.analyzed_at, "analyzed_at")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValidationError("provider must be a non-empty string")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValidationError("model must be a non-empty string")
        if any(not isinstance(keyword, str) or not keyword.strip() for keyword in self.keywords):
            raise ValidationError("keywords must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class KeywordCount:
    keyword: str
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.keyword, str) or not self.keyword.strip():
            raise ValidationError("keyword must be a non-empty string")
        _require_non_negative_integer(self.count, "count")


@dataclass(slots=True)
class ReviewFilter:
    sentiment: Optional[Sentiment] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    product_name: Optional[str] = None
    rating: Optional[int] = None
    rating_min: Optional[int] = None

    def __post_init__(self) -> None:
        if self.sentiment is not None and not isinstance(self.sentiment, Sentiment):
            raise ValidationError("sentiment must be a Sentiment value")
        if self.date_from is not None and (
            not isinstance(self.date_from, date) or isinstance(self.date_from, datetime)
        ):
            raise ValidationError("date_from must be a date")
        if self.date_to is not None and (
            not isinstance(self.date_to, date) or isinstance(self.date_to, datetime)
        ):
            raise ValidationError("date_to must be a date")
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValidationError("date_from must not be after date_to")
        if self.product_name is not None:
            if not isinstance(self.product_name, str) or not self.product_name.strip():
                raise ValidationError("product_name must be a non-empty string")
        for field_name, value in (("rating", self.rating), ("rating_min", self.rating_min)):
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                    raise ValidationError(f"{field_name} must be between 1 and 5")
        if self.rating is not None and self.rating_min is not None:
            raise ValidationError("rating and rating_min are mutually exclusive")


@dataclass(slots=True)
class ReviewQuery:
    filters: ReviewFilter = field(default_factory=ReviewFilter)
    page: int = 1
    size: int = 20
    sort: SortField = SortField.ID
    order: SortOrder = SortOrder.DESC

    def __post_init__(self) -> None:
        _require_positive_integer(self.page, "page")
        _require_positive_integer(self.size, "size")
        if not isinstance(self.sort, SortField):
            raise ValidationError("sort must be a SortField value")
        if not isinstance(self.order, SortOrder):
            raise ValidationError("order must be a SortOrder value")


T = TypeVar("T")


@dataclass(slots=True)
class Page(Generic[T]):
    items: List[T]
    page: int
    size: int
    total_items: int
    total_pages: int

    def __post_init__(self) -> None:
        _require_positive_integer(self.page, "page")
        _require_positive_integer(self.size, "size")
        _require_non_negative_integer(self.total_items, "total_items")
        _require_non_negative_integer(self.total_pages, "total_pages")
        expected_pages = (
            (self.total_items + self.size - 1) // self.size if self.total_items else 0
        )
        if self.total_pages != expected_pages:
            raise ValidationError("total_pages does not match total_items and size")
        if len(self.items) > self.size:
            raise ValidationError("items cannot contain more entries than size")


@dataclass(slots=True)
class ReviewDetail:
    review: CleanReview
    analysis: Optional[AnalysisResult] = None

    def __post_init__(self) -> None:
        if self.analysis is not None and self.analysis.review_id != self.review.id:
            raise ValidationError("analysis.review_id must match review.id")


@dataclass(slots=True)
class InsightResult:
    filters: ReviewFilter
    review_count: int
    generated_at: datetime
    positive_keywords: List[KeywordCount] = field(default_factory=list)
    negative_keywords: List[KeywordCount] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    improvement_suggestions: List[str] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self) -> None:
        _require_non_negative_integer(self.review_count, "review_count")
        _require_utc(self.generated_at, "generated_at")


@dataclass(slots=True)
class ReviewStatistics:
    total_reviews: int
    analyzed_reviews: int
    unanalyzed_reviews: int
    failed_reviews: int
    average_rating: Optional[float] = None
    sentiment_counts: Dict[Sentiment, int] = field(default_factory=dict)
    sentiment_ratios: Dict[Sentiment, float] = field(default_factory=dict)
    daily_sentiment_counts: Dict[date, Dict[Sentiment, int]] = field(
        default_factory=dict
    )
    rating_sentiment_matrix: Dict[int, Dict[Sentiment, int]] = field(
        default_factory=dict
    )
    top_positive_keywords: List[KeywordCount] = field(default_factory=list)
    top_negative_keywords: List[KeywordCount] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name in (
            "total_reviews",
            "analyzed_reviews",
            "unanalyzed_reviews",
            "failed_reviews",
        ):
            _require_non_negative_integer(getattr(self, field_name), field_name)
        if self.analyzed_reviews + self.unanalyzed_reviews > self.total_reviews:
            raise ValidationError("analyzed and unanalyzed counts exceed total_reviews")
        if self.average_rating is not None:
            if isinstance(self.average_rating, bool) or not isinstance(
                self.average_rating, (int, float)
            ):
                raise ValidationError("average_rating must be numeric")
            if not 1.0 <= float(self.average_rating) <= 5.0:
                raise ValidationError("average_rating must be between 1.0 and 5.0")


@dataclass(frozen=True, slots=True)
class ItemError:
    item_ref: Optional[str]
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValidationError("error code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValidationError("error message must be a non-empty string")


@dataclass(slots=True, kw_only=True)
class BatchOperationResult:
    processed: int
    succeeded: int
    skipped: int = 0
    failed: int = 0
    rejected: int = 0
    errors: List[ItemError] = field(default_factory=list)

    def __post_init__(self) -> None:
        for field_name in ("processed", "succeeded", "skipped", "failed", "rejected"):
            _require_non_negative_integer(getattr(self, field_name), field_name)
        completed = self.succeeded + self.skipped + self.failed + self.rejected
        if completed != self.processed:
            raise ValidationError("batch result counts must add up to processed")
        if len(self.errors) < self.failed + self.rejected:
            raise ValidationError("each failed or rejected item must have an ItemError")

    @property
    def is_partial_failure(self) -> bool:
        return self.failed > 0 or self.rejected > 0


@dataclass(slots=True, kw_only=True)
class CleanBatchResult(BatchOperationResult):
    reviews: List[CleanReview] = field(default_factory=list)

    def __post_init__(self) -> None:
        super(CleanBatchResult, self).__post_init__()
        if len(self.reviews) != self.succeeded:
            raise ValidationError("reviews count must match succeeded")


@dataclass(slots=True, kw_only=True)
class AnalysisBatchResult(BatchOperationResult):
    results: List[AnalysisResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        super(AnalysisBatchResult, self).__post_init__()
        if len(self.results) != self.succeeded:
            raise ValidationError("results count must match succeeded")


@dataclass(frozen=True, slots=True)
class OutputArtifact:
    kind: OutputKind
    path: Path
    format: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OutputKind):
            raise ValidationError("artifact kind must be an OutputKind value")
        if not isinstance(self.path, Path):
            raise ValidationError("artifact path must be a Path")
        if not self.path.is_absolute():
            raise ValidationError("artifact path must be absolute")
        if not isinstance(self.format, str) or not self.format.strip():
            raise ValidationError("artifact format must be non-empty")


@dataclass(slots=True)
class DashboardResult:
    artifacts: List[OutputArtifact]
    statistics: ReviewStatistics
    insight: Optional[InsightResult] = None


@dataclass(frozen=True, slots=True)
class ExportResult:
    artifact: OutputArtifact
    row_count: int

    def __post_init__(self) -> None:
        _require_non_negative_integer(self.row_count, "row_count")
        if self.artifact.kind is not OutputKind.EXPORT:
            raise ValidationError("export artifact kind must be EXPORT")


@dataclass(frozen=True, slots=True)
class CleaningOptions:
    policy: DuplicatePolicy
    min_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy, DuplicatePolicy):
            raise ValidationError("policy must be a DuplicatePolicy value")
        _require_positive_integer(self.min_length, "min_length")


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    provider: str
    model: str
    timeout_seconds: int
    max_retries: int
    api_key: Optional[str] = field(default=None, repr=False)
    prompt_version: Optional[str] = None
    base_url: Optional[str] = None
    reasoning_effort: Optional[str] = "minimal"

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValidationError("provider must be non-empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValidationError("model must be non-empty")
        _require_positive_integer(self.timeout_seconds, "timeout_seconds")
        _require_non_negative_integer(self.max_retries, "max_retries")
        if self.base_url is not None and (
            not isinstance(self.base_url, str) or not self.base_url.strip()
        ):
            raise ValidationError("base_url must be a non-empty string or None")
        if self.reasoning_effort not in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValidationError("unsupported reasoning_effort")


@dataclass(frozen=True, slots=True)
class ImportRequest:
    file: Path
    policy: DuplicatePolicy

    def __post_init__(self) -> None:
        if not isinstance(self.file, Path) or not self.file.is_absolute():
            raise ValidationError("import file must be an absolute Path")
        if not isinstance(self.policy, DuplicatePolicy):
            raise ValidationError("policy must be a DuplicatePolicy value")


@dataclass(frozen=True, slots=True)
class CleanRequest:
    options: CleaningOptions


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    target: AnalyzeTarget
    review_id: Optional[int] = None
    limit: Optional[int] = None
    force: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target, AnalyzeTarget):
            raise ValidationError("target must be an AnalyzeTarget value")
        if self.target is AnalyzeTarget.REVIEW_ID:
            if self.review_id is None:
                raise ValidationError("review_id is required for the review_id target")
            _require_positive_integer(self.review_id, "review_id")
        elif self.review_id is not None:
            raise ValidationError("review_id is only valid for the review_id target")
        if self.limit is not None:
            _require_positive_integer(self.limit, "limit")


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    filters: ReviewFilter
    limit: Optional[int] = None

    def __post_init__(self) -> None:
        if self.limit is not None:
            _require_positive_integer(self.limit, "limit")


@dataclass(frozen=True, slots=True)
class ListRequest:
    query: ReviewQuery


@dataclass(frozen=True, slots=True)
class ShowRequest:
    review_id: int

    def __post_init__(self) -> None:
        _require_positive_integer(self.review_id, "review_id")


@dataclass(frozen=True, slots=True)
class StatsRequest:
    filters: ReviewFilter


@dataclass(frozen=True, slots=True)
class DashboardRequest:
    filters: ReviewFilter
    output: Path
    report_format: ReportFormat
    force: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.output, Path) or not self.output.is_absolute():
            raise ValidationError("dashboard output must be an absolute Path")
        if not isinstance(self.report_format, ReportFormat):
            raise ValidationError("report_format must be a ReportFormat value")


@dataclass(frozen=True, slots=True)
class ExportRequest:
    filters: ReviewFilter
    output: Path
    format: ExportFormat
    force: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.output, Path) or not self.output.is_absolute():
            raise ValidationError("export output must be an absolute Path")
        if not isinstance(self.format, ExportFormat):
            raise ValidationError("format must be an ExportFormat value")


__all__ = [
    "AnalysisBatchResult",
    "AnalysisOptions",
    "AnalysisResult",
    "AnalyzeRequest",
    "AnalyzeTarget",
    "BatchOperationResult",
    "CleanBatchResult",
    "CleanRequest",
    "CleanReview",
    "CleaningOptions",
    "DashboardRequest",
    "DashboardResult",
    "DuplicatePolicy",
    "ExportFormat",
    "ExportRequest",
    "ExportResult",
    "ExtractRequest",
    "ImportRequest",
    "InsightResult",
    "ItemError",
    "KeywordCount",
    "ListRequest",
    "OutputArtifact",
    "OutputKind",
    "Page",
    "ProcessingStatus",
    "RawReview",
    "ReportFormat",
    "ReviewDetail",
    "ReviewFilter",
    "ReviewQuery",
    "ReviewStatistics",
    "Sentiment",
    "ShowRequest",
    "SortField",
    "SortOrder",
    "StatsRequest",
]
