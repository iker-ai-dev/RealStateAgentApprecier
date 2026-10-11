"""Builder self-checks with real SDK memory envelopes and blocked sockets."""
import importlib.util
import json
import logging
import os
import pathlib
import socket
import sys
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import sentry_sdk
import sentry_sdk.logger as sentry_logger
from sentry_sdk.transport import Transport

SOURCE = pathlib.Path(__file__).resolve().parents[1] / 'app' / 'observability.py'
spec = importlib.util.spec_from_file_location('safe_observability', SOURCE)
obs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obs)
CANARY = 'PRIVATE_PROPERTY_ACCOUNT_URL_INPUT_OUTPUT_EXCEPTION_LOCALS_KEY_487'
DSN = 'https://public@example.invalid/1'


class MemoryTransport(Transport):
    def __init__(self):
        super().__init__()
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.sockets = patch('socket.socket', side_effect=AssertionError('Network prohibited'))
        self.socket_mock = self.sockets.start()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.secret_patch = patch.object(obs, 'st', types.SimpleNamespace(secrets={}))
        self.secret_patch.start()
        self.clear_clients()
        self.transport = MemoryTransport()
        self.assertTrue(obs.init_sentry(dsn=DSN, traces_sample_rate=1.0, transport=self.transport))

    def clear_clients(self):
        sentry_sdk.get_client().close()
        for scope in (sentry_sdk.get_current_scope(), sentry_sdk.get_isolation_scope(), sentry_sdk.get_global_scope()):
            scope.clear()
            scope.set_client(None)
        obs._client = None

    def tearDown(self):
        self.clear_clients()
        self.secret_patch.stop()
        self.env.stop()
        self.sockets.stop()
        self.assertEqual(self.socket_mock.call_count, 0)

    def payloads(self):
        return [item.payload.json for e in self.transport.envelopes for item in e.items
                if item.type in ('event', 'transaction')]

    def logs(self):
        sentry_sdk.flush(timeout=1)
        return [log for e in self.transport.envelopes for item in e.items
                if item.type == 'log' for log in item.payload.json['items']]

    def contaminate(self):
        for scope in (sentry_sdk.get_global_scope(), sentry_sdk.get_isolation_scope(), sentry_sdk.get_current_scope()):
            scope.set_user({'email': CANARY})
            scope.set_tag('property.id', CANARY)
            scope.set_extra('property', CANARY)
            scope.set_attribute('private.scope', CANARY)
            scope.set_context('request', {'url': CANARY, 'data': CANARY})
            scope.add_attachment(bytes=CANARY.encode(), filename=CANARY+'.txt', add_to_transactions=True)
        sentry_sdk.add_breadcrumb(message=CANARY, data={'Authorization': CANARY})

    def assert_safe(self):
        sentry_sdk.flush(timeout=1)
        for envelope in self.transport.envelopes:
            raw = envelope.serialize()
            self.assertNotIn(CANARY.encode(), raw)
            for field in (b'"request"', b'"locals"', b'"stacktrace"', b'"user"',
                          b'"extra"', b'"breadcrumbs"', b'"dynamic_sampling_context"'):
                self.assertNotIn(field, raw)
            self.assertTrue(all(item.type in ('event', 'transaction', 'log') for item in envelope.items))

    def test_all_existing_apis_ignore_property_data_and_ai_arguments_results(self):
        self.contaminate()
        obs.set_agent_context(CANARY, CANARY, CANARY)
        def fn(question):
            return question
        wrapped = obs.ai_track(CANARY)(fn)
        self.assertIs(wrapped, fn)
        self.assertEqual(wrapped(CANARY), CANARY)
        with obs.start_agent_transaction('retailscope_ai.answer', {'property_id': CANARY}) as tx:
            self.assertIsNone(tx)
            for op, name in obs.STEPS.items():
                with obs.start_span(op, name, {'input': CANARY, 'output': CANARY}) as span:
                    self.assertIsNone(span)
                    sentry_sdk.get_current_scope().span.set_data('leak', CANARY)
        self.assertEqual(len(self.payloads()), 1)
        self.assertEqual(len(self.payloads()[0]['spans']), len(obs.STEPS))
        self.assert_safe()

    def test_raw_exception_message_chain_locals_request_never_survive(self):
        self.contaminate()
        try:
            private_local = CANARY
            try:
                raise RuntimeError(CANARY)
            except RuntimeError as exc:
                raise ValueError(CANARY) from exc
        except ValueError as exc:
            self.assertIsNone(obs.capture_exception(exc))
        payload = self.payloads()[0]
        self.assertEqual(payload['exception']['values'], [{'type': 'OperationError', 'value': obs.ERROR_MESSAGE}])
        self.assert_safe()

    def test_unapproved_raw_events_unknown_transactions_and_dynamic_spans_are_dropped(self):
        self.assertIsNone(sentry_sdk.capture_exception(ValueError(CANARY)))
        self.assertIsNone(sentry_sdk.capture_message(CANARY))
        with sentry_sdk.start_transaction(name=CANARY, op=CANARY):
            pass
        with obs.start_agent_transaction(CANARY):
            pass
        self.assertEqual(self.payloads(), [])
        with obs.start_agent_transaction('retailscope_ai.answer'):
            with obs.start_span('mcp.search', CANARY):
                pass
            with sentry_sdk.start_span(op='http.client', name=CANARY) as span:
                span.set_data('url', CANARY)
        self.assertEqual(self.payloads()[0]['spans'], [])
        self.assert_safe()

    def test_failure_captures_sanitized_error_inside_active_trace(self):
        self.contaminate()
        with self.assertRaises(ValueError):
            with obs.start_agent_transaction('retailscope_ai.answer'):
                with obs.start_span('mcp.search', 'nimble.search'):
                    raise ValueError(CANARY)
        self.assertEqual(len(self.payloads()), 2)
        error, p = self.payloads()
        self.assertEqual(p['contexts']['trace']['status'], 'internal_error')
        self.assertEqual(p['spans'][0]['status'], 'internal_error')
        self.assertEqual(error['contexts']['trace']['trace_id'], p['contexts']['trace']['trace_id'])
        self.assertEqual(error['contexts']['trace']['span_id'], p['contexts']['trace']['span_id'])
        self.assertEqual(error['transaction'], p['transaction'])
        self.assertEqual([log['body'] for log in self.logs()],
                         [obs.WORKFLOW_MESSAGES['started'], obs.WORKFLOW_MESSAGES['failed']])
        self.assertTrue(all(log['trace_id'] == p['contexts']['trace']['trace_id'] for log in self.logs()))
        self.assert_safe()

    def test_automatic_capture_disabled_only_scrubbed_manual_logs_enabled(self):
        client = sentry_sdk.get_client()
        self.assertEqual(client.integrations, {})
        for option in ('default_integrations', 'auto_enabling_integrations', 'include_local_variables',
                       'include_source_context', 'attach_stacktrace', 'auto_session_tracking',
                       'enable_metrics', 'propagate_traces', 'stream_gen_ai_spans',
                       'send_client_reports', 'enable_backpressure_handling'):
            self.assertFalse(client.options[option], option)
        self.assertTrue(client.options['enable_logs'])
        self.assertIs(client.options['before_send_log'], obs._scrub_log)
        for option in ('profiles_sample_rate', 'profile_session_sample_rate', 'max_breadcrumbs'):
            self.assertEqual(client.options[option], 0)
        self.assertEqual(client.options['trace_propagation_targets'], [])
        self.assertEqual(client.options['trace_lifecycle'], 'static')
        self.assertEqual(client.options['max_request_body_size'], 'never')
        logger = logging.getLogger('privacy-test')
        handler = logging.NullHandler()
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.error(CANARY)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(self.payloads(), [])
        sentry_logger.info(CANARY, attributes={'private': CANARY})
        self.assertEqual(self.logs(), [])

    def test_successful_workflows_log_only_static_fields_with_root_correlation(self):
        self.contaminate()
        for name in obs.TRANSACTIONS:
            with obs.start_agent_transaction(name, {'property': CANARY}):
                with obs.start_span('discovery.scan', obs.STEPS['discovery.scan'], {'input': CANARY}):
                    pass
        transactions = self.payloads()
        logs = self.logs()
        self.assertEqual(len(transactions), len(obs.TRANSACTIONS))
        self.assertEqual(len(logs), 2 * len(obs.TRANSACTIONS))
        for i, transaction in enumerate(transactions):
            trace = transaction['contexts']['trace']
            self.assertEqual(trace['status'], 'ok')
            self.assertEqual(transaction['spans'][0]['status'], 'ok')
            self.assertIn('timestamp', transaction)
            self.assertIn('start_timestamp', transaction)
            for log, phase in zip(logs[2*i:2*i+2], ('started', 'completed')):
                self.assertEqual(log['trace_id'], trace['trace_id'])
                self.assertEqual(log['span_id'], trace['span_id'])
                self.assertEqual(log['body'], obs.WORKFLOW_MESSAGES[phase])
                self.assertEqual(log['level'], 'info')
                self.assertEqual(log['attributes']['retailscope.workflow']['value'], transaction['transaction'])
                self.assertEqual(set(log['attributes']), {
                    'retailscope.workflow', 'retailscope.phase', 'retailscope.telemetry',
                    'sentry.environment', 'sentry.release', 'sentry.severity_number', 'sentry.severity_text'})
        self.assert_safe()

    def test_caught_failure_keeps_failed_status_and_child_error_link(self):
        self.contaminate()
        with obs.start_agent_transaction('retailscope_ai.discovery_source'):
            with obs.start_span('discovery.scan', obs.STEPS['discovery.scan']):
                child = sentry_sdk.get_current_scope().span
                obs.capture_exception(ValueError(CANARY))
        error, transaction = self.payloads()
        self.assertEqual(error['contexts']['trace']['span_id'], child.span_id)
        self.assertEqual(error['contexts']['trace']['trace_id'], transaction['contexts']['trace']['trace_id'])
        self.assertEqual(transaction['contexts']['trace']['status'], 'internal_error')
        self.assertEqual(transaction['spans'][0]['status'], 'internal_error')
        self.assertEqual([log['body'] for log in self.logs()],
                         [obs.WORKFLOW_MESSAGES['started'], obs.WORKFLOW_MESSAGES['failed']])
        self.assert_safe()

    def test_captured_then_rethrown_error_is_not_duplicated(self):
        with self.assertRaises(ValueError):
            with obs.start_agent_transaction('retailscope_ai.mcp.get_context'):
                try:
                    raise ValueError(CANARY)
                except ValueError as exc:
                    obs.capture_exception(exc)
                    raise
        self.assertEqual(len(self.payloads()), 2)
        self.assertEqual(len(self.logs()), 2)
        self.assert_safe()

    def test_sdk_log_and_capture_failures_do_not_break_or_replace_application_behavior(self):
        with patch.object(obs.sentry_logger, 'info', side_effect=RuntimeError(CANARY)), \
             patch.object(obs.sentry_logger, 'error', side_effect=RuntimeError(CANARY)), \
             patch.object(sentry_sdk, 'capture_event', side_effect=RuntimeError(CANARY)):
            with obs.start_agent_transaction('retailscope_ai.mcp.list_centers'):
                result = 42
            self.assertEqual(result, 42)
            original = ValueError('application failure')
            with self.assertRaises(ValueError) as caught:
                with obs.start_agent_transaction('retailscope_ai.mcp.get_context'):
                    raise original
            self.assertIs(caught.exception, original)
        self.assertEqual([p['contexts']['trace']['status'] for p in self.payloads()],
                         ['ok', 'internal_error'])
        self.assertEqual(self.logs(), [])
        self.assert_safe()

    def test_owned_client_and_log_correlation_work_in_source_worker_threads(self):
        self.contaminate()
        def worker(fail):
            self.assertTrue(obs._enabled())
            with obs.start_agent_transaction('retailscope_ai.discovery_source', {'source': CANARY}):
                with obs.start_span('discovery.scan', obs.STEPS['discovery.scan']):
                    if fail:
                        raise ValueError(CANARY)
                with obs.start_span('discovery.screen', obs.STEPS['discovery.screen']):
                    pass
        with obs.start_agent_transaction('retailscope_ai.discovery'):
            parent_id = sentry_sdk.get_current_scope().span.trace_id
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = [pool.submit(worker, fail) for fail in (False, True, False)]
                for future, fail in zip(results, (False, True, False)):
                    if fail:
                        with self.assertRaises(ValueError):
                            future.result()
                    else:
                        future.result()
        transactions = [p for p in self.payloads() if p.get('type') == 'transaction']
        errors = [p for p in self.payloads() if p.get('level') == 'error']
        self.assertEqual(len(transactions), 4)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len({p['contexts']['trace']['trace_id'] for p in transactions}), 4)
        logs = self.logs()
        self.assertEqual(len(logs), 8)
        for transaction in transactions:
            trace = transaction['contexts']['trace']
            phases = [log['attributes']['retailscope.phase']['value']
                      for log in logs if log['trace_id'] == trace['trace_id']]
            self.assertEqual(phases, ['started', 'failed' if trace['status'] == 'internal_error' else 'completed'])
        failed = next(p for p in transactions if p['contexts']['trace']['status'] == 'internal_error')
        self.assertEqual(errors[0]['contexts']['trace']['trace_id'], failed['contexts']['trace']['trace_id'])
        self.assertNotEqual(errors[0]['contexts']['trace']['trace_id'], parent_id)
        self.assert_safe()

    def test_log_hook_rebuilds_marker_spoofed_payload_and_drops_unknown_logs(self):
        log = {'body': obs.WORKFLOW_MESSAGES['started'], 'severity_text': CANARY,
               'severity_number': 99, 'time_unix_nano': 1,
               'trace_id': 'a'*32, 'span_id': 'b'*16, 'private': CANARY,
               'attributes': {'retailscope.telemetry': 'workflow',
                              'retailscope.workflow': 'retailscope_ai.answer',
                              'retailscope.phase': 'started', 'property': CANARY,
                              'sentry.environment': CANARY, 'sentry.release': CANARY}}
        safe = obs._scrub_log(log, {})
        self.assertNotIn(CANARY, json.dumps(safe))
        self.assertEqual(safe['severity_text'], 'info')
        for key, value in (('body', CANARY), ('trace_id', CANARY), ('span_id', CANARY),
                           ('time_unix_nano', True), ('time_unix_nano', -1), ('attributes', [])):
            self.assertIsNone(obs._scrub_log(dict(log, **{key: value}), {}))
        for key in ('retailscope.workflow', 'retailscope.phase', 'retailscope.telemetry'):
            attrs = dict(log['attributes'], **{key: CANARY})
            self.assertIsNone(obs._scrub_log(dict(log, attributes=attrs), {}))
        self.contaminate()
        with obs.start_agent_transaction('retailscope_ai.answer'):
            sentry_logger.info(CANARY, attributes=log['attributes'])
        self.assertEqual(len(self.logs()), 2)
        self.assert_safe()

    def test_error_trace_hook_drops_unknown_ops_and_private_context_fields(self):
        raw = {'tags': {'retailscope.telemetry': 'operation_error'}, 'transaction': CANARY,
               'contexts': {'trace': {'op': 'mcp.server', 'trace_id': 'a'*32, 'span_id': 'b'*16,
                                     'status': 'internal_error', 'private': CANARY,
                                     'dynamic_sampling_context': {'private': CANARY}},
                            'private': {'data': CANARY}}}
        safe = obs._scrub_event(raw, {})
        self.assertEqual(safe['contexts']['trace'], {'op': 'mcp.server', 'trace_id': 'a'*32,
                         'span_id': 'b'*16, 'status': 'internal_error'})
        self.assertNotIn(CANARY, json.dumps(safe))
        raw['contexts']['trace']['op'] = CANARY
        self.assertNotIn('contexts', obs._scrub_event(raw, {}))

    def test_runtime_labels_and_sha_release_are_fixed_and_scrubbed_consistently(self):
        for runtime, env_vars, expected_environment, expected_release in (
                ('local', {'SENTRY_ENVIRONMENT': CANARY, 'SENTRY_RELEASE': CANARY}, 'application', obs.RELEASE),
                ('container', {'RETAILSCOPE_PUBLIC_DEMO': 'true'}, 'render-demo', obs.RELEASE),
                ('local', {'RENDER': 'true', 'RENDER_GIT_COMMIT': 'A'*40}, 'render-demo', 'a'*40),
                ('render', {'RENDER_GIT_COMMIT': CANARY, 'GITHUB_SHA': 'b'*40}, 'render-demo', 'b'*40),
                ('local', {'GITHUB_SHA': 'b'*39}, 'application', obs.RELEASE),
                ('local', {'GITHUB_SHA': 'b'*40+'\n'}, 'application', obs.RELEASE)):
            self.clear_clients()
            with patch.dict(os.environ, env_vars, clear=True):
                self.assertTrue(obs.init_sentry(runtime=runtime, dsn=DSN, traces_sample_rate=1,
                                                transport=self.transport))
                client = sentry_sdk.get_client()
                self.assertEqual(client.options['environment'], expected_environment)
                self.assertEqual(client.options['release'], expected_release)
                with obs.start_agent_transaction('retailscope_ai.mcp.list_centers'):
                    pass
                payload = self.payloads()[-1]
                self.assertEqual(payload['environment'], expected_environment)
                self.assertEqual(payload['release'], expected_release)
                log = self.logs()[-1]
                self.assertEqual(log['attributes']['sentry.environment']['value'], expected_environment)
                self.assertEqual(log['attributes']['sentry.release']['value'], expected_release)
        self.assertEqual(obs._runtime_labels('warehouse')[0], 'snowflake')
        self.assert_safe()

    def test_hooks_reconstruct_even_marker_spoofed_raw_event(self):
        self.contaminate()
        sentry_sdk.capture_event({'message': CANARY, 'tags': {'retailscope.telemetry':'operation_error', 'property':CANARY},
                                 'request': {'url': CANARY}, 'exception': {'values':[{'type': CANARY, 'value':CANARY}]},
                                 'extra': {'locals': CANARY}})
        self.assertEqual(self.payloads()[0]['tags'], {'retailscope.telemetry': 'operation_error'})
        self.assert_safe()

    def test_configuration_is_not_read_before_sdk_exists(self):
        self.clear_clients()
        with patch.object(obs, 'sentry_sdk', None), patch.object(obs, '_resolve_dsn') as secret:
            self.assertFalse(obs.init_sentry())
            self.assertFalse(secret.called)
            obs.capture_exception(ValueError(CANARY))
            with obs.start_agent_transaction('retailscope_ai.answer'):
                pass
        self.assertEqual(self.payloads(), [])

    def test_missing_dsn_does_not_initialize_and_disabled_rerun_stops_helpers(self):
        self.assertFalse(obs.init_sentry(dsn=None))
        obs.capture_exception(ValueError(CANARY))
        with obs.start_agent_transaction('retailscope_ai.answer'):
            pass
        self.assertEqual(self.payloads(), [])
        self.clear_clients()
        with patch.object(sentry_sdk, 'init') as init:
            self.assertFalse(obs.init_sentry())
            self.assertFalse(init.called)

    def test_local_secrets_and_env_dsn_and_explicit_empty_disable(self):
        self.clear_clients()
        obs.st.secrets = {'sentry': {'dsn': DSN, 'traces_sample_rate':'1',
                                     'release': CANARY, 'environment': CANARY}}
        self.assertTrue(obs.init_sentry(transport=self.transport))
        self.clear_clients()
        obs.st.secrets = {}
        with patch.dict(os.environ, {'SENTRY_DSN': DSN}):
            self.assertTrue(obs.init_sentry(transport=self.transport))
            self.clear_clients()
            obs.st.secrets = {'sentry': {'dsn': ''}}
            self.assertFalse(obs.init_sentry(transport=self.transport))
            self.assertFalse(obs.init_sentry(dsn=None, transport=self.transport))

    def test_warehouse_uses_managed_alias_never_env_or_toml_or_explicit_local(self):
        self.clear_clients()
        warehouse = types.SimpleNamespace(get_generic_secret_string=lambda alias: '')
        obs.st.secrets = {'sentry': {'dsn': DSN}}
        with patch.dict(sys.modules, {'_snowflake': warehouse}), patch.dict(os.environ, {'SENTRY_DSN': DSN}):
            with patch.object(obs, '_sentry_config', side_effect=AssertionError('No local fallback')):
                self.assertFalse(obs.init_sentry(runtime='local', dsn=DSN, transport=self.transport))
                warehouse.get_generic_secret_string = lambda alias: DSN if alias == 'SENTRY_DSN' else ''
                self.assertTrue(obs.init_sentry(runtime='warehouse', transport=self.transport))
                self.clear_clients()
                warehouse.get_generic_secret_string = lambda alias: (_ for _ in ()).throw(ValueError(CANARY))
                self.assertFalse(obs.init_sentry(runtime='warehouse', transport=self.transport))

    def test_existing_foreign_sdk_is_not_adopted_and_own_init_is_idempotent(self):
        original = sentry_sdk.get_client()
        self.assertTrue(obs.init_sentry(dsn=DSN, transport=self.transport))
        self.assertIs(sentry_sdk.get_client(), original)
        obs._client = None  # Simulate a client initialized outside this module.
        self.assertFalse(obs.init_sentry(dsn=DSN, transport=self.transport))
        obs.capture_exception(ValueError(CANARY))
        self.assertEqual(self.payloads(), [])

    def test_invalid_dsn_and_invalid_rates_fail_without_echoing_private_details(self):
        self.clear_clients()
        self.assertFalse(obs.init_sentry(dsn=CANARY, transport=self.transport))
        for rate in (float('nan'), float('inf'), -1, 2, True, CANARY):
            self.assertFalse(obs.init_sentry(dsn=DSN, traces_sample_rate=rate, transport=self.transport))

    def test_malformed_payload_and_timestamps_fail_closed(self):
        hint = {'attachments':[CANARY]}
        self.assertIsNone(obs._scrub_event({'tags':[CANARY]}, hint))
        self.assertEqual(hint['attachments'], [])
        safe = obs._scrub_transaction({'transaction':'retailscope_ai.answer', 'contexts':[CANARY],
                                      'timestamp':10**1000, 'spans':[None, {'op':['mcp.search']}]}, {})
        self.assertNotIn('timestamp', safe)
        self.assertEqual(safe['spans'], [])
        self.assertNotIn(CANARY, json.dumps(safe))

    def test_import_never_initializes_sdk_or_reads_secrets(self):
        fake_sdk = types.SimpleNamespace(init=unittest.mock.Mock())
        fake_st = types.SimpleNamespace(secrets=unittest.mock.Mock())
        fresh = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'sentry_sdk': fake_sdk, 'streamlit': fake_st}):
            spec.loader.exec_module(fresh)
        fake_sdk.init.assert_not_called()
        fake_st.secrets.assert_not_called()

    def test_missing_sdk_import_graceful_without_importing_ai_monitoring(self):
        import builtins
        real_import = builtins.__import__
        def block_sdk(name, *args, **kwargs):
            if name.startswith('sentry_sdk'):
                raise ImportError('Optional SDK unavailable')
            return real_import(name, *args, **kwargs)
        fresh = importlib.util.module_from_spec(spec)
        with patch.object(builtins, '__import__', side_effect=block_sdk):
            spec.loader.exec_module(fresh)
        self.assertIsNone(fresh.sentry_sdk)
        self.assertFalse(fresh.init_sentry())
        self.assertEqual(fresh.ai_track('label')(lambda: None).__name__, '<lambda>')


if __name__ == '__main__':
    print('Builder self-check, not independent review. SDK', sentry_sdk.VERSION, 'Python', sys.version)
    print('Sentry/API network forbidden; socket.socket blocked; synthetic DSN; memory transport.')
    unittest.main(verbosity=2)
