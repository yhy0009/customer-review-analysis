"""Offline policy thresholds, review provenance and regression gates."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.errors import ValidationError
from src.evaluation import load_dataset
from src.quality_gate import CHECKS, RUBRIC, assess


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation/reviews.v1.json"


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        dataset = load_dataset(DATASET)
        self.data = {"dataset_sha256": dataset["sha256"], "status": "completed", "mode": "injected",
                     "max_retries": 0, "checks": dict.fromkeys(CHECKS, True),
                     "rows": [dict(c, status="ok", predicted=c["expected"]) for c in dataset["cases"]],
                     "calls": [{"stage": "analysis", "elapsed_seconds": 2} for _ in dataset["cases"]],
                     "metrics": {"accuracy_all": 0}}
        self.policy = {"version": "test-v1", "dataset_sha256": dataset["sha256"],
                       "minimums": {"accuracy_all": .9, "macro_f1": .9, "valid_ratio": 1},
                       "max_analysis_p95_seconds": 5, "max_regression": None, "require_human_review": True}
        self.save("evaluation.json", self.data)
        self.save("policy.json", self.policy)

    def save(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def run_gate(self, **kwargs):
        return assess(DATASET, self.root / "evaluation.json", self.root / "policy.json", **kwargs)

    def review(self, kind="human"):
        return {"evaluation_sha256": hashlib.sha256((self.root / "evaluation.json").read_bytes()).hexdigest(),
                "reviewer": "fixture reviewer", "reviewer_kind": kind, "reviewed_at": "2026-09-16T00:00:00Z",
                "labels_reviewed": True,
                "rubric": {name: {"verdict": "pass", "case_ids": ["p01"], "note": "fixture review"} for name in RUBRIC}}

    def test_missing_review_stays_pending_even_with_perfect_classification(self):
        result = self.run_gate()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["metrics"]["accuracy_all"], 1)  # Recomputed, ignoring cached 0.
        self.assertEqual(result["narrative_review"], "pending")

    def test_human_review_can_pass_and_source_artifacts_are_unchanged(self):
        original = (self.root / "evaluation.json").read_bytes()
        result = self.run_gate(review_path=self.save("review.json", self.review()))
        self.assertEqual(result["status"], "pass")
        self.assertEqual((self.root / "evaluation.json").read_bytes(), original)
        self.assertEqual(len(result["review_sha256"]), 64)

    def test_assistant_review_or_unreviewed_labels_cannot_approve(self):
        for change in ({"reviewer_kind": "assistant"}, {"labels_reviewed": False}):
            review = dict(self.review(), **change)
            result = self.run_gate(review_path=self.save("review.json", review))
            self.assertEqual(result["status"], "pending")

    def test_partial_and_fail_human_verdicts_fail(self):
        for verdict in ("partial", "fail"):
            review = self.review()
            review["rubric"]["grounding"]["verdict"] = verdict
            self.assertEqual(self.run_gate(review_path=self.save("review.json", review))["status"], "fail")

    def test_failure_overrides_pending_and_latency_includes_failed_requests(self):
        self.data["rows"][0].update(status="error", predicted=None)
        self.data["calls"][0]["elapsed_seconds"] = 90
        self.data["metrics"] = {"accuracy_all": 1}
        self.save("evaluation.json", self.data)
        result = self.run_gate()
        self.assertEqual(result["status"], "fail")
        self.assertAlmostEqual(result["metrics"]["valid_ratio"], 17 / 18)
        self.assertEqual(result["metrics"]["analysis_p95_seconds"], 90)

    def test_missing_or_false_structure_check_fails(self):
        for checks in ({}, dict.fromkeys(CHECKS, False), dict.fromkeys(CHECKS, "true")):
            self.save("evaluation.json", dict(self.data, checks=checks))
            self.assertEqual(self.run_gate()["status"], "fail")

    def test_explicit_automated_only_policy_does_not_claim_human_approval(self):
        self.policy["require_human_review"] = False
        self.save("policy.json", self.policy)
        result = self.run_gate()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["scope"], "automated_checks_only")
        self.assertEqual(result["narrative_review"], "pending")

    def test_regression_uses_same_dataset_rows_and_recomputes_baseline(self):
        baseline = self.save("baseline.json", self.data)
        self.policy["minimums"] = dict.fromkeys(self.policy["minimums"], 0)
        self.policy["max_regression"] = dict.fromkeys(self.policy["minimums"], .01)
        self.save("policy.json", self.policy)
        self.data["rows"][0]["predicted"] = "negative"
        self.save("evaluation.json", self.data)
        result = self.run_gate(baseline_path=baseline)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(c["name"] == "regression_accuracy_all" and c["status"] == "fail" for c in result["checks"]))

    def test_regression_policy_requires_baseline_and_rejects_different_modes(self):
        self.policy["max_regression"] = dict.fromkeys(self.policy["minimums"], .01)
        self.save("policy.json", self.policy)
        with self.assertRaises(ValidationError):
            self.run_gate()
        with self.assertRaises(ValidationError):
            self.run_gate(baseline_path=self.save("baseline.json", dict(self.data, mode="live")))

    def test_mismatched_data_or_invalid_row_cannot_pass(self):
        changes = [dict(self.data, dataset_sha256="wrong"), dict(self.data, rows=[]), dict(self.data, calls=[])]
        wrong = copy.deepcopy(self.data)
        wrong["rows"][0]["review_text"] = "changed"
        changes.append(wrong)
        wrong = copy.deepcopy(self.data)
        wrong["rows"][0].update(status="error", predicted="positive")
        changes.append(wrong)
        for change in changes:
            self.save("evaluation.json", change)
            with self.assertRaises(ValidationError):
                self.run_gate()

    def test_review_hash_case_ids_and_required_rubric_are_validated(self):
        for mutation in (lambda r: r.update(evaluation_sha256="wrong"),
                         lambda r: r["rubric"].pop("scope"),
                         lambda r: r["rubric"]["grounding"].update(case_ids=["missing"]),
                         lambda r: r["rubric"]["grounding"].update(verdict="not_applicable"),
                         lambda r: r.update(reviewed_at="no date")):
            review = self.review()
            mutation(review)
            with self.assertRaises(ValidationError):
                self.run_gate(review_path=self.save("review.json", review))

    def test_invalid_thresholds_and_duplicate_json_keys_are_rejected(self):
        for value in (True, -1, 1.1, float("nan"), float("inf")):
            self.policy["minimums"]["accuracy_all"] = value
            self.save("policy.json", self.policy)
            with self.assertRaises(ValidationError):
                self.run_gate()
        (self.root / "policy.json").write_text('{"version":"a","version":"b"}')
        with self.assertRaises(ValidationError):
            self.run_gate()

    def test_cli_exit_codes_and_output_collision(self):
        command = [sys.executable, str(ROOT / "scripts/check_ai_quality.py"),
                   "--dataset", str(DATASET), "--evaluation", str(self.root / "evaluation.json"),
                   "--policy", str(self.root / "policy.json"), "--output", str(self.root / "gate.json")]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
        self.policy["require_human_review"] = False
        self.save("policy.json", self.policy)
        command[-1] = str(self.root / "pass.json")
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
