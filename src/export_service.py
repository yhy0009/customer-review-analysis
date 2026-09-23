"""Select all matching review details and hand them to an injected file exporter."""

from src.errors import StorageError
from src.models import ExportRequest, ExportResult, ReviewQuery, SortField, SortOrder
from src.services import ReviewExporter
from src.storage import ReviewRepository


_PAGE_SIZE = 1000


class ExportService:
    """The caller owns the connection and read snapshot for multi-page exports."""

    def __init__(self, repository: ReviewRepository, exporter: ReviewExporter) -> None:
        self.repository = repository
        self.exporter = exporter

    def export_reviews(self, request: ExportRequest) -> ExportResult:
        reviews = []
        page_number = 1
        expected_total = None
        while True:
            page = self.repository.list_reviews(ReviewQuery(
                filters=request.filters, page=page_number, size=_PAGE_SIZE,
                sort=SortField.ID, order=SortOrder.ASC,
            ))
            if expected_total is None:
                expected_total = page.total_items
            elif page.total_items != expected_total:
                raise StorageError("내보내기 도중 리뷰 수가 변경됐습니다. 다시 실행하세요.")
            reviews.extend(page.items)
            if page_number >= page.total_pages:
                break
            page_number += 1
        if len(reviews) != expected_total:
            raise StorageError("전체 리뷰를 조회하지 못해 내보내기를 취소했습니다.")
        return self.exporter.export_reviews(
            reviews, request.output, export_format=request.format, force=request.force,
        )
