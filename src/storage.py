"""SQLite repository implementation and persistence contract for the project.

The public repository contract is kept backend-neutral so a JSONL implementation
can be added later without changing the callers.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

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


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS raw_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_review_id TEXT,
    product_name TEXT,
    review_date TEXT,
    rating TEXT,
    review_text TEXT,
    source_file TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'RAW',
    analysis_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clean_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_review_id TEXT,
    product_name TEXT NOT NULL,
    review_date TEXT NOT NULL,
    rating INTEGER NOT NULL,
    review_text TEXT NOT NULL,
    cleaned_at TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    raw_id INTEGER,
    FOREIGN KEY (raw_id) REFERENCES raw_reviews(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS analysis_results (
    review_id INTEGER PRIMARY KEY,
    sentiment TEXT NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    analyzed_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT,
    FOREIGN KEY (review_id) REFERENCES clean_reviews(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_raw_status
ON raw_reviews(status);

CREATE INDEX IF NOT EXISTS idx_raw_source_review_id
ON raw_reviews(source_review_id);

CREATE INDEX IF NOT EXISTS idx_clean_product_name
ON clean_reviews(product_name);

CREATE INDEX IF NOT EXISTS idx_clean_review_date
ON clean_reviews(review_date);

CREATE INDEX IF NOT EXISTS idx_clean_rating
ON clean_reviews(rating);

CREATE INDEX IF NOT EXISTS idx_analysis_sentiment
ON analysis_results(sentiment);
"""


