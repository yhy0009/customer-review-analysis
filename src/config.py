"""Application configuration loading and validation."""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, TextIO


LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
LOGGER_NAME = "customer_review_analysis"
_HANDLER_MARKER = "_customer_review_analysis_handler"

DEFAULT_CONFIG = {
    "paths": {
        "output_dir": "output",
    },
    "storage": {
        "backend": "sqlite",
        "database_path": "data/app_database.db",
        "raw_table": "raw_reviews",
        "clean_table": "clean_reviews",
    },
    "cleaning": {
        "duplicate_policy": "skip",
        "min_review_length": 3,
    },
    "ai": {
        "provider": "openai",
        "model": "",
        "api_key": None,
        "timeout_seconds": 30,
        "max_retries": 3,
    },
    "visualization": {
        "font_family": "",
        "dpi": 150,
    },
    "logging": {
        "level": "INFO",
        "file": "logs/app.log",
        "max_bytes": 1_048_576,
        "backup_count": 3,
    },
}

ENV_OVERRIDES = {
    "CRA_OUTPUT_DIR": (("paths", "output_dir"), str),
    "CRA_DATABASE_PATH": (("storage", "database_path"), str),
    "CRA_DUPLICATE_POLICY": (("cleaning", "duplicate_policy"), str),
    "CRA_MIN_REVIEW_LENGTH": (("cleaning", "min_review_length"), int),
    "AI_PROVIDER": (("ai", "provider"), str),
    "AI_MODEL": (("ai", "model"), str),
    "AI_API_KEY": (("ai", "api_key"), str),
    "CRA_LOG_LEVEL": (("logging", "level"), str),
    "CRA_LOG_FILE": (("logging", "file"), str),
}


class ConfigError(ValueError):
    """Raised when the application configuration is missing or invalid."""


def _deep_merge(
    target: MutableMapping[str, Any],
    source: Mapping[str, Any],
) -> MutableMapping[str, Any]:
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, MutableMapping) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = deepcopy(value)
    return target


def _set_nested(
    config: MutableMapping[str, Any],
    path: tuple[str, ...],
    value: Any,
) -> None:
    section = config
    for key in path[:-1]:
        nested = section.setdefault(key, {})
        if not isinstance(nested, MutableMapping):
            raise ConfigError(f"설정 항목 '{'.'.join(path[:-1])}'은 객체여야 합니다.")
        section = nested
    section[path[-1]] = value


def _apply_environment_overrides(
    config: MutableMapping[str, Any],
    environ: Mapping[str, str],
) -> None:
    for variable, (path, converter) in ENV_OVERRIDES.items():
        raw_value = environ.get(variable)
        if raw_value is None or raw_value == "":
            continue
        try:
            value = converter(raw_value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"환경변수 {variable}의 값이 올바르지 않습니다.") from exc
        _set_nested(config, path, value)


def _require_mapping(config: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    value = config.get(section)
    if not isinstance(value, Mapping):
        raise ConfigError(f"설정 섹션 '{section}'은 객체여야 합니다.")
    return value


def _require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"설정 항목 '{field}'은 비어 있지 않은 문자열이어야 합니다.")


def _require_positive_integer(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"설정 항목 '{field}'은 1 이상의 정수여야 합니다.")


