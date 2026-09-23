"""Protocols for feature modules and CLI-facing application use cases."""

from __future__ import annotations

from pathlib import Path
from typing import List, Mapping, Optional, Protocol, Sequence, runtime_checkable

from src.models import (
    AnalysisBatchResult,
    AnalysisOptions,
    AnalysisResult,
    AnalyzeRequest,
    BatchOperationResult,
    CleanBatchResult,
    CleanRequest,
    CleanReview,
    CleaningOptions,
    DashboardRequest,
    DashboardResult,
    ExportFormat,
    ExportRequest,
    ExportResult,
    ExtractRequest,
    ImportRequest,
    InsightResult,
    ListRequest,
    OutputArtifact,
    RawReview,
    ReportFormat,
    ReviewDetail,
    ReviewFilter,
    ReviewStatistics,
    ShowRequest,
    StatsRequest,
    Page,
)


@runtime_checkable
class ReviewCollector(Protocol):
    def load_reviews(
        self,
        path: Path | str,
        column_overrides: Optional[Mapping[str, str]] = None,
    ) -> List[RawReview]:
        ...


@runtime_checkable
class ReviewCleaner(Protocol):
    def clean_reviews(
        self,
        reviews: Sequence[RawReview],
        options: CleaningOptions,
    ) -> CleanBatchResult:
        ...


@runtime_checkable
class ReviewAnalyzer(Protocol):
    def analyze_review(
        self,
        review: CleanReview,
        options: AnalysisOptions,
    ) -> AnalysisResult:
        ...

    def analyze_reviews(
        self,
        reviews: Sequence[CleanReview],
        options: AnalysisOptions,
        *,
        force: bool = False,
    ) -> AnalysisBatchResult:
        ...


@runtime_checkable
class InsightExtractor(Protocol):
    def extract_insights(
        self,
        reviews: Sequence[ReviewDetail],
        filters: ReviewFilter,
        *,
        limit: Optional[int] = None,
    ) -> InsightResult:
        ...


@runtime_checkable
class ReviewVisualizer(Protocol):
    def generate_dashboard(
        self,
        statistics: ReviewStatistics,
        output: Path,
        *,
        font_family: str,
        dpi: int,
        force: bool = False,
    ) -> List[OutputArtifact]:
        ...


@runtime_checkable
class ReportGenerator(Protocol):
    def generate_report(
        self,
        statistics: ReviewStatistics,
        insight: Optional[InsightResult],
        output: Path,
        *,
        report_format: ReportFormat,
        force: bool = False,
    ) -> OutputArtifact:
        ...


@runtime_checkable
class ReviewExporter(Protocol):
    def export_reviews(
        self,
        reviews: Sequence[ReviewDetail],
        output: Path,
        *,
        export_format: ExportFormat,
        force: bool = False,
    ) -> ExportResult:
        ...


@runtime_checkable
class ApplicationServices(Protocol):
    """Use-case boundary consumed by the command-line adapter."""

    def import_reviews(self, request: ImportRequest) -> BatchOperationResult:
        ...

    def clean_reviews(self, request: CleanRequest) -> CleanBatchResult:
        ...

    def analyze_reviews(self, request: AnalyzeRequest) -> AnalysisBatchResult:
        ...

    def extract_insights(self, request: ExtractRequest) -> InsightResult:
        ...

    def list_reviews(self, request: ListRequest) -> Page[ReviewDetail]:
        ...

    def show_review(self, request: ShowRequest) -> Optional[ReviewDetail]:
        ...

    def get_statistics(self, request: StatsRequest) -> ReviewStatistics:
        ...

    def create_dashboard(self, request: DashboardRequest) -> DashboardResult:
        ...

    def export_reviews(self, request: ExportRequest) -> ExportResult:
        ...


__all__ = [
    "ApplicationServices",
    "InsightExtractor",
    "ReportGenerator",
    "ReviewAnalyzer",
    "ReviewCleaner",
    "ReviewCollector",
    "ReviewExporter",
    "ReviewVisualizer",
]
