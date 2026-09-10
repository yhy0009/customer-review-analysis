"""Tests for application configuration loading and validation."""

import json
import tempfile
import unittest
from pathlib import Path

from src.config import ConfigError, load_config, load_env_file


class ConfigLoaderTests(unittest.TestCase):
    def write_config(self, directory: str, content: object) -> Path:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def test_defaults_are_applied_to_empty_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(self.write_config(directory, {}), environ={})

        self.assertEqual(config["storage"]["backend"], "sqlite")
        self.assertEqual(config["cleaning"]["duplicate_policy"], "skip")
        self.assertEqual(config["logging"]["level"], "INFO")
        self.assertEqual(config["ai"]["model"], "gpt-5.6-luna")

    def test_nested_values_override_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(
                directory,
                {
                    "storage": {"database_path": "custom/reviews.db"},
                    "cleaning": {"min_review_length": 10},
                },
            )
            config = load_config(path, environ={})

        self.assertEqual(config["storage"]["database_path"], "custom/reviews.db")
        self.assertEqual(config["storage"]["backend"], "sqlite")
        self.assertEqual(config["cleaning"]["min_review_length"], 10)

    def test_environment_values_take_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(
                directory,
                {
                    "cleaning": {"duplicate_policy": "skip"},
                    "logging": {"level": "INFO"},
                },
            )
            config = load_config(
                path,
                environ={
                    "CRA_DUPLICATE_POLICY": "upsert",
                    "CRA_MIN_REVIEW_LENGTH": "7",
                    "CRA_LOG_LEVEL": "DEBUG",
                    "AI_API_KEY": "test-key",
                    "AI_MODEL": "custom-model",
                },
            )

        self.assertEqual(config["cleaning"]["duplicate_policy"], "upsert")
        self.assertEqual(config["cleaning"]["min_review_length"], 7)
        self.assertEqual(config["logging"]["level"], "DEBUG")
        self.assertEqual(config["ai"]["api_key"], "test-key")
        self.assertEqual(config["ai"]["model"], "custom-model")

    def test_unknown_extension_keys_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(
                self.write_config(directory, {"feature_flags": {"alerts": True}}),
                environ={},
            )

        self.assertTrue(config["feature_flags"]["alerts"])

    def test_missing_or_malformed_file_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            malformed = Path(directory) / "malformed.json"
            malformed.write_text("{invalid", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "찾을 수 없습니다"):
                load_config(missing, environ={})
            with self.assertRaisesRegex(ConfigError, "JSON 형식"):
                load_config(malformed, environ={})

    def test_invalid_known_value_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(
                directory,
                {"cleaning": {"duplicate_policy": "replace"}},
            )

            with self.assertRaisesRegex(ConfigError, "skip 또는 upsert"):
                load_config(path, environ={})

    def test_env_file_loads_values_without_overwriting_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\nAI_API_KEY=file-key\nexport AI_MODEL='test-model'\n",
                encoding="utf-8",
            )
            environ = {"AI_API_KEY": "existing-key"}

            loaded = load_env_file(path, environ=environ)

        self.assertEqual(loaded["AI_API_KEY"], "file-key")
        self.assertEqual(environ["AI_API_KEY"], "existing-key")
        self.assertEqual(environ["AI_MODEL"], "test-model")

    def test_invalid_env_file_line_raises_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("INVALID LINE", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "형식이 올바르지 않습니다"):
                load_env_file(path, environ={})


if __name__ == "__main__":
    unittest.main()
