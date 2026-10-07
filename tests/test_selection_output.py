"""Public runtime messages stay useful without exposing internal model data."""
import unittest
from ezread.selection_output import diagnostic, partial_translation


class OutputTests(unittest.TestCase):
    def test_network_retry_details_are_localized_and_keep_attempt_numbers(self):
        self.assertEqual(diagnostic('Network is not available. Reconnecting... 2/5'),
                         '网络不可用. 正在重新连接... 2/5')
        value = diagnostic('2026-10-07T01:02:03.12Z WARN codex_core::stream: Stream disconnected before completion; retrying 3/5; HTTP status 503')
        self.assertIn('响应在完成前断开', value)
        self.assertIn('正在重试 3/5', value)
        self.assertIn('HTTP 状态码 503', value)
        self.assertNotIn('codex_core', value)

    def test_sensitive_details_are_redacted_and_private_payloads_are_omitted(self):
        raw = 'Network error Authorization=Bearer secret-value access_token="private-value" https://host.invalid/?token=hidden C:\\Users\\Somebody\\auth.json'
        value = diagnostic(raw)
        for secret in ('secret-value', 'private-value', 'host.invalid', 'Somebody', 'auth.json'):
            self.assertNotIn(secret, value)
        self.assertEqual(diagnostic('reasoning: private internal text'), '')
        self.assertEqual(diagnostic('prompt=UNTRUSTED_PAPER_DATA sensitive'), '')
        self.assertIn('unexpected public detail', diagnostic('unexpected public detail'))

    def test_partial_json_translation_handles_split_escape_sequences(self):
        for raw, expected in (
                ('{"translation":"动物', '动物'),
                ('{"translation":"line\\nnext', 'line\nnext'),
                ('{"translation":"word\\', 'word'),
                ('{"translation":"word\\u4', 'word'),
                ('{"translation":"word\\u4e2d', 'word中'),
                ('{"translation":"word\\ud83d', 'word'),
                ('{"translation":"word\\ud83d\\ude00', 'word😀'),
                ('{"translation":"完整"}', '完整'),
                ('{"other":"not translation"}', ''),
                ('unstructured output', '')):
            with self.subTest(raw=raw):
                self.assertEqual(partial_translation(raw), expected)


if __name__ == '__main__':
    unittest.main()
