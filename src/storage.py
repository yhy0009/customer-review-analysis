"""Persistence boundary shared by SQLite and JSONL implementations."""

from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, runtime_checkable

from src.models import (
    AnalysisResult,
    BatchOperationResult,
    CleanReview,
    DuplicatePolicy,
    Page,
    ProcessingStatus,
    RawReview,
    ReviewDetail,
    ReviewFilter,
    ReviewQuery,
    ReviewStatistics,
)


@runtime_checkable
class ReviewRepository(Protocol):
    """Backend-neutral persistence contract for the application."""

    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        ...

    def fetch_raw_reviews(
        self,
        *,
        status: Optional[ProcessingStatus] = None,
    ) -> List[RawReview]:
        ...

    def save_clean_reviews(
        self,
        reviews: Sequence[CleanReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        ...

    def fetch_clean_reviews(
        self,
        filters: Optional[ReviewFilter] = None,
        *,
        limit: Optional[int] = None,
    ) -> List[CleanReview]:
        ...

    def fetch_unanalyzed_reviews(
        self,
        *,
        limit: Optional[int] = None,
    ) -> List[CleanReview]:
        ...

    def save_analysis(self, result: AnalysisResult) -> None:
        ...

    def mark_analysis_failed(self, review_id: int, error_message: str) -> None:
        ...

    def get_review(self, review_id: int) -> Optional[ReviewDetail]:
        ...

    def list_reviews(self, query: ReviewQuery) -> Page[ReviewDetail]:
        ...

    def get_statistics(
        self,
        filters: Optional[ReviewFilter] = None,
    ) -> ReviewStatistics:
        ...


__all__ = ["ReviewRepository"]
