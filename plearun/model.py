from dataclasses import dataclass
from typing import Optional, Tuple

MATCH = "match"
CONFLICT = "conflict"
NOT_SETTLED = "not_settled"
NOT_CHECKABLE = "not_checkable"


@dataclass
class Row:
    file: str                       # path relative to the records dir
    row: int                        # 1-based record / event / cue number
    raw: str                        # exact raw text of the row (quotes are checked against this)
    source: str                     # camera | dispatch | notes | bodycam
    device: str                     # identity of the clock, e.g. "camera:CAM-07"
    t: Optional[float] = None       # epoch seconds on the device's own clock
    naive: Optional[float] = None   # wall-clock seconds with unknown timezone (resolved later)
    tod: Optional[float] = None     # time of day only, date unknown (resolved later)
    res: float = 1.0                # timestamp resolution in seconds (60 for HH:MM)
    kinds: Tuple[str, ...] = ()     # semantic event labels, e.g. "stop", "speed:58"
    speed: Optional[float] = None
    zone: Optional[float] = None
    unit: Optional[str] = None
    hint: str = ""                  # preferred quote (must be a substring of raw)
    tz_offset: Optional[int] = None # offset seen in an ISO stamp, used to infer local tz


@dataclass
class Claim:
    text: str
    kind: str       # speed | event_time | relative | count | uncheckable
    params: dict


@dataclass
class Decision:
    finding: str
    row: Optional[Row] = None
    quote: Optional[str] = None
    reason: str = ""
