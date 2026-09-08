"""SQLite implementation of the :class:`~src.storage.ReviewRepository` contract.

경계 명세(docs/INTERFACE_BOUNDARY_SPEC.md 8절)의 기준 백엔드 구현이다.

핵심 설계:
- Raw / Clean / Analysis를 각각 raw_reviews, clean_reviews, analysis_results
  테이블로 분리한다.
- 중복 판정 키(dedupe_key)는 raw와 clean 양쪽에서 동일하게 계산되며, 두 영역을
  잇는 연결 키로도 사용한다.
- CleanReview.id는 저장소가 소유한다. save_clean_reviews는 입력 객체의 id를
  무시하고 DB가 부여한 AUTOINCREMENT id를 사용한다.
- 데이터 한 건의 문제는 행 단위 savepoint로 격리하고 성공 건은 커밋한다.
  연결/스키마 같은 인프라 문제는 배치 전체를 롤백하고 StorageError를 낸다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from src.config import get_logger
from src.errors import StorageError
from src.models import (
    AnalysisResult,
    BatchOperationResult,
    CleanReview,
    DuplicatePolicy,
    ItemError,
    KeywordCount,
    Page,
    ProcessingStatus,
    RawReview,
    ReviewDetail,
    ReviewFilter,
    ReviewQuery,
    ReviewStatistics,
    Sentiment,
    SortField,
    SortOrder,
)

logger = get_logger("storage.sqlite")


# 상위 통계에서 노출하는 키워드 상위 개수
_TOP_KEYWORD_LIMIT = 10

# AI 입력에 영향을 주는 Clean 필드. 이 값이 바뀌면 기존 분석 결과를 폐기한다.
_AI_RELEVANT_FIELDS = ("product_name", "review_date", "rating", "review_text")

_WHITESPACE = re.compile(r"\s+")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S",
)

_SORT_COLUMNS = {
    SortField.ID: "c.id",
    SortField.DATE: "c.review_date",
    SortField.RATING: "c.rating",
    SortField.SENTIMENT: "a.sentiment",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key        TEXT    NOT NULL UNIQUE,
    source_review_id  TEXT,
    product_name      TEXT,
    review_date       TEXT,
    rating            TEXT,
    review_text       TEXT,
    source_file       TEXT,
    raw_payload       TEXT    NOT NULL DEFAULT '{}',
    status            TEXT    NOT NULL DEFAULT 'RAW',
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS clean_reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id            INTEGER,
    dedupe_key        TEXT    NOT NULL UNIQUE,
    source_review_id  TEXT,
    product_name      TEXT    NOT NULL,
    review_date       TEXT    NOT NULL,
    rating            INTEGER NOT NULL,
    review_text       TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'CLEANED',
    cleaned_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    FOREIGN KEY (raw_id) REFERENCES raw_reviews (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id      INTEGER NOT NULL UNIQUE,
    sentiment      TEXT    NOT NULL,
    confidence     REAL    NOT NULL,
    summary        TEXT,
    keywords       TEXT    NOT NULL DEFAULT '[]',
    analyzed_at    TEXT    NOT NULL,
    provider       TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    prompt_version TEXT,
    FOREIGN KEY (review_id) REFERENCES clean_reviews (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_reviews (status);
CREATE INDEX IF NOT EXISTS idx_clean_status ON clean_reviews (status);
CREATE INDEX IF NOT EXISTS idx_clean_review_date ON clean_reviews (review_date);
CREATE INDEX IF NOT EXISTS idx_clean_product_name ON clean_reviews (product_name);
"""


# ---------------------------------------------------------------------------
# 직렬화 헬퍼
# ---------------------------------------------------------------------------


def _utc_iso(value: datetime) -> str:
    """timezone-aware datetime을 Z가 포함된 UTC ISO 8601 문자열로 만든다."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(text: str) -> datetime:
    """저장된 UTC ISO 문자열을 timezone-aware datetime으로 되돌린다."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_date(value: date) -> str:
    return value.isoformat()


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


# ---------------------------------------------------------------------------
# dedupe_key 계산 (명세 8.1)
#   1) source_review_id가 있으면 정규화한 값을 사용한다.
#   2) 없으면 정규화한 product_name/review_date/rating/review_text의 SHA-256.
# raw와 clean이 같은 리뷰에 대해 동일한 키를 만들도록 정규화를 공유한다.
# ---------------------------------------------------------------------------


