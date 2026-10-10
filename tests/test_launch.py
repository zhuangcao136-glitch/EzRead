"""Launcher identity checks without starting services or touching real data."""
import importlib.machinery
import importlib.util
import os
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

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
        os.environ.pop('EZREAD_DATA_DIR', None)

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
        os.environ['EZREAD_DATA_DIR'] = 'work/isolated'
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
             patch.object(launch, 'StartupServer') as process:
            with self.assertRaisesRegex(RuntimeError, '端口已被其他服务占用'):
                launch.ensure_server()
            process.assert_not_called()

    def transaction(self, folder):
        patches = [patch.object(launch, 'startup_lock', return_value=nullcontext()),
                   patch.object(launch, 'data_directory', return_value=Path(folder)),
                   patch.object(launch, 'shell_command'), patch.object(launch.time, 'sleep')]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_preflight_failure_never_starts_backend(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            self.transaction(folder)
            with patch.object(launch, 'shell_command', side_effect=RuntimeError('missing component')), \
                 patch.object(launch, 'ensure_server') as ensure:
                with self.assertRaisesRegex(RuntimeError, 'missing component'):
                    launch.start_application()
                ensure.assert_not_called()

    def test_verified_server_exit_between_health_reads_restarts(self):
        status = {'app_root': str(ROOT), 'data_dir': str(ROOT / 'data')}
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            status['data_dir'] = folder
            self.transaction(folder)
            owned = Mock()
            owned.process.poll.return_value = None
            with patch.object(launch, '_read_status', side_effect=[status, OSError('reset'), OSError('refused'), status]), \
                 patch.object(launch, '_port_open', return_value=False), \
                 patch.object(launch, 'StartupServer', return_value=owned):
                self.assertIs(launch.ensure_server(), owned)
            owned.start.assert_called_once()
            owned.cancel.assert_not_called()

    def test_deferred_open_tolerates_backend_exit_during_first_health_read(self):
        status = {'app_root': str(ROOT), 'data_dir': str(ROOT / 'data')}
        with patch.object(launch, '_read_status', side_effect=[OSError('reset'), status, status]), \
             patch.object(launch, '_port_open', return_value=True), \
             patch.object(launch.time, 'sleep'), patch.object(launch, 'StartupServer') as owner:
            self.assertIsNone(launch.ensure_server(wait_for_close=True))
            owner.assert_not_called()

    def test_native_failure_cancels_only_new_server(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            self.transaction(folder)
            owned = Mock()
            with patch.object(launch, 'ensure_server', return_value=owned), \
                 patch.object(launch, 'open_window', side_effect=RuntimeError('native failure')):
                with self.assertRaisesRegex(RuntimeError, 'native failure'):
                    launch.start_application()
            owned.cancel.assert_called_once()
            owned.commit.assert_not_called()

    def test_existing_backend_failure_does_not_create_or_cancel_an_owner(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            self.transaction(folder)
            with patch.object(launch, 'ensure_server', return_value=None), \
                 patch.object(launch, 'StartupServer') as owner, \
                 patch.object(launch, 'open_window', side_effect=RuntimeError('native failure')):
                with self.assertRaises(RuntimeError): launch.start_application()
                owner.assert_not_called()

    def test_close_deferred_open_retries_then_commits_new_backend(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            self.transaction(folder)
            owned = Mock()
            with patch.object(launch, 'ensure_server', side_effect=[None, owned]), \
                 patch.object(launch, 'open_window') as opening, \
                 patch.object(launch, 'wait_for_window', side_effect=[3, 0]):
                launch.start_application()
                self.assertEqual(opening.call_count, 2)
            owned.commit.assert_called_once()
            owned.cancel.assert_not_called()

    def test_unready_native_report_cleans_only_spawned_process(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            report = Path(folder) / 'ready.json'
            report.write_text(json.dumps({'ready': False, 'process_id': 42}))
            process = Mock(pid=42)
            process.poll.return_value = None
            with self.assertRaisesRegex(RuntimeError, '初始化'):
                launch.wait_for_window(process, report)
            process.kill.assert_called_once()

    def test_startup_ready_requires_matching_process_id(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as folder:
            report = Path(folder) / 'ready.json'
            process = Mock(pid=42)
            process.poll.return_value = None
            report.write_text(json.dumps({'ready': True, 'process_id': 42}))
            self.assertEqual(launch.wait_for_window(process, report), 0)
            process.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
