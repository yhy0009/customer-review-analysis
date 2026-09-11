"""Compose executable commands lazily from the loaded application config.

Each query owns one repository connection. Importing this module does not open a
DB, import an AI SDK or create clients for commands that have not been connected.
"""
from __future__ import annotations

import argparse
from typing import Any, Callable

from src.cli import CommandHandler
from src.handlers import build_list_handler, build_show_handler, build_stats_handler
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


def build_default_handlers() -> dict[str, CommandHandler]:
    return {
        "list": _query_handler(build_list_handler, QueryService.list_reviews),
        "show": _query_handler(build_show_handler, QueryService.show_review),
        "stats": _query_handler(build_stats_handler, QueryService.get_statistics),
    }
