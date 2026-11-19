"""Decide match / conflict / not_settled / not_checkable per claim, conservatively."""
from bisect import bisect_left, bisect_right
from collections import defaultdict

from .claims import extract_claims, incident_date
from .clocks import TimeBasis, estimate_alignment
from .model import CONFLICT, MATCH, NOT_CHECKABLE, NOT_SETTLED, Decision
from .verify import quote_for, verify_quote

SAME_DAY = 12 * 3600          # rows further than this from the claimed time are other events
SPEED_CLOSE, SPEED_FAR, SPEED_CLEAR_GAP = 1.0, 3.0, 8.0
MAX_PAIRS = 100000            # cap on subject x reference rows compared for a relative claim


class Context:
    def __init__(self, report_text, rows):
        self.rows = rows
        self.registry = {(r.file, r.row): r for r in rows}
        self.basis = TimeBasis(rows, incident_date(report_text))
        self.alignment = estimate_alignment(rows)
        self.by_kind = defaultdict(list)
        cams = defaultdict(list)
        for r in rows:
            for k in r.kinds:
                self.by_kind[k].append(r)
            tt = self.true_time(r)
            if r.source == "camera" and r.speed is not None and tt is not None:
                cams[r.device].append((tt, r))
        self.camera_index = {}
        for dev, items in cams.items():
            items.sort(key=lambda x: x[0])
            self.camera_index[dev] = ([x[0] for x in items], [x[1] for x in items])

    def true_time(self, row):
        if row.t is None or not self.alignment.aligned(row.device):
            return None
        return row.t - self.alignment.offset[row.device]

    def slack(self, row):
        return self.alignment.unc.get(row.device, 0.0) + row.res


def _ns(reason):
    return Decision(NOT_SETTLED, reason=reason)


def _hit(finding, row, reason):
    return Decision(finding, row, quote_for(row), reason)


def check_event_time(claim, ctx):
    p = claim.params
    rows = ctx.by_kind.get(p["event"], [])
    if not rows:
        return _ns("no record of this event")
    target = ctx.basis.report_epoch(p["tod"])
    tol = 180.0 if p["hedge"] else 60.0
    scored = []
    for r in rows:
        tt = ctx.true_time(r)
        if tt is not None and abs(tt - target) <= SAME_DAY:
            scored.append((abs(tt - target), ctx.slack(r), r))
    if not scored:
        return _ns("event rows are on unaligned clocks or other days")
    scored.sort(key=lambda x: x[0])
    diff, slack, row = scored[0]
    if diff <= tol + slack:
        return _hit(MATCH, row, "%.0fs from claimed time" % diff)
    if all(d > max(300.0, 3 * tol) + s for d, s, _ in scored):
        return _hit(CONFLICT, row, "%.0fs from claimed time, all candidates far" % diff)
    return _ns("gap of %.0fs is too ambiguous" % diff)


def check_speed(claim, ctx):
    p = claim.params
    if p["tod"] is None:
        return _ns("no time given, cannot select a reading")
    if not ctx.camera_index:
        return _ns("no camera clock could be aligned")
    target = ctx.basis.report_epoch(p["tod"])
    tol = 180.0 if p["hedge"] else 60.0
    seen, found = set(), []
    for dev, (times, rows) in ctx.camera_index.items():
        slack = ctx.alignment.unc[dev]
        lo = bisect_left(times, target - tol - slack - 1)
        hi = bisect_right(times, target + tol + slack + 1)
        for i in range(lo, hi):
            r = rows[i]
            key = (dev, times[i], r.speed)
            if r.speed > 0 and key not in seen:        # 0 mph = stopped or sensor dropout
                seen.add(key)
                found.append((abs(times[i] - target), r))
    if not found:
        return _ns("no non-zero camera reading near the claimed time")
    v = p["speed"]
    close = [f for f in found if abs(f[1].speed - v) <= SPEED_CLOSE]
    far = [f for f in found if abs(f[1].speed - v) > SPEED_FAR]
    if close and not far:
        _, row = min(close, key=lambda f: f[0])
        if p["zone"] is not None and row.zone is not None and row.zone != p["zone"]:
            return _ns("speed matches but posted zone differs")
        return _hit(MATCH, row, "reading %.0f mph" % row.speed)
    if len(far) == len(found):
        gap = min(abs(f[1].speed - v) for f in far)
        if (not p["hedge"]) or gap >= SPEED_CLEAR_GAP:
            _, row = min(far, key=lambda f: f[0])
            return _hit(CONFLICT, row, "all readings differ, nearest %.0f mph" % row.speed)
    return _ns("readings near the claimed time are mixed or only slightly different")


