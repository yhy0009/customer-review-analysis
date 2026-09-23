"""Select a stable set of analyzed reviews before asking the extractor for insights."""

from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from typing import Callable

from src.errors import StorageError
from src.models import ExtractRequest, InsightResult, ReviewDetail, ReviewQuery, SortField, SortOrder
from src.services import InsightExtractor
from src.storage import ReviewRepository


_PAGE_SIZE = 1000


class InsightService:
    """Inject SQLite's read_snapshot factory to isolate reads, releasing it before AI I/O."""

    def __init__(
        self, repository: ReviewRepository, extractor: InsightExtractor, *,
        snapshot: Callable[[], AbstractContextManager] = nullcontext,
        save_result: Callable[[list[ReviewDetail], InsightResult, int | None], object] | None = None,
    ) -> None:
        self.repository = repository
        self.extractor = extractor
        self._snapshot = snapshot
        self._save_result = save_result

    def extract_insights(self, request: ExtractRequest) -> InsightResult:
        filters = replace(request.filters)
        reviews = []
        expected_total = None
        seen_count = 0
        last_id = 0
        page_number = 1
        with self._snapshot():
            while True:
                page = self.repository.list_reviews(ReviewQuery(
                    filters=filters, page=page_number, size=_PAGE_SIZE,
                    sort=SortField.ID, order=SortOrder.ASC,
                ))
                if expected_total is None:
                    expected_total = page.total_items
                elif expected_total != page.total_items:
                    raise StorageError("추출 도중 리뷰 수가 변경됐습니다. 다시 실행하세요.")
                for detail in page.items:
                    if detail.review.id <= last_id:
                        raise StorageError("추출 대상 리뷰의 순서가 일관되지 않습니다.")
                    last_id = detail.review.id
                    seen_count += 1
                    if detail.analysis is not None:
                        reviews.append(detail)
                        if request.limit is not None and len(reviews) >= request.limit:
                            break
                if request.limit is not None and len(reviews) >= request.limit:
                    break
                if page_number >= page.total_pages:
                    if seen_count != expected_total:
                        raise StorageError("전체 리뷰를 조회하지 못해 추출을 취소했습니다.")
                    break
                page_number += 1
        result = self.extractor.extract_insights(reviews, filters, limit=request.limit)
        if self._save_result is not None:
            self._save_result(reviews, result, request.limit)
        return result
