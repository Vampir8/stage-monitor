"""Bounded StageLinQ wire primitives. No playback/control messages are implemented."""
import json
import struct

MAX_FRAME = 65536


def u32(value):
    return struct.pack('!I', value)


def string(value):
    raw = value.encode('utf-16-be')
    if len(raw) > MAX_FRAME:
        raise ValueError('StageLinQ string too long')
    return u32(len(raw)) + raw


def read_string(data, offset):
    if len(data) < offset + 4:
        raise ValueError('Truncated string')
    size = struct.unpack_from('!I', data, offset)[0]
    if size > MAX_FRAME or size % 2 or offset + 4 + size > len(data):
        raise ValueError('Invalid string length')
    return data[offset + 4:offset + 4 + size].decode('utf-16-be'), offset + 4 + size


def announce(token, port, name='StageControl', software='StageControl', version='1.0'):
    if len(token) != 16 or not 0 < port <= 65535:
        raise ValueError('Invalid announcement identity')
    return b'airD' + token + b''.join(string(s) for s in
        (name, 'DISCOVERER_HOWDY_', software, version)) + struct.pack('!H', port)


def discovery(data):
    if len(data) < 38 or data[:4] != b'airD':
        raise ValueError('Invalid discovery frame')
    offset, fields = 20, []
    for _ in range(4):
        value, offset = read_string(data, offset)
        fields.append(value)
    if len(data) != offset + 2 or fields[1] != 'DISCOVERER_HOWDY_':
        raise ValueError('Not a discovery announcement')
    port = struct.unpack_from('!H', data, offset)[0]
    if not port:
        raise ValueError('Missing directory port')
    return {'token': data[4:20], 'name': fields[0], 'software':fields[2],
            'version':fields[3], 'port':port}


def subscribe(path, interval):
    body = b'smaa' + u32(2002) + string(path) + u32(interval)
    return u32(len(body)) + body


def response(body):
    if len(body) < 16 or body[:4] != b'smaa':
        raise ValueError('Invalid StateMap frame')
    kind = struct.unpack_from('!I', body, 4)[0]
    if kind not in (0, 2001):
        raise ValueError('Not a state response')
    path, offset = read_string(body, 8)
    if body[offset:] == b'\xff' * 4:
        return path, None
    value, offset = read_string(body, offset)
    if offset != len(body):
        raise ValueError('Trailing StateMap data')
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError('Invalid state value')
    return path, decoded
