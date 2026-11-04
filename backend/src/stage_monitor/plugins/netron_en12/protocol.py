"""Read-only Art-Net wire format. No DMX output or configuration packets."""
import ipaddress
import re
import struct


ART_POLL = (b'Art-Net\x00' + struct.pack('<H', 0x2000)
            + b'\x00\x0e\x20\x00' + struct.pack('>HH', 0x7fff, 0))


def text(data):
    return ''.join(c for c in data.split(b'\x00', 1)[0].decode('utf-8', 'replace')
                   if c.isprintable())


def parse_reply(data):
    if not 207 <= len(data) <= 1024 or data[:10] != b'Art-Net\x00\x00\x21':
        raise ValueError('Invalid ArtPollReply')
    if struct.unpack_from('<H', data, 14)[0] != 6454:
        raise ValueError('Invalid advertised Art-Net port')
    count = struct.unpack_from('>H', data, 172)[0]
    if count > 4:
        raise ValueError('Unsupported ArtPollReply port bank size')
    # Zero-fill fields added after the minimum-length legacy packet.
    extended = len(data) >= 217
    data = data.ljust(239, b'\x00')
    report = text(data[108:172])
    match = re.match(r'^#([0-9a-fA-F]{4})', report)
    ports = []
    for index in range(count):
        kind, inp, out = data[174 + index], data[178 + index], data[182 + index]
        output, input_ = bool(kind & 0x80), bool(kind & 0x40)
        base = ((data[18] & 127) << 8) | ((data[19] & 15) << 4)
        ports.append({
            'index': index + 1, 'input': input_, 'output': output,
            'input_universe': base | (data[186 + index] & 15) if input_ else None,
            'output_universe': base | (data[190 + index] & 15) if output else None,
            'input_active': bool(inp & 0x80) if input_ else None,
            'output_active': bool(out & 0x80) if output else None,
            'input_error': bool(inp & 4) if input_ else None,
            'output_short': bool(out & 4) if output else None,
            'merging': bool(out & 8) if output else None,
            'merge_mode': ('LTP' if out & 2 else 'HTP') if output else None,
            'input_protocol': ('sACN' if inp & 1 else 'Art-Net') if input_ else None,
            'output_protocol': ('sACN' if out & 1 else 'Art-Net') if output else None,
            # Extension not present is unknown, rather than falsely RDM enabled.
            'rdm_enabled': not bool(data[213 + index] & 128) if output and extended else None,
        })
    return {'ip': str(ipaddress.IPv4Address(data[10:14])),
            'firmware_revision': struct.unpack_from('>H', data, 16)[0],
            'oem': struct.unpack_from('>H', data, 20)[0],
            'manufacturer_code': struct.unpack_from('<H', data, 24)[0],
            'short_name': text(data[26:44]), 'long_name': text(data[44:108]),
            'report': report, 'report_code': int(match[1], 16) if match else None,
            'mac': ':'.join(f'{b:02x}' for b in data[201:207]) if any(data[201:207]) else None,
            'bind_index': data[211], 'rdm_capable': bool(data[23] & 2), 'ports': ports}
