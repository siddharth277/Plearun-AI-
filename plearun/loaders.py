"""One loader per record format. Every loader keeps the exact raw text of each row."""
import codecs
import csv
import json
import os
import re
import sys

from .model import Row
from .timeparse import parse_timestamp

_TIME_KEYS = ("logged", "timestamp", "time", "ts", "datetime", "created", "occurred", "at")
_TIME_COLS = ("timestamp", "time", "datetime", "ts", "date_time", "logged")
_SPEED_COLS = ("speed_mph", "speed", "mph")
_ZONE_COLS = ("zone_mph", "zone", "limit_mph", "limit", "posted_mph")
_DEVICE_COLS = ("device", "camera", "camera_id", "cam", "device_id")
_NOTE_COLS = ("note", "notes", "text", "comment", "comments", "description", "message", "entry", "details")
_KNOWN_COLS = set(_TIME_COLS + _SPEED_COLS + _ZONE_COLS + _DEVICE_COLS + _NOTE_COLS
                  + ("id", "author", "officer", "unit"))


def _col(rec, names):
    """First non-missing value among alias column names."""
    for n in names:
        if n in rec and rec[n] is not None:
            return rec[n]
    return None
_CLOSING_TOKENS = {"cleared", "closed", "complete", "completed", "cancelled", "canceled", "transfer", "transferred"}
_SPEED_TEXT = re.compile(r"\b(\d{2,3})\s*(?:mph\b|in\s+an?\s+\d{2,3}\b)", re.I)