class SQLiteReviewRepository:
    """SQLite implementation of :class:`ReviewRepository`."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._connection = sqlite3.connect(str(self.db_path))
            self._connection.row_factory = sqlite3.Row

            self._connection.execute("PRAGMA foreign_keys = ON")

            if str(self.db_path) != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")

            self._initialize_schema()

        except (sqlite3.Error, OSError) as exc:
            raise StorageError(
                f"failed to initialize SQLite storage: {exc}"
            ) from exc

    def close(self) -> None:
        """Close the SQLite connection."""
        try:
            self._connection.close()
        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to close SQLite storage: {exc}"
            ) from exc

    def __enter__(self) -> "SQLiteReviewRepository":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    # ================================================================
    # Schema / serialization helpers
    # ================================================================

    def _initialize_schema(self) -> None:
        try:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

        except sqlite3.Error as exc:
            self._connection.rollback()
            raise StorageError(
                f"failed to initialize database schema: {exc}"
            ) from exc

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _datetime_to_text(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")

        if value.utcoffset().total_seconds() != 0:
            raise ValueError("datetime must use UTC")

        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _text_to_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )

    @staticmethod
    def _json_loads(value: str, default: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if value is None:
            return ""

        text = str(value).strip()
        return re.sub(r"\s+", " ", text)

    @classmethod
    def _normalize_source_id(
        cls,
        value: Any,
    ) -> Optional[str]:
        text = cls._normalize_text(value)
        return text or None

    @classmethod
    def _normalize_rating(cls, value: Any) -> str:
        text = cls._normalize_text(value)

        try:
            number = float(text)
        except (TypeError, ValueError):
            return text

        if number.is_integer():
            return str(int(number))

        return str(number)

    @classmethod
    def _dedupe_key(
        cls,
        *,
        source_review_id: Any,
        product_name: Any,
        review_date: Any,
        rating: Any,
        review_text: Any,
    ) -> str:
        """Create the canonical duplicate-detection key.

        The specification requires:

        1. normalized source_review_id when available
        2. otherwise SHA-256 of normalized product/date/rating/text
        """

        source_id = cls._normalize_source_id(source_review_id)

        if source_id is not None:
            return f"id:{source_id}"

        if isinstance(review_date, datetime):
            date_text = review_date.date().isoformat()

        elif isinstance(review_date, date):
            date_text = review_date.isoformat()

        else:
            date_text = cls._normalize_text(review_date)

        canonical = "|".join(
            (
                cls._normalize_text(product_name),
                date_text,
                cls._normalize_rating(rating),
                cls._normalize_text(review_text),
            )
        )

        digest = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()

        return f"hash:{digest}"

    @staticmethod
    def _error(
        item_ref: Optional[str],
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> ItemError:
        return ItemError(
            item_ref=item_ref,
            code=code,
            message=message,
            retryable=retryable,
        )

    # ================================================================
    # Filter helpers
    # ================================================================

    @classmethod
    def _build_filter_sql(
        cls,
        filters: Optional[ReviewFilter],
        *,
        alias: str = "c",
        include_analysis: bool = True,
    ) -> Tuple[str, List[Any]]:
        if filters is None:
            filters = ReviewFilter()

        clauses: List[str] = []
        params: List[Any] = []

        if filters.sentiment is not None and include_analysis:
            clauses.append("a.sentiment = ?")
            params.append(filters.sentiment.value)

        if filters.date_from is not None:
            clauses.append(
                f"{alias}.review_date >= ?"
            )
            params.append(filters.date_from.isoformat())

        if filters.date_to is not None:
            clauses.append(
                f"{alias}.review_date <= ?"
            )
            params.append(filters.date_to.isoformat())

        if filters.product_name is not None:
            clauses.append(
                f"LOWER({alias}.product_name) LIKE LOWER(?)"
            )
            params.append(
                f"%{filters.product_name.strip()}%"
            )

        if filters.rating is not None:
            clauses.append(
                f"{alias}.rating = ?"
            )
            params.append(filters.rating)

        if filters.rating_min is not None:
            clauses.append(
                f"{alias}.rating >= ?"
            )
            params.append(filters.rating_min)

        if not clauses:
            return "1=1", params

        return " AND ".join(clauses), params

    @staticmethod
    def _sort_expression(sort: SortField) -> str:
        return {
            SortField.ID: "c.id",
            SortField.DATE: "c.review_date",
            SortField.RATING: "c.rating",
            SortField.SENTIMENT: "a.sentiment",
        }[sort]

    # ================================================================
    # Raw reviews
    # ================================================================

    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        processed = len(reviews)

        succeeded = 0
        skipped = 0
        failed = 0
        rejected = 0

        errors: List[ItemError] = []

        try:
            with self._connection:

                for index, review in enumerate(
                    reviews,
                    start=1,
                ):
                    savepoint = f"raw_row_{index}"

                    try:
                        self._connection.execute(
                            f"SAVEPOINT {savepoint}"
                        )

                        dedupe_key = self._dedupe_key(
                            source_review_id=review.source_review_id,
                            product_name=review.product_name,
                            review_date=review.review_date,
                            rating=review.rating,
                            review_text=review.review_text,
                        )

                        existing = self._connection.execute(
                            """
                            SELECT id
                            FROM raw_reviews
                            WHERE dedupe_key = ?
                            """,
                            (dedupe_key,),
                        ).fetchone()

                        # ------------------------------------------------
                        # Duplicate + SKIP
                        # ------------------------------------------------
                        if (
                            existing is not None
                            and policy is DuplicatePolicy.SKIP
                        ):
                            skipped += 1

                            self._connection.execute(
                                f"RELEASE SAVEPOINT {savepoint}"
                            )

                            continue

                        now = self._datetime_to_text(
                            self._utc_now()
                        )

                        source_id = self._normalize_source_id(
                            review.source_review_id
                        )

                        payload = self._json_dumps(
                            review.raw_payload or {}
                        )

                        # ------------------------------------------------
                        # New raw review
                        # ------------------------------------------------
                        if existing is None:

                            self._connection.execute(
                                """
                                INSERT INTO raw_reviews (
                                    source_review_id,
                                    product_name,
                                    review_date,
                                    rating,
                                    review_text,
                                    source_file,
                                    raw_payload,
                                    dedupe_key,
                                    status,
                                    analysis_error,
                                    created_at,
                                    updated_at
                                )
                                VALUES (
                                    ?, ?, ?, ?, ?, ?,
                                    ?, ?, ?, NULL, ?, ?
                                )
                                """,
                                (
                                    source_id,
                                    (
                                        None
                                        if review.product_name is None
                                        else str(review.product_name)
                                    ),
                                    (
                                        None
                                        if review.review_date is None
                                        else str(review.review_date)
                                    ),
                                    (
                                        None
                                        if review.rating is None
                                        else str(review.rating)
                                    ),
                                    (
                                        None
                                        if review.review_text is None
                                        else str(review.review_text)
                                    ),
                                    review.source_file,
                                    payload,
                                    dedupe_key,
                                    ProcessingStatus.RAW.value,
                                    now,
                                    now,
                                ),
                            )

                        # ------------------------------------------------
                        # Existing raw review + UPSERT
                        # ------------------------------------------------
                        else:
                            raw_id = int(existing["id"])

                            # Raw data changed, so the old clean/analysis
                            # results are no longer valid.
                            self._delete_clean_for_raw(
                                raw_id
                            )

                            self._connection.execute(
                                """
                                UPDATE raw_reviews
                                SET
                                    source_review_id = ?,
                                    product_name = ?,
                                    review_date = ?,
                                    rating = ?,
                                    review_text = ?,
                                    source_file = ?,
                                    raw_payload = ?,
                                    status = ?,
                                    analysis_error = NULL,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    source_id,
                                    (
                                        None
                                        if review.product_name is None
                                        else str(review.product_name)
                                    ),
                                    (
                                        None
                                        if review.review_date is None
                                        else str(review.review_date)
                                    ),
                                    (
                                        None
                                        if review.rating is None
                                        else str(review.rating)
                                    ),
                                    (
                                        None
                                        if review.review_text is None
                                        else str(review.review_text)
                                    ),
                                    review.source_file,
                                    payload,
                                    ProcessingStatus.RAW.value,
                                    now,
                                    raw_id,
                                ),
                            )

                        self._connection.execute(
                            f"RELEASE SAVEPOINT {savepoint}"
                        )

                        succeeded += 1

                    except (
                        sqlite3.IntegrityError,
                        TypeError,
                        ValueError,
                    ) as exc:

                        self._connection.execute(
                            f"ROLLBACK TO SAVEPOINT {savepoint}"
                        )

                        self._connection.execute(
                            f"RELEASE SAVEPOINT {savepoint}"
                        )

                        failed += 1

                        errors.append(
                            self._error(
                                str(index),
                                "RAW_SAVE_FAILED",
                                str(exc),
                            )
                        )

        except sqlite3.Error as exc:
            self._connection.rollback()

            raise StorageError(
                f"failed to save raw reviews: {exc}"
            ) from exc

        return BatchOperationResult(
            processed=processed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            rejected=rejected,
            errors=errors,
        )

    def fetch_raw_reviews(
        self,
        *,
        status: Optional[ProcessingStatus] = None,
    ) -> List[RawReview]:

        try:
            if status is None:
                rows = self._connection.execute(
                    """
                    SELECT *
                    FROM raw_reviews
                    ORDER BY id
                    """
                ).fetchall()

            else:
                rows = self._connection.execute(
                    """
                    SELECT *
                    FROM raw_reviews
                    WHERE status = ?
                    ORDER BY id
                    """,
                    (status.value,),
                ).fetchall()

            return [
                self._raw_from_row(row)
                for row in rows
            ]

        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to fetch raw reviews: {exc}"
            ) from exc

    def _raw_from_row(
        self,
        row: sqlite3.Row,
    ) -> RawReview:
        return RawReview(
            source_review_id=row["source_review_id"],
            product_name=row["product_name"],
            review_date=row["review_date"],
            rating=row["rating"],
            review_text=row["review_text"],
            source_file=row["source_file"],
            raw_payload=self._json_loads(
                row["raw_payload"],
                {},
            ),
        )

    def _delete_clean_for_raw(
        self,
        raw_id: int,
    ) -> None:
        self._connection.execute(
            """
            DELETE FROM clean_reviews
            WHERE raw_id = ?
            """,
            (raw_id,),
        )

    # ================================================================
    # Clean reviews
    # ================================================================

    def save_clean_reviews(
        self,
        reviews: Sequence[CleanReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:

        processed = len(reviews)

        succeeded = 0
        skipped = 0
        failed = 0
        rejected = 0

        errors: List[ItemError] = []

        try:
            with self._connection:

                for index, review in enumerate(
                    reviews,
                    start=1,
                ):
                    savepoint = f"clean_row_{index}"

                    try:
                        self._connection.execute(
                            f"SAVEPOINT {savepoint}"
                        )

                        dedupe_key = self._dedupe_key(
                            source_review_id=review.source_review_id,
                            product_name=review.product_name,
                            review_date=review.review_date,
                            rating=review.rating,
                            review_text=review.review_text,
                        )

                        existing = self._connection.execute(
                            """
                            SELECT *
                            FROM clean_reviews
                            WHERE dedupe_key = ?
                            """,
                            (dedupe_key,),
                        ).fetchone()

                        # ------------------------------------------------
                        # Duplicate + SKIP
                        # ------------------------------------------------
                        if (
                            existing is not None
                            and policy is DuplicatePolicy.SKIP
                        ):
                            skipped += 1

                            self._connection.execute(
                                f"RELEASE SAVEPOINT {savepoint}"
                            )

                            continue

                        raw_id = self._find_raw_id(review)

                        normalized_source_id = (
                            self._normalize_source_id(
                                review.source_review_id
                            )
                        )

                        normalized_product = (
                            review.product_name.strip()
                        )

                        normalized_date = (
                            review.review_date.isoformat()
                        )

                        normalized_text = (
                            review.review_text.strip()
                        )

                        changed = (
                            existing is None
                            or existing["source_review_id"]
                            != normalized_source_id
                            or existing["product_name"]
                            != normalized_product
                            or existing["review_date"]
                            != normalized_date
                            or existing["rating"]
                            != review.rating
                            or existing["review_text"]
                            != normalized_text
                        )

                        # ------------------------------------------------
                        # New clean review
                        # ------------------------------------------------
                        if existing is None:

                            self._connection.execute(
                                """
                                INSERT INTO clean_reviews (
                                    source_review_id,
                                    product_name,
                                    review_date,
                                    rating,
                                    review_text,
                                    cleaned_at,
                                    dedupe_key,
                                    raw_id
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    normalized_source_id,
                                    normalized_product,
                                    normalized_date,
                                    review.rating,
                                    normalized_text,
                                    self._datetime_to_text(
                                        review.cleaned_at
                                    ),
                                    dedupe_key,
                                    raw_id,
                                ),
                            )

                        # ------------------------------------------------
                        # Existing clean review + UPSERT
                        # ------------------------------------------------
                        else:
                            clean_id = int(
                                existing["id"]
                            )

                            # Clean fields affect AI input.
                            if changed:
                                self._connection.execute(
                                    """
                                    DELETE FROM analysis_results
                                    WHERE review_id = ?
                                    """,
                                    (clean_id,),
                                )

                            self._connection.execute(
                                """
                                UPDATE clean_reviews
                                SET
                                    source_review_id = ?,
                                    product_name = ?,
                                    review_date = ?,
                                    rating = ?,
                                    review_text = ?,
                                    cleaned_at = ?,
                                    raw_id = ?
                                WHERE id = ?
                                """,
                                (
                                    normalized_source_id,
                                    normalized_product,
                                    normalized_date,
                                    review.rating,
                                    normalized_text,
                                    self._datetime_to_text(
                                        review.cleaned_at
                                    ),
                                    raw_id,
                                    clean_id,
                                ),
                            )

                        # ------------------------------------------------
                        # Raw status → CLEANED
                        # ------------------------------------------------
                        if raw_id is not None:
                            self._connection.execute(
                                """
                                UPDATE raw_reviews
                                SET
                                    status = ?,
                                    analysis_error = NULL,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    ProcessingStatus.CLEANED.value,
                                    self._datetime_to_text(
                                        self._utc_now()
                                    ),
                                    raw_id,
                                ),
                            )

                        self._connection.execute(
                            f"RELEASE SAVEPOINT {savepoint}"
                        )

                        succeeded += 1

                    except (
                        sqlite3.IntegrityError,
                        TypeError,
                        ValueError,
                    ) as exc:

                        self._connection.execute(
                            f"ROLLBACK TO SAVEPOINT {savepoint}"
                        )

                        self._connection.execute(
                            f"RELEASE SAVEPOINT {savepoint}"
                        )

                        failed += 1

                        errors.append(
                            self._error(
                                str(index),
                                "CLEAN_SAVE_FAILED",
                                str(exc),
                            )
                        )

        except sqlite3.Error as exc:
            self._connection.rollback()

            raise StorageError(
                f"failed to save clean reviews: {exc}"
            ) from exc

        return BatchOperationResult(
            processed=processed,
            succeeded=succeeded,
            skipped=skipped,
            failed=failed,
            rejected=rejected,
            errors=errors,
        )

    def _find_raw_id(
        self,
        review: CleanReview,
    ) -> Optional[int]:

        source_id = self._normalize_source_id(
            review.source_review_id
        )

        if source_id is not None:
            row = self._connection.execute(
                """
                SELECT id
                FROM raw_reviews
                WHERE source_review_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()

            return (
                None
                if row is None
                else int(row["id"])
            )

        key = self._dedupe_key(
            source_review_id=None,
            product_name=review.product_name,
            review_date=review.review_date,
            rating=review.rating,
            review_text=review.review_text,
        )

        row = self._connection.execute(
            """
            SELECT id
            FROM raw_reviews
            WHERE dedupe_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()

        return (
            None
            if row is None
            else int(row["id"])
        )

    def fetch_clean_reviews(
        self,
        filters: Optional[ReviewFilter] = None,
        *,
        limit: Optional[int] = None,
    ) -> List[CleanReview]:

        if limit is not None and limit < 1:
            raise ValueError(
                "limit must be positive"
            )

        where_sql, params = (
            self._build_filter_sql(filters)
        )

        sql = f"""
            SELECT c.*
            FROM clean_reviews c
            LEFT JOIN analysis_results a
                ON a.review_id = c.id
            WHERE {where_sql}
            ORDER BY c.id
        """

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        try:
            rows = self._connection.execute(
                sql,
                params,
            ).fetchall()

            return [
                self._clean_from_row(row)
                for row in rows
            ]

        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to fetch clean reviews: {exc}"
            ) from exc

    def fetch_unanalyzed_reviews(
        self,
        *,
        limit: Optional[int] = None,
    ) -> List[CleanReview]:

        if limit is not None and limit < 1:
            raise ValueError(
                "limit must be positive"
            )

        sql = """
            SELECT c.*
            FROM clean_reviews c
            LEFT JOIN analysis_results a
                ON a.review_id = c.id
            WHERE a.review_id IS NULL
            ORDER BY c.id
        """

        params: List[Any] = []

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        try:
            rows = self._connection.execute(
                sql,
                params,
            ).fetchall()

            return [
                self._clean_from_row(row)
                for row in rows
            ]

        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to fetch unanalyzed reviews: {exc}"
            ) from exc

    @staticmethod
    def _clean_from_row(
        row: sqlite3.Row,
    ) -> CleanReview:

        return CleanReview(
            id=int(row["id"]),
            source_review_id=row["source_review_id"],
            product_name=row["product_name"],
            review_date=date.fromisoformat(
                row["review_date"]
            ),
            rating=int(row["rating"]),
            review_text=row["review_text"],
            cleaned_at=SQLiteReviewRepository._text_to_datetime(
                row["cleaned_at"]
            ),
        )

    # ================================================================
    # Analysis
    # ================================================================

    def save_analysis(
        self,
        result: AnalysisResult,
    ) -> None:

        try:
            with self._connection:

                clean_exists = self._connection.execute(
                    """
                    SELECT 1
                    FROM clean_reviews
                    WHERE id = ?
                    """,
                    (result.review_id,),
                ).fetchone()

                if clean_exists is None:
                    raise StorageError(
                        "cannot save analysis: "
                        f"review_id {result.review_id} "
                        "does not exist"
                    )

                self._connection.execute(
                    """
                    INSERT INTO analysis_results (
                        review_id,
                        sentiment,
                        confidence,
                        summary,
                        keywords,
                        analyzed_at,
                        provider,
                        model,
                        prompt_version
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(review_id)
                    DO UPDATE SET
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
                        self._json_dumps(
                            result.keywords
                        ),
                        self._datetime_to_text(
                            result.analyzed_at
                        ),
                        result.provider,
                        result.model,
                        result.prompt_version,
                    ),
                )

                self._connection.execute(
                    """
                    UPDATE raw_reviews
                    SET
                        status = ?,
                        analysis_error = NULL,
                        updated_at = ?
                    WHERE id = (
                        SELECT raw_id
                        FROM clean_reviews
                        WHERE id = ?
                    )
                    """,
                    (
                        ProcessingStatus.ANALYZED.value,
                        self._datetime_to_text(
                            self._utc_now()
                        ),
                        result.review_id,
                    ),
                )

        except StorageError:
            self._connection.rollback()
            raise

        except (
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as exc:

            self._connection.rollback()

            raise StorageError(
                f"failed to save analysis: {exc}"
            ) from exc

    def mark_analysis_failed(
        self,
        review_id: int,
        error_message: str,
    ) -> None:

        if (
            not isinstance(error_message, str)
            or not error_message.strip()
        ):
            raise ValueError(
                "error_message must be non-empty"
            )

        try:
            with self._connection:

                exists = self._connection.execute(
                    """
                    SELECT 1
                    FROM clean_reviews
                    WHERE id = ?
                    """,
                    (review_id,),
                ).fetchone()

                if exists is None:
                    raise StorageError(
                        "cannot mark analysis failed: "
                        f"review_id {review_id} "
                        "does not exist"
                    )

                self._connection.execute(
                    """
                    UPDATE raw_reviews
                    SET
                        status = ?,
                        analysis_error = ?,
                        updated_at = ?
                    WHERE id = (
                        SELECT raw_id
                        FROM clean_reviews
                        WHERE id = ?
                    )
                    """,
                    (
                        ProcessingStatus.ANALYSIS_FAILED.value,
                        error_message.strip(),
                        self._datetime_to_text(
                            self._utc_now()
                        ),
                        review_id,
                    ),
                )

        except StorageError:
            self._connection.rollback()
            raise

        except sqlite3.Error as exc:
            self._connection.rollback()

            raise StorageError(
                f"failed to mark analysis failure: {exc}"
            ) from exc

    # ================================================================
    # Query / list / show
    # ================================================================

    def get_review(
        self,
        review_id: int,
    ) -> Optional[ReviewDetail]:

        try:
            row = self._connection.execute(
                """
                SELECT
                    c.*,

                    a.review_id AS a_review_id,
                    a.sentiment,
                    a.confidence,
                    a.summary,
                    a.keywords,
                    a.analyzed_at,
                    a.provider,
                    a.model,
                    a.prompt_version

                FROM clean_reviews c

                LEFT JOIN analysis_results a
                    ON a.review_id = c.id

                WHERE c.id = ?
                """,
                (review_id,),
            ).fetchone()

            if row is None:
                return None

            return self._detail_from_row(row)

        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to fetch review {review_id}: {exc}"
            ) from exc

    def list_reviews(
        self,
        query: ReviewQuery,
    ) -> Page[ReviewDetail]:

        where_sql, params = (
            self._build_filter_sql(
                query.filters
            )
        )

        sort_sql = self._sort_expression(
            query.sort
        )

        order_sql = (
            "ASC"
            if query.order is SortOrder.ASC
            else "DESC"
        )

        count_sql = f"""
            SELECT COUNT(*)
            FROM clean_reviews c

            LEFT JOIN analysis_results a
                ON a.review_id = c.id

            WHERE {where_sql}
        """

        select_sql = f"""
            SELECT
                c.*,

                a.review_id AS a_review_id,
                a.sentiment,
                a.confidence,
                a.summary,
                a.keywords,
                a.analyzed_at,
                a.provider,
                a.model,
                a.prompt_version

            FROM clean_reviews c

            LEFT JOIN analysis_results a
                ON a.review_id = c.id

            WHERE {where_sql}

            ORDER BY
                {sort_sql} {order_sql},
                c.id DESC

            LIMIT ? OFFSET ?
        """

        try:
            total_items = int(
                self._connection.execute(
                    count_sql,
                    params,
                ).fetchone()[0]
            )

            offset = (
                query.page - 1
            ) * query.size

            rows = self._connection.execute(
                select_sql,
                [
                    *params,
                    query.size,
                    offset,
                ],
            ).fetchall()

            items = [
                self._detail_from_row(row)
                for row in rows
            ]

            total_pages = (
                (
                    total_items
                    + query.size
                    - 1
                )
                // query.size
                if total_items
                else 0
            )

            return Page(
                items=items,
                page=query.page,
                size=query.size,
                total_items=total_items,
                total_pages=total_pages,
            )

        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to list reviews: {exc}"
            ) from exc

    def _detail_from_row(
        self,
        row: sqlite3.Row,
    ) -> ReviewDetail:

        review = self._clean_from_row(row)

        analysis = None

        if row["a_review_id"] is not None:
            keywords = self._json_loads(
                row["keywords"],
                [],
            )

            if not isinstance(keywords, list):
                keywords = []

            analysis = AnalysisResult(
                review_id=int(
                    row["a_review_id"]
                ),
                sentiment=Sentiment(
                    row["sentiment"]
                ),
                confidence=float(
                    row["confidence"]
                ),
                summary=row["summary"],
                keywords=[
                    str(keyword)
                    for keyword in keywords
                ],
                analyzed_at=self._text_to_datetime(
                    row["analyzed_at"]
                ),
                provider=row["provider"],
                model=row["model"],
                prompt_version=row[
                    "prompt_version"
                ],
            )

        return ReviewDetail(
            review=review,
            analysis=analysis,
        )

    # ================================================================
    # Statistics
    # ================================================================

    def get_statistics(
        self,
        filters: Optional[ReviewFilter] = None,
    ) -> ReviewStatistics:

        where_sql, params = (
            self._build_filter_sql(filters)
        )

        base_sql = f"""
            SELECT
                c.id,
                c.review_date,
                c.rating,

                a.review_id AS a_review_id,
                a.sentiment,
                a.keywords,

                r.status AS raw_status

            FROM clean_reviews c

            LEFT JOIN analysis_results a
                ON a.review_id = c.id

            LEFT JOIN raw_reviews r
                ON r.id = c.raw_id

            WHERE {where_sql}
        """

        try:
            rows = self._connection.execute(
                base_sql,
                params,
            ).fetchall()

        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to fetch statistics data: {exc}"
            ) from exc

        total_reviews = len(rows)

        analyzed_reviews = sum(
            1
            for row in rows
            if row["a_review_id"] is not None
        )

        failed_reviews = sum(
            1
            for row in rows
            if row["raw_status"]
            == ProcessingStatus.ANALYSIS_FAILED.value
        )

        unanalyzed_reviews = (
            total_reviews
            - analyzed_reviews
        )

        ratings = [
            int(row["rating"])
            for row in rows
        ]

        average_rating = (
            sum(ratings) / len(ratings)
            if ratings
            else None
        )

        # ------------------------------------------------------------
        # Sentiment counts
        # ------------------------------------------------------------

        sentiment_counts: dict[
            Sentiment,
            int,
        ] = {
            Sentiment.POSITIVE: 0,
            Sentiment.NEUTRAL: 0,
            Sentiment.NEGATIVE: 0,
        }

        # ------------------------------------------------------------
        # Daily sentiment counts
        # ------------------------------------------------------------

        daily_sentiment_counts: dict[
            date,
            dict[Sentiment, int],
        ] = {}

        # ------------------------------------------------------------
        # Rating × sentiment matrix
        # ------------------------------------------------------------

        rating_sentiment_matrix: dict[
            int,
            dict[Sentiment, int],
        ] = {
            rating: {
                Sentiment.POSITIVE: 0,
                Sentiment.NEUTRAL: 0,
                Sentiment.NEGATIVE: 0,
            }
            for rating in range(1, 6)
        }

        # ------------------------------------------------------------
        # Keyword counts
        # ------------------------------------------------------------

        positive_keywords: dict[
            str,
            int,
        ] = {}

        negative_keywords: dict[
            str,
            int,
        ] = {}

        # ------------------------------------------------------------
        # Aggregate
        # ------------------------------------------------------------

        for row in rows:

            if row["sentiment"] is None:
                continue

            sentiment = Sentiment(
                row["sentiment"]
            )

            sentiment_counts[
                sentiment
            ] += 1

            review_date = date.fromisoformat(
                row["review_date"]
            )

            daily = daily_sentiment_counts.setdefault(
                review_date,
                {
                    Sentiment.POSITIVE: 0,
                    Sentiment.NEUTRAL: 0,
                    Sentiment.NEGATIVE: 0,
                },
            )

            daily[sentiment] += 1

            rating_sentiment_matrix[
                int(row["rating"])
            ][sentiment] += 1

            keywords = self._json_loads(
                row["keywords"],
                [],
            )

            if not isinstance(
                keywords,
                list,
            ):
                continue

            if sentiment is Sentiment.POSITIVE:
                target = positive_keywords

            elif sentiment is Sentiment.NEGATIVE:
                target = negative_keywords

            else:
                continue

            for keyword in keywords:

                normalized = (
                    self._normalize_text(
                        keyword
                    )
                )

                if normalized:
                    target[normalized] = (
                        target.get(
                            normalized,
                            0,
                        )
                        + 1
                    )

        # ------------------------------------------------------------
        # Sentiment ratios
        # ------------------------------------------------------------

        if analyzed_reviews:
            sentiment_ratios = {
                sentiment: (
                    sentiment_counts[
                        sentiment
                    ]
                    / analyzed_reviews
                )
                for sentiment in Sentiment
            }

        else:
            sentiment_ratios = {
                sentiment: 0.0
                for sentiment in Sentiment
            }

        return ReviewStatistics(
            total_reviews=total_reviews,
            analyzed_reviews=analyzed_reviews,
            unanalyzed_reviews=unanalyzed_reviews,
            failed_reviews=failed_reviews,
            average_rating=average_rating,
            sentiment_counts=sentiment_counts,
            sentiment_ratios=sentiment_ratios,
            daily_sentiment_counts=dict(
                sorted(
                    daily_sentiment_counts.items()
                )
            ),
            rating_sentiment_matrix=(
                rating_sentiment_matrix
            ),
            top_positive_keywords=(
                self._top_keywords(
                    positive_keywords
                )
            ),
            top_negative_keywords=(
                self._top_keywords(
                    negative_keywords
                )
            ),
        )

    @staticmethod
    def _top_keywords(
        counts: dict[str, int],
        limit: int = 10,
    ) -> List[KeywordCount]:

        return [
            KeywordCount(
                keyword=keyword,
                count=count,
            )
            for keyword, count in sorted(
                counts.items(),
                key=lambda item: (
                    -item[1],
                    item[0],
                ),
            )[:limit]
        ]


__all__ = [
    "ReviewRepository",
    "SQLiteReviewRepository",
]