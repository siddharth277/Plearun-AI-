"""Pull checkable claims out of report text. Anything unrecognised is not_checkable."""
import re

from .model import Claim

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}
_NUM = r"(\d{1,3}|" + "|".join(NUM_WORDS) + r")"
TIME = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)(?:\s*([ap])\.?\s?m\b\.?)?", re.I)
SPEED = re.compile(r"\b(\d{2,3})\s*(?:mph|m\.p\.h\.?|miles per hour|mi/h)(?![a-z])", re.I)
SPEED_IN_ZONE = re.compile(r"\b(\d{2,3})\s+in\s+an?\s+(?:posted\s+)?(\d{2,3})\b(?![\s-]*(?:person|people|car|cars|unit|units|minute|minutes|second|seconds))", re.I)
ZONE = re.compile(r"\b(?:in\s+an?\s+(?:posted\s+)?|posted\s+(?:limit\s+of\s+)?|limit\s+of\s+)(\d{2,3})\b", re.I)
REL = re.compile(r"\b" + _NUM + r"\s+(second|minute|min|hour)s?\s+(after|before)\b(?:\s+(?:the|their|his|her)\s+([a-z]+))?", re.I)
_COUNT_NUM = r"(\d{1,3}|" + "|".join(w for w in NUM_WORDS if w not in ("a", "an")) + r")"   # "a unit" is not a count
COUNT = re.compile(r"\b" + _COUNT_NUM + r"\s+(?:separate\s+)?(911\s+calls?|calls?|units?)\b", re.I)
HEDGE_BEFORE = re.compile(r"(?:about|around|approximately|approx\.?|roughly|nearly|circa|almost|~)\s*(?:at\s+)?$", re.I)
HEADER = re.compile(r"^(SYNTHETIC|FICTIONAL)\b|^Incident\s+\d{4}-\d{2}-\d{2}", re.I)
EVENT_PATTERNS = [
    ("on_scene", r"on scene|\barriv"),
    ("dispatched", r"\bdispatched\b"),
    ("911_call", r"\b911\b|\bcaller\b|\bcall\b"),
    ("stop", r"\bstopp?ed\b|\bpulled\b|\bpulling\b|\bstop\b"),
    ("observed", r"\bobserved\b|\bspotted\b|\bfirst saw\b|\bsighted\b"),
]
REF_NOUNS = {"stop": "stop", "stopped": "stop", "pullover": "stop", "call": "911_call",
             "dispatch": "dispatched", "arrival": "on_scene", "observation": "observed",
             "sighting": "observed"}
UNIT_SECONDS = {"second": 1, "minute": 60, "min": 60, "hour": 3600}


def split_sentences(text):
    flat = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", flat) if s.strip()]


def event_kind(text, near=None):
    """Event named in text. With `near` (a character index, e.g. where the time is written) pick
    the keyword closest to it, so "stopped at 21:16 and a unit arrived" is about the stop."""
    low = text.lower()
    best = None
    for rank, (kind, pattern) in enumerate(EVENT_PATTERNS):
        for m in re.finditer(pattern, low):
            dist = 0 if near is None else min(abs(m.start() - near), abs(m.end() - near))
            key = (dist, rank)
            if best is None or key < best[0]:
                best = (key, kind)
            if near is None:
                break
    return best[1] if best else None


def _num(token):
    token = token.lower()
    return int(token) if token.isdigit() else NUM_WORDS[token]


def _first_time(text):
    """First valid clock time in text -> (seconds since midnight, hedged?, index) or None."""
    for m in TIME.finditer(text):
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        ampm = (m.group(4) or "").lower()
        if ampm and 1 <= h <= 12:                  # "21:16 p.m." is already 24-hour: ignore the suffix
            h = h % 12 + (12 if ampm == "p" else 0)
        if h < 24 and mi < 60 and s < 60:
            hedged = bool(HEDGE_BEFORE.search(text[max(0, m.start() - 20):m.start()]))
            return h * 3600 + mi * 60 + s, hedged, m.start()
    return None


def incident_date(text):
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return m.group(1) if m else None


def _clause_claim(clause, sentence_time):
    c = clause.strip().rstrip(".")
    sp = SPEED.search(c)
    sz = None if sp else SPEED_IN_ZONE.search(c)
    if sp or sz:
        if sp:
            zone = ZONE.search(c, sp.end())
            zone_v = float(zone.group(1)) if zone else None
        else:
            zone_v = float(sz.group(2))
        t = _first_time(c) or sentence_time
        return Claim(c, "speed", {"speed": float((sp or sz).group(1)), "zone": zone_v,
                                  "tod": t[0] if t else None, "hedge": t[1] if t else False})
    rel = REL.search(c)
    if rel:
        subject = event_kind(c[:rel.start()])
        ref = REF_NOUNS.get((rel.group(4) or "").lower())
        hedged = bool(HEDGE_BEFORE.search(c[max(0, rel.start() - 20):rel.start()]))
        return Claim(c, "relative", {
            "subject": subject, "reference": ref, "hedge": hedged,
            "delta": _num(rel.group(1)) * UNIT_SECONDS[rel.group(2).lower()] * (1 if rel.group(3).lower() == "after" else -1),
            "unit_seconds": UNIT_SECONDS[rel.group(2).lower()]})
    t = _first_time(c)
    kind = event_kind(c, t[2] if t else None)
    if kind and t:
        return Claim(c, "event_time", {"event": kind, "tod": t[0], "hedge": t[1]})
    cnt = COUNT.search(c)
    if cnt:
        noun = "calls" if "call" in cnt.group(2).lower() else "units"
        return Claim(c, "count", {"noun": noun, "n": _num(cnt.group(1))})
    return Claim(c, "uncheckable", {})


_CONJ = re.compile(r";\s+|,?\s+(?:and|but|while|whereas)\s+", re.I)


def _split_compound(clause):
    """Split "A at 21:10 and B at 21:14" into two claims, but only when every part carries its own
    time; otherwise keep the clause whole so relative claims ("... and 2 minutes after") survive."""
    parts = [p for p in _CONJ.split(clause) if p.strip()]
    if len(parts) > 1 and all(_first_time(p) for p in parts):
        return parts
    return [clause]


def extract_claims(text):
    claims = []
    for sentence in split_sentences(text):
        if HEADER.search(sentence):
            continue
        sentence_time = _first_time(sentence)
        for clause in re.split(r",\s+(?=(?:travell?ing|driving|going)\b)", sentence):
            for part in _split_compound(clause):
                if part.strip().rstrip("."):
                    claims.append(_clause_claim(part, sentence_time))
    return claims
