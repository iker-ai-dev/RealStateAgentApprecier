"""Validation diagnostics through real offline Sentry envelopes, not raw payloads."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import sentry_sdk
from sentry_sdk.transport import Transport


spec = importlib.util.spec_from_file_location(
    'validation_observability', Path(__file__).resolve().parents[1] / 'app' / 'observability.py')
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)
P = obs.VALIDATION_PREFIX
PRIVATE = 'PRIVATE_SOURCE_PROMPT_CREDENTIAL_RAW_ERROR_728159'


class Sink(Transport):
    def __init__(self):
        super().__init__()
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


class ValidationObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.clear_client()
        self.addCleanup(self.clear_client)
        self.socket = patch('socket.socket', side_effect=AssertionError('Network forbidden')).start()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(obs, 'st', types.SimpleNamespace(secrets={})).start()
        self.sink = Sink()
        self.assertTrue(obs.init_sentry(dsn='https://public@example.invalid/1',
                                       traces_sample_rate=1, transport=self.sink))

    def clear_client(self):
        sentry_sdk.get_client().close()
        for scope in (sentry_sdk.get_current_scope(), sentry_sdk.get_isolation_scope(),
                      sentry_sdk.get_global_scope()):
            scope.clear()
            scope.set_client(None)
        obs._client = None

    def payloads(self, kind):
        sentry_sdk.flush(timeout=1)
        items = [item.payload.json for envelope in self.sink.envelopes for item in envelope.items
                 if item.type == kind]
        return [log for item in items for log in item['items']] if kind == 'log' else items

    def assert_private(self):
        sentry_sdk.flush(timeout=1)
        raw = b''.join(envelope.serialize() for envelope in self.sink.envelopes)
        for forbidden in (PRIVATE, '123456789.987', '"stacktrace"', '"breadcrumbs"', '"user"',
                          '"request"', '"extra"', '"locals"', '"attachments"'):
            self.assertNotIn(forbidden.encode(), raw)
        self.socket.assert_not_called()

    def test_two_rejections_keep_reasons_attempts_error_grouping_and_usage(self):
        scope = sentry_sdk.get_isolation_scope()
        scope.set_user({'email': PRIVATE})
        scope.set_extra('prompt', PRIVATE)
        scope.set_attribute('credentials', PRIVATE)
        scope.add_attachment(bytes=PRIVATE.encode(), filename=PRIVATE, add_to_transactions=True)
        with self.assertRaisesRegex(RuntimeError, PRIVATE):
            with obs.start_agent_transaction('retailscope_ai.answer'):
                for attempt, reason in ((1, 'quotation_mismatch'), (2, 'numeric_provenance')):
                    obs.record_cortex_usage({'prompt_tokens': 10, 'completion_tokens': 5})
                    with obs.start_span('ai.chat_completions.create.snowflake_cortex',
                                        obs.STEPS['ai.chat_completions.create.snowflake_cortex']):
                        pass
                    obs.record_validation(attempt, 'rejected', reason)
                raise RuntimeError(PRIVATE)
        transaction, = self.payloads('transaction')
        trace = transaction['contexts']['trace']
        data = trace['data']
        self.assertEqual(trace['status'], 'internal_error')
        self.assertEqual(len(transaction['spans']), 2)
        self.assertEqual(data['retailscope.cortex.calls'], 2)
        self.assertEqual(data['retailscope.cortex.prompt_tokens'], 20)
        self.assertEqual(data[P + 'attempt_1.reason'], 'quotation_mismatch')
        self.assertEqual(data[P + 'attempt_2.reason'], 'numeric_provenance')
        self.assertEqual(data[P + 'attempt'], 2)
        event, = self.payloads('event')
        self.assertEqual(event['fingerprint'], ['retailscope.validation', 'numeric_provenance'])
        self.assertEqual(event['contexts']['trace']['data'][P + 'attempt'], 2)
        self.assertEqual(event['exception']['values'],
                         [{'type': 'OperationError', 'value': obs.ERROR_MESSAGE}])
        logs = self.payloads('log')
        self.assertEqual([log['body'] for log in logs], [obs.WORKFLOW_MESSAGES['started'],
                         obs.VALIDATION_MESSAGE, obs.VALIDATION_MESSAGE, obs.WORKFLOW_MESSAGES['failed']])
        self.assertEqual(logs[-1]['attributes'][P + 'reason']['value'], 'numeric_provenance')
        for log in logs:
            self.assertEqual(log['trace_id'], trace['trace_id'])
            self.assertEqual(log['span_id'], trace['span_id'])
        self.assert_private()

    def test_repair_success_retains_first_rejection_without_reporting_an_error(self):
        with obs.start_agent_transaction('retailscope_ai.answer'):
            obs.record_validation(1, 'rejected', 'response_schema')
            obs.record_validation(2, 'passed', PRIVATE)
        transaction, = self.payloads('transaction')
        trace = transaction['contexts']['trace']
        self.assertEqual(trace['status'], 'ok')
        self.assertEqual(trace['data'][P + 'attempt_1.reason'], 'response_schema')
        self.assertEqual(trace['data'][P + 'reason'], 'none')
        self.assertEqual(self.payloads('event'), [])
        self.assertEqual(self.payloads('log')[-1]['body'], obs.WORKFLOW_MESSAGES['completed'])
        self.assert_private()

    def test_compiled_graph_repair_exports_only_bounded_diagnostics(self):
        from app import live_agent as agent, observability as app_obs
        from tests.test_live_agent import LEADS, analysis

        leads = copy.deepcopy(LEADS)
        leads[0]['summary'] += '\n' + PRIVATE
        rejected = analysis()
        rejected['paragraphs'][0]['text'] = 'The listing asks $123456789.987.'
        rejected['paragraphs'][2]['text'] = 'The missing operating statements cover 987654321.123 months.'
        repaired = analysis()
        session = Mock()
        session.sql.return_value.collect.side_effect = [
            [[{'structured_output': [{'raw_message': response}], 'model': agent.DEFAULT_MODEL,
               'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens,
                         'total_tokens': prompt_tokens + completion_tokens}}]]
            for response, prompt_tokens, completion_tokens in ((rejected, 10, 5), (repaired, 12, 8))]
        stages = []
        # Use the fixture-owned real SDK client without mocking the recorder or hooks.
        with patch.object(app_obs, '_client', obs._client), \
             patch.object(agent, 'get_snowflake_config', return_value={'account': PRIVATE}), \
             patch.object(agent, '_active_session', return_value=None), \
             patch.object(agent, 'get_snowpark_session', return_value=session):
            result = agent.analyze(PRIVATE, leads, on_progress=stages.append)

        self.assertEqual(session.sql.call_count, 2)
        self.assertEqual(session.sql.return_value.collect.call_count, 2)
        self.assertEqual(session.close.call_count, 2)
        self.assertEqual(stages, ['analyzing', 'validating', 'repairing', 'validating'])
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertEqual(result['usage']['total_tokens'], 35)
        self.assertIn(repaired['paragraphs'][0]['text'], result['answer'])
        self.assertNotIn('123456789.987', result['answer'])
        self.assertNotIn('987654321.123', result['answer'])

        first_prompt, repair_prompt = [call.kwargs['params'][1] for call in session.sql.call_args_list]
        self.assertNotIn('ALL_PARAGRAPH_FAILURES=', first_prompt)
        self.assertIn(PRIVATE, repair_prompt)
        self.assertIn('REPAIR_REQUIRED: Paragraph 1: Numeric model claims', repair_prompt)
        failures, _ = json.JSONDecoder().raw_decode(repair_prompt.split('ALL_PARAGRAPH_FAILURES=', 1)[1])
        self.assertEqual([failure['paragraph'] for failure in failures], [1, 3])
        for failure, number in zip(failures, ('123456789.987', '987654321.123')):
            self.assertTrue(failure['instruction'].startswith(f"Paragraph {failure['paragraph']}:"))
            self.assertEqual(failure['reason'], 'numeric_provenance')
            self.assertIn(number, failure['detail'])
        self.assertEqual(json.loads(repair_prompt.split('REJECTED_RESPONSE=', 1)[1]), rejected)

        transaction, = self.payloads('transaction')
        self.assertEqual(transaction['transaction'], 'retailscope_ai.answer')
        trace = transaction['contexts']['trace']
        self.assertEqual(trace['status'], 'ok')
        data = trace['data']
        self.assertEqual(data['retailscope.cortex.calls'], 2)
        self.assertEqual(data['retailscope.cortex.prompt_tokens'], 22)
        self.assertEqual(data['retailscope.cortex.completion_tokens'], 13)
        self.assertEqual(data['retailscope.cortex.total_tokens'], 35)
        self.assertEqual({key: value for key, value in data.items() if key.startswith(P)}, {
            P + 'attempt': 2, P + 'outcome': 'passed', P + 'reason': 'none',
            P + 'attempt_1.attempt': 1, P + 'attempt_1.outcome': 'rejected',
            P + 'attempt_1.reason': 'numeric_provenance',
            P + 'attempt_2.attempt': 2, P + 'attempt_2.outcome': 'passed', P + 'attempt_2.reason': 'none'})
        self.assertEqual(len(transaction['spans']), 2)
        for span in transaction['spans']:
            self.assertEqual(span['op'], 'ai.chat_completions.create.snowflake_cortex')
            self.assertEqual(span['status'], 'ok')
            self.assertEqual(span['trace_id'], trace['trace_id'])
        self.assertEqual(self.payloads('event'), [])

        logs = self.payloads('log')
        self.assertEqual([log['body'] for log in logs], [obs.WORKFLOW_MESSAGES['started'],
                         obs.VALIDATION_MESSAGE, obs.VALIDATION_MESSAGE, obs.WORKFLOW_MESSAGES['completed']])
        for log, attempt, outcome, reason in zip(logs[1:3], (1, 2), ('rejected', 'passed'),
                                                ('numeric_provenance', 'none')):
            attrs = {key: value['value'] for key, value in log['attributes'].items()}
            self.assertEqual({key: value for key, value in attrs.items() if key.startswith(P)},
                             {P + 'attempt': attempt, P + 'outcome': outcome, P + 'reason': reason})
        for log in logs:
            self.assertEqual(log['trace_id'], trace['trace_id'])
            self.assertEqual(log['span_id'], trace['span_id'])
        raw = b''.join(envelope.serialize() for envelope in self.sink.envelopes)
        for forbidden in (PRIVATE, '123456789.987', '987654321.123', '1,500,000',
                          leads[0]['title'], leads[0]['url'], leads[0]['summary'],
                          'INPUT_DATA', 'REPAIR_REQUIRED', 'ALL_PARAGRAPH_FAILURES',
                          'REJECTED_RESPONSE', 'Unsupported numeric tokens',
                          *(p['text'] for response in (rejected, repaired) for p in response['paragraphs'])):
            self.assertNotIn(forbidden.encode(), raw)
        self.assert_private()

    def test_first_pass_and_diagnostic_state_do_not_bleed_between_workflows(self):
        with obs.start_agent_transaction('retailscope_ai.answer'):
            obs.record_validation(1, 'passed')
            with obs.start_agent_transaction('retailscope_ai.research'):
                obs.record_validation(2, 'rejected', 'numeric_provenance')
        with obs.start_agent_transaction('retailscope_ai.answer'):
            pass
        transactions = self.payloads('transaction')
        self.assertNotIn('data', transactions[0]['contexts']['trace'])
        self.assertEqual(transactions[1]['contexts']['trace']['data'][P + 'attempt'], 1)
        self.assertNotIn('data', transactions[2]['contexts']['trace'])
        self.assert_private()

    def test_invalid_inputs_unknown_reasons_and_duplicates_are_bounded(self):
        with obs.start_agent_transaction('retailscope_ai.answer'):
            for attempt in (True, False, 0, 3, -1, 123456789.987, '1', [], {}):
                obs.record_validation(attempt, 'rejected', PRIVATE)
            for outcome in (PRIVATE, [], {}, None, 1):
                obs.record_validation(1, outcome, PRIVATE)
            obs.record_validation(1, 'rejected', {'secret': PRIVATE})
            obs.record_validation(1, 'rejected', 'response_schema')
            obs.record_validation(2, 'passed')
            obs.record_validation(1, 'rejected', 'response_schema')
            obs.record_validation(2, 'rejected', 'response_schema')
        data = self.payloads('transaction')[0]['contexts']['trace']['data']
        self.assertEqual(data[P + 'attempt_1.reason'], 'validation_unknown')
        self.assertEqual(data[P + 'attempt_2.outcome'], 'passed')
        self.assertEqual(len(self.payloads('log')), 4)
        self.assert_private()

    def test_every_reason_survives_but_direct_scrubber_injection_does_not(self):
        for reason in obs.VALIDATION_REASONS:
            with obs.start_agent_transaction('retailscope_ai.answer'):
                obs.record_validation(1, 'rejected', reason)
        self.assertEqual({t['contexts']['trace']['data'][P + 'reason']
                          for t in self.payloads('transaction')}, obs.VALIDATION_REASONS)
        raw = {P + 'attempt': 1, P + 'outcome': 'rejected', P + 'reason': PRIVATE,
               P + 'prompt': PRIVATE, P + 'number': 123456789.987}
        self.assertEqual(obs._validation_fields(raw, history=True), {})
        raw[P + 'reason'] = 'numeric_provenance'
        raw.update({P + 'attempt_99.reason': PRIVATE})
        self.assertEqual(set(obs._validation_fields(raw, history=True)),
                         {P + 'attempt', P + 'outcome', P + 'reason'})
        self.assertNotIn(PRIVATE, json.dumps(obs._validation_fields(raw, history=True)))
        self.assert_private()

    def test_disabled_unowned_outside_answer_and_sdk_failures_are_noops(self):
        obs.record_validation(1, 'rejected', 'numeric_provenance')
        with obs.start_agent_transaction('retailscope_ai.research'):
            obs.record_validation(1, 'rejected', 'numeric_provenance')
        with obs.start_agent_transaction('retailscope_ai.answer'):
            with patch.object(obs.sentry_logger, 'info', side_effect=RuntimeError(PRIVATE)):
                obs.record_validation(1, 'rejected', 'numeric_provenance')
            with patch.object(sentry_sdk, 'get_current_scope', side_effect=RuntimeError(PRIVATE)):
                obs.record_validation(2, 'passed')
        with patch.object(obs, '_client', None):
            obs.record_validation(1, 'rejected', 'numeric_provenance')
        with patch.object(obs, 'sentry_sdk', None):
            obs.record_validation(1, 'rejected', 'numeric_provenance')
        self.assertEqual(self.payloads('event'), [])
        self.assert_private()

    def test_failed_repair_provider_is_not_misclassified_as_validation_exhaustion(self):
        with self.assertRaises(RuntimeError):
            with obs.start_agent_transaction('retailscope_ai.answer'):
                obs.record_validation(1, 'rejected', 'numeric_provenance')
                raise RuntimeError(PRIVATE)
        event, = self.payloads('event')
        self.assertEqual(event['fingerprint'], ['retailscope.operation_error'])
        self.assertEqual(event['contexts']['trace']['data'][P + 'reason'], 'numeric_provenance')
        self.assert_private()

    def test_unsampled_trace_still_emits_correlated_diagnostic_logs(self):
        self.clear_client()
        self.assertTrue(obs.init_sentry(dsn='https://public@example.invalid/1',
                                       traces_sample_rate=0, transport=self.sink))
        with obs.start_agent_transaction('retailscope_ai.answer'):
            trace_id = sentry_sdk.get_current_scope().span.trace_id
            obs.record_validation(1, 'rejected', 'quotation_mismatch')
            obs.record_validation(2, 'passed')
        self.assertEqual(self.payloads('transaction'), [])
        logs = self.payloads('log')
        self.assertEqual(len(logs), 4)
        self.assertTrue(all(log['trace_id'] == trace_id for log in logs))
        self.assertEqual(logs[1]['attributes'][P + 'reason']['value'], 'quotation_mismatch')
        self.assert_private()

    def test_all_export_scrubbers_rebuild_diagnostics_and_strip_raw_injected_fields(self):
        data = {P + 'attempt': 2, P + 'outcome': 'rejected', P + 'reason': 'numeric_provenance',
                P + 'attempt_1.attempt': 1, P + 'attempt_1.outcome': 'rejected',
                P + 'attempt_1.reason': PRIVATE, P + 'number': 123456789.987,
                P + 'repair_hint': PRIVATE, P + 'source': PRIVATE}
        trace = {'trace_id': 'a' * 32, 'span_id': 'b' * 16, 'op': 'ai.pipeline', 'data': data}
        event = {'transaction': 'retailscope_ai.answer', 'contexts': {'trace': trace},
                 'tags': {'retailscope.telemetry': 'operation_error'}, 'extra': {'secret': PRIVATE}}
        log = {'body': obs.VALIDATION_MESSAGE, 'trace_id': 'a' * 32, 'span_id': 'b' * 16,
               'time_unix_nano': 1, 'attributes': {'retailscope.telemetry': 'validation',
               'retailscope.workflow': 'retailscope_ai.answer', **data}}
        for scrubber, raw in ((obs._scrub_transaction, event), (obs._scrub_event, event),
                              (obs._scrub_log, log)):
            hint = {'attachments': [PRIVATE]}
            safe = scrubber(raw, hint)
            self.assertIsNotNone(safe)
            encoded = json.dumps(safe)
            self.assertIn('numeric_provenance', encoded)
            self.assertNotIn(PRIVATE, encoded)
            self.assertNotIn('123456789.987', encoded)
            self.assertNotIn('attempt_1', encoded)
            self.assertEqual(hint['attachments'], [])
        for key, value in ((P + 'attempt', True), (P + 'attempt', 3),
                           (P + 'outcome', PRIVATE), (P + 'reason', PRIVATE)):
            contaminated = dict(log, attributes={**log['attributes'], key: value})
            self.assertIsNone(obs._scrub_log(contaminated, {}))
        self.assertIsNone(obs._scrub_log(dict(log, body=PRIVATE), {}))


if __name__ == '__main__':
    unittest.main()
