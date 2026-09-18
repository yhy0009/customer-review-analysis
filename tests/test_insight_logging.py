"""Safe insight diagnostics across SDK failures, validation and CLI output."""
import contextlib
import io
import json
import tempfile
import traceback
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from openai import OpenAI

from src.ai_provider import NonRetryableAIError, OpenAIProvider, ProviderResponse
from src.cli import parse_args
from src.config import DEFAULT_CONFIG, configure_logging, reset_logging
from src.errors import AIErrorCode, AIProviderError, safe_ai_error_details
from src.handlers import build_extract_handler
from src.insight_evidence import check_coverage, parse_evidence
from src.insight_extractor import AIInsightExtractor, _parse_response
from src.models import AnalysisOptions, ReviewFilter
from tests.insight_fixtures import evidence_reply
from tests.test_insight_extractor import detail


class InsightLoggingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(reset_logging)
        self.logfile = Path(temporary.name) / 'app.log'
        self.console = io.StringIO()
        config = deepcopy(DEFAULT_CONFIG)
        config['logging']['file'] = str(self.logfile)
        configure_logging(config, stream=self.console)
        self.options = AnalysisOptions(provider='fake', model='private-model', api_key='private-key',
                                       timeout_seconds=5, max_retries=0)
        self.review = detail(1, review_text='private-review 배송이 빨라요.')
        self.provider = Mock()
        self.sleep = Mock()

    def extract(self, *, retries=0, batch_size=20, reviews=None):
        return AIInsightExtractor(replace(self.options, max_retries=retries), self.provider,
                                  sleep=self.sleep, batch_size=batch_size).extract_insights(
            [self.review] if reviews is None else reviews, ReviewFilter())

    def logs(self):
        self.assertEqual(self.console.getvalue(), self.logfile.read_text(encoding='utf-8'))
        return self.console.getvalue()

    def test_quote_failure_reaches_cli_and_file_without_private_content(self):
        self.provider.complete.return_value = ProviderResponse(json.dumps({'reviews': [{
            'review_number': 1, 'complaints': [{'label': '불편', 'quote': 'private-invented-quote'}],
            'praises': [],
        }]}), 'private-response-model')
        args = parse_args(['extract', '--limit', '1'])
        stderr, stdout = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            code = build_extract_handler(lambda request: self.extract())(args)
        self.assertEqual(code, 4)
        self.assertFalse(stdout.getvalue())
        for output in (stderr.getvalue(), self.logs()):
            self.assertIn('stage=evidence', output)
            self.assertIn('code=EVIDENCE_QUOTE_MISMATCH', output)
            self.assertIn('retries=0', output)
            self.assertNotIn('private-', output)
        self.assertIn('attempts=1', stderr.getvalue())
        self.assertIn('ERROR', self.logs())
        self.assertIn('will_retry=False', self.logs())

    def test_unknown_provider_metadata_and_exception_text_are_not_logged(self):
        error = NonRetryableAIError('private-error sk-secret https://private.example')
        error.code = 'private-code\nFORGED LOG'
        error.http_status = 'private-status'
        self.provider.complete.side_effect = error
        with self.assertRaises(AIProviderError) as caught:
            self.extract(retries=3)
        output = self.logs() + ''.join(traceback.format_exception(caught.exception))
        self.assertIn('code=AI_ERROR', output)
        self.assertIn('attempts=1, retries=0', str(caught.exception))
        for private in ('private-', 'sk-secret', 'FORGED LOG', 'https://'):
            self.assertNotIn(private, output)
        self.sleep.assert_not_called()

    def test_retry_then_recovery_logs_attempts_for_each_stage(self):
        responses = [AIProviderError('private-error', code=AIErrorCode.TIMEOUT)]
        def respond(messages, schema, options):
            if responses:
                raise responses.pop()
            if 'reviews' in schema['properties']:
                return evidence_reply(messages)
            return ProviderResponse(json.dumps({'summary': 'private-summary', 'issues': ['배송 지연'],
                                                'improvement_suggestions': []}), 'private-response-model')
        self.provider.complete.side_effect = respond
        result = self.extract(retries=2)
        self.assertEqual(result.summary, 'private-summary')
        logs = self.logs()
        self.assertIn('WARNING', logs)
        self.assertIn('code=AI_TIMEOUT', logs)
        self.assertIn('will_retry=True', logs)
        self.assertIn('succeeded: stage=evidence batch=1/1 attempts=2 retries=1', logs)
        self.assertIn('succeeded: stage=summary batch=1/1 attempts=1 retries=0', logs)
        self.assertNotIn('private-', logs)
        self.sleep.assert_called_once_with(1.0)

    def test_summary_validation_exhaustion_retries_only_summary(self):
        def respond(messages, schema, options):
            if 'reviews' in schema['properties']:
                return evidence_reply(messages)
            return ProviderResponse(json.dumps({'summary': 'private-summary', 'issues': [],
                                                'improvement_suggestions': []}), 'test')
        self.provider.complete.side_effect = respond
        with self.assertRaises(AIProviderError) as caught:
            self.extract(retries=1)
        self.assertEqual(safe_ai_error_details(caught.exception), ('INSIGHT_ISSUE_MISSING', None))
        self.assertIn('stage=summary', str(caught.exception))
        self.assertIn('attempts=2, retries=1', str(caught.exception))
        self.assertEqual(self.provider.complete.call_count, 3)
        self.assertEqual(self.logs().count('started: stage=evidence'), 1)
        self.assertEqual(self.logs().count('started: stage=summary'), 2)
        self.assertNotIn('private-', self.logs())

    def test_failed_later_batch_is_identified(self):
        calls = 0
        def respond(messages, schema, options):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise AIProviderError('private-error', code=AIErrorCode.HTTP, http_status=503)
            return evidence_reply(messages)
        self.provider.complete.side_effect = respond
        with self.assertRaises(AIProviderError) as caught:
            self.extract(batch_size=1, reviews=[self.review, detail(2)])
        self.assertIn('stage=evidence, batch=2/2', str(caught.exception))
        self.assertIn('http_status=503', str(caught.exception))
        self.assertNotIn('stage=summary', self.logs())

    def test_empty_selection_has_no_request_logs(self):
        self.assertEqual(self.extract(reviews=[]).review_count, 0)
        self.provider.complete.assert_not_called()
        self.assertEqual(self.logs(), '')


