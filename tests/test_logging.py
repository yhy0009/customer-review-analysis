"""Tests for application logging setup."""

import io
import logging
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.config import (
    DEFAULT_CONFIG,
    ConfigError,
    configure_logging,
    get_logger,
    reset_logging,
)


class LoggingSetupTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_logging()

    def make_config(self, log_file: object) -> dict:
        config = deepcopy(DEFAULT_CONFIG)
        config["logging"]["file"] = log_file
        return config

    def test_writes_to_console_and_rotating_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "nested" / "app.log"
            stream = io.StringIO()
            configure_logging(self.make_config(str(log_path)), stream=stream)

            get_logger("test").info("설정 완료")

            self.assertIn("INFO", stream.getvalue())
            self.assertIn("설정 완료", stream.getvalue())
            self.assertIn("설정 완료", log_path.read_text(encoding="utf-8"))

    def test_reconfiguration_does_not_duplicate_handlers(self) -> None:
        stream = io.StringIO()
        config = self.make_config(None)

        logger = configure_logging(config, stream=stream)
        logger = configure_logging(config, stream=stream)
        logger.info("한 번만 출력")

        self.assertEqual(len(logger.handlers), 1)
        self.assertEqual(stream.getvalue().count("한 번만 출력"), 1)

    def test_level_override_takes_precedence(self) -> None:
        stream = io.StringIO()
        config = self.make_config(None)
        config["logging"]["level"] = "ERROR"

        logger = configure_logging(config, level_override="debug", stream=stream)
        logger.debug("디버그 메시지")

        self.assertEqual(logger.level, logging.DEBUG)
        self.assertIn("디버그 메시지", stream.getvalue())

    def test_unresolvable_log_path_cleans_up_console_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loop = Path(directory) / "loop.log"
            loop.symlink_to(loop.name)
            with self.assertRaises(ConfigError):
                configure_logging(self.make_config(str(loop)), stream=io.StringIO())
            self.assertEqual(get_logger().handlers, [])

    def test_invalid_level_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "지원하지 않는 로그 레벨"):
            configure_logging(self.make_config(None), level_override="TRACE")


if __name__ == "__main__":
    unittest.main()
