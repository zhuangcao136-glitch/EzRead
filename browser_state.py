"""Read only EzRead localStorage from its legacy Chromium profile.

Small, bounded LevelDB reader; never opens/modifies the live database, and never
reads cookies, passwords or other origins. See LevelDB's table/log format docs.
"""
import json
from pathlib import Path
import struct

LIMIT = 2 * 1024 * 1024


def varint(data, position=0):
    result = 0
    for shift in range(0, 64, 7):
        if position >= len(data):
            raise ValueError('truncated varint')
        value = data[position]; position += 1
        result |= (value & 127) << shift
        if value < 128:
            return result, position
    raise ValueError('oversized varint')


def crc32c(data):
    crc = 0xffffffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82f63b78 if crc & 1 else 0)
    return crc ^ 0xffffffff


def valid_crc(data, stored):
    value = crc32c(data)
    return (((value >> 15) | (value << 17)) + 0xa282ead8) & 0xffffffff == stored


def snappy(data):
    size, pos = varint(data)
    if size > LIMIT:
        raise ValueError('oversized block')
    result = bytearray()
    while pos < len(data):
        tag = data[pos]; pos += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                count = length - 59
                length = int.from_bytes(data[pos:pos + count], 'little')
                pos += count
            length += 1
            if pos + length > len(data):
                raise ValueError('truncated literal')
            result.extend(data[pos:pos + length]); pos += length
        else:
            if kind == 1:
                length = 4 + ((tag >> 2) & 7)
                if pos >= len(data): raise ValueError('truncated copy')
                offset = ((tag & 224) << 3) | data[pos]; pos += 1
            else:
                length = 1 + (tag >> 2)
                count = 2 if kind == 2 else 4
                if pos + count > len(data): raise ValueError('truncated copy')
                offset = int.from_bytes(data[pos:pos + count], 'little'); pos += count
            if offset <= 0 or offset > len(result):
                raise ValueError('invalid copy offset')
            for _ in range(length): result.append(result[-offset])
        if len(result) > size: raise ValueError('block size mismatch')
    if len(result) != size: raise ValueError('block size mismatch')
    return bytes(result)


def block(data, offset, size):
    if size > LIMIT or offset + size + 5 > len(data):
        raise ValueError('invalid block extent')
    raw, trailer = data[offset:offset + size], data[offset + size:offset + size + 5]
    if not valid_crc(raw + trailer[:1], int.from_bytes(trailer[1:], 'little')):
        raise ValueError('block checksum mismatch')
    if trailer[0] == 0: return raw
    if trailer[0] == 1: return snappy(raw)
    raise ValueError('unsupported block compression')


def entries(data):
    if len(data) < 4: raise ValueError('invalid restart block')
    end = len(data) - 4 - int.from_bytes(data[-4:], 'little') * 4
    if end < 0: raise ValueError('invalid restart extent')
    pos, previous = 0, b''
    while pos < end:
        shared, pos = varint(data, pos)
        unshared, pos = varint(data, pos)
        size, pos = varint(data, pos)
        if shared > len(previous) or pos + unshared + size > end: raise ValueError('invalid entry extent')
        key = previous[:shared] + data[pos:pos + unshared]; pos += unshared
        value = data[pos:pos + size]; pos += size
        yield key, value
        previous = key


def table_records(data):
    if len(data) < 48 or data[-8:] != bytes.fromhex('57fb808b247547db'):
        raise ValueError('invalid table footer')
    footer = data[-48:-8]
    _, pos = varint(footer); _, pos = varint(footer, pos)
    offset, pos = varint(footer, pos); size, pos = varint(footer, pos)
    for _, handle in entries(block(data, offset, size)):
        offset, pos = varint(handle); size, _ = varint(handle, pos)
        for internal, value in entries(block(data, offset, size)):
            if len(internal) < 8: raise ValueError('invalid internal key')
            version = int.from_bytes(internal[-8:], 'little')
            yield version >> 8, version & 255, internal[:-8], value


def batch_records(data):
    if len(data) < 12: raise ValueError('truncated write batch')
    sequence, count = struct.unpack_from('<QI', data)
    pos = 12
    for index in range(count):
        if pos >= len(data): raise ValueError('truncated batch')
        kind = data[pos]; pos += 1
        key_size, pos = varint(data, pos)
        key = data[pos:pos + key_size]; pos += key_size
        value = b''
        if kind == 1:
            size, pos = varint(data, pos)
            value = data[pos:pos + size]; pos += size
        elif kind != 0: raise ValueError('unsupported batch entry')
        if pos > len(data): raise ValueError('truncated batch entry')
        yield sequence + index, kind, key, value


def log_records(data):
    fragments = bytearray()
    for base in range(0, len(data), 32768):
        pos, end = base, min(base + 32768, len(data))
        while pos + 7 <= end:
            checksum, size, kind = struct.unpack_from('<IHB', data, pos); pos += 7
            if kind == 0 and size == 0: break
            if pos + size > end: break  # A currently-written trailing record.
            payload = data[pos:pos + size]; pos += size
            if not valid_crc(bytes([kind]) + payload, checksum): raise ValueError('log checksum mismatch')
            if kind == 1: yield from batch_records(payload)
            elif kind == 2: fragments = bytearray(payload)
            elif kind == 3: fragments.extend(payload)
            elif kind == 4:
                fragments.extend(payload)
                yield from batch_records(fragments)
                fragments.clear()
            else: raise ValueError('unsupported log record')
            if len(fragments) > LIMIT: raise ValueError('oversized batch')


def chromium_string(data):
    if not data: return ''
    if data[0] == 0: return data[1:].decode('utf-16-le')
    if data[0] == 1: return data[1:].decode('latin1')
    raise ValueError('unsupported Chromium string')


def read_legacy_storage(data_dir, origin):
    folder = Path(data_dir) / 'browser-profile' / 'Default' / 'Local Storage' / 'leveldb'
    prefix = b'_' + origin.encode('ascii') + b'\0'
    versions = {}
    for path in sorted(folder.glob('*')):
        if path.suffix not in {'.ldb', '.log'}: continue
        if path.stat().st_size > LIMIT: raise ValueError('legacy storage file too large')
        content = path.read_bytes()
        records = table_records(content) if path.suffix == '.ldb' else log_records(content)
        for sequence, kind, key, value in records:
            if not key.startswith(prefix): continue
            name = chromium_string(key[len(prefix):])
            if not (name.startswith(('ezread-', 'readx-')) or name == 'tudu-sort'): continue
            if kind not in {0, 1}: raise ValueError('unsupported key type')
            if sequence >= versions.get(name, (-1, None))[0]:
                versions[name] = (sequence, chromium_string(value) if kind else None)
    result = {key: value for key, (_, value) in versions.items() if value is not None}
    if len(json.dumps(result).encode('utf-8')) > LIMIT: raise ValueError('legacy storage exceeds limit')
    return result


def prepare_migration(data_dir, origin):
    target = Path(data_dir) / 'webview2-migration.json'
    if target.exists(): return  # Never refresh old drafts over the new profile.
    values = read_legacy_storage(data_dir, origin)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
