"""Cache identity follows generation settings, without retaining credentials."""

import json
import tempfile
import time
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.serve_dashboard import configured_generation, seed_demo
from src.config import DEFAULT_CONFIG
from src.errors import ValidationError
from src.insight_provenance import generation_profile, validate_profile
from src.models import AnalysisOptions, ReviewFilter
from src.web_dashboard import DashboardData, load_insight_artifact
from tests.test_dashboard_insight_jobs import fake_insight


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db, self.legacy = seed_demo(self.root)
        self.cache = self.root / "cache"
        self.options = AnalysisOptions(provider="openai-compatible", model="test-model", timeout_seconds=30,
                                       max_retries=0, api_key="private-test-key",
                                       base_url="https://router.example/private-route/v1")
        self.profile = generation_profile(self.options, "test-prompt-v1")
        self.factory = Mock(return_value=Mock(extract_insights=Mock(side_effect=fake_insight)))

    def data(self, profile):
        return DashboardData(self.db, cache_dir=self.cache, extractor_factory=self.factory,
                             generation_profile=profile)

    def generate(self, data):
        snapshot = data.create_snapshot(ReviewFilter()).response
        job = data.start_generation(snapshot["snapshot_id"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = data.jobs.get(job["id"])
            if result["status"] != "running" and data.jobs.active is None:
                self.assertEqual(result["status"], "succeeded")
                return result
            time.sleep(.01)
        self.fail("generation did not finish")

    def test_each_semantic_setting_invalidates_cache_without_calling_ai_on_read(self):
        self.generate(self.data(self.profile))
        variations = [dict(self.profile, model="other-model"), dict(self.profile, provider="other-provider"),
                      dict(self.profile, prompt_version="v2"), dict(self.profile, reasoning_effort="high"),
                      dict(self.profile, endpoint_sha256="0" * 64)]
        for profile in variations:
            with self.subTest(profile=profile):
                body = self.data(profile).create_snapshot(ReviewFilter()).response
                self.assertEqual(body["insight_status"], "config_mismatch")
                self.assertIsNone(body["insight"])
                self.assertIsNone(body["insight_provenance"])
        self.factory.assert_called_once()

    def test_changed_model_regenerates_then_reuses_and_readonly_shows_saved_provenance(self):
        self.generate(self.data(self.profile))
        changed = self.data(dict(self.profile, model="new-model"))
        self.assertFalse(self.generate(changed)["reused"])
        self.assertTrue(self.generate(changed)["reused"])
        self.assertEqual(self.factory.call_count, 2)
        body = DashboardData(self.db, cache_dir=self.cache).create_snapshot(ReviewFilter()).response
        self.assertEqual(body["insight_status"], "available")
        self.assertEqual(body["insight_provenance"]["model"], "new-model")
        self.assertNotIn("endpoint_sha256", json.dumps(body))

    def test_credentials_and_retry_settings_do_not_invalidate_cache(self):
        rotated = replace(self.options, api_key="rotated-key", timeout_seconds=90, max_retries=3)
        self.assertEqual(generation_profile(rotated, "test-prompt-v1"), self.profile)
        slash = replace(self.options, base_url=self.options.base_url + "/")
        self.assertEqual(generation_profile(slash, "test-prompt-v1"), self.profile)
        self.generate(self.data(self.profile))
        self.assertTrue(self.generate(self.data(generation_profile(rotated, "test-prompt-v1")))["reused"])
        self.factory.assert_called_once()

    def test_legacy_result_is_readable_but_not_reused_as_current_profile(self):
        cache = self.data(self.profile)
        self.cache.mkdir()
        path = self.cache / (cache.jobs.key(ReviewFilter()) + ".json")
        path.write_bytes(self.legacy.read_bytes())
        body = DashboardData(self.db, cache_dir=self.cache).create_snapshot(ReviewFilter()).response
        self.assertEqual(body["insight_status"], "available")
        self.assertIsNone(body["insight_provenance"])
        self.assertEqual(cache.create_snapshot(ReviewFilter()).response["insight_status"], "config_mismatch")
        self.assertFalse(self.generate(cache)["reused"])
        self.assertEqual(load_insight_artifact(path)["schema_version"], 2)
        fixed = DashboardData(self.db, insight_path=self.legacy, cache_dir=self.root / "empty",
                              extractor_factory=self.factory, generation_profile=self.profile)
        self.assertEqual(fixed.create_snapshot(ReviewFilter()).response["insight_status"], "config_mismatch")

    def test_profile_allowlist_and_artifact_version_are_strict(self):
        data = self.data(self.profile)
        self.generate(data)
        path = self.cache / (data.jobs.key(ReviewFilter()) + ".json")
        original = json.loads(path.read_bytes())
        for changed in (dict(self.profile, api_key="must-not-save"), dict(self.profile, endpoint_sha256="wrong"),
                        dict(self.profile, model=""), dict(self.profile, reasoning_effort="unsupported")):
            with self.assertRaises(ValidationError):
                validate_profile(changed)
            content = dict(original, generation_profile=changed)
            path.write_text(json.dumps(content))
            with self.assertRaises(ValidationError):
                load_insight_artifact(path)
        for version in (True, 4, 1):
            path.write_text(json.dumps(dict(original, schema_version=version)))
            with self.assertRaises(ValidationError):
                load_insight_artifact(path)

    def test_disk_and_public_response_never_include_key_or_endpoint(self):
        data = self.data(self.profile)
        self.generate(data)
        disk = next(self.cache.glob("*.json")).read_text()
        response = json.dumps(data.create_snapshot(ReviewFilter()).response)
        for text in (disk, response):
            self.assertNotIn(self.options.api_key, text)
            self.assertNotIn("router.example", text)
            self.assertNotIn("private-route", text)
        self.assertIn("endpoint_sha256", disk)
        self.assertNotIn("endpoint_sha256", response)

    def test_configured_factory_freezes_options_and_metadata_together(self):
        config = deepcopy(DEFAULT_CONFIG)
        config["ai"].update(model="first-model", api_key="private-test-key")
        with patch("src.config.load_env_file"), patch("src.config.load_config", return_value=config) as loader, \
                patch("src.insight_extractor.AIInsightExtractor") as extractor:
            factory, profile = configured_generation()
            extractor.assert_not_called()
            config["ai"]["model"] = "changed-after-startup"
            factory()
            self.assertEqual(profile["model"], "first-model")
            self.assertEqual(extractor.call_args.args[0].model, "first-model")
            loader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
