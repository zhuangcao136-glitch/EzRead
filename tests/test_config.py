"""Renamed configuration must keep the existing library and CLI selection."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import codex_bridge
from ezread.config import data_directory, environment_value


class ConfigurationTests(unittest.TestCase):
    def test_default_and_relative_paths_belong_to_the_checkout(self):
        self.assertEqual(data_directory(ROOT, {}), ROOT / 'data')
        self.assertEqual(data_directory(ROOT, {'EZREAD_DATA_DIR': 'work/isolated'}), ROOT / 'work' / 'isolated')

    def test_new_directory_wins_and_explicit_empty_does_not_restore_old_path(self):
        values = {'EZREAD_DATA_DIR': 'work/new', 'TUDU_DATA_DIR': 'work/previous'}
        self.assertEqual(data_directory(ROOT, values), ROOT / 'work' / 'new')
        values['EZREAD_DATA_DIR'] = ''
        self.assertEqual(data_directory(ROOT, values), ROOT / 'data')

    def test_old_directory_still_resolves_to_the_existing_library(self):
        self.assertEqual(data_directory(ROOT, {'TUDU_DATA_DIR': 'work/existing'}), ROOT / 'work' / 'existing')

    def test_model_and_executable_aliases_use_the_same_precedence(self):
        for kind in ('CODEX_PATH', 'CODEX_MODEL'):
            new, previous = 'EZREAD_' + kind, 'TUDU_' + kind
            self.assertEqual(environment_value(new, environment={previous: 'existing'}), 'existing')
            self.assertEqual(environment_value(new, environment={new: 'chosen', previous: 'existing'}), 'chosen')
            self.assertEqual(environment_value(new, environment={new: '', previous: 'existing'}), '')

    def test_bridge_selects_the_new_explicit_cli_without_running_it(self):
        with tempfile.TemporaryDirectory() as temp:
            chosen, previous = Path(temp) / 'chosen.exe', Path(temp) / 'previous.exe'
            chosen.write_bytes(b'not an executable')
            previous.write_bytes(b'not an executable')
            with patch.dict(os.environ, {'EZREAD_CODEX_PATH': str(chosen), 'TUDU_CODEX_PATH': str(previous)}):
                self.assertEqual(codex_bridge._cli(), str(chosen.resolve()))


if __name__ == '__main__':
    unittest.main()
