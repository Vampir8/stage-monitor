"""SLX-D command-string facts from Shure's SLXD command reference (document 7798)."""

import re

FRAME = re.compile(r"^<\s*(REP|SAMPLE)\s+(?:([12])\s+)?([A-Z_]+)\s+(\{[^{}]*\}|[^<>]+?)\s*>$")


def parse_frame(data):
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    match = FRAME.fullmatch(text)
    if not match:
        return None
    kind, channel, key, value = match.groups()
    if kind == "SAMPLE" and (channel is None or key != "ALL"):
        return None
    if value.startswith("{"):
        value = value[1:-1].strip()
    return kind, int(channel) if channel else None, key, value


def number(value, maximum=9999999):
    if not re.fullmatch(r"[0-9]{1,7}", value):
        return None
    result = int(value)
    return result if result <= maximum else None


def sample(value):
    fields = value.split()
    if len(fields) != 3:
        return None
    raw = [number(field, 120) for field in fields]
    if any(item is None for item in raw):
        return None
    # Peak/RMS are offset dBFS; RSSI is offset dBm, not QLX-D's antenna/raw tuple.
    return tuple(item - 120 for item in raw)
