"""Resolve wall-clock stamps, then estimate which device clocks can be trusted.

A device is aligned only if >=2 DIFFERENT shared events (a majority of its candidates)
agree on its offset to within the stamps' resolution. Otherwise it stays unaligned and
its times are never compared with other devices.
"""
import calendar
import statistics
from collections import Counter, defaultdict

DAY = 86400.0


class TimeBasis:
    def __init__(self, rows, date_text):
        offsets = Counter(r.tz_offset for r in rows if r.tz_offset is not None)
        self.local_offset = offsets.most_common(1)[0][0] if offsets else 0
        pool = [r for r in rows if r.t is not None and r.source not in ("bodycam", "camera")]
        pool = pool or [r for r in rows if r.t is not None and r.source != "bodycam"]
        if date_text:
            y, m, d = (int(x) for x in date_text.split("-"))
            self.midnight = calendar.timegm((y, m, d, 0, 0, 0)) - self.local_offset
            near = [r.t for r in pool if abs(r.t - (self.midnight + DAY / 2)) <= 36 * 3600]
            self.median = statistics.median(near) if near else None   # ignores bulk data on other days
        else:
            self.median = statistics.median([r.t for r in pool]) if pool else None
            if self.median is not None:
                local = self.median + self.local_offset
                self.midnight = (local // DAY) * DAY - self.local_offset
            else:
                self.midnight = 0.0
        for r in rows:
            if r.t is None and r.naive is not None:
                r.t = r.naive - self.local_offset
            elif r.t is None and r.tod is not None:
                r.t = self.report_epoch(r.tod)

    def report_epoch(self, tod):
        """Time of day -> epoch on the incident date, shifted by at most one day toward the data."""
        t = self.midnight + tod
        if self.median is not None:
            t += DAY * max(-1, min(1, round((self.median - t) / DAY)))
        return t


class Alignment:
    def __init__(self):
        self.offset = {}     # device -> clock offset vs reference (device_time - true_time)
        self.unc = {}        # device -> uncertainty in seconds
        self.reference = None
        self.log = []

    def aligned(self, device):
        return device in self.offset


def _unique_by_kind(rows):
    seen = defaultdict(list)
    for r in rows:
        for k in r.kinds:
            seen[k].append(r)
    return {k: v[0] for k, v in seen.items() if len(v) == 1}   # ambiguous kinds are dropped


def _estimate(dev_rows, ref_rows, ref_offset):
    mine, theirs = _unique_by_kind(dev_rows), _unique_by_kind(ref_rows)
    anchors = [(k, mine[k].t - (theirs[k].t - ref_offset), mine[k].res, theirs[k].res)
               for k in sorted(set(mine) & set(theirs))]
    if len(anchors) < 2:
        return None, "%d shared event(s), need 2+" % len(anchors)
    slack = 30.0 + max(a[2] for a in anchors) + max(a[3] for a in anchors)
    ordered = sorted(anchors, key=lambda a: a[1])
    best = []
    for i, first in enumerate(ordered):
        cluster = [a for a in ordered[i:] if a[1] - first[1] <= slack]
        if len(cluster) > len(best):
            best = cluster
    if len(best) < 2 or len(best) * 2 <= len(anchors):
        return None, "anchors disagree (spread too large)"
    offs = [a[1] for a in best]
    unc = (max(offs) - min(offs)) / 2 + (max(a[2] for a in best) + max(a[3] for a in best)) / 2
    return (statistics.median(offs), unc), "aligned using %s" % ", ".join(a[0] for a in best)


def estimate_alignment(rows):
    al = Alignment()
    by_dev = defaultdict(list)
    for r in rows:
        if r.t is not None:
            by_dev[r.device].append(r)
    if not by_dev:
        return al
    al.reference = "dispatch" if "dispatch" in by_dev else sorted(by_dev)[0]
    al.offset[al.reference], al.unc[al.reference] = 0.0, 0.0
    progress = True
    while progress:
        progress = False
        for dev in sorted(by_dev):
            if al.aligned(dev):
                continue
            best = None
            for ref in sorted(al.offset):
                est, why = _estimate(by_dev[dev], by_dev[ref], al.offset[ref])
                al.log.append("%s vs %s: %s" % (dev, ref, why))
                if est and (best is None or est[1] + al.unc[ref] < best[1]):
                    best = (est[0], est[1] + al.unc[ref])
            if best:
                al.offset[dev], al.unc[dev] = best
                progress = True
    for dev in sorted(by_dev):
        if not al.aligned(dev):
            al.log.append("%s: UNALIGNED, times not compared across devices" % dev)
    return al
