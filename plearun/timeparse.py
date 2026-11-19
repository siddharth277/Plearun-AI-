"""Turn every timestamp style found in records into a uniform description."""
import calendar
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?"
    r"\s*(Z|[+-]\d{2}(?::?\d{2})?)?$"
)
_TOD = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?$")
_NUM = re.compile(r"^\d{9,19}(?:\.\d+)?$")


@dataclass
class Parsed:
    kind: str                 # "abs" (epoch known) | "naive" (no tz) | "tod" (no date)
    value: float
    res: float
    offset: Optional[int] = None   # tz offset in seconds if the stamp carried one


def _epoch_seconds(x):
    if x >= 1e17:
        x /= 1e9      # nanoseconds
    elif x >= 1e14:
        x /= 1e6      # microseconds
    elif x >= 1e11:
        x /= 1e3      # milliseconds
    return x if 1e8 <= x <= 4.2e9 else None


def _offset_seconds(tz):
    if tz == "Z":
        return 0
    sign = -1 if tz[0] == "-" else 1
    digits = tz[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) >= 4 else 0
    return sign * (hours * 3600 + minutes * 60)


def parse_timestamp(value) -> Optional[Parsed]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        secs = _epoch_seconds(float(value))
        return Parsed("abs", secs, 1.0) if secs is not None else None
    text = str(value).strip()
    if not text:
        return None
    if _NUM.match(text):
        secs = _epoch_seconds(float(text))
        return Parsed("abs", secs, 1.0) if secs is not None else None
    m = _ISO.match(text)
    if m:
        y, mo, d, h, mi, s, frac, tz = m.groups()
        try:
            wall = calendar.timegm(datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0)).timetuple())
        except ValueError:
            return None
        if frac:
            wall += float("0." + frac)
        res = 60.0 if s is None else 1.0
        if tz is None:
            return Parsed("naive", wall, res)
        off = _offset_seconds(tz)
        return Parsed("abs", wall - off, res, off)
    m = _TOD.match(text)
    if m:
        h, mi, s, ampm = m.groups()
        h, mi, s = int(h), int(mi), int(s or 0)
        if ampm:
            if not 1 <= h <= 12:
                return None
            h = h % 12 + (12 if ampm.lower() == "pm" else 0)
        if h > 23 or mi > 59 or s > 59:
            return None
        return Parsed("tod", h * 3600 + mi * 60 + s, 60.0 if m.group(3) is None else 1.0)
    return None
