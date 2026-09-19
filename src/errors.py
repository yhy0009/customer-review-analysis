"""Application exception hierarchy shared across feature boundaries."""

from enum import Enum


class AIErrorCode(str, Enum):
    """Allowlisted diagnostics; never derive these values from provider text."""

    UNKNOWN = "AI_ERROR"
    HTTP = "AI_HTTP_ERROR"
    RATE_LIMIT = "AI_RATE_LIMITED"
    QUOTA = "AI_QUOTA_EXCEEDED"
    TIMEOUT = "AI_TIMEOUT"
    CONNECTION = "AI_CONNECTION_FAILED"
    RESPONSE = "AI_RESPONSE_INVALID"
    OUTPUT_LIMIT = "AI_OUTPUT_LIMIT"
    REFUSAL = "AI_REFUSAL"
    INTERRUPTED = "AI_OUTPUT_INTERRUPTED"
    INSIGHT_FORMAT = "INSIGHT_FORMAT_INVALID"
    EVIDENCE_FORMAT = "EVIDENCE_FORMAT_INVALID"
    EVIDENCE_REVIEWS = "EVIDENCE_REVIEW_MISMATCH"
    EVIDENCE_QUOTE = "EVIDENCE_QUOTE_MISMATCH"
    EVIDENCE_COMPLAINT = "EVIDENCE_COMPLAINT_MISSING"
    ISSUE_COVERAGE = "INSIGHT_ISSUE_MISSING"
    PRAISE_COVERAGE = "INSIGHT_PRAISE_MISSING"
    UNGROUNDED_ISSUE = "INSIGHT_UNGROUNDED_ISSUE"
    SUGGESTION = "INSIGHT_SUGGESTION_INVALID"


class AppError(Exception):
    """Base class for expected application-level failures."""


class ConfigError(AppError):
    """Raised when application configuration is missing or invalid."""


class InputFileError(AppError):
    """Raised when an input file cannot be read or interpreted."""


class ValidationError(AppError):
    """Raised when a command or domain value violates the public contract."""


class StorageError(AppError):
    """Raised for repository connection, schema, or persistence failures."""


class RawReviewChangedError(StorageError):
    """A cleaning result no longer matches the stored original; retry it."""


class AIProviderError(AppError):
    """Raised after an AI provider request cannot be completed."""

    def __init__(self, *args: object, code: AIErrorCode = AIErrorCode.UNKNOWN,
                 http_status: int | None = None) -> None:
        super().__init__(*args)
        self.code = code
        self.http_status = http_status


def safe_ai_error_details(error: AIProviderError) -> tuple[str, int | None]:
    """Custom providers may attach arbitrary attributes; check at output time."""
    code = getattr(error, "code", None)
    status = getattr(error, "http_status", None)
    return (
        code.value if isinstance(code, AIErrorCode) else AIErrorCode.UNKNOWN.value,
        status if type(status) is int and 100 <= status <= 599 else None,
    )


class OutputError(AppError):
    """Raised when an export, chart, or report cannot be written."""


__all__ = [
    "AIErrorCode",
    "AIProviderError",
    "AppError",
    "ConfigError",
    "InputFileError",
    "OutputError",
    "StorageError",
    "RawReviewChangedError",
    "ValidationError",
    "safe_ai_error_details",
]
