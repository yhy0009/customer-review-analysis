from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Sequence

from src.models import (
    CleanBatchResult,
    CleanReview,
    CleaningOptions,
    ItemError,
    RawReview,
)

logger = logging.getLogger(__name__)


def clean_reviews(
    reviews: Sequence[RawReview],
    options: CleaningOptions,
) -> CleanBatchResult:
    """
    RawReview 목록을 정제하여 CleanReview 목록으로 변환한다.

    Cleaner의 책임:
    - 문자열 정규화
    - 필수값 검증
    - 날짜 정규화
    - 평점 정규화 및 범위 검증
    - 리뷰 최소 길이 검증

    Cleaner가 담당하지 않는 것:
    - 중복 탐지
    - skip / upsert
    - DB 저장
    - 트랜잭션

    저장 관련 정책은 storage.py가 담당한다.
    """

    processed = len(reviews)

    cleaned_reviews: list[CleanReview] = []
    errors: list[ItemError] = []

    rejected = 0
    failed = 0

    for index, raw_review in enumerate(reviews, start=1):
        try:
            clean_review = _clean_one(
                raw_review,
                index=index,
                min_length=options.min_length,
            )

            cleaned_reviews.append(clean_review)

        except _RejectedReview as exc:
            rejected += 1

            errors.append(
                ItemError(
                    item_ref=_item_ref(raw_review, index),
                    code=exc.code,
                    message=exc.message,
                    retryable=False,
                )
            )

            logger.warning(
                "Review rejected: item_ref=%s code=%s",
                _item_ref(raw_review, index),
                exc.code,
            )

        except Exception as exc:
            failed += 1

            errors.append(
                ItemError(
                    item_ref=_item_ref(raw_review, index),
                    code="CLEANING_ERROR",
                    message=str(exc),
                    retryable=False,
                )
            )

            logger.error(
                "Review cleaning failed: item_ref=%s",
                _item_ref(raw_review, index),
            )

    return CleanBatchResult(
        processed=processed,
        succeeded=len(cleaned_reviews),
        skipped=0,
        failed=failed,
        rejected=rejected,
        errors=errors,
        reviews=cleaned_reviews,
    )


def _clean_one(
    raw_review: RawReview,
    *,
    index: int,
    min_length: int,
) -> CleanReview:
    """RawReview 한 건을 CleanReview로 변환한다."""

    # 1. product_name
    product_name = _normalize_text(raw_review.product_name)

    if not product_name:
        raise _RejectedReview(
            "MISSING_PRODUCT_NAME",
            "product_name is required",
        )

    # 2. review_text
    review_text = _normalize_text(raw_review.review_text)

    if not review_text:
        raise _RejectedReview(
            "MISSING_REVIEW_TEXT",
            "review_text is required",
        )

    if len(review_text) < min_length:
        raise _RejectedReview(
            "REVIEW_TOO_SHORT",
            f"review_text must be at least {min_length} characters",
        )

    # 3. review_date
    review_date = _normalize_date(raw_review.review_date)

    if review_date is None:
        raise _RejectedReview(
            "INVALID_REVIEW_DATE",
            "review_date is missing or invalid",
        )

    # 4. rating
    rating = _normalize_rating(raw_review.rating)

    if rating is None:
        raise _RejectedReview(
            "INVALID_RATING",
            "rating must be an integer between 1 and 5",
        )

    # 5. source_review_id
    source_review_id = _normalize_source_id(
        raw_review.source_review_id
    )

    # 6. CleanReview 생성
    cleaned_at = datetime.now(timezone.utc)

    return CleanReview(
        id=index,
        source_review_id=source_review_id,
        product_name=product_name,
        review_date=review_date,
        rating=rating,
        review_text=review_text,
        cleaned_at=cleaned_at,
    )


def _normalize_text(value: object | None) -> str | None:
    """
    문자열을 정규화한다.

    - None → None
    - 문자열 변환
    - 앞뒤 공백 제거
    - 연속된 whitespace를 하나의 공백으로 변경
    """

    if value is None:
        return None

    text = str(value)

    # 줄바꿈, 탭 등의 연속 공백을 하나로 통합
    text = re.sub(r"\s+", " ", text)

    text = text.strip()

    return text if text else None


def _normalize_source_id(value: object | None) -> str | None:
    """원본 리뷰 ID를 문자열로 정규화한다."""

    if value is None:
        return None

    value_string = str(value).strip()

    if not value_string:
        return None

    return value_string


def _normalize_date(value: object | None) -> date | None:
    """
    RawReview의 날짜 값을 datetime.date로 변환한다.

    지원 형식:
    - datetime
    - date
    - YYYY-MM-DD
    - YYYY/MM/DD
    - YYYY.MM.DD
    - YYYY-MM-DD HH:MM:SS
    - YYYY/MM/DD HH:MM:SS
    - YYYY.MM.DD HH:MM:SS
    """

    if value is None:
        return None

    # datetime은 date보다 먼저 처리한다.
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    if not text:
        return None

    # ISO 날짜 / 날짜시간
    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
        return parsed.date()
    except ValueError:
        pass

    # 일반적인 날짜 형식
    date_formats = (
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    )

    for fmt in date_formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def _normalize_rating(value: object | None) -> int | None:
    """
    RawReview의 평점을 정수 1~5로 정규화한다.

    지원 예:
    - 1
    - "1"
    - 5.0
    - "5.0"

    범위를 벗어나거나 정수로 변환할 수 없는 값은
    None으로 반환한다.
    """

    if value is None:
        return None

    # bool은 int의 subclass이므로 제외한다.
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        rating = value

    elif isinstance(value, float):
        if not value.is_integer():
            return None

        rating = int(value)

    else:
        text = str(value).strip()

        if not text:
            return None

        try:
            numeric_value = float(text)
        except ValueError:
            return None

        if not numeric_value.is_integer():
            return None

        rating = int(numeric_value)

    if not 1 <= rating <= 5:
        return None

    return rating


def _item_ref(
    raw_review: RawReview,
    index: int,
) -> str:
    """
    오류 발생 시 사용할 리뷰 식별자를 만든다.

    source_review_id가 있으면 그것을 우선 사용하고,
    없으면 입력 행 번호를 사용한다.
    """

    source_id = _normalize_source_id(
        raw_review.source_review_id
    )

    if source_id:
        return source_id

    return f"row:{index}"


class _RejectedReview(Exception):
    """정제 규칙에 의해 제외된 리뷰."""

    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