class ProviderDiagnosticTests(unittest.TestCase):
    def run_provider(self, handler):
        options = AnalysisOptions(provider='openai', model='test', api_key='private-key',
                                  timeout_seconds=5, max_retries=0)
        with patch('src.ai_provider.OpenAI', side_effect=lambda **kwargs: OpenAI(
                **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))):
            return OpenAIProvider().complete([{'role': 'user', 'content': 'private-review'}], {}, options)

    def test_http_status_and_quota_are_classified_without_provider_message(self):
        cases = [(400, None, AIErrorCode.HTTP, True), (429, None, AIErrorCode.RATE_LIMIT, False),
                 (429, 'insufficient_quota', AIErrorCode.QUOTA, True), (503, None, AIErrorCode.HTTP, False)]
        for status, remote_code, code, terminal in cases:
            with self.subTest(status=status, code=code), self.assertRaises(AIProviderError) as caught:
                self.run_provider(lambda request: httpx.Response(status, json={
                    'error': {'message': 'private-error', 'code': remote_code}}))
            self.assertEqual(safe_ai_error_details(caught.exception), (code.value, status))
            self.assertEqual(isinstance(caught.exception, NonRetryableAIError), terminal)
            self.assertNotIn('private-', ''.join(traceback.format_exception(caught.exception)))

    def test_timeout_and_connection_failure_have_distinct_codes(self):
        for exception, code in ((httpx.ReadTimeout, AIErrorCode.TIMEOUT),
                                (httpx.ConnectError, AIErrorCode.CONNECTION)):
            def fail(request):
                raise exception('private-network-error', request=request)
            with self.subTest(code=code), self.assertRaises(AIProviderError) as caught:
                self.run_provider(fail)
            self.assertEqual(safe_ai_error_details(caught.exception), (code.value, None))
            self.assertNotIn('private-', ''.join(traceback.format_exception(caught.exception)))

    def test_output_limit_refusal_and_malformed_response_are_distinct(self):
        cases = [('length', 'private-response', None, AIErrorCode.OUTPUT_LIMIT),
                 ('content_filter', None, None, AIErrorCode.REFUSAL),
                 ('stop', None, 'private-refusal', AIErrorCode.REFUSAL),
                 ('tool_calls', None, None, AIErrorCode.INTERRUPTED),
                 ('stop', '', None, AIErrorCode.RESPONSE)]
        for reason, content, refusal, code in cases:
            def respond(request):
                return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1,
                    'model': 'test', 'choices': [{'index': 0, 'finish_reason': reason,
                    'message': {'role': 'assistant', 'content': content, 'refusal': refusal}}]})
            with self.subTest(reason=reason, code=code), self.assertRaises(AIProviderError) as caught:
                self.run_provider(respond)
            self.assertEqual(safe_ai_error_details(caught.exception), (code.value, None))
            self.assertNotIn('private-', ''.join(traceback.format_exception(caught.exception)))

    def test_diagnostic_boundary_rejects_arbitrary_codes_and_invalid_status_types(self):
        for status in ('private-status', True, 503.0, 999, None):
            error = AIProviderError('private-error', code='private-code', http_status=status)
            self.assertEqual(safe_ai_error_details(error), ('AI_ERROR', None))


