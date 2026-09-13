"""Compose executable commands lazily from the loaded application config.

Each query or export owns one repository connection. Importing this module does
not open a DB, import an AI SDK or create clients for unconnected commands.
"""
from __future__ import annotations

import argparse
from typing import Any, Callable

from src.cli import CommandHandler
from src.errors import OutputError
from src.export_service import ExportService
from src.handlers import (
    build_export_handler,
    build_list_handler,
    build_show_handler,
    build_stats_handler,
)
from src.models import ExportRequest, ExportResult
from src.query_service import QueryService
from src.sqlite_repository import SQLiteReviewRepository


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


def build_default_handlers() -> dict[str, CommandHandler]:
    return {
        "export": _export_handler,
        "list": _query_handler(build_list_handler, QueryService.list_reviews),
        "show": _query_handler(build_show_handler, QueryService.show_review),
        "stats": _query_handler(build_stats_handler, QueryService.get_statistics),
    }
