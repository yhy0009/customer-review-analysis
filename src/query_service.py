"""Read-only CLI use cases; the caller owns the repository connection."""

from typing import Optional

from src.models import (
    ListRequest,
    Page,
    ReviewDetail,
    ReviewStatistics,
    ShowRequest,
    StatsRequest,
)
from src.storage import ReviewRepository


class QueryService:
    """Pass query requests to the shared repository without redoing aggregates."""

    def __init__(self, repository: ReviewRepository) -> None:
        self.repository = repository

    def list_reviews(self, request: ListRequest) -> Page[ReviewDetail]:
        return self.repository.list_reviews(request.query)

    def show_review(self, request: ShowRequest) -> Optional[ReviewDetail]:
        return self.repository.get_review(request.review_id)

    def get_statistics(self, request: StatsRequest) -> ReviewStatistics:
        return self.repository.get_statistics(request.filters)
