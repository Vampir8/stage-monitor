"""ASCII frames documented by Shure, Command Strings for QLX-D, 2023-11-02."""

import re

FRAME = re.compile(r"^<\s*(REP|SAMPLE)\s+(?:(1)\s+)?([A-Z_]+)\s+(.+?)\s*>$")


def parse_frame(data):
    """Return (kind, parameter, value); malformed/foreign-channel frames are ignored."""
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    match = FRAME.fullmatch(text)
    if not match:
        return None
    kind, channel, key, value = match.groups()
    if kind == "SAMPLE" and (channel != "1" or key != "ALL"):
        return None
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    return kind, key, value


def numeric(value, low, high):
    if not re.fullmatch(r"\d{1,6}", value):
        return None
    number = int(value)
    return number if low <= number <= high else None


def sample(value):
    fields = value.split()
    if len(fields) != 3 or fields[0] not in {"AX", "XB", "XX"}:
        return None
    return fields[0], numeric(fields[1], 0, 115), numeric(fields[2], 0, 50)
