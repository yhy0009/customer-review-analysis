"""Tests for repository, service, and CLI adapter contracts."""

import argparse
import contextlib
import io
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.cli import parse_args
from src.errors import (
    AIProviderError,
    AppError,
    ConfigError,
    InputFileError,
    OutputError,
    StorageError,
    ValidationError,
)
from src.handlers import build_handlers
from src.models import (
    AnalysisBatchResult,
    AnalyzeRequest,
    AnalyzeTarget,
    BatchOperationResult,
    CleanBatchResult,
    CleanRequest,
    DashboardRequest,
    DashboardResult,
    DuplicatePolicy,
    ExportFormat,
    ExportRequest,
    ExportResult,
    ExtractRequest,
    ImportRequest,
    InsightResult,
    ItemError,
    ListRequest,
    OutputArtifact,
    OutputKind,
    Page,
    ReportFormat,
    ReviewStatistics,
    ShowRequest,
    StatsRequest,
)
from src.services import (
    ApplicationServices,
    InsightExtractor,
    ReportGenerator,
    ReviewAnalyzer,
    ReviewCleaner,
    ReviewCollector,
    ReviewExporter,
    ReviewVisualizer,
)
from src.storage import ReviewRepository


UTC_NOW = datetime(2026, 9, 6, 1, 2, 3, tzinfo=timezone.utc)
TEST_CONFIG = {
    "paths": {"output_dir": "build/output"},
    "cleaning": {"duplicate_policy": "skip", "min_review_length": 3},
}


def empty_statistics() -> ReviewStatistics:
    return ReviewStatistics(0, 0, 0, 0)


class FakeRepository:
    def save_raw_reviews(self, reviews, policy):
        return BatchOperationResult(processed=0, succeeded=0)

    def fetch_raw_reviews(self, *, status=None):
        return []

    def save_clean_reviews(self, reviews, policy):
        return BatchOperationResult(processed=0, succeeded=0)

    def fetch_clean_reviews(self, filters=None, *, limit=None):
        return []

    def fetch_unanalyzed_reviews(self, *, limit=None):
        return []

    def save_analysis(self, result):
        return None

    def mark_analysis_failed(self, review_id, error_message):
        return None

    def get_review(self, review_id):
        return None

    def list_reviews(self, query):
        return Page([], query.page, query.size, 0, 0)

    def get_statistics(self, filters=None):
        return empty_statistics()


class FakeServices:
    def __init__(self) -> None:
        self.calls = []
        self.error = None
        self.import_result = BatchOperationResult(processed=0, succeeded=0)

    def _record(self, name, request):
        self.calls.append((name, request))
        if self.error is not None:
            raise self.error

    def import_reviews(self, request):
        self._record("import", request)
        return self.import_result

    def clean_reviews(self, request):
        self._record("clean", request)
        return CleanBatchResult(processed=0, succeeded=0)

    def analyze_reviews(self, request):
        self._record("analyze", request)
        return AnalysisBatchResult(processed=0, succeeded=0)

    def extract_insights(self, request):
        self._record("extract", request)
        return InsightResult(request.filters, 0, UTC_NOW)

    def list_reviews(self, request):
        self._record("list", request)
        return Page([], request.query.page, request.query.size, 0, 0)

    def show_review(self, request):
        self._record("show", request)
        return None

    def get_statistics(self, request):
        self._record("stats", request)
        return empty_statistics()

    def create_dashboard(self, request):
        self._record("dashboard", request)
        return DashboardResult([], empty_statistics())

    def export_reviews(self, request):
        self._record("export", request)
        artifact = OutputArtifact(OutputKind.EXPORT, request.output, request.format.value)
        return ExportResult(artifact, 0)


class FakeFeatureModules:
    def load_reviews(self, path, column_overrides=None):
        return []

    def clean_reviews(self, reviews, options):
        return CleanBatchResult(processed=0, succeeded=0)

    def analyze_review(self, review, options):
        raise NotImplementedError

    def analyze_reviews(self, reviews, options, *, force=False):
        return AnalysisBatchResult(processed=0, succeeded=0)

    def extract_insights(self, reviews, filters, *, limit=None):
        return InsightResult(filters, 0, UTC_NOW)

    def generate_dashboard(
        self, statistics, output, *, font_family, dpi, force=False
    ):
        return []

    def generate_report(
        self, statistics, insight, output, *, report_format, force=False
    ):
        return OutputArtifact(OutputKind.REPORT, output, report_format.value)

    def export_reviews(self, reviews, output, *, export_format, force=False):
        artifact = OutputArtifact(OutputKind.EXPORT, output, export_format.value)
        return ExportResult(artifact, 0)


