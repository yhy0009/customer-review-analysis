"""Compose executable commands lazily from the loaded application config.

Each command owns one repository connection. AI services and their SDK are
imported only when analyze or extract executes, after request validation.
The file collector and pandas are loaded only when import executes.
"""
from __future__ import annotations

import argparse
from typing import Any, Callable, Mapping, TypeVar

from src.cli import CommandHandler
from src.errors import ConfigError, OutputError
from src.export_service import ExportService
from src.handlers import (
    build_analyze_handler,
    build_clean_handler,
    build_dashboard_handler,
    build_import_handler,
    build_extract_handler,
    build_export_handler,
    build_list_handler,
    build_show_handler,
    build_stats_handler,
)
from src.models import (
    AnalysisBatchResult,
    AnalysisOptions,
    AnalyzeRequest,
    BatchOperationResult,
    CleanBatchResult,
    CleanRequest,
    DashboardRequest,
    DashboardResult,
    ImportRequest,
    ExportRequest,
    ExportResult,
    ExtractRequest,
    InsightResult,
)
from src.query_service import QueryService
from src.sqlite_repository import SQLiteReviewRepository
from src.storage import ReviewRepository


RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")
# A factory wires a command to this invocation's repository and loaded config.
# It returns a bound service method accepting the existing request dataclass.
ServiceFactory = Callable[
    [ReviewRepository, Mapping[str, Any]], Callable[[RequestT], ResultT]
]


def _ai_options(config: Mapping[str, Any]) -> AnalysisOptions:
    ai = config.get("ai")
    if not isinstance(ai, Mapping):
        raise ConfigError("설정 섹션 'ai'가 올바르지 않습니다.")
    # config permits future extension keys; pass only the shared supported fields.
    # Each AI module selects its own built-in prompt version when this is None.
    return AnalysisOptions(
        provider=ai.get("provider"),
        model=ai.get("model"),
        timeout_seconds=ai.get("timeout_seconds"),
        max_retries=ai.get("max_retries"),
        api_key=ai.get("api_key"),
        base_url=ai.get("base_url"),
        reasoning_effort=ai.get("reasoning_effort", "minimal"),
    )


def _analyze_handler(args: argparse.Namespace) -> int:
    def execute(request: AnalyzeRequest) -> AnalysisBatchResult:
        options = _ai_options(args.app_config)
        try:
            from src.ai_provider import provider_for
            from src.analysis_service import AnalysisService
            from src.analyzer import BatchReviewAnalyzer
        except ImportError:
            raise ConfigError(
                "AI 명령 실행에 필요한 패키지가 없습니다. requirements.txt의 의존성을 설치하세요."
            ) from None

        provider = provider_for(options)
        with SQLiteReviewRepository.from_config(args.app_config) as repository:
            analyzer = BatchReviewAnalyzer(repository, provider=provider)
            return AnalysisService(repository, analyzer, options).analyze_reviews(request)

    return build_analyze_handler(execute)(args)


def _extract_handler(args: argparse.Namespace) -> int:
    def execute(request: ExtractRequest) -> InsightResult:
        options = _ai_options(args.app_config)
        try:
            from src.ai_provider import provider_for
            from src.insight_extractor import AIInsightExtractor
            from src.insight_service import InsightService
        except ImportError:
            raise ConfigError(
                "AI 명령 실행에 필요한 패키지가 없습니다. requirements.txt의 의존성을 설치하세요."
            ) from None

        # The review-by-review evidence response needs the extractor's output
        # budget even when runtime constructs and injects the provider.
        provider = provider_for(options, max_completion_tokens=8192)
        with SQLiteReviewRepository.from_config(args.app_config) as repository:
            extractor = AIInsightExtractor(options, provider=provider)
            service = InsightService(repository, extractor, snapshot=repository.read_snapshot)
            return service.extract_insights(request)

    return build_extract_handler(execute)(args)


