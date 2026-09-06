"""Application exception hierarchy shared across feature boundaries."""


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


class AIProviderError(AppError):
    """Raised after an AI provider request cannot be completed."""


class OutputError(AppError):
    """Raised when an export, chart, or report cannot be written."""


__all__ = [
    "AIProviderError",
    "AppError",
    "ConfigError",
    "InputFileError",
    "OutputError",
    "StorageError",
    "ValidationError",
]
