"""Language-stratified scoring and evaluation metadata isolation, without API calls."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.errors import AIProviderError, ValidationError
from src.evaluation import classification_metrics_by_language, load_dataset, run_evaluation
from src.models import AnalysisOptions
from tests.insight_fixtures import evidence_reply


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'evaluation/reviews.multilingual.v1.json'


class MultilingualEvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset = load_dataset(DATASET)
        self.provider = Mock(spec=AnalysisProvider)
        self.options = AnalysisOptions(provider='fake', model='fixture', timeout_seconds=5, max_retries=2)

    def complete(self, messages, schema, options):
        body = json.loads(messages[1]['content'])
        if 'sentiment' in schema['properties']:
            self.assertEqual(set(body), {'product_name', 'rating', 'review_text'})
            case = next(case for case in self.dataset['cases'] if case['review_text'] == body['review_text'])
            # Fixture oracle only: a perfect mock score is not model accuracy.
            return ProviderResponse(json.dumps({'sentiment': case['expected'], 'confidence': .8,
                'summary': '검증용 요약입니다.', 'keywords': ['음질']}, ensure_ascii=False), 'fixture')
        if 'reviews' in schema['properties']:
            return evidence_reply(messages)
        return ProviderResponse(json.dumps({'summary': body.get('praise_label') or '검증용 전체 요약',
            'issues': body.get('issue_candidates', ['배송 지연']), 'improvement_suggestions': []}), 'fixture')

    def test_fixture_has_balanced_languages_labels_and_boundary_cases(self):
        self.assertEqual(len(self.dataset['cases']), 27)
        for language in ('ko', 'en', 'mixed'):
            cases = [case for case in self.dataset['cases'] if case['language'] == language]
            self.assertEqual(len(cases), 9)
            for label in ('positive', 'neutral', 'negative'):
                self.assertEqual(sum(case['expected'] == label for case in cases), 3)
            self.assertTrue({'negation', 'sarcasm', 'instruction_in_review', 'factual', 'balanced', 'unassessed'} <= {case['category'] for case in cases})

    def test_language_metrics_include_errors_and_unattempted_rows(self):
        rows = [dict(language='ko', expected='positive', predicted='positive', status='ok'),
                dict(language='en', expected='negative', predicted=None, status='error'),
                dict(language='en', expected='neutral', predicted='neutral', status='ok'),
                dict(language='mixed', expected='positive', predicted=None, status='not_run'),
                dict(expected='neutral', predicted='neutral', status='ok')]
        metrics = classification_metrics_by_language(rows)
        self.assertEqual(metrics['ko']['accuracy_all'], 1)
        self.assertEqual(metrics['en']['accuracy_all'], .5)
        self.assertEqual(metrics['en']['errors'], 1)
        self.assertIsNone(metrics['mixed']['accuracy_valid'])
        self.assertEqual(metrics['mixed']['not_run'], 1)
        self.assertEqual(metrics['unspecified']['total'], 1)
        self.assertEqual(classification_metrics_by_language([]), {})

    def test_optional_language_is_backwards_compatible_and_strict(self):
        legacy = load_dataset(ROOT / 'evaluation/reviews.v1.json')
        self.assertTrue(all('language' not in case for case in legacy['cases']))
        for value in ('', 'EN', 'fr', None, [], 1):
            data = json.loads(DATASET.read_text())
            data['cases'][0]['language'] = value
            path = self.root / 'invalid.json'
            path.write_text(json.dumps(data))
            with self.subTest(value=value), self.assertRaises(ValidationError):
                load_dataset(path)
        data['cases'][0]['language'] = 'ko'
        data['cases'][0]['unexpected_field'] = 'extra'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValidationError):
            load_dataset(path)

    def test_evaluation_persists_language_metrics_without_sending_annotations(self):
        self.provider.complete.side_effect = self.complete
        result = run_evaluation(DATASET, self.root / 'run', self.options, provider=self.provider)
        self.assertTrue(result['structural_checks_passed'])
        self.assertEqual(result['mode'], 'injected')
        self.assertEqual(result['analysis_prompt_version'], 'review-sentiment-v2-multilingual')
        self.assertEqual(result['latency']['analysis_requests'], 27)
        for language in ('ko', 'en', 'mixed'):
            self.assertEqual(result['metrics_by_language'][language]['total'], 9)
            self.assertEqual(result['metrics_by_language'][language]['correct'], 9)
        self.assertEqual(json.loads((self.root / 'run/evaluation.json').read_text())['metrics_by_language'], result['metrics_by_language'])
        self.assertEqual(self.provider.complete.call_count, 30)  # 27 classification + 2 evidence + 1 summary

    def test_failed_english_case_remains_in_english_denominator(self):
        def complete(messages, schema, options):
            if 'sentiment' in schema['properties'] and json.loads(messages[1]['content'])['review_text'] == self.dataset['cases'][9]['review_text']:
                raise AIProviderError('private provider response')
            return self.complete(messages, schema, options)
        self.provider.complete.side_effect = complete
        result = run_evaluation(DATASET, self.root / 'run', self.options, provider=self.provider)
        self.assertFalse(result['structural_checks_passed'])
        self.assertEqual(result['metrics_by_language']['en']['errors'], 1)
        self.assertAlmostEqual(result['metrics_by_language']['en']['accuracy_all'], 8/9)
        self.assertEqual(result['metrics_by_language']['ko']['errors'], 0)
        self.assertNotIn('private provider response', (self.root / 'run/evaluation.json').read_text())

    def test_interruption_retains_language_breakdown(self):
        self.provider.complete.side_effect = KeyboardInterrupt()
        result = run_evaluation(DATASET, self.root / 'run', self.options, provider=self.provider)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['metrics_by_language']['ko']['errors'], 1)
        self.assertEqual(result['metrics_by_language']['en']['not_run'], 9)
        self.assertEqual(result['metrics_by_language']['mixed']['not_run'], 9)

    def test_validate_only_runs_without_site_packages_config_or_api(self):
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'scripts/evaluate_ai.py'), '--validate-only',
                                 '--dataset', str(DATASET)], cwd=self.root, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['language_counts'], {'ko': 9, 'en': 9, 'mixed': 9})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_live_mode_without_sdk_reports_error_before_creating_output(self):
        output = self.root / 'live'
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'scripts/evaluate_ai.py'), '--live',
                                 '--dataset', str(DATASET), '--output', str(output)], cwd=self.root,
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn('AI 패키지', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())