def _norm_text(value: object | None) -> str:
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", str(value)).strip()


def _norm_source_id(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_date_str(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _norm_rating_str(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def _dedupe_key(
    source_review_id: object | None,
    product_name: object | None,
    review_date: object | None,
    rating: object | None,
    review_text: object | None,
) -> str:
    source_id = _norm_source_id(source_review_id)
    if source_id:
        return f"sid:{source_id}"

    payload = "\x1f".join(
        (
            _norm_text(product_name),
            _norm_date_str(review_date),
            _norm_rating_str(rating),
            _norm_text(review_text),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# 필터 → SQL WHERE 변환
# ---------------------------------------------------------------------------


def _build_where(filters: Optional[ReviewFilter]) -> tuple[str, list[Any]]:
    """ReviewFilter를 WHERE 절과 파라미터로 변환한다 (별칭 c=clean, a=analysis)."""
    if filters is None:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    if filters.sentiment is not None:
        clauses.append("a.sentiment = ?")
        params.append(filters.sentiment.value)
    if filters.date_from is not None:
        clauses.append("c.review_date >= ?")
        params.append(filters.date_from.isoformat())
    if filters.date_to is not None:
        clauses.append("c.review_date <= ?")
        params.append(filters.date_to.isoformat())
    if filters.product_name is not None:
        # 대소문자를 구분하지 않는 부분 일치
        clauses.append("LOWER(c.product_name) LIKE ?")
        params.append(f"%{filters.product_name.lower()}%")
    if filters.rating is not None:
        clauses.append("c.rating = ?")
        params.append(filters.rating)
    if filters.rating_min is not None:
        clauses.append("c.rating >= ?")
        params.append(filters.rating_min)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SqliteReviewRepository:
    """SQLite 기반 ReviewRepository 구현."""

    def __init__(self, database_path: Path | str) -> None:
        self._path = Path(database_path)
        try:
            if str(self._path) != ":memory:":
                self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
        except (sqlite3.Error, OSError) as exc:
            raise StorageError(f"저장소에 연결할 수 없습니다: {self._path}") from exc

        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "SqliteReviewRepository":
        """설정의 storage 섹션에서 database_path를 읽어 저장소를 만든다."""
        storage = config.get("storage")
        if not isinstance(storage, Mapping):
            raise StorageError("설정 섹션 'storage'가 올바르지 않습니다.")
        database_path = storage.get("database_path")
        if not isinstance(database_path, str) or not database_path.strip():
            raise StorageError("설정 항목 'storage.database_path'가 필요합니다.")
        return cls(database_path)

    # -- lifecycle ---------------------------------------------------------

    def _initialize_schema(self) -> None:
        try:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StorageError("저장소 스키마를 초기화할 수 없습니다.") from exc

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteReviewRepository":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- raw ---------------------------------------------------------------

    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        processed = len(reviews)
        succeeded = skipped = failed = 0
        errors: list[ItemError] = []
        now = _utc_iso(datetime.now(timezone.utc))

        for index, raw in enumerate(reviews, start=1):
            key = _dedupe_key(
                raw.source_review_id,
                raw.product_name,
                raw.review_date,
                raw.rating,
                raw.review_text,
            )
            savepoint = f"raw_{index}"
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                existing = self._conn.execute(
                    "SELECT id FROM raw_reviews WHERE dedupe_key = ?", (key,)
                ).fetchone()

                if existing is None:
                    self._insert_raw(raw, key, now)
                    succeeded += 1
                elif policy is DuplicatePolicy.SKIP:
                    skipped += 1
                else:
                    self._upsert_raw(existing["id"], raw, key, now)
                    succeeded += 1

                self._conn.execute(f"RELEASE {savepoint}")
            except sqlite3.IntegrityError as exc:
                # 한 건의 데이터 문제는 격리하고 계속 진행한다.
                self._conn.execute(f"ROLLBACK TO {savepoint}")
                self._conn.execute(f"RELEASE {savepoint}")
                failed += 1
                errors.append(
                    ItemError(
                        item_ref=_norm_source_id(raw.source_review_id) or f"row:{index}",
                        code="RAW_SAVE_ERROR",
                        message=str(exc),
                        retryable=False,
                    )
                )
                logger.warning("Raw review 저장 실패: row=%s", index)
            except sqlite3.Error as exc:
                # 인프라 오류는 배치 전체를 롤백한다.
                self._conn.rollback()
                raise StorageError("Raw 리뷰 저장 중 저장소 오류가 발생했습니다.") from exc

        self._conn.commit()
        logger.info(
            "save_raw_reviews 완료: processed=%d succeeded=%d skipped=%d failed=%d",
            processed,
            succeeded,
            skipped,
            failed,
        )
        return BatchOperationResult(
            processed=processed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            rejected=0,
            errors=errors,
        )

    def _insert_raw(self, raw: RawReview, key: str, now: str) -> None:
        self._conn.execute(
            """
            INSERT INTO raw_reviews (
                dedupe_key, source_review_id, product_name, review_date,
                rating, review_text, source_file, raw_payload, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RAW', ?, ?)
            """,
            (
                key,
                _stored_scalar(raw.source_review_id),
                _stored_scalar(raw.product_name),
                _stored_scalar(raw.review_date),
                _stored_scalar(raw.rating),
                _stored_scalar(raw.review_text),
                raw.source_file,
                json.dumps(raw.raw_payload, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )

    def _upsert_raw(self, raw_id: int, raw: RawReview, key: str, now: str) -> None:
        # Raw upsert: 연결된 Clean/Analysis를 지우고 다시 정제 대상(RAW)으로 만든다.
        self._conn.execute(
            "DELETE FROM clean_reviews WHERE dedupe_key = ?", (key,)
        )
        self._conn.execute(
            """
            UPDATE raw_reviews SET
                source_review_id = ?, product_name = ?, review_date = ?,
                rating = ?, review_text = ?, source_file = ?, raw_payload = ?,
                status = 'RAW', updated_at = ?
            WHERE id = ?
            """,
            (
                _stored_scalar(raw.source_review_id),
                _stored_scalar(raw.product_name),
                _stored_scalar(raw.review_date),
                _stored_scalar(raw.rating),
                _stored_scalar(raw.review_text),
                raw.source_file,
                json.dumps(raw.raw_payload, ensure_ascii=False, default=str),
                now,
                raw_id,
            ),
        )

    def fetch_raw_reviews(
        self,
        *,
        status: Optional[str] = None,
    ) -> list[RawReview]:
        query = "SELECT * FROM raw_reviews"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(_status_value(status))
        query += " ORDER BY id ASC"

        try:
            rows = self._conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("Raw 리뷰를 조회할 수 없습니다.") from exc
        return [_row_to_raw(row) for row in rows]

    # -- clean -------------------------------------------------------------

    def save_clean_reviews(
        self,
        reviews: Sequence[CleanReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        processed = len(reviews)
        succeeded = skipped = failed = 0
        errors: list[ItemError] = []
        now = _utc_iso(datetime.now(timezone.utc))

        for index, review in enumerate(reviews, start=1):
            key = _dedupe_key(
                review.source_review_id,
                review.product_name,
                review.review_date,
                review.rating,
                review.review_text,
            )
            savepoint = f"clean_{index}"
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                existing = self._conn.execute(
                    "SELECT * FROM clean_reviews WHERE dedupe_key = ?", (key,)
                ).fetchone()

                if existing is None:
                    self._insert_clean(review, key, now)
                    succeeded += 1
                elif policy is DuplicatePolicy.SKIP:
                    skipped += 1
                else:
                    self._upsert_clean(existing, review, now)
                    succeeded += 1

                self._conn.execute(f"RELEASE {savepoint}")
            except sqlite3.IntegrityError as exc:
                self._conn.execute(f"ROLLBACK TO {savepoint}")
                self._conn.execute(f"RELEASE {savepoint}")
                failed += 1
                errors.append(
                    ItemError(
                        item_ref=review.source_review_id or f"row:{index}",
                        code="CLEAN_SAVE_ERROR",
                        message=str(exc),
                        retryable=False,
                    )
                )
                logger.warning("Clean review 저장 실패: row=%s", index)
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StorageError("Clean 리뷰 저장 중 저장소 오류가 발생했습니다.") from exc

        self._conn.commit()
        logger.info(
            "save_clean_reviews 완료: processed=%d succeeded=%d skipped=%d failed=%d",
            processed,
            succeeded,
            skipped,
            failed,
        )
        return BatchOperationResult(
            processed=processed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            rejected=0,
            errors=errors,
        )

    def _insert_clean(self, review: CleanReview, key: str, now: str) -> None:
        # 같은 dedupe_key의 raw 행과 연결하고 그 상태를 CLEANED로 올린다.
        raw_row = self._conn.execute(
            "SELECT id FROM raw_reviews WHERE dedupe_key = ?", (key,)
        ).fetchone()
        raw_id = raw_row["id"] if raw_row is not None else None

        # 입력 객체의 id는 무시하고 DB가 새 id를 부여한다(A안).
        self._conn.execute(
            """
            INSERT INTO clean_reviews (
                raw_id, dedupe_key, source_review_id, product_name,
                review_date, rating, review_text, status, cleaned_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'CLEANED', ?, ?)
            """,
            (
                raw_id,
                key,
                review.source_review_id,
                review.product_name,
                _iso_date(review.review_date),
                review.rating,
                review.review_text,
                _utc_iso(review.cleaned_at),
                now,
            ),
        )
        if raw_id is not None:
            self._conn.execute(
                "UPDATE raw_reviews SET status = 'CLEANED', updated_at = ? WHERE id = ?",
                (now, raw_id),
            )

    def _upsert_clean(
        self,
        existing: sqlite3.Row,
        review: CleanReview,
        now: str,
    ) -> None:
        review_id = existing["id"]
        new_values = {
            "product_name": review.product_name,
            "review_date": _iso_date(review.review_date),
            "rating": review.rating,
            "review_text": review.review_text,
        }
        ai_input_changed = any(
            existing[field] != new_values[field] for field in _AI_RELEVANT_FIELDS
        )

        self._conn.execute(
            """
            UPDATE clean_reviews SET
                source_review_id = ?, product_name = ?, review_date = ?,
                rating = ?, review_text = ?, cleaned_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                review.source_review_id,
                new_values["product_name"],
                new_values["review_date"],
                new_values["rating"],
                new_values["review_text"],
                _utc_iso(review.cleaned_at),
                now,
                review_id,
            ),
        )

        if ai_input_changed:
            # AI 입력이 바뀌면 기존 분석 결과를 폐기하고 CLEANED로 되돌린다.
            self._conn.execute(
                "DELETE FROM analysis_results WHERE review_id = ?", (review_id,)
            )
            self._conn.execute(
                "UPDATE clean_reviews SET status = 'CLEANED' WHERE id = ?",
                (review_id,),
            )

    def fetch_clean_reviews(
        self,
        filters: Optional[ReviewFilter] = None,
        *,
        limit: Optional[int] = None,
    ) -> list[CleanReview]:
        where, params = _build_where(filters)
        query = (
            "SELECT c.* FROM clean_reviews c "
            "LEFT JOIN analysis_results a ON a.review_id = c.id"
            f"{where} ORDER BY c.id ASC"
        )
        if limit is not None:
            query += " LIMIT ?"
            params = [*params, limit]

        try:
            rows = self._conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("Clean 리뷰를 조회할 수 없습니다.") from exc
        return [_row_to_clean(row) for row in rows]

    def fetch_unanalyzed_reviews(
        self,
        *,
        limit: Optional[int] = None,
    ) -> list[CleanReview]:
        # 분석 결과가 없는 리뷰(신규 CLEANED + 재시도 대상 ANALYSIS_FAILED).
        query = (
            "SELECT c.* FROM clean_reviews c "
            "LEFT JOIN analysis_results a ON a.review_id = c.id "
            "WHERE a.review_id IS NULL ORDER BY c.id ASC"
        )
        params: list[Any] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        try:
            rows = self._conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("미분석 리뷰를 조회할 수 없습니다.") from exc
        return [_row_to_clean(row) for row in rows]

    # -- analysis ----------------------------------------------------------

    def save_analysis(self, result: AnalysisResult) -> None:
        try:
            self._conn.execute(
                """
                INSERT INTO analysis_results (
                    review_id, sentiment, confidence, summary, keywords,
                    analyzed_at, provider, model, prompt_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(review_id) DO UPDATE SET
                    sentiment = excluded.sentiment,
                    confidence = excluded.confidence,
                    summary = excluded.summary,
                    keywords = excluded.keywords,
                    analyzed_at = excluded.analyzed_at,
                    provider = excluded.provider,
                    model = excluded.model,
                    prompt_version = excluded.prompt_version
                """,
                (
                    result.review_id,
                    result.sentiment.value,
                    float(result.confidence),
                    result.summary,
                    json.dumps(result.keywords, ensure_ascii=False),
                    _utc_iso(result.analyzed_at),
                    result.provider,
                    result.model,
                    result.prompt_version,
                ),
            )
            updated = self._conn.execute(
                "UPDATE clean_reviews SET status = 'ANALYZED' WHERE id = ?",
                (result.review_id,),
            )
            if updated.rowcount == 0:
                # 대상 Clean 리뷰가 없으면 분석 결과를 남기지 않는다.
                self._conn.rollback()
                raise StorageError(
                    f"분석 대상 리뷰를 찾을 수 없습니다: review_id={result.review_id}"
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise StorageError("분석 결과를 저장할 수 없습니다.") from exc

    def mark_analysis_failed(self, review_id: int, error_message: str) -> None:
        try:
            updated = self._conn.execute(
                "UPDATE clean_reviews SET status = 'ANALYSIS_FAILED' WHERE id = ?",
                (review_id,),
            )
            if updated.rowcount == 0:
                self._conn.rollback()
                raise StorageError(
                    f"분석 대상 리뷰를 찾을 수 없습니다: review_id={review_id}"
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise StorageError("분석 실패 상태를 저장할 수 없습니다.") from exc
        logger.warning("Analysis failed: review_id=%s", review_id)

    # -- read models -------------------------------------------------------

    def get_review(self, review_id: int) -> Optional[ReviewDetail]:
        try:
            row = self._conn.execute(
                """
                SELECT c.*, a.review_id AS a_review_id, a.sentiment, a.confidence,
                       a.summary, a.keywords, a.analyzed_at, a.provider, a.model,
                       a.prompt_version
                FROM clean_reviews c
                LEFT JOIN analysis_results a ON a.review_id = c.id
                WHERE c.id = ?
                """,
                (review_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError("리뷰를 조회할 수 없습니다.") from exc

        if row is None:
            return None
        return _row_to_detail(row)

    def list_reviews(self, query: ReviewQuery) -> Page[ReviewDetail]:
        where, params = _build_where(query.filters)
        base = (
            "FROM clean_reviews c "
            "LEFT JOIN analysis_results a ON a.review_id = c.id"
            f"{where}"
        )

        try:
            total = self._conn.execute(
                f"SELECT COUNT(*) AS n {base}", params
            ).fetchone()["n"]

            order_column = _SORT_COLUMNS[query.sort]
            direction = "ASC" if query.order is SortOrder.ASC else "DESC"
            offset = (query.page - 1) * query.size

            rows = self._conn.execute(
                f"""
                SELECT c.*, a.review_id AS a_review_id, a.sentiment, a.confidence,
                       a.summary, a.keywords, a.analyzed_at, a.provider, a.model,
                       a.prompt_version
                {base}
                ORDER BY {order_column} {direction}, c.id {direction}
                LIMIT ? OFFSET ?
                """,
                [*params, query.size, offset],
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("리뷰 목록을 조회할 수 없습니다.") from exc

        items = [_row_to_detail(row) for row in rows]
        total_pages = (total + query.size - 1) // query.size if total else 0
        return Page(
            items=items,
            page=query.page,
            size=query.size,
            total_items=total,
            total_pages=total_pages,
        )

    def get_statistics(
        self,
        filters: Optional[ReviewFilter] = None,
    ) -> ReviewStatistics:
        where, params = _build_where(filters)
        try:
            rows = self._conn.execute(
                f"""
                SELECT c.rating, c.review_date, c.status,
                       a.sentiment, a.keywords
                FROM clean_reviews c
                LEFT JOIN analysis_results a ON a.review_id = c.id
                {where}
                """,
                params,
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("통계를 계산할 수 없습니다.") from exc

        return _aggregate_statistics(rows)


# ---------------------------------------------------------------------------
# 행 → dataclass 매핑
# ---------------------------------------------------------------------------


def _stored_scalar(value: object | None) -> Optional[str]:
    """Raw 스칼라를 TEXT로 저장하기 위한 표현. None은 그대로 둔다."""
    if value is None:
        return None
    return str(value)


def _status_value(status: object) -> str:
    return status.value if isinstance(status, ProcessingStatus) else str(status)


def _row_to_raw(row: sqlite3.Row) -> RawReview:
    try:
        payload = json.loads(row["raw_payload"])
    except (TypeError, ValueError):
        payload = {}
    return RawReview(
        source_review_id=row["source_review_id"],
        product_name=row["product_name"],
        review_date=row["review_date"],
        rating=row["rating"],
        review_text=row["review_text"],
        source_file=row["source_file"],
        raw_payload=payload if isinstance(payload, dict) else {},
    )


def _row_to_clean(row: sqlite3.Row) -> CleanReview:
    return CleanReview(
        id=row["id"],
        source_review_id=row["source_review_id"],
        product_name=row["product_name"],
        review_date=_parse_date(row["review_date"]),
        rating=row["rating"],
        review_text=row["review_text"],
        cleaned_at=_parse_utc(row["cleaned_at"]),
    )


def _row_to_analysis(row: sqlite3.Row) -> Optional[AnalysisResult]:
    if row["a_review_id"] is None:
        return None
    try:
        keywords = json.loads(row["keywords"])
    except (TypeError, ValueError):
        keywords = []
    return AnalysisResult(
        review_id=row["a_review_id"],
        sentiment=Sentiment(row["sentiment"]),
        confidence=row["confidence"],
        analyzed_at=_parse_utc(row["analyzed_at"]),
        provider=row["provider"],
        model=row["model"],
        summary=row["summary"],
        keywords=list(keywords) if isinstance(keywords, list) else [],
        prompt_version=row["prompt_version"],
    )


def _row_to_detail(row: sqlite3.Row) -> ReviewDetail:
    return ReviewDetail(review=_row_to_clean(row), analysis=_row_to_analysis(row))


# ---------------------------------------------------------------------------
# 통계 집계 (명세 9.2). storage에서 한 번만 계산한다.
# ---------------------------------------------------------------------------


def _aggregate_statistics(rows: Sequence[sqlite3.Row]) -> ReviewStatistics:
    total = len(rows)
    ratings: list[int] = []

    analyzed = 0
    failed = 0
    sentiment_counts: Counter[Sentiment] = Counter()
    daily: dict[date, Counter[Sentiment]] = defaultdict(Counter)
    rating_matrix: dict[int, Counter[Sentiment]] = defaultdict(Counter)
    positive_keywords: Counter[str] = Counter()
    negative_keywords: Counter[str] = Counter()

    for row in rows:
        ratings.append(row["rating"])

        if row["status"] == ProcessingStatus.ANALYSIS_FAILED.value:
            failed += 1

        if row["sentiment"] is None:
            continue

        analyzed += 1
        sentiment = Sentiment(row["sentiment"])
        sentiment_counts[sentiment] += 1
        daily[_parse_date(row["review_date"])][sentiment] += 1
        rating_matrix[int(row["rating"])][sentiment] += 1

        keywords = _load_keywords(row["keywords"])
        if sentiment is Sentiment.POSITIVE:
            positive_keywords.update(keywords)
        elif sentiment is Sentiment.NEGATIVE:
            negative_keywords.update(keywords)

    unanalyzed = total - analyzed - failed
    average_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

    counts = {sentiment: sentiment_counts.get(sentiment, 0) for sentiment in Sentiment}
    ratios = {
        sentiment: (counts[sentiment] / analyzed if analyzed else 0.0)
        for sentiment in Sentiment
    }

    return ReviewStatistics(
        total_reviews=total,
        analyzed_reviews=analyzed,
        unanalyzed_reviews=unanalyzed,
        failed_reviews=failed,
        average_rating=average_rating,
        sentiment_counts=counts,
        sentiment_ratios=ratios,
        daily_sentiment_counts={
            day: dict(sentiments) for day, sentiments in sorted(daily.items())
        },
        rating_sentiment_matrix={
            rating: dict(sentiments)
            for rating, sentiments in sorted(rating_matrix.items())
        },
        top_positive_keywords=_top_keywords(positive_keywords),
        top_negative_keywords=_top_keywords(negative_keywords),
    )


def _load_keywords(raw: object) -> list[str]:
    try:
        keywords = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(keywords, list):
        return []
    return [str(keyword) for keyword in keywords if str(keyword).strip()]


def _top_keywords(counter: Counter[str]) -> list[KeywordCount]:
    return [
        KeywordCount(keyword=keyword, count=count)
        for keyword, count in counter.most_common(_TOP_KEYWORD_LIMIT)
    ]


__all__ = ["SqliteReviewRepository"]
