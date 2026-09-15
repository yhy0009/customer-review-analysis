"""Fixed-input comparisons must isolate prompt changes and preserve source evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.compare_insights import compare
from src.ai_provider import AnalysisProvider, ProviderResponse
from src.errors import AIProviderError, ValidationError
from src.evaluation import load_dataset
from src.models import AnalysisOptions
from tests.insight_fixtures import staged_reply


DATASET = Path(__file__).resolve().parents[1] / "evaluation/reviews.validation.v1.json"


class InsightComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.saved = self.root / "evaluation.json"
        dataset = load_dataset(DATASET)
        self.source = {"dataset_sha256": dataset["sha256"], "status": "completed", "provider": "fake",
                       "rows": [dict(c, status="ok", predicted=c["expected"], confidence=.9,
                                     keywords=["テスト"], model="fixture") for c in dataset["cases"]]}
        self.saved.write_text(json.dumps(self.source))
        self.options = AnalysisOptions(provider="fake", model="fixture", timeout_seconds=5, max_retries=3,
                                       api_key="do-not-persist-this")
        self.provider = Mock(spec=AnalysisProvider)
        self.provider.complete.return_value = ProviderResponse(
            json.dumps({"summary": "테스트 요약", "issues": ["테스트 불편"], "improvement_suggestions": []}), "fixture")
        self.provider.complete.side_effect = lambda messages, schema, options: staged_reply(
            messages, schema, self.provider.complete.return_value, label="테스트 불편")

    def run_pair(self):
        return compare(DATASET, self.saved, self.root / "pair", self.options, provider=self.provider)

    def test_same_payload_and_schema_without_labels_or_secret_and_source_unchanged(self):
        before = self.saved.read_bytes()
        result = self.run_pair()
        self.assertTrue(result["completed"])
        first, second, third = [c.args for c in self.provider.complete.call_args_list]
        self.assertEqual(first[0][1], second[0][1])
        self.assertEqual(first[2], third[2])
        self.assertEqual(set(first[1]["properties"]), set(third[1]["properties"]))
        self.assertEqual(json.loads(first[0][1]["content"])["reviews"],
                         json.loads(third[0][1]["content"])["reviews"])
        self.assertNotEqual(first[0][0], second[0][0])
        for review in json.loads(first[0][1]["content"])["reviews"]:
            self.assertEqual(set(review), {"product_name", "rating", "review_text", "sentiment"})
        self.assertEqual(first[2].max_retries, 0)
        self.assertEqual(self.saved.read_bytes(), before)
        self.assertNotIn(self.options.api_key, (self.root / "pair/comparison.json").read_text())
        with self.assertRaises(FileExistsError):
            self.run_pair()
        self.assertEqual(self.provider.complete.call_count, 3)

    def test_mismatched_or_failed_saved_rows_rejected_before_request_or_output(self):
        for field, value in (("review_text", "changed"), ("status", "error"), ("predicted", "unknown")):
            original = self.source["rows"][0][field]
            self.source["rows"][0][field] = value
            self.saved.write_text(json.dumps(self.source))
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.run_pair()
            self.source["rows"][0][field] = original
        self.assertFalse((self.root / "pair").exists())
        self.provider.complete.assert_not_called()

    def test_partial_failure_is_checkpointed_without_retry_or_sensitive_error(self):
        def complete(messages, schema, options):
            if self.provider.complete.call_count == 1:
                raise AIProviderError("do-not-persist-this")
            return staged_reply(messages, schema, self.provider.complete.return_value, label="테스트 불편")
        self.provider.complete.side_effect = complete
        result = self.run_pair()
        self.assertFalse(result["completed"])
        self.assertEqual([r["status"] for r in result["runs"]], ["error", "ok"])
        self.assertEqual(self.provider.complete.call_count, 3)
        self.assertNotIn("do-not-persist-this", (self.root / "pair/comparison.json").read_text())

    def test_case_selection_is_identical_in_both_versions_and_unknown_ids_fail(self):
        result = compare(DATASET, self.saved, self.root / "pair", self.options,
                         provider=self.provider, case_ids=["vn02"])
        self.assertEqual(result["case_ids"], ["vn02"])
        self.assertTrue(result["completed"])
        for call in self.provider.complete.call_args_list:
            reviews = json.loads(call.args[0][1]["content"])["reviews"]
            self.assertEqual(len(reviews), 1)
            self.assertIn("글자가 흐리게", reviews[0]["review_text"])
        with self.assertRaises(ValidationError):
            compare(DATASET, self.saved, self.root / "bad", self.options,
                    provider=self.provider, case_ids=["not-in-fixture"])
        self.assertFalse((self.root / "bad").exists())
        self.assertEqual(self.provider.complete.call_count, 3)

    def test_current_only_replay_does_not_claim_a_paired_comparison(self):
        result = compare(DATASET, self.saved, self.root / "pair", self.options,
                         provider=self.provider, current_only=True)
        self.assertTrue(result["completed"])
        self.assertIsNone(result["same_input"])
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(self.provider.complete.call_count, 2)

    def test_batched_comparison_compares_source_not_first_batch(self):
        from tests.test_insight_batching import reply
        def complete(messages, schema, options):
            body = json.loads(messages[1]["content"])
            if "reviews" in schema["properties"] or "issue_candidates" in body:
                return reply(messages, schema, options)
            return self.provider.complete.return_value
        self.provider.complete.side_effect = complete
        result = compare(DATASET, self.saved, self.root / "batch-pair", self.options,
                         provider=self.provider, batch_size=3)
        self.assertTrue(result["same_input"])
        self.assertTrue(result["completed"])
        self.assertEqual(self.provider.complete.call_count, 5)
        hashes = [call["request_sha256"] for call in result["calls"]]
        self.assertNotEqual(hashes[0], hashes[1])
        import hashlib
        self.assertEqual(result["calls"][-1]["system_prompt_sha256"],
                         hashlib.sha256(result["runs"][-1]["compact_prompt"].encode()).hexdigest())
