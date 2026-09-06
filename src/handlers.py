"""CLI adapters that translate argparse values into application requests."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from src.cli import CommandHandler
from src.errors import (
    AIProviderError,
    AppError,
    ConfigError,
    InputFileError,
    OutputError,
    StorageError,
    ValidationError,
)
from src.models import (
    AnalyzeRequest,
    AnalyzeTarget,
    BatchOperationResult,
    CleanRequest,
    CleaningOptions,
    DashboardRequest,
    DuplicatePolicy,
    ExportFormat,
    ExportRequest,
    ExtractRequest,
    ImportRequest,
    ListRequest,
    ReportFormat,
    ReviewFilter,
    ReviewQuery,
    Sentiment,
    ShowRequest,
    SortField,
    SortOrder,
    StatsRequest,
)
from src.services import ApplicationServices


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _app_config(args: argparse.Namespace) -> Mapping[str, Any]:
    config = getattr(args, "app_config", None)
    if not isinstance(config, Mapping):
        raise ConfigError(
            "애플리케이션 설정이 핸들러에 전달되지 않았습니다."
        )
    return config


def _config_section(args: argparse.Namespace, name: str) -> Mapping[str, Any]:
    section = _app_config(args).get(name)
    if not isinstance(section, Mapping):
        raise ConfigError(f"설정 섹션 '{name}'이 올바르지 않습니다.")
    return section


def _project_path(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _optional_date(value: Optional[str]) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc


def _duplicate_policy(value: object) -> DuplicatePolicy:
    if isinstance(value, DuplicatePolicy):
        return value
    try:
        return DuplicatePolicy(str(value))
    except ValueError as exc:
        raise ConfigError("중복 정책은 skip 또는 upsert여야 합니다.") from exc


def _review_filter(args: argparse.Namespace) -> ReviewFilter:
    sentiment_value = getattr(args, "sentiment", None)
    try:
        sentiment = Sentiment(sentiment_value) if sentiment_value is not None else None
    except ValueError as exc:
        raise ValidationError("지원하지 않는 감정 필터입니다.") from exc
    return ReviewFilter(
        sentiment=sentiment,
        date_from=_optional_date(getattr(args, "date_from", None)),
        date_to=_optional_date(getattr(args, "date_to", None)),
        product_name=getattr(args, "product", None),
        rating=getattr(args, "rating", None),
        rating_min=getattr(args, "rating_min", None),
    )


def build_import_request(args: argparse.Namespace) -> ImportRequest:
    cleaning = _config_section(args, "cleaning")
    policy = getattr(args, "policy", None) or cleaning.get("duplicate_policy")
    return ImportRequest(file=_project_path(args.file), policy=_duplicate_policy(policy))


def build_clean_request(args: argparse.Namespace) -> CleanRequest:
    cleaning = _config_section(args, "cleaning")
    policy = getattr(args, "policy", None) or cleaning.get("duplicate_policy")
    min_length = getattr(args, "min_length", None)
    if min_length is None:
        min_length = cleaning.get("min_review_length")
    if isinstance(min_length, bool) or not isinstance(min_length, int):
        raise ConfigError("cleaning.min_review_length는 정수여야 합니다.")
    return CleanRequest(
        options=CleaningOptions(
            policy=_duplicate_policy(policy),
            min_length=min_length,
        )
    )


def build_analyze_request(args: argparse.Namespace) -> AnalyzeRequest:
    if getattr(args, "analyze_all", False):
        target = AnalyzeTarget.ALL
    elif getattr(args, "review_id", None) is not None:
        target = AnalyzeTarget.REVIEW_ID
    elif getattr(args, "unanalyzed", False):
        target = AnalyzeTarget.UNANALYZED
    else:
        raise ValidationError("분석 대상을 하나 선택해야 합니다.")
    return AnalyzeRequest(
        target=target,
        review_id=getattr(args, "review_id", None),
        limit=getattr(args, "limit", None),
        force=getattr(args, "force", False),
    )


def build_extract_request(args: argparse.Namespace) -> ExtractRequest:
    return ExtractRequest(
        filters=_review_filter(args),
        limit=getattr(args, "limit", None),
    )


def build_list_request(args: argparse.Namespace) -> ListRequest:
    try:
        return ListRequest(
            query=ReviewQuery(
                filters=_review_filter(args),
                page=args.page,
                size=args.size,
                sort=SortField(args.sort),
                order=SortOrder(args.order),
            )
        )
    except ValueError as exc:
        raise ValidationError("정렬 기준 또는 방향이 올바르지 않습니다.") from exc


def build_show_request(args: argparse.Namespace) -> ShowRequest:
    return ShowRequest(review_id=args.review_id)


def build_stats_request(args: argparse.Namespace) -> StatsRequest:
    return StatsRequest(filters=_review_filter(args))


def build_dashboard_request(args: argparse.Namespace) -> DashboardRequest:
    paths = _config_section(args, "paths")
    output = getattr(args, "output", None) or paths.get("output_dir")
    if not isinstance(output, (str, Path)):
        raise ConfigError("paths.output_dir은 경로 문자열이어야 합니다.")
    try:
        report_format = ReportFormat(args.report_format)
    except ValueError as exc:
        raise ValidationError("지원하지 않는 리포트 형식입니다.") from exc
    return DashboardRequest(
        filters=_review_filter(args),
        output=_project_path(output),
        report_format=report_format,
        force=getattr(args, "force", False),
    )


def build_export_request(args: argparse.Namespace) -> ExportRequest:
    try:
        export_format = ExportFormat(args.format)
    except ValueError as exc:
        raise ValidationError("지원하지 않는 내보내기 형식입니다.") from exc
    return ExportRequest(
        filters=_review_filter(args),
        output=_project_path(args.output),
        format=export_format,
        force=getattr(args, "force", False),
    )


def _exit_code_for(result: object, *, missing_is_failure: bool = False) -> int:
    if isinstance(result, BatchOperationResult) and result.is_partial_failure:
        return 1
    if result is None and missing_is_failure:
        return 1
    return 0


def _adapt(
    service_method: Callable[[Any], object],
    request_builder: Callable[[argparse.Namespace], object],
    *,
    missing_is_failure: bool = False,
) -> CommandHandler:
    def handler(args: argparse.Namespace) -> int:
        try:
            result = service_method(request_builder(args))
        except (ConfigError, ValidationError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2
        except (InputFileError, StorageError, OutputError) as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 3
        except AIProviderError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 4
        except AppError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        return _exit_code_for(result, missing_is_failure=missing_is_failure)

    return handler


def build_handlers(services: ApplicationServices) -> Dict[str, CommandHandler]:
    """Build the complete CLI handler map around an application service object."""
    return {
        "import": _adapt(services.import_reviews, build_import_request),
        "clean": _adapt(services.clean_reviews, build_clean_request),
        "analyze": _adapt(services.analyze_reviews, build_analyze_request),
        "extract": _adapt(services.extract_insights, build_extract_request),
        "list": _adapt(services.list_reviews, build_list_request),
        "show": _adapt(
            services.show_review,
            build_show_request,
            missing_is_failure=True,
        ),
        "stats": _adapt(services.get_statistics, build_stats_request),
        "dashboard": _adapt(services.create_dashboard, build_dashboard_request),
        "export": _adapt(services.export_reviews, build_export_request),
    }


__all__ = [
    "build_analyze_request",
    "build_clean_request",
    "build_dashboard_request",
    "build_export_request",
    "build_extract_request",
    "build_handlers",
    "build_import_request",
    "build_list_request",
    "build_show_request",
    "build_stats_request",
]
