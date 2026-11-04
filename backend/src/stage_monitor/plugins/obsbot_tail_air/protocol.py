"""OBSBOT command table, Sony VISCA-over-IP framing; no socket dependencies."""
import struct


def packet(kind, sequence, payload):
    return struct.pack('!HHI', kind, len(payload), sequence) + payload


def unpack(data):
    if len(data) < 9:
        raise ValueError('Truncated VISCA packet')
    kind, length, sequence = struct.unpack('!HHI', data[:8])
    if length != len(data) - 8 or length > 16:
        raise ValueError('Invalid VISCA payload length')
    return kind, sequence, data[8:]


def nibbles(value):
    value &= 0xffff
    return bytes((value >> shift) & 15 for shift in (12, 8, 4, 0))


def number(data, *, signed=False):
    if len(data) != 4 or any(n > 15 for n in data):
        raise ValueError('Invalid VISCA nibble value')
    value = sum(n << shift for n, shift in zip(data, (12, 8, 4, 0)))
    return value - 65536 if signed and value & 0x8000 else value


def command(name, parameters):
    if name in {'recall_preset', 'save_preset'}:
        preset = parameters['preset_number']
        if isinstance(preset, bool) or not isinstance(preset, int) or not 1 <= preset <= 256:
            raise ValueError('Invalid preset number')
        return bytes([0x81, 1, 4, 0x3f, 2 if name == 'recall_preset' else 1, preset - 1, 0xff])
    if name == 'set_tracking':
        modes = {'off': 0, 'normal': 1, 'upper_body': 2, 'close_up': 3}
        mode = parameters['mode']
        if mode not in modes:
            raise ValueError('Invalid tracking mode')
        return bytes([0x81, 1, 0x8e, 1, modes[mode], 0xff])
    if name == 'home':
        return bytes.fromhex('81 01 06 04 FF')
    if name == 'set_focus_mode':
        mode = parameters['mode']
        if mode not in {'auto', 'manual'}:
            raise ValueError('Invalid focus mode')
        return bytes([0x81, 1, 4, 0x38, 2 if mode == 'auto' else 3, 0xff])
    if name == 'set_zoom':
        ratio = parameters['zoom_ratio']
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 1 <= ratio <= 4:
            raise ValueError('Zoom ratio outside 1..4')
        return bytes.fromhex('81 01 04 47') + nibbles(round(ratio * 100)) + b'\xff'
    if name == 'set_position':
        pan, tilt = parameters['pan_deg'], parameters['tilt_deg']
        for value, limit in [(pan, 148.95), (tilt, 84)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not -limit <= value <= limit:
                raise ValueError('Position outside camera range')
        # Moderate fixed speeds; avoid unbounded continuous movement on disconnect.
        return bytes.fromhex('81 01 06 02 08 08') + nibbles(round(pan / .075)) + nibbles(round(tilt / .075)) + b'\xff'
    raise ValueError('Unsupported Tail Air command')
