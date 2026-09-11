"""Command adapters render successful results and keep shared error codes."""
import contextlib
import io
import unittest
from unittest.mock import Mock

from src.cli import parse_args
from src.errors import StorageError
from src.handlers import build_list_handler, build_show_handler, build_stats_handler
from src.models import Page, ReviewStatistics


class QueryHandlerTests(unittest.TestCase):
    def run_handler(self, builder, method, argv):
        args = parse_args(argv)
        args.app_config = {}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = builder(method)(args)
        return code, out.getvalue(), err.getvalue()

    def test_empty_list_is_success_and_forwards_filters_and_sort(self):
        method = Mock(return_value=Page([], 3, 5, 0, 0))
        code, out, err = self.run_handler(build_list_handler, method,
                         ['list', '--page', '3', '--size', '5', '--rating', '2', '--sort', 'date'])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())
        self.assertFalse(err)
        request = method.call_args.args[0]
        self.assertEqual((request.query.page, request.query.size, request.query.filters.rating), (3, 5, 2))

    def test_missing_detail_is_exit_one_with_stderr(self):
        code, out, err = self.run_handler(build_show_handler, Mock(return_value=None), ['show', '--id', '42'])
        self.assertEqual(code, 1)
        self.assertFalse(out)
        self.assertIn('ID=42', err)

    def test_database_failure_uses_exit_three_without_success_output(self):
        code, out, err = self.run_handler(build_stats_handler, Mock(side_effect=StorageError('저장소 오류')), ['stats'])
        self.assertEqual(code, 3)
        self.assertFalse(out)
        self.assertIn('저장소 오류', err)

    def test_reversed_dates_fail_before_service_call(self):
        method = Mock()
        code, out, err = self.run_handler(build_list_handler, method,
                                       ['list', '--date-from', '2026-09-11', '--date-to', '2026-09-01'])
        self.assertEqual(code, 2)
        method.assert_not_called()
        self.assertFalse(out)
        self.assertTrue(err)

    def test_stats_success_prints_output(self):
        code, out, err = self.run_handler(build_stats_handler,
                         Mock(return_value=ReviewStatistics(0, 0, 0, 0)), ['stats'])
        self.assertEqual(code, 0)
        self.assertIn('정제 리뷰 수:', out)
        self.assertFalse(err)


if __name__ == '__main__':
    unittest.main()
