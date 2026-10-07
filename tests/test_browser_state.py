"""Legacy browser migration: log versions, deletes, corruption and scope."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import browser_state as storage


def vi(number):
    result = bytearray()
    while number >= 128:
        result.append((number & 127) | 128); number >>= 7
    result.append(number)
    return bytes(result)


def encoded(value): return b'\x00' + value.encode('utf-16-le')


def batch(sequence, changes):
    result = struct.pack('<QI', sequence, len(changes))
    for origin, name, value in changes:
        key = b'_' + origin.encode('ascii') + b'\0' + encoded(name)
        result += bytes([0 if value is None else 1]) + vi(len(key)) + key
        if value is not None:
            data = encoded(value); result += vi(len(data)) + data
    crc = storage.crc32c(b'\x01' + result)
    crc = (((crc >> 15) | (crc << 17)) + 0xa282ead8) & 0xffffffff
    return struct.pack('<IHB', crc, len(result), 1) + result


class BrowserMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.folder = self.directory / 'browser-profile' / 'Default' / 'Local Storage' / 'leveldb'
        self.folder.mkdir(parents=True)
        self.origin = 'http://127.0.0.1:47831'

    def tearDown(self): self.tmp.cleanup()

    def test_latest_sequence_and_tombstone_override_older_records(self):
        (self.folder / '000001.log').write_bytes(batch(5, [(self.origin, 'ezread-sort', 'year'), (self.origin, 'ezread-reader-notes:paper', '草稿')]))
        (self.folder / '000002.log').write_bytes(batch(10, [(self.origin, 'ezread-sort', 'recent'), (self.origin, 'ezread-reader-notes:paper', None)]))
        self.assertEqual(storage.read_legacy_storage(self.directory, self.origin), {'ezread-sort': 'recent'})

    def test_imports_only_app_keys_from_exact_origin_and_preserves_unicode(self):
        changes = [(self.origin, 'ezread-reader-notes:p', '{"value":"中文草稿"}'), (self.origin, 'readx-preferences', '{}'), (self.origin, 'tudu-sort', 'year'), ('https://example.com', 'ezread-sort', 'wrong'), (self.origin, 'password', 'not-an-app-key')]
        (self.folder / '000001.log').write_bytes(batch(1, changes))
        result = storage.read_legacy_storage(self.directory, self.origin)
        self.assertEqual(len(result), 3)
        self.assertEqual(json.loads(result['ezread-reader-notes:p'])['value'], '中文草稿')

    def test_checksum_failure_does_not_commit_partial_migration(self):
        content = bytearray(batch(1, [(self.origin, 'ezread-sort', 'year')]))
        content[-1] ^= 1
        (self.folder / '000001.log').write_bytes(content)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            storage.prepare_migration(self.directory, self.origin)
        self.assertFalse((self.directory / 'webview2-migration.json').exists())

    def test_committed_snapshot_is_never_replaced_with_stale_browser_state(self):
        (self.folder / '000001.log').write_bytes(batch(1, [(self.origin, 'ezread-sort', 'year')]))
        storage.prepare_migration(self.directory, self.origin)
        (self.folder / '000001.log').write_bytes(batch(2, [(self.origin, 'ezread-sort', 'recent')]))
        storage.prepare_migration(self.directory, self.origin)
        self.assertEqual(json.loads((self.directory / 'webview2-migration.json').read_text(encoding='utf-8')), {'ezread-sort': 'year'})

    def test_snappy_overlap_and_size_validation(self):
        self.assertEqual(storage.snappy(bytes([8, 4]) + b'ab' + bytes([22, 2, 0])), b'abababab')
        with self.assertRaises(ValueError): storage.snappy(bytes([3, 2, 0, 0]))


if __name__ == '__main__': unittest.main()
