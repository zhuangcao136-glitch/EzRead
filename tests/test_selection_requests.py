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


if __name__ == '__main__': unittest.main()
