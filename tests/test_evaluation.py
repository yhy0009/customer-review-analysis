"""Evaluation metrics, label isolation and full pipeline without network calls."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.errors import AIProviderError, ConfigError, OutputError, ValidationError
from src.evaluation import classification_metrics, load_dataset, run_evaluation
from src.models import AnalysisOptions
from tests.insight_fixtures import evidence_reply


DATASET = Path(__file__).resolve().parents[1] / "evaluation/reviews.v1.json"


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.provider = Mock(spec=AnalysisProvider)
        self.options = AnalysisOptions(provider="fake", model="fixture", timeout_seconds=5,
                                       max_retries=3, api_key="never-write-this-secret")
        self.dataset = load_dataset(DATASET)

    def fake_complete(self, messages, schema, options):
        self.assertEqual(options.max_retries, 0)
        body = json.loads(messages[1]["content"])
        self.assertNotIn("expected", body)
        self.assertNotIn("reason", body)
        self.assertNotIn("category", body)
        if "sentiment" in schema["properties"]:
            self.assertEqual(set(body), {"product_name", "review_text", "rating"})
            # Synthetic provider uses the frozen fixture as its oracle in tests only.
            expected = next(c["expected"] for c in self.dataset["cases"] if c["review_text"] == body["review_text"])
            response = {"sentiment": expected, "confidence": .8, "keywords": ["음질"], "summary": "테스트 요약"}
        elif "reviews" in schema["properties"]:
            return evidence_reply(messages)
        else:
            response = {"summary": "테스트 묶음 요약", "issues": ["배송 지연"], "improvement_suggestions": []}
        return ProviderResponse(json.dumps(response), "fixture-snapshot")

    def test_balanced_fixed_fixture_has_ids_and_reasons(self):
        self.assertEqual(len(self.dataset["cases"]), 18)
        for label in ("positive", "neutral", "negative"):
            self.assertEqual(sum(c["expected"] == label for c in self.dataset["cases"]), 6)
        self.assertEqual(len(self.dataset["sha256"]), 64)
        self.assertIn("sarcasm", {c["category"] for c in self.dataset["cases"]})

    def test_metrics_include_errors_and_unattempted_in_overall_denominator(self):
        rows = [dict(expected="positive", predicted="positive", status="ok"),
                dict(expected="positive", predicted="negative", status="ok"),
                dict(expected="negative", predicted=None, status="error"),
                dict(expected="neutral", predicted=None, status="not_run")]
        metrics = classification_metrics(rows)
        self.assertEqual(metrics["accuracy_all"], .25)
        self.assertEqual(metrics["accuracy_valid"], .5)
        self.assertEqual(metrics["confusion_matrix"]["negative"]["error"], 1)
        self.assertEqual(metrics["per_class"]["positive"]["recall"], .5)
        self.assertAlmostEqual(metrics["macro_f1"], (2/3)/3)
        self.assertIsNone(classification_metrics([])["accuracy_all"])

    def test_full_run_isolated_persists_metadata_and_does_not_leak_labels_or_key(self):
        self.provider.complete.side_effect = self.fake_complete
        result = run_evaluation(DATASET, self.root / "run", self.options, provider=self.provider)
        self.assertTrue(result["structural_checks_passed"])
        self.assertEqual(result["metrics"]["accuracy_all"], 1)
        self.assertEqual(result["metrics"]["macro_f1"], 1)
        self.assertEqual(result["narrative_review"], "pending")
        self.assertEqual(self.provider.complete.call_count, 20)
        self.assertEqual(result["latency"]["analysis_requests"], 18)
        saved = (self.root / "run/evaluation.json").read_text()
        self.assertNotIn(self.options.api_key, saved)
        self.assertIn("2026-09-01", saved)
        self.assertTrue((self.root / "run/report.md").exists())
        self.assertTrue((self.root / "run/report.txt").exists())
        self.assertEqual(json.loads(saved)["status"], "completed")

    def test_failures_are_not_hidden_by_valid_only_accuracy(self):
        def complete(messages, schema, options):
            if "sentiment" in schema["properties"] and "p01" == next(
                (c["id"] for c in self.dataset["cases"] if c["review_text"] == json.loads(messages[1]["content"]).get("review_text")), None):
                raise AIProviderError("private provider response")
            return self.fake_complete(messages, schema, options)
        self.provider.complete.side_effect = complete
        result = run_evaluation(DATASET, self.root / "run", self.options, provider=self.provider)
        self.assertEqual(result["metrics"]["errors"], 1)
        self.assertEqual(result["metrics"]["accuracy_valid"], 1)
        self.assertAlmostEqual(result["metrics"]["accuracy_all"], 17/18)
        self.assertFalse(result["structural_checks_passed"])
        self.assertEqual(result["insight"]["review_count"], 17)
        self.assertNotIn("private provider response", (self.root / "run/evaluation.json").read_text())

    def test_insight_failure_still_writes_statistics_reports(self):
        def complete(messages, schema, options):
            if "sentiment" not in schema["properties"]:
                raise AIProviderError("private")
            return self.fake_complete(messages, schema, options)
        self.provider.complete.side_effect = complete
        result = run_evaluation(DATASET, self.root / "run", self.options, provider=self.provider)
        self.assertFalse(result["structural_checks_passed"])
        self.assertIsNone(result["insight"])
        self.assertIn("AI 인사이트가 제공되지 않았습니다", (self.root / "run/report.md").read_text())

    def test_fatal_configuration_error_checkpoints_without_exception_text(self):
        self.provider.complete.side_effect = ConfigError("private config")
        result = run_evaluation(DATASET, self.root / "run", self.options, provider=self.provider)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["metrics"]["not_run"], 17)
        self.assertEqual(result["metrics"]["errors"], 1)
        self.assertEqual(self.provider.complete.call_count, 1)
        self.assertNotIn("private config", (self.root / "run/evaluation.json").read_text())

    def test_existing_directory_is_rejected_before_requests(self):
        with self.assertRaises(OutputError):
            run_evaluation(DATASET, self.root, self.options, provider=self.provider)
        self.provider.complete.assert_not_called()

    def test_interruption_keeps_checkpoint_and_marks_attempted_case(self):
        self.provider.complete.side_effect = KeyboardInterrupt()
        result = run_evaluation(DATASET, self.root / "run", self.options, provider=self.provider)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["metrics"]["errors"], 1)
        self.assertEqual(result["metrics"]["not_run"], 17)
        self.assertEqual(json.loads((self.root / "run/evaluation.json").read_text())["status"], "interrupted")

    def test_all_analysis_failures_do_not_trigger_insight_request(self):
        self.provider.complete.side_effect = AIProviderError("private")
        result = run_evaluation(DATASET, self.root / "run", self.options, provider=self.provider)
        self.assertEqual(self.provider.complete.call_count, 18)
        self.assertEqual(result["metrics"]["accuracy_all"], 0)
        self.assertIsNone(result["metrics"]["accuracy_valid"])
        self.assertFalse(result["checks"]["insight_generated"])
        self.assertTrue(result["checks"]["reports_written"])

    def test_duplicate_json_keys_are_rejected(self):
        path = self.root / "duplicate.json"
        path.write_text('{"version":"hidden",' + DATASET.read_text()[1:])
        with self.assertRaises(ValidationError):
            load_dataset(path)

    def test_invalid_labels_duplicate_ids_and_empty_sets_rejected(self):
        for change in ("invalid_label", "duplicate", "empty", "rating"):
            data = json.loads(DATASET.read_text())
            if change == "invalid_label":
                data["cases"][0]["expected"] = "unknown"
            elif change == "duplicate":
                data["cases"][1]["id"] = data["cases"][0]["id"]
            elif change == "empty":
                data["cases"] = []
            else:
                data["cases"][0]["rating"] = True
            path = self.root / "invalid.json"
            path.write_text(json.dumps(data))
            with self.subTest(change=change), self.assertRaises(ValidationError):
                load_dataset(path)


if __name__ == "__main__":
    unittest.main()