def _query_handler(
    handler_factory: Callable[[Callable[[Any], Any]], CommandHandler],
    service_method: Callable[[QueryService, Any], Any],
) -> CommandHandler:
    def handler(args: argparse.Namespace) -> int:
        def execute(request: Any) -> Any:
            # The handler validates its request before opening storage, and its
            # common exception boundary also covers DB initialization failures.
            with SQLiteReviewRepository.from_config(args.app_config) as repository:
                return service_method(QueryService(repository), request)

        return handler_factory(execute)(args)

    return handler


def _export_handler(args: argparse.Namespace) -> int:
    def execute(request: ExportRequest) -> ExportResult:
        from src.exporter import FileReviewExporter

        with SQLiteReviewRepository.from_config(args.app_config) as repository:
            output = request.output
            database = repository.database_path
            protected_paths = [database] + [
                database.with_name(database.name + suffix)
                for suffix in ("-wal", "-shm", "-journal")
            ]
            try:
                protected = output in protected_paths or (
                    output.exists() and output.samefile(database)
                )
            except OSError as exc:
                raise OutputError("출력 경로를 확인할 수 없습니다.") from exc
            if protected:
                raise OutputError("사용 중인 SQLite 저장소를 출력 파일로 지정할 수 없습니다.")
            with repository.read_snapshot():
                return ExportService(repository, FileReviewExporter()).export_reviews(request)

    return build_export_handler(execute)(args)


def _pipeline_handler(
    handler_factory: Callable[[Callable[[RequestT], ResultT]], CommandHandler],
    service_factory: ServiceFactory[RequestT, ResultT],
) -> CommandHandler:
    def handler(args: argparse.Namespace) -> int:
        def execute(request: RequestT) -> ResultT:
            # Request validation happens in the adapter before opening the DB.
            # The service borrows this connection; this context owns its lifetime.
            with SQLiteReviewRepository.from_config(args.app_config) as repository:
                try:
                    operation = service_factory(repository, args.app_config)
                    return operation(request)
                except ImportError:
                    raise ConfigError(
                        f"'{args.command}' 명령 실행에 필요한 패키지를 불러올 수 없습니다. "
                        "requirements.txt의 의존성을 확인하세요."
                    ) from None

        return handler_factory(execute)(args)

    return handler


def _default_import_factory(
    repository: ReviewRepository, config: Mapping[str, Any],
) -> Callable[[ImportRequest], BatchOperationResult]:
    from src import collector
    from src.import_service import ImportService

    return ImportService(repository, collector).import_reviews


def _default_clean_factory(
    repository: ReviewRepository, config: Mapping[str, Any],
) -> Callable[[CleanRequest], CleanBatchResult]:
    from src import cleaner
    from src.clean_service import CleanService

    return CleanService(repository, cleaner).clean_reviews


def build_default_handlers(
    *,
    import_factory: ServiceFactory[ImportRequest, BatchOperationResult] | None = _default_import_factory,
    clean_factory: ServiceFactory[CleanRequest, CleanBatchResult] | None = _default_clean_factory,
    dashboard_factory: ServiceFactory[DashboardRequest, DashboardResult] | None = None,
) -> dict[str, CommandHandler]:
    """Register default import/clean and existing commands, plus supplied services.

    Factories run lazily after argument validation with a fresh repository and
    the loaded config. They return one service method, not a full application.
    Import and clean use their services by default. Dashboard still needs a
    factory. Explicit None disables that pipeline command (exit code 2).
    """
    handlers = {
        "analyze": _analyze_handler,
        "extract": _extract_handler,
        "export": _export_handler,
        "list": _query_handler(build_list_handler, QueryService.list_reviews),
        "show": _query_handler(build_show_handler, QueryService.show_review),
        "stats": _query_handler(build_stats_handler, QueryService.get_statistics),
    }
    for name, factory, adapter in (
        ("import", import_factory, build_import_handler),
        ("clean", clean_factory, build_clean_handler),
        ("dashboard", dashboard_factory, build_dashboard_handler),
    ):
        if factory is not None:
            handlers[name] = _pipeline_handler(adapter, factory)
    return handlers
