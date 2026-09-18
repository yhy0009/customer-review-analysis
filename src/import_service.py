"""Load original reviews and persist them; the caller owns the connection."""

from src.models import BatchOperationResult, ImportRequest
from src.services import ReviewCollector
from src.storage import ReviewRepository


class ImportService:
    """Keep file parsing in the collector and duplicate handling in storage."""

    def __init__(self, repository: ReviewRepository, collector: ReviewCollector) -> None:
        self.repository = repository
        self.collector = collector

    def import_reviews(self, request: ImportRequest) -> BatchOperationResult:
        reviews = self.collector.load_reviews(request.file)
        return self.repository.save_raw_reviews(reviews, request.policy)
