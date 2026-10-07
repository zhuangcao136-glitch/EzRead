"""Cancellation identity/race checks independent of HTTP and model access."""
import unittest
from codex_bridge import TranslationError
from ezread.selection_requests import SelectionRequests

PID = '0123456789abcdef'


def request(number=1, client='a'):
    return {'client_id': client * 32, 'request_id': f'request-{number:024d}'}


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.jobs = SelectionRequests()
    def test_cancel_before_post_prevents_later_inference(self):
        data = request()
        self.jobs.cancel(PID, data)
        with self.assertRaises(TranslationError) as caught:
            self.jobs.begin(PID, data)
        self.assertEqual(caught.exception.code, 'cancelled')
    def test_new_request_cancels_old_and_old_cancel_cannot_cancel_new(self):
        one, two = request(1), request(2)
        first = self.jobs.begin(PID, one)
        second = self.jobs.begin(PID, two)
        self.assertTrue(first.is_set())
        self.jobs.cancel(PID, one)
        self.jobs.finish(PID, one, first)
        self.assertFalse(second.is_set())
        self.assertIn(two['client_id'], self.jobs.active)
        self.jobs.cancel(PID, two)
        self.assertTrue(second.is_set())
    def test_other_windows_and_papers_do_not_cancel_a_request(self):
        one, two = request(1), request(2, client='b')
        first = self.jobs.begin(PID, one)
        second = self.jobs.begin(PID, two)
        self.jobs.cancel('fedcba9876543210', one)
        self.assertFalse(first.is_set())
        self.jobs.cancel(PID, two)
        self.assertFalse(first.is_set())
        self.assertTrue(second.is_set())
    def test_duplicate_and_bad_identifiers_do_not_replace_active_request(self):
        data = request()
        first = self.jobs.begin(PID, data)
        with self.assertRaises(ValueError): self.jobs.begin(PID, data)
        with self.assertRaises(ValueError): self.jobs.cancel(PID, {'request_id': 'bad'})
        self.assertFalse(first.is_set())
    def test_shutdown_cancels_active_requests(self):
        first = self.jobs.begin(PID, request())
        self.jobs.shutdown()
        self.assertTrue(first.is_set())
    def test_progress_is_request_scoped_and_survives_completion(self):
        one, two = request(1), request(2, client='b')
        event = self.jobs.begin(PID, one)
        self.jobs.begin(PID, two)
        self.jobs.report(PID, one, {'text': '网络不可用，正在重连', 'kind': 'diagnostic'})
        self.jobs.report(PID, one, {'text': '部分译文', 'kind': 'output'})
        self.jobs.report(PID, one, {'text': '正在返回当前译文'})
        snapshot = self.jobs.snapshot(PID, one)
        self.assertFalse(snapshot['done'])
        self.assertEqual(snapshot['output'], '部分译文')
        self.assertEqual(len(snapshot['events']), 1)
        self.assertEqual(snapshot['events'][0]['text'], '正在返回当前译文')
        self.assertEqual(self.jobs.snapshot(PID, two)['events'], [])
        snapshot['events'][0]['text'] = 'mutated client copy'
        self.jobs.finish(PID, one, event)
        final = self.jobs.snapshot(PID, one)
        self.assertTrue(final['done'])
        self.assertEqual(final['events'][0]['text'], '正在返回当前译文')
        self.jobs.report(PID, one, {'text': 'late stale event'})
        self.assertEqual(len(self.jobs.snapshot(PID, one)['events']), 1)


if __name__ == '__main__': unittest.main()
