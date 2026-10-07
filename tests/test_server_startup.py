"""Server --open must use the native shell and never open an unknown listener."""
import sys
import unittest
from unittest.mock import patch

import server


class ServerStartupTests(unittest.TestCase):
    def run_main(self, binding):
        with patch.object(server, 'init_db'), patch.object(server, 'all_docs', return_value=[]), \
             patch.object(server.threading, 'Thread'), patch.object(server, 'ThreadingHTTPServer', side_effect=binding), \
             patch.object(server, 'CANCEL', {}), patch('codex_usage.shutdown'), patch('codex_models.shutdown'), \
             patch.object(sys, 'argv', ['server.py', '--open', '--port', '51234']), \
             patch('desktop_runtime.open_desktop') as native:
            try:
                server.main()
            except OSError:
                pass
            return native

    def test_port_conflict_never_opens_an_unidentified_listener(self):
        native = self.run_main(OSError('already in use'))
        native.assert_not_called()

    def test_open_passes_this_root_data_and_custom_port_to_native_window(self):
        from unittest.mock import MagicMock
        listener = MagicMock()
        native = self.run_main([listener])
        native.assert_called_once_with(server.ROOT, server.DATA, 'http://127.0.0.1:51234')
        listener.serve_forever.assert_called_once()


if __name__ == '__main__': unittest.main()