class ValidationDiagnosticTests(unittest.TestCase):
    def test_evidence_failure_codes_are_specific(self):
        review = {'review_text': '배송 지연', 'sentiment': 'negative'}
        valid = {'review_number': 1, 'complaints': [{'label': '배송', 'quote': '배송 지연'}], 'praises': []}
        cases = [('private-not-json', AIErrorCode.EVIDENCE_FORMAT),
                 (json.dumps({'reviews': []}), AIErrorCode.EVIDENCE_REVIEWS),
                 (json.dumps({'reviews': [dict(valid, review_number=2)]}), AIErrorCode.EVIDENCE_REVIEWS),
                 (json.dumps({'reviews': [dict(valid, complaints=[])]}), AIErrorCode.EVIDENCE_COMPLAINT),
                 (json.dumps({'reviews': [dict(valid, complaints=[{'label': '배송', 'quote': 'private-quote'}])]}),
                  AIErrorCode.EVIDENCE_QUOTE)]
        for content, code in cases:
            with self.subTest(code=code), self.assertRaises(AIProviderError) as caught:
                parse_evidence(content, [review])
            self.assertIs(caught.exception.code, code)

    def test_summary_format_and_coverage_codes_are_specific(self):
        with self.assertRaises(AIProviderError) as caught:
            _parse_response('private-not-json')
        self.assertIs(caught.exception.code, AIErrorCode.INSIGHT_FORMAT)
        narrative = {'summary': '요약', 'issues': [], 'improvement_suggestions': []}
        for row, code in (({'complaints': [{'label': '불편'}], 'praises': []}, AIErrorCode.ISSUE_COVERAGE),
                          ({'complaints': [], 'praises': [{'label': '장점'}]}, AIErrorCode.PRAISE_COVERAGE)):
            with self.subTest(code=code), self.assertRaises(AIProviderError) as caught:
                check_coverage(narrative, [row])
            self.assertIs(caught.exception.code, code)


if __name__ == '__main__':
    unittest.main()