def check_relative(claim, ctx):
    p = claim.params
    if not p["subject"] or not p["reference"]:
        return _ns("could not identify both events")
    subs, refs = ctx.by_kind.get(p["subject"], []), ctx.by_kind.get(p["reference"], [])
    if len(subs) * len(refs) > MAX_PAIRS:      # thousands of candidates: they cannot all agree anyway
        return _ns("too many candidate event pairs to decide")
    pairs = []
    for a in subs:
        for b in refs:
            if a is b or a.t is None or b.t is None:
                continue
            if a.device == b.device:                   # same clock: difference needs no alignment
                pairs.append((a, b, a.t - b.t, a.res + b.res))
            elif ctx.alignment.aligned(a.device) and ctx.alignment.aligned(b.device):
                diff = ctx.true_time(a) - ctx.true_time(b)
                pairs.append((a, b, diff, ctx.slack(a) + ctx.slack(b)))
    if not pairs:
        return _ns("events are on clocks that cannot be compared")
    tol = {1: 5.0, 60: 60.0, 3600: 600.0}[p["unit_seconds"]]
    verdicts = []
    for a, _b, obs, slack in pairs:
        err = abs(obs - p["delta"])
        if err <= tol + slack:
            verdicts.append((MATCH, a, err))
        elif err > 3 * tol + slack:
            verdicts.append((CONFLICT, a, err))
        else:
            verdicts.append((NOT_SETTLED, a, err))
    kinds = {v[0] for v in verdicts}
    if len(kinds) == 1 and kinds != {NOT_SETTLED}:
        verdict, row, err = min(verdicts, key=lambda v: v[2])
        return _hit(verdict, row, "observed gap off by %.0fs" % err)
    return _ns("candidate pairs disagree or are borderline")


def check_count(claim, ctx):
    p = claim.params
    dispatch = [r for r in ctx.rows if r.source == "dispatch"]
    if not dispatch:
        return _ns("no dispatch records to count")
    if p["noun"] == "calls":
        items = [r for r in dispatch if "911_call" in r.kinds]
    else:
        items = [r for r in dispatch if r.unit]
        distinct = {r.unit for r in items}
        items = items if len(distinct) == p["n"] else []
        if items:
            return _hit(MATCH, items[0], "%d distinct units" % len(distinct))
        return _ns("unit count differs or is unclear")
    if len(items) == p["n"]:
        return _hit(MATCH, items[0], "%d dispatch 911 rows" % len(items))
    return _ns("record count differs; records may be incomplete")


CHECKS = {"event_time": check_event_time, "speed": check_speed,
          "relative": check_relative, "count": check_count}


def evaluate(claim, ctx):
    try:
        if claim.kind == "uncheckable":
            d = Decision(NOT_CHECKABLE, reason="no record type covers this statement")
        else:
            d = CHECKS[claim.kind](claim, ctx)
    except Exception as exc:                           # never crash on one claim
        d = _ns("internal error: %s" % exc)
    if d.finding in (MATCH, CONFLICT):                 # rule 1: proof or downgrade
        if d.row is None or not verify_quote(ctx.registry, d.row.file, d.row.row, d.quote):
            d = _ns("quote could not be verified; downgraded")
    out = {"claim": claim.text, "finding": d.finding}
    if d.finding in (MATCH, CONFLICT):
        out["cite"] = {"file": d.row.file, "row": d.row.row}
        out["quote"] = d.quote
    return out, d.reason


def run(report_text, rows):
    ctx = Context(report_text, rows)
    results = [evaluate(c, ctx) for c in extract_claims(report_text)]
    return [r[0] for r in results], [r[1] for r in results], ctx
