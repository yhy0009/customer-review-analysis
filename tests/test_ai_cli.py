"""Exercise default AI command wiring with real SQLite and an offline SDK stub."""

import contextlib
import io
import json
import csv
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.cli import main
from src.config import reset_logging
from src.errors import StorageError
from src.models import AnalysisResult, CleanReview, DuplicatePolicy, RawReview, Sentiment
from src.sqlite_repository import SQLiteReviewRepository


ANALYSIS_PAYLOAD = {
    "sentiment": "negative", "confidence": .85,
    "summary": "배송이 늦었습니다.", "keywords": ["배송", "포장"],
}
INSIGHT_PAYLOAD = {
    "issues": ["배송 지연"],
    "improvement_suggestions": ["출고 일정을 점검하세요."],
    "summary": "일부 리뷰에서 배송 불편이 보고됩니다.",
}


def response(payload):
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(model="test-response-model", choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=content, refusal=None),
    )])


class AiCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(reset_logging)
        self.root = Path(self.temp.name)
        self.database = self.root / "reviews.db"
        self.config_path = self.root / "config.json"
        self.config = {
            "storage": {"database_path": str(self.database)},
            "ai": {
                "provider": "openai", "model": "test-request-model",
                "api_key": "test-private-api-key", "base_url": None,
                "reasoning_effort": "high", "timeout_seconds": 17, "max_retries": 0,
                # Future config fields must not be unpacked into AnalysisOptions.
                "future_option": {"enabled": True},
            },
            "logging": {"file": None, "level": "WARNING"},
        }
        self.client = Mock()
        self.client.chat.completions.create.return_value = response(ANALYSIS_PAYLOAD)
        self.opened = []

    def seed(self):
        now = datetime(2026, 9, 14, tzinfo=timezone.utc)
        with SQLiteReviewRepository(self.database) as repository:
            repository.save_raw_reviews([
                RawReview(source_review_id=str(i)) for i in range(1, 7)
            ], DuplicatePolicy.SKIP)
            repository.save_clean_reviews([
                CleanReview(id=i, source_review_id=str(i),
                            product_name="다른 제품" if i == 4 else "이어폰",
                            rating=2 if i in (3, 4) else 4,
                            review_date=date(2026, 9, i),
                            review_text=f"배송 리뷰 {i}", cleaned_at=now)
                for i in range(1, 7)
            ], DuplicatePolicy.SKIP)
            for i, sentiment in ((3, Sentiment.NEGATIVE), (4, Sentiment.NEGATIVE),
                                 (5, Sentiment.POSITIVE), (6, Sentiment.NEUTRAL)):
                repository.save_analysis(AnalysisResult(
                    review_id=i, sentiment=sentiment, confidence=.8, analyzed_at=now,
                    provider="old", model="old-model", prompt_version="old-prompt",
                    keywords=["배송", "포장"], summary="기존 요약",
                ))
            repository.mark_analysis_failed(2, "이전 분석 실패")

    def run_cli(self, *args, **kwargs):
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        self.opened = []
        original_from_config = SQLiteReviewRepository.from_config

        def open_repository(config):
            repository = original_from_config(config)
            self.opened.append(repository)
            return repository

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            stack.enter_context(patch("src.config.PROJECT_ROOT", self.root))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            stack.enter_context(patch.object(SQLiteReviewRepository, "from_config",
                                             side_effect=open_repository))
            sdk = stack.enter_context(patch("src.ai_provider.OpenAI"))
            sdk.return_value.__enter__.return_value = self.client
            try:
                code = main(["--config", str(self.config_path), *args], **kwargs)
            finally:
                reset_logging()
        for repository in self.opened:
            with self.assertRaises(StorageError):
                repository.fetch_clean_reviews()
        return SimpleNamespace(code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue(), sdk=sdk)

    def read_details(self):
        with SQLiteReviewRepository(self.database) as repository:
            return [repository.get_review(i) for i in range(1, 7)]

    def insight_response(self, **kwargs):
        body = json.loads(kwargs["messages"][1]["content"])
        if "evidence" not in body:
            return response({"reviews": [{"review_number": i, "complaints": [
                {"label": "배송 지연", "quote": review["review_text"]}], "praises": []}
                for i, review in enumerate(body["reviews"], 1)]})
        return response(dict(INSIGHT_PAYLOAD,
                             improvement_suggestions=body["suggestion_candidates"][:1]))

    def test_extract_is_persisted_and_reused_by_markdown_text_and_html_without_api(self):
        from src.web_dashboard import load_insight_artifact

        self.seed()
        before = self.read_details()
        self.client.chat.completions.create.side_effect = self.insight_response
        result = self.run_cli("extract", "--sentiment", "negative", "--limit", "2")
        self.assertEqual(result.code, 0, result.stderr)
        path = Path(next(line.split(": ", 1)[1] for line in result.stdout.splitlines()
                         if line.startswith("인사이트 파일: ")))
        self.assertTrue(path.is_file())
        artifact = load_insight_artifact(path)
        self.assertEqual(artifact["review_ids"], [3, 4])
        self.assertEqual(artifact["generation_profile"]["model"], "test-request-model")
        self.assertNotIn("test-private-api-key", path.read_text())
        self.assertEqual(self.read_details(), before)
        self.config["ai"]["api_key"] = None
        self.client.chat.completions.create.reset_mock()
        for fmt in ("md", "txt"):
            directory = self.root / f"reports-{fmt}"
            result = self.run_cli("dashboard", "--output", str(directory), "--report-format", fmt, "--html")
            self.assertEqual(result.code, 0, result.stderr)
            self.assertIn("리포트에 저장된 AI 인사이트를 포함했습니다", result.stdout)
            self.assertIn("실제 추출 대상: 분석 완료 리뷰 2건", result.stdout)
            result.sdk.assert_not_called()
            self.client.chat.completions.create.assert_not_called()
            for suffix in (fmt, "html"):
                content = next(directory.glob(f"*.{suffix}")).read_text()
                self.assertIn("일부 리뷰에서 배송 불편이 보고됩니다", content)
                self.assertIn("배송 지연", content)
                self.assertIn("증상을 재현해 점검", content)
                self.assertIn("리뷰 3", content)
                self.assertIn("대상 범위는 서로 다를 수 있습니다", content)
        self.assertEqual(self.read_details(), before)

    def test_optional_body_only_csv_runs_through_extract_and_report(self):
        path = self.root / "body-only.csv"
        path.write_text("review_text\n배송이 늦어서 아쉬웠습니다.\n", encoding="utf-8-sig")
        for arguments in (("import", "--file", str(path)), ("clean",), ("analyze", "--unanalyzed")):
            result = self.run_cli(*arguments)
            self.assertEqual(result.code, 0, result.stderr)
        self.client.chat.completions.create.side_effect = self.insight_response
        result = self.run_cli("extract")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("제품명 없음", result.stdout)
        saved = next((self.root / "output/insights").glob("*/*.json"))
        self.assertIsNone(json.loads(saved.read_bytes())["selection_limit"])
        output = self.root / "body-reports"
        result = self.run_cli("dashboard", "--output", str(output), "--html", "--insight-file", str(saved))
        self.assertEqual(result.code, 0, result.stderr)
        result.sdk.assert_not_called()
        report = next(output.glob("*.md")).read_text()
        self.assertIn("제품명 없음", report)
        self.assertIn("N/A", report)
        self.assertIn("배송이 늦어서 아쉬웠습니다", report)

    def test_invalid_explicit_insight_stops_before_generating_files_and_no_insights_skips_cache(self):
        self.seed()
        self.client.chat.completions.create.side_effect = self.insight_response
        self.assertEqual(self.run_cli("extract").code, 0)
        path = next((self.root / "output/insights").glob("*/*.json"))
        output = self.root / "explicit-invalid"
        path.write_text("broken JSON")
        result = self.run_cli("dashboard", "--output", str(output), "--insight-file", str(path))
        self.assertEqual(result.code, 2, result.stderr)
        self.assertFalse(output.exists())
        result.sdk.assert_not_called()
        result = self.run_cli("dashboard", "--output", str(output), "--no-insights")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("--no-insights로 생략", result.stdout)
        self.assertNotIn("일부 리뷰에서 배송 불편", next(output.glob("*.md")).read_text())

    def test_unwritable_extract_output_fails_before_ai_and_failed_replacement_keeps_old_json(self):
        self.seed()
        blocked = self.root / "blocked-output"
        blocked.write_text("existing file")
        self.config["paths"] = {"output_dir": str(blocked)}
        result = self.run_cli("extract")
        self.assertEqual(result.code, 3, result.stderr)
        result.sdk.assert_not_called()
        self.config["paths"]["output_dir"] = str(self.root / "output")
        self.client.chat.completions.create.side_effect = self.insight_response
        self.assertEqual(self.run_cli("extract").code, 0)
        path = next((self.root / "output/insights").glob("*/*.json"))
        before = path.read_bytes()
        with patch("src.cli_insights.os.replace", side_effect=OSError("disk failure")):
            result = self.run_cli("extract")
        self.assertEqual(result.code, 3, result.stderr)
        self.assertNotIn("인사이트 파일:", result.stdout)
        self.assertEqual(path.read_bytes(), before)
        self.client.chat.completions.create.side_effect = None
        self.client.chat.completions.create.return_value = response("invalid AI JSON")
        result = self.run_cli("extract")
        self.assertEqual(result.code, 4, result.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_multilingual_csv_import_clean_analyze_and_export_preserve_original_text(self):
        reviews = [
            ('한국어 제품', '연결이 안정적이고 음질도 좋아서 만족합니다.', 'positive'),
            ('English Speaker', "It doesn't hold a charge. I can't recommend it.", 'negative'),
            ('혼합 Speaker', '아직 unopened 상태라 performance는 모르겠어요.', 'neutral'),
        ]
        input_path = self.root / 'multilingual.csv'
        with input_path.open('w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['review_id', 'product_name', 'review_date', 'rating', 'review_text'])
            for index, (product, text, _) in enumerate(reviews, 1):
                writer.writerow([f'multi-{index}', product, '2026-09-01', 3, text])
        for args in [('import', '--file', str(input_path)), ('clean',)]:
            result = self.run_cli(*args)
            self.assertEqual(result.code, 0, result.stderr)
            result.sdk.assert_not_called()
        def complete(**kwargs):
            body = json.loads(kwargs['messages'][1]['content'])
            self.assertEqual(set(body), {'product_name', 'rating', 'review_text'})
            product, text, label = next(row for row in reviews if row[1] == body['review_text'])
            self.assertEqual(body['product_name'], product)
            return response({'sentiment': label, 'confidence': .8, 'summary': '검증용 한국어 요약입니다.', 'keywords': ['음질']})
        self.client.chat.completions.create.side_effect = complete
        result = self.run_cli('analyze', '--unanalyzed')
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn('succeeded=3', result.stdout)
        self.assertEqual(self.client.chat.completions.create.call_count, 3)
        with SQLiteReviewRepository(self.database) as repo:
            details = [repo.get_review(index) for index in range(1, 4)]
            for detail, (_, text, label) in zip(details, reviews):
                self.assertEqual(detail.review.review_text, text)
                self.assertEqual(detail.analysis.sentiment.value, label)
                self.assertEqual(detail.analysis.prompt_version, 'review-sentiment-v2-multilingual')
            self.assertEqual(repo.get_statistics().sentiment_counts, {sentiment: 1 for sentiment in Sentiment})
        result = self.run_cli('analyze', '--all')
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn('skipped=3', result.stdout)
        result.sdk.assert_not_called()
        output = self.root / 'multilingual.jsonl'
        result = self.run_cli('export', '--format', 'jsonl', '--output', str(output))
        self.assertEqual(result.code, 0, result.stderr)
        exported = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual({row['review_text'] for row in exported}, {row[1] for row in reviews})
        self.assertEqual({row['prompt_version'] for row in exported}, {'review-sentiment-v2-multilingual'})

    def test_multilingual_excel_import_clean_and_analyze(self):
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.append(['review_id', 'product_name', 'review_date', 'rating', 'review_text'])
        text = "Not what I hoped for. 배터리가 doesn't last even an hour."
        workbook.active.append(['mixed-excel', 'Mixed Speaker', '2026-09-01', 1, text])
        input_path = self.root / 'mixed.xlsx'
        workbook.save(input_path)
        for args in [('import', '--file', str(input_path)), ('clean',), ('analyze', '--unanalyzed')]:
            result = self.run_cli(*args)
            self.assertEqual(result.code, 0, result.stderr)
        call = self.client.chat.completions.create.call_args
        self.assertEqual(json.loads(call.kwargs['messages'][1]['content'])['review_text'], text)
        with SQLiteReviewRepository(self.database) as repo:
            self.assertEqual(repo.get_review(1).review.review_text, text)
            self.assertEqual(repo.get_review(1).analysis.prompt_version, 'review-sentiment-v2-multilingual')

    def test_analyze_all_limit_skips_existing_and_saves_configured_result(self):
        self.seed()
        result = self.run_cli("analyze", "--all", "--limit", "3")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("processed=3 succeeded=2 skipped=1 failed=0", result.stdout)
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(result.sdk.call_count, 2)
        result.sdk.assert_called_with(api_key="test-private-api-key",
                                      base_url="https://api.openai.com/v1", timeout=17, max_retries=0)
        calls = self.client.chat.completions.create.call_args_list
        self.assertEqual([json.loads(c.kwargs["messages"][1]["content"])["review_text"]
                          for c in calls], ["배송 리뷰 1", "배송 리뷰 2"])
        self.assertTrue(all(c.kwargs["model"] == "test-request-model" for c in calls))
        self.assertTrue(all(c.kwargs["reasoning_effort"] == "high" for c in calls))
        details = self.read_details()
        for detail in details[:2]:
            self.assertEqual(detail.analysis.sentiment, Sentiment.NEGATIVE)
            self.assertEqual(detail.analysis.confidence, .85)
            self.assertEqual(detail.analysis.keywords, ["배송", "포장"])
            self.assertEqual(detail.analysis.model, "test-response-model")
            self.assertEqual(detail.analysis.prompt_version, "review-sentiment-v2-multilingual")
        self.assertEqual(details[2].analysis.model, "old-model")
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual(repository.fetch_unanalyzed_reviews(), [])

    def test_analyze_single_id_force_replaces_only_requested_analysis(self):
        self.seed()
        before = self.read_details()
        self.config["ai"]["api_key"] = None
        result = self.run_cli("analyze", "--id", "5")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("processed=1 succeeded=0 skipped=1 failed=0", result.stdout)
        result.sdk.assert_not_called()
        self.config["ai"]["api_key"] = "test-private-api-key"
        result = self.run_cli("analyze", "--id", "5", "--force")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("processed=1 succeeded=1 skipped=0 failed=0", result.stdout)
        after = self.read_details()
        self.assertEqual(after[4].analysis.model, "test-response-model")
        self.assertEqual(before[:4] + before[5:], after[:4] + after[5:])

    def test_unanalyzed_limit_includes_previous_failure_and_force_keeps_selection(self):
        self.seed()
        result = self.run_cli("analyze", "--unanalyzed", "--limit", "1", "--force")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("processed=1 succeeded=1 skipped=0 failed=0", result.stdout)
        self.client.chat.completions.create.reset_mock()
        result = self.run_cli("analyze", "--unanalyzed", "--force")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertIn("processed=1 succeeded=1 skipped=0 failed=0", result.stdout)
        call = self.client.chat.completions.create.call_args
        self.assertEqual(json.loads(call.kwargs["messages"][1]["content"])["review_text"], "배송 리뷰 2")
        self.assertEqual(self.read_details()[2].analysis.model, "old-model")

    def test_analyze_partial_and_total_failures_return_one_and_persist_safe_status(self):
        self.seed()
        self.client.chat.completions.create.side_effect = [
            response(ANALYSIS_PAYLOAD), response("private invalid provider content"),
        ]
        result = self.run_cli("analyze", "--unanalyzed")
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn("processed=2 succeeded=1 skipped=0 failed=1", result.stdout)
        self.assertNotIn("private invalid provider content", result.stdout + result.stderr)
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual([r.id for r in repository.fetch_unanalyzed_reviews()], [2])
            self.assertEqual(repository.get_review(1).analysis.model, "test-response-model")
            self.assertIsNone(repository.get_review(2).analysis)
        self.client.chat.completions.create.side_effect = None
        self.client.chat.completions.create.return_value = response("private invalid provider content")
        result = self.run_cli("analyze", "--unanalyzed")
        self.assertEqual(result.code, 1, result.stderr)
        self.assertIn("processed=1 succeeded=0 skipped=0 failed=1", result.stdout)
        with sqlite3.connect(self.database) as connection:
            dump = "\n".join(connection.iterdump())
        self.assertNotIn("private invalid provider content", dump)
        self.assertNotIn("test-private-api-key", dump)

    def test_empty_targets_succeed_without_api_key_or_client(self):
        self.config["ai"]["api_key"] = None
        for arguments in (("analyze", "--all"), ("extract",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.code, 0, result.stderr)
                result.sdk.assert_not_called()
                expected = ("processed=0 succeeded=0 skipped=0 failed=0"
                            if arguments[0] == "analyze" else "실제 추출 대상: 분석 완료 리뷰 0건")
                self.assertIn(expected, result.stdout)
        self.assertEqual(len(self.opened), 1)

    def test_nonempty_targets_require_api_key_and_preserve_data(self):
        self.seed()
        before = self.read_details()
        self.config["ai"]["api_key"] = None
        for arguments in (("analyze", "--unanalyzed"), ("extract",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.code, 2, result.stderr)
                self.assertIn("AI_API_KEY", result.stderr)
                result.sdk.assert_not_called()
                self.assertEqual(self.read_details(), before)

    def test_extract_filters_limit_and_prints_insights_without_changing_analyses(self):
        self.seed()
        before = self.read_details()
        self.client.chat.completions.create.side_effect = self.insight_response
        result = self.run_cli("extract", "--product", "이어", "--sentiment", "negative",
                              "--date-from", "2026-09-03", "--date-to", "2026-09-05", "--limit", "1")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.client.chat.completions.create.call_count, 2)
        for request in self.client.chat.completions.create.call_args_list:
            self.assertEqual(request.kwargs["max_completion_tokens"], 8192)
        call = self.client.chat.completions.create.call_args
        content = json.loads(call.kwargs["messages"][1]["content"])
        self.assertEqual(content["review_count"], 1)
        self.assertEqual([r["review_text"] for r in content["reviews"]], ["배송 리뷰 3"])
        self.assertEqual(content["negative_keywords"], [
            {"keyword": "배송", "count": 1}, {"keyword": "포장", "count": 1},
        ])
        for text in ("실제 추출 대상: 분석 완료 리뷰 1건", "배송", "포장",
                     "배송 지연", "발생 조건을 확인하고", INSIGHT_PAYLOAD["summary"]):
            self.assertIn(text, result.stdout)
        self.assertEqual(self.read_details(), before)

    def test_extract_limit_counts_only_analyzed_rows_across_pages(self):
        self.seed()
        self.client.chat.completions.create.side_effect = self.insight_response
        with patch("src.insight_service._PAGE_SIZE", 2):
            result = self.run_cli("extract", "--limit", "2")
        self.assertEqual(result.code, 0, result.stderr)
        content = json.loads(self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(content["review_count"], 2)
        self.assertEqual([r["review_text"] for r in content["reviews"]], ["배송 리뷰 3", "배송 리뷰 4"])

    def test_extract_snapshot_is_active_during_reads_and_released_before_ai(self):
        self.seed()
        states = []
        original_read = SQLiteReviewRepository.list_reviews

        def read(repository, query):
            states.append(repository._require_connection().in_transaction)
            return original_read(repository, query)

        def complete(**kwargs):
            self.assertFalse(self.opened[-1]._require_connection().in_transaction)
            # A second writer can commit even with the default rollback journal.
            with sqlite3.connect(self.database, timeout=.1) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS cli_snapshot_probe (id INTEGER)")
            return self.insight_response(**kwargs)

        self.client.chat.completions.create.side_effect = complete
        with patch.object(SQLiteReviewRepository, "list_reviews", read):
            result = self.run_cli("extract")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertTrue(states)
        self.assertTrue(all(states))
        self.assertEqual(self.client.chat.completions.create.call_count, 2)

    def test_extract_failure_returns_four_without_modifying_analysis_or_status(self):
        self.seed()
        before = self.read_details()
        self.client.chat.completions.create.return_value = response("private invalid provider content")
        result = self.run_cli("extract")
        self.assertEqual(result.code, 4, result.stderr)
        self.assertNotIn("private invalid provider content", result.stdout + result.stderr)
        self.assertNotIn("test-private-api-key", result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.read_details(), before)
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual([r.id for r in repository.fetch_unanalyzed_reviews()], [1, 2])

    def test_openai_compatible_options_reach_existing_provider(self):
        self.seed()
        self.config["ai"].update(provider="openai-compatible", base_url="http://localhost:8080/v1/")
        result = self.run_cli("analyze", "--id", "1")
        self.assertEqual(result.code, 0, result.stderr)
        result.sdk.assert_called_once_with(api_key="test-private-api-key",
                                            base_url="http://localhost:8080/v1", timeout=17, max_retries=0)
        self.assertEqual(set(self.client.chat.completions.create.call_args.kwargs), {"model", "messages"})
        self.assertEqual(self.read_details()[0].analysis.provider, "openai-compatible")

    def test_invalid_request_or_ai_options_fail_before_opening_database(self):
        result = self.run_cli("extract", "--date-from", "2026-09-14", "--date-to", "2026-09-01")
        self.assertEqual(result.code, 2, result.stderr)
        self.assertFalse(self.database.exists())
        self.assertEqual(self.opened, [])
        self.config["ai"]["model"] = " "
        for arguments in (("analyze", "--all"), ("extract",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.code, 2, result.stderr)
                self.assertFalse(self.database.exists())
                result.sdk.assert_not_called()

    def test_unsupported_provider_fails_before_opening_database(self):
        self.config["ai"]["provider"] = "unsupported"
        for arguments in (("analyze", "--all"), ("extract",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.code, 2, result.stderr)
                self.assertFalse(self.database.exists())
                result.sdk.assert_not_called()

    def test_corrupt_database_returns_three_without_calling_ai(self):
        self.database.write_bytes(b"not a SQLite database")
        for arguments in (("analyze", "--all"), ("extract",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.code, 3, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                result.sdk.assert_not_called()

    def test_missing_review_id_returns_two_and_closes_connection(self):
        self.seed()
        result = self.run_cli("analyze", "--id", "999")
        self.assertEqual(result.code, 2, result.stderr)
        self.assertEqual(len(self.opened), 1)
        result.sdk.assert_not_called()

    def test_explicit_empty_handler_map_keeps_default_runtime_disabled(self):
        result = self.run_cli("analyze", "--all", handlers={})
        self.assertEqual(result.code, 2)
        self.assertIn("아직 연결되지", result.stderr)
        self.assertFalse(self.database.exists())
        self.assertEqual(self.opened, [])
        result.sdk.assert_not_called()


class AiCliWithoutSdkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / "src", self.root / "src", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(source / "main.py", self.root / "main.py")
        (self.root / "config").mkdir()
        (self.root / "config/config.json").write_text('{"logging": {"file": null}}', encoding="utf-8")
        self.environment = {k: v for k, v in os.environ.items()
                            if k in {"PATH", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}}
        self.environment["PYTHONDONTWRITEBYTECODE"] = "1"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-S", str(self.root / "main.py"), *args],
                              cwd=self.temp.name, env=self.environment,
                              text=True, capture_output=True, timeout=20)

    def test_ai_commands_report_missing_dependency_without_traceback_or_database(self):
        for arguments in (("analyze", "--all"), ("extract",)):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("아직 연결되지", result.stderr)
                self.assertFalse((self.root / "data/app_database.db").exists())

    def test_query_and_export_commands_run_without_ai_sdk(self):
        cases = (("list",), ("stats",), ("show", "--id", "999"),
                 ("export", "--format", "jsonl", "--output", "output/reviews.jsonl"))
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 1 if arguments[0] == "show" else 0, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        self.assertEqual((self.root / "output/reviews.jsonl").read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