class ProtocolContractTests(unittest.TestCase):
    def test_fake_implementations_satisfy_runtime_protocols(self) -> None:
        self.assertIsInstance(FakeRepository(), ReviewRepository)
        self.assertIsInstance(FakeServices(), ApplicationServices)

        features = FakeFeatureModules()
        for protocol in (
            ReviewCollector,
            ReviewCleaner,
            ReviewAnalyzer,
            InsightExtractor,
            ReviewVisualizer,
            ReportGenerator,
            ReviewExporter,
        ):
            with self.subTest(protocol=protocol.__name__):
                self.assertIsInstance(features, protocol)


class HandlerAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.services = FakeServices()
        self.handlers = build_handlers(self.services)

    def invoke(self, argv):
        args = parse_args(argv)
        args.app_config = TEST_CONFIG
        exit_code = self.handlers[args.command](args)
        return exit_code, self.services.calls[-1][1]

    def test_all_commands_are_registered(self) -> None:
        self.assertEqual(
            set(self.handlers),
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

    def test_commands_are_converted_to_typed_requests(self) -> None:
        cases = (
            (["import", "--file", "data/reviews.csv"], ImportRequest, 0),
            (["clean"], CleanRequest, 0),
            (["analyze", "--unanalyzed"], AnalyzeRequest, 0),
            (["extract"], ExtractRequest, 0),
            (["list"], ListRequest, 0),
            (["show", "--id", "1"], ShowRequest, 1),
            (["stats"], StatsRequest, 0),
            (["dashboard"], DashboardRequest, 0),
            (
                ["export", "--format", "csv", "--output", "output/out.csv"],
                ExportRequest,
                0,
            ),
        )

        for argv, request_type, expected_code in cases:
            with self.subTest(command=argv[0]):
                exit_code, request = self.invoke(argv)
                self.assertEqual(exit_code, expected_code)
                self.assertIsInstance(request, request_type)

    def test_cli_values_override_config_defaults(self) -> None:
        _, imported = self.invoke(
            ["import", "--file", "data/reviews.csv", "--policy", "upsert"]
        )
        _, cleaned = self.invoke(["clean", "--policy", "upsert", "--min-length", "9"])

        self.assertEqual(imported.policy, DuplicatePolicy.UPSERT)
        self.assertEqual(cleaned.options.policy, DuplicatePolicy.UPSERT)
        self.assertEqual(cleaned.options.min_length, 9)

    def test_analyze_target_and_force_are_preserved(self) -> None:
        _, request = self.invoke(["analyze", "--id", "7", "--force"])

        self.assertEqual(request.target, AnalyzeTarget.REVIEW_ID)
        self.assertEqual(request.review_id, 7)
        self.assertTrue(request.force)

    def test_dashboard_uses_config_output_and_export_passes_force(self) -> None:
        _, dashboard = self.invoke(["dashboard", "--report-format", "txt", "--force"])
        _, exported = self.invoke(
            [
                "export",
                "--format",
                "jsonl",
                "--output",
                "output/out.jsonl",
                "--force",
            ]
        )

        self.assertEqual(dashboard.output.name, "output")
        self.assertEqual(dashboard.report_format, ReportFormat.TXT)
        self.assertTrue(dashboard.force)
        self.assertEqual(exported.format, ExportFormat.JSONL)
        self.assertTrue(exported.force)
        self.assertTrue(exported.output.is_absolute())

    def test_partial_batch_result_returns_one(self) -> None:
        self.services.import_result = BatchOperationResult(
            processed=2,
            succeeded=1,
            failed=1,
            errors=[ItemError("2", "FAILED", "실패")],
        )

        exit_code, _ = self.invoke(["import", "--file", "data/reviews.csv"])

        self.assertEqual(exit_code, 1)

    def test_expected_errors_map_to_exit_codes(self) -> None:
        cases = (
            (ConfigError("설정 실패"), 2),
            (ValidationError("잘못된 요청"), 2),
            (InputFileError("입력 실패"), 3),
            (StorageError("저장 실패"), 3),
            (OutputError("출력 실패"), 3),
            (AIProviderError("AI 실패"), 4),
            (AppError("기타 실패"), 1),
        )

        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                self.services.error = error
                args = parse_args(["stats"])
                args.app_config = TEST_CONFIG
                with contextlib.redirect_stderr(io.StringIO()):
                    exit_code = self.handlers["stats"](args)
                self.assertEqual(exit_code, expected_code)


if __name__ == "__main__":
    unittest.main()
