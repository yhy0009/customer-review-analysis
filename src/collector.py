from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping

import pandas as pd

from src.models import RawReview

logger = logging.getLogger(__name__)


# 외부 파일의 컬럼명을 내부 표준 필드로 매핑하기 위한 별칭
COLUMN_ALIASES = {
    "source_review_id": [
        "source_review_id",
        "review_id",
        "id",
        "리뷰id",
        "리뷰_id",
    ],
    "product_name": [
        "product_name",
        "movie_title",
        "product",
        "product_title",
        "상품명",
        "영화명",
        "영화 제목",
    ],
    "review_date": [
        "review_date",
        "date",
        "created_at",
        "작성일",
        "작성일자",
        "날짜",
    ],
    "rating": [
        "rating",
        "score",
        "평점",
        "점수",
    ],
    "review_text": [
        "review_text",
        "review",
        "content",
        "comment",
        "text",
        "리뷰",
        "리뷰내용",
        "리뷰 내용",
        "코멘트",
        "내용",
    ],
}


SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


class InputFileError(Exception):
    """입력 파일을 읽거나 컬럼을 해석할 수 없을 때 발생하는 예외."""
    pass


def load_reviews(
    path: Path | str,
    column_overrides: Mapping[str, str] | None = None,
) -> list[RawReview]:
    """
    CSV 또는 Excel 파일을 읽어 RawReview 목록으로 변환한다.

    Collector의 책임:
    - 파일 읽기
    - 컬럼명 표준화
    - 외부 필드 → RawReview 필드 매핑
    - 원본의 추가 컬럼 보존

    Collector는 데이터의 유효성 검사를 수행하지 않는다.
    날짜/평점/리뷰 내용의 검증은 Cleaner의 책임이다.

    Args:
        path:
            CSV/XLSX/XLS 파일 경로
        column_overrides:
            표준 필드명을 실제 컬럼명으로 직접 지정하는 매핑.
            예:
            {
                "review_text": "코멘트",
                "product_name": "영화제목"
            }

    Returns:
        list[RawReview]

    Raises:
        FileNotFoundError:
            파일이 존재하지 않을 경우
        InputFileError:
            지원하지 않는 확장자 또는 파일을 읽을 수 없는 경우
    """

    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {file_path}")

    if not file_path.is_file():
        raise InputFileError(f"파일이 아닙니다: {file_path}")

    extension = file_path.suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise InputFileError(
            f"지원하지 않는 파일 형식입니다: {extension}"
        )

    try:
        dataframe = _read_file(file_path)
    except Exception as exc:
        raise InputFileError(
            f"입력 파일을 읽을 수 없습니다: {file_path}"
        ) from exc

    if dataframe.empty:
        raise InputFileError(
            f"입력 파일에 데이터가 없습니다: {file_path}"
        )

    # 컬럼명을 문자열로 통일
    dataframe.columns = [
        str(column).strip()
        for column in dataframe.columns
    ]

    column_map = _resolve_columns(
        dataframe.columns,
        column_overrides,
    )

    # 리뷰 본문 컬럼은 Collector 단계에서 반드시 찾아야 한다.
    if "review_text" not in column_map:
        raise InputFileError(
            "리뷰 본문 컬럼(review_text)을 찾을 수 없습니다."
        )

    return _build_raw_reviews(
        dataframe,
        column_map,
        file_path,
    )


def _read_file(path: Path) -> pd.DataFrame:
    """CSV 또는 Excel 파일을 DataFrame으로 읽는다."""

    extension = path.suffix.lower()

    if extension == ".csv":
        # 네이버 등 국내 CSV에서 자주 사용하는 인코딩을 우선 처리
        try:
            return pd.read_csv(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="cp949")

    if extension in {".xlsx", ".xls"}:
        return pd.read_excel(path)

    raise InputFileError(
        f"지원하지 않는 파일 형식입니다: {extension}"
    )


def _resolve_columns(
    columns: pd.Index,
    column_overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    실제 파일 컬럼명을 RawReview의 표준 필드명으로 매핑한다.

    반환 예:
    {
        "source_review_id": "review_id",
        "product_name": "movie_title",
        "review_date": "review_date",
        "rating": "rating",
        "review_text": "review_text",
    }
    """

    available = {
        str(column).strip(): str(column).strip()
        for column in columns
    }

    result: dict[str, str] = {}

    # 1. 사용자가 직접 지정한 매핑을 가장 우선한다.
    if column_overrides:
        for standard_name, actual_name in column_overrides.items():
            if standard_name not in COLUMN_ALIASES:
                logger.warning(
                    "알 수 없는 표준 필드가 지정되었습니다: %s",
                    standard_name,
                )
                continue

            actual_name = str(actual_name).strip()

            if actual_name in available:
                result[standard_name] = available[actual_name]
            else:
                raise InputFileError(
                    f"지정한 컬럼을 찾을 수 없습니다: "
                    f"{standard_name} -> {actual_name}"
                )

    # 2. 별칭을 이용해 나머지 컬럼을 자동으로 찾는다.
    normalized_available = {
        _normalize_column_name(column): column
        for column in available
    }

    for standard_name, aliases in COLUMN_ALIASES.items():
        if standard_name in result:
            continue

        for alias in aliases:
            normalized_alias = _normalize_column_name(alias)

            if normalized_alias in normalized_available:
                result[standard_name] = normalized_available[
                    normalized_alias
                ]
                break

    return result


def _normalize_column_name(value: str) -> str:
    """컬럼명 비교를 위한 최소한의 정규화."""

    return (
        str(value)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def _build_raw_reviews(
    dataframe: pd.DataFrame,
    column_map: Mapping[str, str],
    source_file: Path,
) -> list[RawReview]:
    """
    DataFrame의 각 행을 RawReview로 변환한다.

    표준 필드 이외의 컬럼은 raw_payload에 그대로 보존한다.
    """

    standard_columns = set(column_map.values())
    reviews: list[RawReview] = []

    for _, row in dataframe.iterrows():

        source_review_id = _raw_value(
            row.get(column_map.get("source_review_id"))
        )

        product_name = _raw_value(
            row.get(column_map.get("product_name"))
        )

        review_date = _raw_value(
            row.get(column_map.get("review_date"))
        )

        rating = _raw_value(
            row.get(column_map.get("rating"))
        )

        review_text = _raw_value(
            row.get(column_map.get("review_text"))
        )

        # 표준 필드 이외의 원본 컬럼을 모두 보존
        raw_payload: dict[str, object] = {}

        for column in dataframe.columns:
            if column in standard_columns:
                continue

            value = _raw_value(row[column])

            raw_payload[column] = value

        review = RawReview(
            source_review_id=source_review_id,
            product_name=product_name,
            review_date=review_date,
            rating=rating,
            review_text=review_text,
            source_file=str(source_file),
            raw_payload=raw_payload,
        )

        reviews.append(review)

    return reviews


def _raw_value(value: object) -> object | None:
    """
    pandas의 NaN/NaT 등을 None으로 변환한다.

    그 외 값은 Collector 단계에서 임의로 변환하지 않는다.
    날짜/평점/텍스트의 실제 검증 및 정규화는 Cleaner가 담당한다.
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value