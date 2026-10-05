"""Launcher identity checks without starting services or touching real data."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ezread_launch_test', str(ROOT / 'launch.pyw'))
spec = importlib.util.spec_from_loader(loader.name, loader)
launch = importlib.util.module_from_spec(spec)
loader.exec_module(launch)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        os.environ.pop('TUDU_DATA_DIR', None)

    def tearDown(self):
        self.environment.stop()

    def test_accepts_this_checkout_and_library(self):
        with patch.object(launch, '_read_status', return_value={'app_root': str(ROOT), 'data_dir': str(ROOT / 'data')}):
            self.assertTrue(launch.running())

    def test_rejects_different_library(self):
        with patch.object(launch, '_read_status', return_value={'app_root': str(ROOT), 'data_dir': str(ROOT / 'other')}):
            with self.assertRaisesRegex(RuntimeError, '另一处文献库'):
                launch.running()

    def test_rejects_old_checkout_even_for_same_library(self):
        with patch.object(launch, '_read_status', return_value={'app_root': str(ROOT / 'old'), 'data_dir': str(ROOT / 'data')}):
            with self.assertRaisesRegex(RuntimeError, '不是当前目录'):
                launch.running()

    def test_respects_relative_override_from_server_working_directory(self):
        os.environ['TUDU_DATA_DIR'] = 'work/isolated'
        expected = (ROOT / 'work/isolated').resolve()
        self.assertEqual(launch.data_directory(), expected)
        with patch.object(launch, '_read_status', return_value={'app_root': str(ROOT), 'data_dir': str(expected)}):
            self.assertTrue(launch.running())

    def test_fallback_identifies_legacy_without_accepting_it(self):
        missing = urllib.error.HTTPError(launch.URL, 404, 'not found', None, None)
        with patch.object(launch, '_read_status', side_effect=[missing, {'data_dir': str(ROOT / 'data')}]) as read:
            with self.assertRaisesRegex(RuntimeError, '旧版本'):
                launch.running()
        self.assertEqual([call.args[0] for call in read.call_args_list], ['/api/health', '/api/status'])

    def test_free_port_allows_start(self):
        with patch.object(launch, '_read_status', side_effect=OSError('connection refused')), \
             patch.object(launch, '_port_open', return_value=False):
            self.assertFalse(launch.running())

    def test_unknown_listener_is_not_overwritten_or_killed(self):
        with patch.object(launch, '_read_status', side_effect=ValueError('not JSON')), \
             patch.object(launch, '_port_open', return_value=True), \
             patch.object(launch.subprocess, 'Popen') as process:
            with self.assertRaisesRegex(RuntimeError, '端口已被其他服务占用'):
                launch.ensure_server()
            process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
