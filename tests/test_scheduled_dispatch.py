import io
import json
import unittest
from unittest.mock import patch

from scripts.run_scheduled_searches import main


def response(body):
    return io.StringIO(json.dumps(body))


@patch.dict('os.environ', {'APP_URL': 'https://example.com', 'RETAILSCOPE_SCHEDULER_SECRET': 'x' * 64})
class DispatchTests(unittest.TestCase):
    def test_empty_tick_does_not_send_credentials_to_health_endpoint(self):
        with patch('urllib.request.urlopen', side_effect=[response({'status': 'ok'}), response({'ran': False})]) as call:
            main()
        self.assertEqual(call.call_args_list[0].args, ('https://example.com/healthz',))
        request = call.call_args_list[1].args[0]
        self.assertEqual(request.full_url, 'https://example.com/internal/schedules/tick')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + 'x' * 64)

    def test_ambiguous_post_failure_is_never_retried(self):
        with patch('urllib.request.urlopen', side_effect=[response({'status': 'ok'}), OSError('private upstream error')]) as call:
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(call.call_count, 2)
        self.assertNotIn('private upstream', str(error.exception))

    def test_pre_provider_admission_is_retried_but_bounded(self):
        responses = [response({'status': 'ok'})]
        for _ in range(4):
            responses.extend([response({'deferred': True}), response({'ran': False})])
        with patch('urllib.request.urlopen', side_effect=responses), patch('time.sleep') as sleep:
            with self.assertRaises(SystemExit):
                main()
        self.assertEqual(sleep.call_count, 3)

    def test_twenty_successful_runs_are_bounded_and_not_reported_as_failure(self):
        responses = [response({'status': 'ok'})] + [response({'ran': True, 'status': 'completed'}) for _ in range(20)]
        with patch('urllib.request.urlopen', side_effect=responses) as call:
            main()
        self.assertEqual(call.call_count, 21)

    def test_partial_run_surfaces_failure_after_other_due_work_finishes(self):
        responses = [response({'status': 'ok'}), response({'ran': True, 'status': 'partial'}), response({'ran': False})]
        with patch('urllib.request.urlopen', side_effect=responses):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertIn('1 scheduled searches', str(error.exception))