def read_text(path):
    """Decode bytes: UTF-16 by BOM, else UTF-8 (BOM stripped), else Windows-1252."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def classify_text(text, source):
    """Label free text with event kinds. Keywords only classify; text is never executed."""
    t = text.lower()
    kinds = []
    if source == "notes" and re.search(r"\b911\b|\bcaller\b", t):
        kinds.append("911_call")
    if re.search(r"\bstopp?ed\b|pulling in|pulled in|pulled over|pulled into", t):
        kinds.append("stop")
    if re.search(r"\bobserved\b|\bspotted\b|\bfirst saw\b", t):
        kinds.append("observed")
    if re.search(r"on scene|\barrived\b|\barrival\b", t):
        kinds.append("on_scene")
    if re.search(r"\bdispatched\b", t):
        kinds.append("dispatched")
    for m in _SPEED_TEXT.finditer(t):
        kinds.append("speed:%d" % int(m.group(1)))
    return tuple(dict.fromkeys(kinds))


def _csv_records(text):
    """Yield (fields, raw_text) per CSV record, keeping quoted multi-line fields intact."""
    buffer = []

    def lines():
        for line in text.splitlines(keepends=True):
            buffer.append(line)
            yield line

    for fields in csv.reader(lines()):
        raw = "".join(buffer).rstrip("\r\n")
        buffer.clear()
        if fields:
            yield fields, raw


def _apply_time(row, value):
    p = parse_timestamp(value)
    if p is None:
        return
    row.res = p.res
    if p.kind == "abs":
        row.t, row.tz_offset = p.value, p.offset
    elif p.kind == "naive":
        row.naive = p.value
    else:
        row.tod = p.value


def _number(value):
    try:
        x = float(str(value).strip())
    except ValueError:
        return None
    return x if 0 <= x <= 400 else None


def load_csv(path, rel):
    text = read_text(path)
    header, rows, number = None, [], 0
    for fields, raw in _csv_records(text):
        if header is None:
            names = [f.strip().lower() for f in fields]
            if sum(n in _KNOWN_COLS for n in names) >= 2:
                header = names
            continue                      # preamble / marker lines before the header
        number += 1
        rec = dict(zip(header, fields))
        if any(n in header for n in _SPEED_COLS):
            device = "camera:" + ((_col(rec, _DEVICE_COLS) or "").strip() or "unknown")
            row = Row(rel, number, raw, "camera", device, hint=raw[:300])
            _apply_time(row, _col(rec, _TIME_COLS))
            row.speed = _number(_col(rec, _SPEED_COLS) or "")
            row.zone = _number(_col(rec, _ZONE_COLS) or "")
            if row.speed:
                row.kinds = ("speed:%d" % round(row.speed),)
        elif any(n in header for n in _NOTE_COLS):
            row = Row(rel, number, raw, "notes", "notes", hint=raw[:300])
            _apply_time(row, _col(rec, _TIME_COLS))
            row.kinds = classify_text(_col(rec, _NOTE_COLS) or "", "notes")
        else:
            continue
        rows.append(row)
    return rows


def _array_object_spans(text):
    """Spans of every JSON object that is an element of an array (string-aware)."""
    stack, spans = [], []
    for m in re.finditer(r'"(?:[^"\\]|\\.)*"|[{}\[\]]', text):
        c = m.group(0)
        if c in "{[":
            stack.append((c, m.start()))
        elif c in "}]" and stack:
            opener, start = stack.pop()
            if opener == "{" and c == "}" and stack and stack[-1][0] == "[":
                spans.append((start, m.end()))
    spans.sort()
    return spans


def _event_time(ev):
    for key in _TIME_KEYS:
        if key in ev:
            p = parse_timestamp(ev[key])
            if p and p.kind != "tod":
                return key, p
    for key, val in ev.items():
        if isinstance(val, (str, int, float)) and not isinstance(val, bool):
            p = parse_timestamp(val)
            if p and p.kind != "tod":
                return key, p
    return None, None


def load_json(path, rel):
    text = read_text(path)
    rows, number, kept_end = [], 0, -1
    for start, end in _array_object_spans(text):
        if start < kept_end:
            continue                      # inside an event already captured
        raw = text[start:end]
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        key, p = _event_time(ev)
        if p is None:
            continue
        kept_end = end
        number += 1
        etype = str(ev.get("type", ev.get("event", ev.get("event_type", "event"))))
        low = etype.lower()
        toks = set(re.split(r"[^a-z0-9]+", low))
        kinds = []
        if ("911" in low or toks & {"call", "caller"}) and not toks & _CLOSING_TOKENS:
            kinds.append("911_call")
        if "on_scene" in low or "onscene" in low or "arriv" in low or {"on", "scene"} <= toks:
            kinds.append("on_scene")
        if "dispatch" in low:
            kinds.append("dispatched")
        hint = re.search(r'"%s"\s*:\s*(?:"(?:[^"\\]|\\.)*"|[^,}\s]+)' % re.escape(key), raw)
        unit = ev.get("unit")
        row = Row(rel, number, raw, "dispatch", "dispatch", kinds=tuple(kinds),
                  unit=str(unit) if unit is not None else None,
                  hint=hint.group(0) if hint else raw[:300].strip())
        row.res = p.res
        if p.kind == "abs":
            row.t, row.tz_offset = p.value, p.offset
        else:
            row.naive = p.value
        rows.append(row)
    return rows


_VTT_TIME = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")


def load_vtt(path, rel):
    text = read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    rows, number = [], 0
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        m = _VTT_TIME.search(lines[idx])
        if not m:
            continue
        h, mi, s, ms = m.group(1), m.group(2), m.group(3), m.group(4)
        start = int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000.0
        text_lines = [ln for ln in lines[idx + 1:] if ln.strip()]
        body = " ".join(text_lines).strip()
        number += 1
        raw = "\n".join(lines)
        # quote one real line: a space-joined multi-line body is not a substring of the raw cue
        hint = next((ln.strip() for ln in text_lines if classify_text(ln, "bodycam")), None) \
            or (text_lines[0].strip() if text_lines else "")
        row = Row(rel, number, raw, "bodycam", "bodycam", t=start, res=1.0,
                  kinds=classify_text(body, "bodycam"), hint=hint[:300])
        rows.append(row)
    return rows


def load_records(directory):
    """Load every recognised file under directory. Bad files are reported, never fatal."""
    rows, problems = [], []
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            ext = name.lower().rsplit(".", 1)[-1]
            loader = {"csv": load_csv, "json": load_json, "vtt": load_vtt}.get(ext)
            if loader is None:
                continue
            try:
                rows.extend(loader(path, rel))
            except Exception as exc:          # one bad file must not stop the run
                problems.append("%s: %s" % (rel, exc))
                print("warning: skipped %s (%s)" % (rel, exc), file=sys.stderr)
    return rows, problems