def _require_non_negative_integer(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"설정 항목 '{field}'은 0 이상의 정수여야 합니다.")


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate known settings while allowing future extension keys."""
    paths = _require_mapping(config, "paths")
    storage = _require_mapping(config, "storage")
    cleaning = _require_mapping(config, "cleaning")
    ai = _require_mapping(config, "ai")
    visualization = _require_mapping(config, "visualization")
    logging_config = _require_mapping(config, "logging")

    _require_non_empty_string(paths.get("output_dir"), "paths.output_dir")

    if storage.get("backend") not in {"sqlite", "jsonl"}:
        raise ConfigError("설정 항목 'storage.backend'는 sqlite 또는 jsonl이어야 합니다.")
    _require_non_empty_string(storage.get("database_path"), "storage.database_path")
    _require_non_empty_string(storage.get("raw_table"), "storage.raw_table")
    _require_non_empty_string(storage.get("clean_table"), "storage.clean_table")

    if cleaning.get("duplicate_policy") not in {"skip", "upsert"}:
        raise ConfigError("설정 항목 'cleaning.duplicate_policy'는 skip 또는 upsert여야 합니다.")
    _require_positive_integer(
        cleaning.get("min_review_length"),
        "cleaning.min_review_length",
    )

    _require_non_empty_string(ai.get("provider"), "ai.provider")
    if not isinstance(ai.get("model"), str):
        raise ConfigError("설정 항목 'ai.model'은 문자열이어야 합니다.")
    api_key = ai.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise ConfigError("설정 항목 'ai.api_key'는 문자열 또는 null이어야 합니다.")
    _require_positive_integer(ai.get("timeout_seconds"), "ai.timeout_seconds")
    _require_non_negative_integer(ai.get("max_retries"), "ai.max_retries")

    if not isinstance(visualization.get("font_family"), str):
        raise ConfigError("설정 항목 'visualization.font_family'는 문자열이어야 합니다.")
    _require_positive_integer(visualization.get("dpi"), "visualization.dpi")

    level = logging_config.get("level")
    if not isinstance(level, str) or level.upper() not in LOG_LEVELS:
        raise ConfigError(
            "설정 항목 'logging.level'은 DEBUG, INFO, WARNING, ERROR, CRITICAL 중 하나여야 합니다."
        )
    log_file = logging_config.get("file")
    if log_file is not None and (
        not isinstance(log_file, str) or not log_file.strip()
    ):
        raise ConfigError("설정 항목 'logging.file'은 문자열 또는 null이어야 합니다.")
    _require_positive_integer(logging_config.get("max_bytes"), "logging.max_bytes")
    _require_non_negative_integer(
        logging_config.get("backup_count"),
        "logging.backup_count",
    )


def load_config(
    path: Path | str,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Load JSON configuration, merge defaults, then apply environment values."""
    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            loaded = json.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"설정 파일의 JSON 형식이 올바르지 않습니다: {config_path} ({exc.msg})"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다: {config_path}") from exc

    if not isinstance(loaded, Mapping):
        raise ConfigError("설정 파일의 최상위 값은 JSON 객체여야 합니다.")

    config = _deep_merge(deepcopy(DEFAULT_CONFIG), loaded)
    _apply_environment_overrides(config, os.environ if environ is None else environ)
    validate_config(config)
    return dict(config)


def _clear_managed_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def _mark_handler(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def configure_logging(
    config: Mapping[str, Any],
    level_override: Optional[str] = None,
    stream: Optional[TextIO] = None,
) -> logging.Logger:
    """Configure the application logger with console and rotating file output."""
    logging_config = _require_mapping(config, "logging")
    configured_level = level_override or logging_config.get("level")
    if not isinstance(configured_level, str):
        raise ConfigError("로그 레벨은 문자열이어야 합니다.")

    level_name = configured_level.upper()
    if level_name not in LOG_LEVELS:
        raise ConfigError(f"지원하지 않는 로그 레벨입니다: {configured_level}")
    level = getattr(logging, level_name)

    logger = logging.getLogger(LOGGER_NAME)
    _clear_managed_handlers(logger)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = _mark_handler(logging.StreamHandler(stream))
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    log_file = logging_config.get("file")
    if log_file is not None:
        log_path = Path(log_file)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = _mark_handler(
                RotatingFileHandler(
                    log_path,
                    maxBytes=logging_config["max_bytes"],
                    backupCount=logging_config["backup_count"],
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            _clear_managed_handlers(logger)
            raise ConfigError(f"로그 파일을 준비할 수 없습니다: {log_path}") from exc
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return the application logger or one of its child loggers."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def reset_logging() -> None:
    """Close handlers installed by :func:`configure_logging`."""
    logger = logging.getLogger(LOGGER_NAME)
    _clear_managed_handlers(logger)
