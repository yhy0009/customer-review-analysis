"""Exercise injected pipeline commands with real SQLite and isolated config."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.cli import main
from src.config import reset_logging
from src.errors import OutputError, StorageError
from src.models import (
    BatchOperationResult, CleanBatchResult, DashboardResult, DuplicatePolicy,
    ItemError, RawReview, ReviewStatistics,
)
from src.runtime import build_default_handlers
from src.sqlite_repository import SQLiteReviewRepository


class PipelineRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(reset_logging)
        self.root = Path(temporary.name).resolve()
        self.database = self.root / 'data/reviews.db'
        (self.root / 'config').mkdir()
        self.settings = {
            'storage': {'database_path': str(self.database)},
            'cleaning': {'duplicate_policy': 'upsert', 'min_review_length': 7},
            'visualization': {'font_family': 'TestFont', 'dpi': 200},
            'logging': {'file': None, 'level': 'WARNING'},
        }
        (self.root / 'config/config.json').write_text(json.dumps(self.settings))
        self.connections = []
        self.configs = []

    def run_cli(self, argv, handlers):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
             patch('src.config.PROJECT_ROOT', self.root), \
             patch('src.handlers.PROJECT_ROOT', self.root), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, handlers=handlers)
        return code, out.getvalue(), err.getvalue()

    def factory(self, operation):
        def build(repository, config):
            self.connections.append(repository)
            self.configs.append(config)
            repository.get_statistics()  # The borrowed connection must be open.
            return lambda request: operation(repository, request)
        return Mock(side_effect=build)

    def assert_connections_closed(self):
        self.assertTrue(self.connections)
        for repository in self.connections:
            with self.assertRaises(StorageError):
                repository.get_statistics()

    def test_registration_is_lazy_and_independent_and_keeps_existing_commands(self):
        existing = {'import', 'clean', 'analyze', 'extract', 'list', 'show', 'stats', 'export'}
        self.assertEqual(set(build_default_handlers()), existing)
        factory = Mock()
        for name in ('import', 'clean', 'dashboard'):
            with self.subTest(command=name):
                handlers = build_default_handlers(**{name + '_factory': factory})
                self.assertEqual(set(handlers), existing | {name})
        factory.assert_not_called()
        self.assertFalse(self.database.exists())

    def test_explicit_none_keeps_command_unconnected_and_does_not_create_db(self):
        for argv in (['import', '--file', 'reviews.csv'], ['clean'], ['dashboard']):
            code, out, err = self.run_cli(argv, build_default_handlers(import_factory=None, clean_factory=None))
            self.assertEqual(code, 2)
            self.assertFalse(out)
            self.assertIn('아직 연결되지 않았습니다', err)
            self.assertFalse(self.database.exists())

    def test_import_commits_through_real_repository_and_passes_config_and_request(self):
        requests = []
        def operation(repository, request):
            requests.append(request)
            return repository.save_raw_reviews([RawReview(source_review_id='synthetic-id',
                product_name='제품', review_text='테스트 리뷰', rating=5, review_date='2026-09-01')],
                request.policy)
        factory = self.factory(operation)
        handlers = build_default_handlers(import_factory=factory)
        code, out, err = self.run_cli(['import', '--file', 'data/input.csv'], handlers)
        self.assertEqual(code, 0)
        self.assertFalse(err)
        self.assertIn('succeeded=1', out)
        self.assertEqual(requests[0].file, self.root / 'data/input.csv')
        self.assertIs(requests[0].policy, DuplicatePolicy.UPSERT)
        self.assertEqual(self.configs[0]['visualization']['font_family'], 'TestFont')
        self.assertEqual(self.configs[0]['visualization']['dpi'], 200)
        self.assert_connections_closed()
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual(len(repository.fetch_raw_reviews()), 1)

    def test_clean_partial_failure_closes_connection_and_reports_rejections(self):
        requests = []
        def operation(repository, request):
            requests.append(request)
            return CleanBatchResult(processed=1, succeeded=0, rejected=1,
                errors=[ItemError('1', 'REVIEW_TOO_SHORT', '본문이 너무 짧습니다.')])
        code, out, err = self.run_cli(['clean', '--min-length', '4'],
                                     build_default_handlers(clean_factory=self.factory(operation)))
        self.assertEqual(code, 1)
        self.assertIn('rejected=1', out)
        self.assertFalse(err)
        self.assertEqual(requests[0].options.min_length, 4)
        self.assertIs(requests[0].options.policy, DuplicatePolicy.UPSERT)
        self.assert_connections_closed()

    def test_dashboard_uses_its_own_factory_and_preserves_other_defaults(self):
        other = Mock()
        factory = self.factory(lambda repository, request:
            DashboardResult([], repository.get_statistics(request.filters)))
        handlers = build_default_handlers(import_factory=other, clean_factory=other,
                                           dashboard_factory=factory)
        code, out, err = self.run_cli(['dashboard'], handlers)
        self.assertEqual(code, 0)
        self.assertIn('생성된 파일이 없습니다', out)
        self.assertFalse(err)
        other.assert_not_called()
        self.assert_connections_closed()
        self.assertEqual(self.run_cli(['stats'], handlers)[0], 0)
        factory.assert_called_once()

    def test_each_invocation_gets_a_fresh_connection(self):
        factory = self.factory(lambda repository, request: CleanBatchResult(processed=0, succeeded=0))
        handlers = build_default_handlers(clean_factory=factory)
        for _ in range(2):
            self.assertEqual(self.run_cli(['clean'], handlers)[0], 0)
        self.assertIsNot(self.connections[0], self.connections[1])
        self.assert_connections_closed()

    def test_bad_request_fails_before_opening_database_or_constructing_service(self):
        factory = Mock()
        handlers = build_default_handlers(dashboard_factory=factory)
        with patch('src.runtime.SQLiteReviewRepository.from_config') as open_db:
            code, out, err = self.run_cli(
                ['dashboard', '--date-from', '2026-09-15', '--date-to', '2026-09-01'], handlers)
        self.assertEqual(code, 2)
        self.assertFalse(out)
        self.assertTrue(err)
        factory.assert_not_called()
        open_db.assert_not_called()

    def test_database_error_does_not_construct_service(self):
        self.database.parent.mkdir()
        self.database.write_bytes(b'not a sqlite database')
        factory = Mock()
        code, out, err = self.run_cli(['clean'], build_default_handlers(clean_factory=factory))
        self.assertEqual(code, 3)
        self.assertFalse(out)
        self.assertTrue(err)
        factory.assert_not_called()
        self.assertEqual(self.database.read_bytes(), b'not a sqlite database')

    def test_factory_and_operation_errors_close_repository_without_success_output(self):
        for stage in ('factory', 'operation'):
            with self.subTest(stage=stage):
                self.connections.clear()
                def fail(repository, config_or_request):
                    if stage == 'factory':
                        self.connections.append(repository)
                    raise OutputError('파일을 생성할 수 없습니다.')
                factory = fail if stage == 'factory' else self.factory(fail)
                code, out, err = self.run_cli(['dashboard'],
                    build_default_handlers(dashboard_factory=factory))
                self.assertEqual(code, 3)
                self.assertFalse(out)
                self.assertIn('파일을 생성할 수 없습니다', err)
                self.assert_connections_closed()

    def test_missing_dependency_is_config_error_and_closes_repository(self):
        for stage in ('factory', 'operation'):
            with self.subTest(stage=stage):
                self.connections.clear()
                def missing(repository, config_or_request):
                    if stage == 'factory':
                        self.connections.append(repository)
                    raise ModuleNotFoundError('private-path-or-module')
                factory = missing if stage == 'factory' else self.factory(missing)
                code, out, err = self.run_cli(['import', '--file', 'input.csv'],
                    build_default_handlers(import_factory=factory))
                self.assertEqual(code, 2)
                self.assertFalse(out)
                self.assertIn('requirements.txt', err)
                self.assertNotIn('private-path-or-module', err)
                self.assert_connections_closed()

    def test_user_interruption_closes_repository_and_propagates(self):
        def interrupted(repository, request):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_cli(['clean'], build_default_handlers(clean_factory=self.factory(interrupted)))
        self.assert_connections_closed()

    def test_injected_handlers_run_from_other_cwd_without_optional_packages(self):
        source = Path(__file__).resolve().parents[1]
        project = self.root / 'copy'
        shutil.copytree(source / 'src', project / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        (project / 'config').mkdir()
        (project / 'config/config.json').write_text('{"logging":{"file":null}}')
        script = '''
import sys
from pathlib import Path
project = Path(sys.argv[1])
sys.path.insert(0, str(project))
from src.cli import main
from src.models import BatchOperationResult
from src.runtime import build_default_handlers
def factory(repository, config):
    def execute(request):
        assert request.file == project / "data/input.csv"
        assert repository.database_path == project / "data/app_database.db"
        return BatchOperationResult(processed=0, succeeded=0)
    return execute
code = main(["import", "--file", "data/input.csv"],
            handlers=build_default_handlers(import_factory=factory))
assert not {"openai", "pandas", "matplotlib", "src.collector", "src.visualizer"} & set(sys.modules)
raise SystemExit(code)
'''
        environment = {key: value for key, value in os.environ.items()
                       if key in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run([sys.executable, '-S', '-c', script, str(project)],
                                cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('processed=0', result.stdout)
        self.assertTrue((project / 'data/app_database.db').exists())
        self.assertFalse(self.database.exists())


if __name__ == '__main__':
    unittest.main()
