"""Bounded OSC messages and immediate bundles; no device-specific addresses."""
import math
import struct

MAX_PACKET = 16384


def string(value):
    if not isinstance(value, str) or '\0' in value or len(value.encode('utf-8')) > 4096:
        raise ValueError('Invalid OSC string')
    raw = value.encode('utf-8') + b'\0'
    return raw + bytes(-len(raw) % 4)


def encode(address, arguments):
    if not isinstance(address, str) or not address.startswith('/') or len(address) > 256:
        raise ValueError('Invalid OSC address')
    tags, body = ',', b''
    if len(arguments) > 32:
        raise ValueError('Too many OSC arguments')
    for argument in arguments:
        tag, value = argument['type'], argument.get('value')
        tags += tag
        if tag == 'i' and type(value) is int and -(2**31) <= value < 2**31:
            body += struct.pack('!i', value)
        elif tag == 'f' and type(value) in (int, float) and math.isfinite(value) and abs(value) <= 3.4028234e38:
            body += struct.pack('!f', value)
        elif tag == 's':
            body += string(value)
        elif tag not in {'T', 'F', 'N'}:
            raise ValueError('Invalid OSC argument')
    packet = string(address) + string(tags) + body
    if len(packet) > MAX_PACKET:
        raise ValueError('OSC packet too large')
    return packet


def decode(packet):
    if not packet or len(packet) > MAX_PACKET or len(packet) % 4:
        raise ValueError('Invalid OSC packet size')
    messages = []

    def parse(data, depth=0):
        if depth > 8 or len(messages) >= 128:
            raise ValueError('OSC bundle limit')
        if data.startswith(b'#bundle\0'):
            if len(data) < 16 or struct.unpack('!Q', data[8:16])[0] != 1:
                raise ValueError('Only immediate OSC bundles are supported')
            offset = 16
            while offset < len(data):
                if offset + 4 > len(data):
                    raise ValueError('Truncated OSC bundle')
                size = struct.unpack('!I', data[offset:offset+4])[0]
                offset += 4
                if not size or size % 4 or offset + size > len(data):
                    raise ValueError('Invalid OSC bundle element')
                parse(data[offset:offset+size], depth+1)
                offset += size
            return
        offset = 0

        def text():
            nonlocal offset
            end = data.find(b'\0', offset)
            if end < 0 or end-offset > 4096:
                raise ValueError('Invalid OSC string')
            value = data[offset:end].decode('utf-8')
            after = (end+4)//4*4
            if after > len(data) or any(data[end:after]):
                raise ValueError('Invalid OSC padding')
            offset = after
            return value

        address, tags = text(), text()
        if not address.startswith('/') or len(address) > 256 or not tags.startswith(',') or len(tags) > 33:
            raise ValueError('Invalid OSC message')
        args = []
        for tag in tags[1:]:
            if tag in {'i', 'f', 'h', 'd'}:
                size, fmt = {'i': (4, '!i'), 'f': (4, '!f'), 'h': (8, '!q'), 'd': (8, '!d')}[tag]
                if offset+size > len(data):
                    raise ValueError('Truncated OSC number')
                value = struct.unpack(fmt, data[offset:offset+size])[0]
                offset += size
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError('Non-finite OSC number')
                args.append(value)
            elif tag == 's':
                args.append(text())
            elif tag in {'T', 'F', 'N'}:
                args.append({'T': True, 'F': False, 'N': None}[tag])
            else:
                raise ValueError('Unsupported OSC type')
        if offset != len(data):
            raise ValueError('Trailing OSC data')
        messages.append((address, args))
    parse(packet)
    return messages
