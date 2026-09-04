"""Tests for the command-line interface contract."""

import argparse
import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

from src.cli import build_parser, dispatch, main, parse_args
from src.config import ConfigError


class CliParserTests(unittest.TestCase):
    def test_all_required_subcommands_are_registered(self) -> None:
        parser = build_parser()
        subparsers_action = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )

        self.assertEqual(
            set(subparsers_action.choices),
            {
                "import",
                "clean",
                "analyze",
                "extract",
                "list",
                "show",
                "stats",
                "dashboard",
                "export",
            },
        )

    def test_import_requires_and_parses_file(self) -> None:
        args = parse_args(["import", "--file", "data/sample_reviews.csv"])

        self.assertEqual(args.command, "import")
        self.assertEqual(args.file, Path("data/sample_reviews.csv"))

    def test_analyze_requires_exactly_one_target(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["analyze"])

        args = parse_args(["analyze", "--unanalyzed", "--limit", "20"])
        self.assertTrue(args.unanalyzed)
        self.assertEqual(args.limit, 20)

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["analyze", "--all", "--id", "1"])

    def test_list_parses_filters_and_pagination(self) -> None:
        args = parse_args(
            [
                "list",
                "--sentiment",
                "negative",
                "--rating",
                "2",
                "--date-from",
                "2026-01-01",
                "--page",
                "2",
                "--size",
                "5",
            ]
        )

        self.assertEqual(args.sentiment, "negative")
        self.assertEqual(args.rating, 2)
        self.assertEqual(args.date_from, "2026-01-01")
        self.assertEqual(args.page, 2)
        self.assertEqual(args.size, 5)

    def test_invalid_rating_and_date_are_rejected(self) -> None:
        invalid_arguments = (
            ["list", "--rating", "6"],
            ["list", "--date-from", "2026/01/01"],
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parse_args(arguments)

    def test_export_requires_format_and_output(self) -> None:
        args = parse_args(
            [
                "export",
                "--format",
                "jsonl",
                "--output",
                "output/reviews.jsonl",
                "--rating-min",
                "3",
            ]
        )

        self.assertEqual(args.format, "jsonl")
        self.assertEqual(args.output, Path("output/reviews.jsonl"))
        self.assertEqual(args.rating_min, 3)


class CliDispatchTests(unittest.TestCase):
    def test_dispatch_calls_injected_handler(self) -> None:
        received = []

        def handler(args: argparse.Namespace) -> None:
            received.append(args.command)

        args = parse_args(["stats"])
        exit_code = dispatch(args, handlers={"stats": handler})

        self.assertEqual(exit_code, 0)
        self.assertEqual(received, ["stats"])

    def test_dispatch_reports_unconnected_command(self) -> None:
        args = parse_args(["stats"])
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = dispatch(args)

        self.assertEqual(exit_code, 2)
        self.assertIn("아직 연결되지 않았습니다", stderr.getvalue())

    def test_main_loads_config_and_passes_it_to_handler(self) -> None:
        with mock.patch("src.cli.load_env_file"):
            with mock.patch(
                "src.cli.load_config",
                return_value={
                    "logging": {
                        "level": "INFO",
                        "file": None,
                        "max_bytes": 1024,
                        "backup_count": 1,
                    }
                },
            ):
                received = []

                def handler(args: argparse.Namespace) -> None:
                    received.append(args.app_config["logging"]["level"])

                exit_code = main(["stats"], handlers={"stats": handler})

        self.assertEqual(exit_code, 0)
        self.assertEqual(received, ["INFO"])

    def test_main_reports_configuration_error(self) -> None:
        stderr = io.StringIO()
        with mock.patch("src.cli.load_env_file"):
            with mock.patch(
                "src.cli.load_config",
                side_effect=ConfigError("테스트 설정 오류"),
            ):
                with contextlib.redirect_stderr(stderr):
                    exit_code = main(["stats"])

        self.assertEqual(exit_code, 2)
        self.assertIn("테스트 설정 오류", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
