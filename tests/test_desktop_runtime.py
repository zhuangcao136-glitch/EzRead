"""Native launcher contract; no services, model requests or real library writes."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import desktop_runtime


class DesktopRuntimeTests(unittest.TestCase):
    def test_native_window_gets_exact_library_and_source_without_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'Chinese path 中文 with spaces'
            binary = root / 'desktop' / 'bin'
            binary.mkdir(parents=True)
            for name in ('EzRead.Desktop.exe', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll'):
                (binary / name).touch()
            command = desktop_runtime.shell_command(root, root / 'private data', 'http://127.0.0.1:47831')
            # Windows TEMP may use an 8.3 alias; the launcher uses canonical paths.
            expected_root = root.resolve()
            expected_data = (root / 'private data').resolve()
            self.assertEqual(command, [str(expected_root / 'desktop' / 'bin' / 'EzRead.Desktop.exe'),
                                      '--app-root', str(expected_root), '--data-dir', str(expected_data),
                                      '--url', 'http://127.0.0.1:47831'])

    def test_missing_dll_does_not_fall_back_to_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, '桌面组件尚未构建'):
                desktop_runtime.shell_command(folder, folder, 'http://127.0.0.1:47831')

    def test_migration_failure_keeps_native_start_available_and_records_only_error_type(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(desktop_runtime.os, 'name', 'nt'), \
             patch.object(desktop_runtime, 'shell_command', return_value=['EzRead.Desktop.exe']), \
             patch('browser_state.prepare_migration', side_effect=ValueError('private draft content')), \
             patch.object(desktop_runtime.subprocess, 'Popen') as process:
            desktop_runtime.open_desktop(folder, folder, 'http://127.0.0.1:47831')
            self.assertNotIn('private draft content', (Path(folder) / 'desktop.log').read_text())
            process.assert_called_once()
            self.assertEqual(process.call_args.args[0], ['EzRead.Desktop.exe'])


if __name__ == '__main__': unittest.main()
