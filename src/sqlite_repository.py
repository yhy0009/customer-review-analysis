"""SQLite implementation shared by Raw, Clean, Analysis and read models."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.config import get_logger
from src.errors import StorageError, ValidationError

from typing import Any, List, Mapping, Optional, Sequence
from collections import Counter, defaultdict

from src.models import (
    AnalysisResult,
    BatchOperationResult,
    CleanReview,
    DuplicatePolicy,
    ItemError,
    KeywordCount,
    Sentiment,
    SortField,
    SortOrder,
    Page,
    ProcessingStatus,
    RawReview,
    ReviewDetail,
    ReviewFilter,
    ReviewQuery,
    ReviewStatistics,
)


# Version 1 is the initial schema; future changes require an explicit migration.
_SCHEMA_VERSION = 1
_SCHEMA = (
    """CREATE TABLE raw_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key TEXT NOT NULL UNIQUE,
        source_review_id TEXT,
        product_name TEXT NOT NULL,
        review_date TEXT NOT NULL,
        rating TEXT NOT NULL,
        review_text TEXT NOT NULL,
        source_file TEXT,
        raw_payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'RAW'
            CHECK (status IN ('RAW', 'REJECTED', 'CLEANED', 'ANALYZED', 'ANALYSIS_FAILED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE clean_reviews (
        id INTEGER PRIMARY KEY REFERENCES raw_reviews(id) ON DELETE CASCADE,
        source_review_id TEXT,
        product_name TEXT NOT NULL,
        review_date TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        review_text TEXT NOT NULL,
        cleaned_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'CLEANED'
            CHECK (status IN ('CLEANED', 'ANALYZED', 'ANALYSIS_FAILED')),
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE analysis_results (
        review_id INTEGER PRIMARY KEY REFERENCES clean_reviews(id) ON DELETE CASCADE,
        sentiment TEXT NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
        confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
        summary TEXT,
        keywords TEXT NOT NULL,
        analyzed_at TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_version TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_raw_reviews_status ON raw_reviews(status)",
)
_SCHEMA_COLUMNS = {
    "raw_reviews": "id, dedupe_key, source_review_id, product_name, review_date, rating, "
                   "review_text, source_file, raw_payload, status, created_at, updated_at",
    "clean_reviews": "id, source_review_id, product_name, review_date, rating, review_text, "
                     "cleaned_at, status, error_message, created_at, updated_at",
    "analysis_results": "review_id, sentiment, confidence, summary, keywords, analyzed_at, "
                        "provider, model, prompt_version, created_at, updated_at",
}


def _json_value(value: object) -> object:
    """Convert file-input values to JSON without silently stringifying objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric value")
        return value
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("Unsupported raw value")


def _json_dump(value: object) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def _normalized_text(value: object) -> str:
    value = _json_value(value)
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        text = _json_dump(value)
    else:
        text = str(value)
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _normalized_number(value: object) -> str:
    text = _normalized_text(value)
    try:
        number = Decimal(text)
        if number.is_finite():
            if number == 0:
                return "0"
            # Decimal.normalize() uses the active precision and can round large
            # raw values or overflow. Strip zeros exactly, without arithmetic.
            sign, digits, exponent = number.as_tuple()
            digits = list(digits)
            while digits[-1] == 0:
                digits.pop()
                exponent += 1
            return ("-" if sign else "") + "".join(map(str, digits)) + "e" + str(exponent)
    except InvalidOperation:
        pass
    return text


def _raw_dedupe_key(review: RawReview) -> str:
    source_id = _normalized_text(review.source_review_id)
    # Excel often represents integer identifiers as floats. Preserve textual
    # leading zeros ("001" remains different from "1").
    if isinstance(review.source_review_id, (int, float)) and not isinstance(
        review.source_review_id, bool
    ) and source_id:
        number = Decimal(source_id)
        if number == number.to_integral_value():
            source_id = str(int(number))
    if source_id:
        return "id:" + source_id
    review_date = _normalized_text(review.review_date)
    try:
        review_date = date.fromisoformat(review_date).isoformat()
    except ValueError:
        pass  # Invalid dates remain raw input for the cleaner.
    fields = [
        _normalized_text(review.product_name), review_date,
        _normalized_number(review.rating), _normalized_text(review.review_text),
    ]
    # JSON framing avoids collisions between adjacent fields containing delimiters.
    return "sha256:" + hashlib.sha256(_json_dump(fields).encode("utf-8")).hexdigest()


_TOP_KEYWORD_LIMIT = 10
_AI_RELEVANT_FIELDS = ("product_name", "review_date", "rating", "review_text")
_SORT_COLUMNS = {
    SortField.ID: "c.id", SortField.DATE: "c.review_date",
    SortField.RATING: "c.rating", SortField.SENTIMENT: "a.sentiment",
}

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
        clauses.append("CASEFOLD(c.product_name) LIKE ? ESCAPE '!' ")
        literal = filters.product_name.casefold().replace("!", "!!").replace("%", "!%").replace("_", "!_")
        params.append(f"%{literal}%")
    if filters.rating is not None:
        clauses.append("c.rating = ?")
        params.append(filters.rating)
    if filters.rating_min is not None:
        clauses.append("c.rating >= ?")
        params.append(filters.rating_min)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


class SQLiteReviewRepository:
    """File-backed repository using schema v1 and stable Raw/Clean IDs.

    Relative database paths are resolved against the project root.
    """

    def __init__(self, database_path: Path | str) -> None:
        if not str(database_path).strip() or str(database_path) == ":memory:":
            raise ValidationError("A persistent database file path is required")
        path = Path(database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        self.database_path = path.resolve()
        self._connection: Optional[sqlite3.Connection] = None
        self._logger = get_logger("storage")
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.create_function("CASEFOLD", 1, lambda value: value.casefold(), deterministic=True)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._initialize_schema()
        except (OSError, sqlite3.Error, StorageError) as exc:
            self.close()
            self._logger.error("SQLite initialization failed")
            raise StorageError("SQLite 저장소를 초기화할 수 없습니다.") from exc

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("SQLite 저장소가 닫혀 있습니다.")
        return self._connection

    def _initialize_schema(self) -> None:
        connection = self._require_connection()
        # Lock before inspecting version so concurrent first opens cannot race.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                existing = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existing:
                    raise StorageError("기존 미등록 스키마는 자동으로 변경하지 않습니다.")
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
            elif version != _SCHEMA_VERSION:
                raise StorageError("지원하지 않는 SQLite 스키마 버전입니다.")
            for table, columns in _SCHEMA_COLUMNS.items():
                connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")

    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        """Commit valid rows together; isolate row errors using savepoints.

        Input IDs are ignored. UPSERT retains the stored ID and created_at,
        resets status to RAW, and invalidates dependent Clean/Analysis rows.
        """
        if not isinstance(policy, DuplicatePolicy):
            raise ValidationError("policy must be a DuplicatePolicy value")
        connection = self._require_connection()
        succeeded = skipped = failed = 0
        errors: List[ItemError] = []
        try:
            with connection:
                # BEGIN is essential: releasing a top-level savepoint alone would
                # commit each row and prevent whole-batch rollback on a DB failure.
                connection.execute("BEGIN IMMEDIATE")
                for row_number, review in enumerate(reviews, start=1):
                    connection.execute("SAVEPOINT raw_item")
                    try:
                        if not isinstance(review, RawReview):
                            raise ValueError("Expected RawReview")
                        if review.source_file is not None and not isinstance(review.source_file, str):
                            raise ValueError("Invalid source_file")
                        if not isinstance(review.raw_payload, dict):
                            raise ValueError("Invalid raw_payload")
                        values = tuple(_json_dump(value) for value in (
                            review.source_review_id, review.product_name, review.review_date,
                            review.rating, review.review_text,
                        )) + (review.source_file, _json_dump(review.raw_payload))
                        key = _raw_dedupe_key(review)
                        existing = connection.execute(
                            "SELECT id FROM raw_reviews WHERE dedupe_key = ?", (key,)
                        ).fetchone()
                        if existing is not None and policy is DuplicatePolicy.SKIP:
                            skipped += 1
                        else:
                            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                            if existing is None:
                                connection.execute(
                                    "INSERT INTO raw_reviews (source_review_id, product_name, "
                                    "review_date, rating, review_text, source_file, raw_payload, "
                                    "dedupe_key, created_at, updated_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    values + (key, now, now),
                                )
                            else:
                                # Foreign-key cascade also removes analysis_results.
                                connection.execute("DELETE FROM clean_reviews WHERE id = ?", (existing["id"],))
                                connection.execute(
                                    "UPDATE raw_reviews SET source_review_id=?, product_name=?, "
                                    "review_date=?, rating=?, review_text=?, source_file=?, raw_payload=?, "
                                    "status='RAW', updated_at=? WHERE id=?",
                                    values + (now, existing["id"]),
                                )
                            succeeded += 1
                    except (ValueError, TypeError, OverflowError, RecursionError,
                            sqlite3.IntegrityError):
                        connection.execute("ROLLBACK TO SAVEPOINT raw_item")
                        failed += 1
                        errors.append(ItemError(
                            item_ref=str(row_number), code="RAW_STORAGE_INVALID_ITEM",
                            message="원본 행을 저장할 수 없습니다. 값의 형식과 저장 제약을 확인하세요.",
                        ))
                        self._logger.warning("Raw row storage failed: row=%d", row_number)
                    finally:
                        connection.execute("RELEASE SAVEPOINT raw_item")
        except sqlite3.Error as exc:
            self._logger.error("Raw batch rolled back due to a storage failure")
            raise StorageError("원본 리뷰 저장에 실패하여 배치 전체를 취소했습니다.") from exc
        result = BatchOperationResult(
            processed=len(reviews), succeeded=succeeded, skipped=skipped,
            failed=failed, errors=errors,
        )
        self._logger.info(
            "Raw storage: processed=%d succeeded=%d skipped=%d failed=%d",
            result.processed, succeeded, skipped, failed,
        )
        return result

    def fetch_raw_reviews(
        self, *, status: Optional[ProcessingStatus] = None,
    ) -> List[RawReview]:
        """Return original values and storage-assigned IDs in ascending ID order."""
        if status is not None and not isinstance(status, ProcessingStatus):
            raise ValidationError("status must be a ProcessingStatus value")
        connection = self._require_connection()
        sql = "SELECT * FROM raw_reviews"
        parameters = ()
        if status is not None:
            sql += " WHERE status = ?"
            parameters = (status.value,)
        try:
            rows = connection.execute(sql + " ORDER BY id", parameters).fetchall()
            reviews = []
            for row in rows:
                values = {name: json.loads(row[name]) for name in (
                    "source_review_id", "product_name", "review_date", "rating",
                    "review_text", "raw_payload",
                )}
                if not isinstance(values["raw_payload"], dict):
                    raise ValueError("Invalid persisted raw_payload")
                reviews.append(RawReview(id=row["id"], source_file=row["source_file"], **values))
            return reviews
        except (sqlite3.Error, ValueError, TypeError, RecursionError) as exc:
            self._logger.error("Raw review read failed")
            raise StorageError("원본 리뷰를 읽을 수 없습니다.") from exc

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SQLiteReviewRepository:
        storage = config.get("storage")
        if not isinstance(storage, Mapping) or storage.get("backend", "sqlite") != "sqlite":
            raise StorageError("SQLite 저장소 설정이 필요합니다.")
        path = storage.get("database_path")
        if not isinstance(path, str) or not path.strip():
            raise StorageError("storage.database_path가 필요합니다.")
        return cls(path)

    def save_clean_reviews(
        self, reviews: Sequence[CleanReview], policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        """Persist cleaned records under their original Raw IDs, atomically."""
        if not isinstance(policy, DuplicatePolicy):
            raise ValidationError("policy must be a DuplicatePolicy value")
        connection = self._require_connection()
        succeeded = skipped = failed = 0
        errors: List[ItemError] = []
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                for index, review in enumerate(reviews, start=1):
                    connection.execute("SAVEPOINT clean_item")
                    try:
                        if not isinstance(review, CleanReview):
                            raise ValidationError("Expected CleanReview")
                        review.__post_init__()
                        if review.source_review_id is not None and not isinstance(review.source_review_id, str):
                            raise ValidationError("source_review_id must be a string")
                        if connection.execute("SELECT id FROM raw_reviews WHERE id=?", (review.id,)).fetchone() is None:
                            raise ValidationError("Matching Raw review is required")
                        existing = connection.execute("SELECT * FROM clean_reviews WHERE id=?", (review.id,)).fetchone()
                        if existing is not None and policy is DuplicatePolicy.SKIP:
                            skipped += 1
                        else:
                            now = _utc_iso(datetime.now(timezone.utc))
                            values = (review.source_review_id, review.product_name, _iso_date(review.review_date),
                                      review.rating, review.review_text, _utc_iso(review.cleaned_at))
                            if existing is None:
                                connection.execute(
                                    "INSERT INTO clean_reviews (source_review_id, product_name, review_date, "
                                    "rating, review_text, cleaned_at, id, created_at, updated_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (review.id, now, now),
                                )
                            else:
                                changed = any(existing[field] != value for field, value in zip(
                                    _AI_RELEVANT_FIELDS, values[1:5]))
                                connection.execute(
                                    "UPDATE clean_reviews SET source_review_id=?, product_name=?, review_date=?, "
                                    "rating=?, review_text=?, cleaned_at=?, updated_at=? WHERE id=?",
                                    values + (now, review.id),
                                )
                                if changed:
                                    connection.execute("DELETE FROM analysis_results WHERE review_id=?", (review.id,))
                                    connection.execute("UPDATE clean_reviews SET status='CLEANED', error_message=NULL WHERE id=?", (review.id,))
                            connection.execute(
                                "UPDATE raw_reviews SET status=(SELECT status FROM clean_reviews WHERE id=?), "
                                "updated_at=? WHERE id=?", (review.id, now, review.id),
                            )
                            succeeded += 1
                    except (ValidationError, ValueError, TypeError, OverflowError, sqlite3.IntegrityError):
                        connection.execute("ROLLBACK TO SAVEPOINT clean_item")
                        failed += 1
                        errors.append(ItemError(item_ref=str(index), code="CLEAN_STORAGE_INVALID_ITEM",
                                                message="정제 행을 저장할 수 없습니다. 원본 ID와 데이터 형식을 확인하세요."))
                        self._logger.warning("Clean row storage failed: row=%d", index)
                    finally:
                        connection.execute("RELEASE SAVEPOINT clean_item")
        except sqlite3.Error as exc:
            self._logger.error("Clean batch rolled back due to a storage failure")
            raise StorageError("정제 리뷰 저장에 실패하여 배치 전체를 취소했습니다.") from exc
        self._logger.info("Clean storage: processed=%d succeeded=%d skipped=%d failed=%d",
                          len(reviews), succeeded, skipped, failed)
        return BatchOperationResult(processed=len(reviews), succeeded=succeeded,
                                    skipped=skipped, failed=failed, errors=errors)

    def save_analysis(self, result: AnalysisResult) -> None:
        result.__post_init__()
        connection = self._require_connection()
        now = _utc_iso(datetime.now(timezone.utc))
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO analysis_results (review_id, sentiment, confidence, summary, keywords, "
                    "analyzed_at, provider, model, prompt_version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(review_id) DO UPDATE SET sentiment=excluded.sentiment, "
                    "confidence=excluded.confidence, summary=excluded.summary, keywords=excluded.keywords, "
                    "analyzed_at=excluded.analyzed_at, provider=excluded.provider, model=excluded.model, "
                    "prompt_version=excluded.prompt_version, updated_at=excluded.updated_at",
                    (result.review_id, result.sentiment.value, result.confidence, result.summary,
                     _json_dump(list(dict.fromkeys(result.keywords))), _utc_iso(result.analyzed_at),
                     result.provider, result.model, result.prompt_version, now, now),
                )
                connection.execute("UPDATE clean_reviews SET status='ANALYZED', error_message=NULL, updated_at=? WHERE id=?",
                                   (now, result.review_id))
                connection.execute("UPDATE raw_reviews SET status='ANALYZED', updated_at=? WHERE id=?", (now, result.review_id))
        except sqlite3.Error as exc:
            self._logger.error("Analysis storage failed: review_id=%d", result.review_id)
            raise StorageError("분석 결과를 저장할 수 없습니다.") from exc

    def mark_analysis_failed(self, review_id: int, error_message: str) -> None:
        _validate_id(review_id)
        connection = self._require_connection()
        now = _utc_iso(datetime.now(timezone.utc))
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT id FROM clean_reviews WHERE id=?", (review_id,)).fetchone() is None:
                    raise StorageError("분석 대상 리뷰를 찾을 수 없습니다.")
                # A failed latest attempt must be retryable and must not count as
                # both analyzed and failed in downstream statistics.
                connection.execute("DELETE FROM analysis_results WHERE review_id=?", (review_id,))
                connection.execute("UPDATE clean_reviews SET status='ANALYSIS_FAILED', error_message=?, updated_at=? WHERE id=?",
                                   ("AI analysis failed", now, review_id))
                connection.execute("UPDATE raw_reviews SET status='ANALYSIS_FAILED', updated_at=? WHERE id=?", (now, review_id))
        except sqlite3.Error as exc:
            raise StorageError("분석 실패 상태를 저장할 수 없습니다.") from exc
        # Provider errors can contain credentials or review text; do not persist
        # or log the untrusted error_message argument.
        self._logger.warning("Analysis failed: review_id=%d", review_id)

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
            _validate_id(limit, "limit")
            query += " LIMIT ?"
            params = [*params, limit]

        try:
            rows = self._require_connection().execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("Clean 리뷰를 조회할 수 없습니다.") from exc
        return _decode_rows(rows, _row_to_clean)

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
            _validate_id(limit, "limit")
            query += " LIMIT ?"
            params.append(limit)

        try:
            rows = self._require_connection().execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise StorageError("미분석 리뷰를 조회할 수 없습니다.") from exc
        return _decode_rows(rows, _row_to_clean)

    def get_review(self, review_id: int) -> Optional[ReviewDetail]:
        _validate_id(review_id)
        try:
            row = self._require_connection().execute(
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
        return _decode_rows([row], _row_to_detail)[0]

    def list_reviews(self, query: ReviewQuery) -> Page[ReviewDetail]:
        where, params = _build_where(query.filters)
        base = (
            "FROM clean_reviews c "
            "LEFT JOIN analysis_results a ON a.review_id = c.id"
            f"{where}"
        )

        try:
            total = self._require_connection().execute(
                f"SELECT COUNT(*) AS n {base}", params
            ).fetchone()["n"]

            order_column = _SORT_COLUMNS[query.sort]
            direction = "ASC" if query.order is SortOrder.ASC else "DESC"
            offset = (query.page - 1) * query.size

            rows = self._require_connection().execute(
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

        items = _decode_rows(rows, _row_to_detail)
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
            rows = self._require_connection().execute(
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

        try:
            return _aggregate_statistics(rows)
        except (ValueError, TypeError, ValidationError) as exc:
            raise StorageError("저장된 통계 데이터를 해석할 수 없습니다.") from exc


    def close(self) -> None:
        """Close the connection; repeated calls are safe."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> SQLiteReviewRepository:
        self._require_connection()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


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
    keywords = _load_keywords(row["keywords"])
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
    # Keep the calculation precise; presentation code chooses display rounding.
    average_rating = sum(ratings) / len(ratings) if ratings else None

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
            day: {sentiment: sentiments.get(sentiment, 0) for sentiment in Sentiment}
            for day, sentiments in sorted(daily.items())
        },
        rating_sentiment_matrix={
            rating: {sentiment: rating_matrix.get(rating, {}).get(sentiment, 0)
                     for sentiment in Sentiment}
            for rating in range(1, 6)
        },
        top_positive_keywords=_top_keywords(positive_keywords),
        top_negative_keywords=_top_keywords(negative_keywords),
    )


def _load_keywords(raw: object) -> list[str]:
    keywords = json.loads(raw)
    if not isinstance(keywords, list) or any(not isinstance(k, str) or not k.strip() for k in keywords):
        raise ValueError("Invalid persisted keywords")
    return list(dict.fromkeys(keywords))


def _top_keywords(counter: Counter[str]) -> list[KeywordCount]:
    return [
        KeywordCount(keyword=keyword, count=count)
        for keyword, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:_TOP_KEYWORD_LIMIT]
    ]



def _decode_rows(rows, decoder):
    try:
        return [decoder(row) for row in rows]
    except (ValueError, TypeError, ValidationError) as exc:
        raise StorageError("저장된 리뷰 데이터를 해석할 수 없습니다.") from exc


def _validate_id(value: int, field: str = "review_id") -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field} must be a positive integer")


# Both prior import spellings refer to the same implementation.
SqliteReviewRepository = SQLiteReviewRepository
__all__ = ["SQLiteReviewRepository", "SqliteReviewRepository"]
