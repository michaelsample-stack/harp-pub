"""Which timber marks arrived, and when.

    from harp import log_deliveries
    arrivals, report = log_deliveries.read(paths, month="2026-08", log=log)

WHAT THIS IS FOR
----------------
The chip delivery record says how much fibre arrived. It does not say which
harvest it came from, because a chip carries no mark - which is why most of a
month resolves to a search area.

A log delivery record says the other half: which marks arrived, and when. A
mark on a real arrival names a specific harvest, so these resolve to a cut
block and are finished - no detection needed to narrow them.

They are two records of two different things and neither replaces the other.
**Log arrivals never enter the tonnage arithmetic**: those logs are chipped
elsewhere and arrive later as chips under their own delivery, and counting
both would count the same wood twice. Their volume is cubic metres of log
where everything else is bone-dry tonnes of chip, and it is carried as stated
rather than converted - the factor varies by species and moisture, and a
wrong one is worse than two units side by side.

MORE THAN ONE FORMAT
--------------------
Some suppliers send a scale return straight out of their system - a
multi-section CSV with a header row per record type. Others will send a
spreadsheet somebody typed. More formats will follow.

All of them answer the same three questions, so this reads for the answers
rather than the layout:

    which mark      TIMBER_MARK, MARK, TIMBERMARK, Timber Mark
    when it came    Arrival_Date, DATE_IN, Received, Delivery Date
    how much        METRIC_NET, Volume, m3, Net

`COLUMNS` below is where a new spelling goes. A new supplier's format should
be a line in that table, not a new reader.
"""

from __future__ import annotations

import csv
import os
import re
from collections import defaultdict

# What a column might be called. Matched on a normalised header, most
# specific first - "net volume" should win over "volume" where both appear.
COLUMNS = {
    "mark": ("timber mark", "timbermark", "timber_mark", "mark",
             "harvest mark", "scale mark"),
    "date": ("arrival date", "arrival_date", "date in", "date_in",
             "received", "receipt date", "delivery date", "scale date",
             "boom create date", "date"),
    "volume": ("metric net", "metric_net", "net volume", "net m3", "volume",
               "m3", "cubic metres", "net"),
    "supplier": ("supplier", "owner name", "owner_name", "vendor",
                 "seller", "supp"),
    "destination": ("destination", "allocation_destinationid", "consignee",
                    "delivered to", "chipper"),
}

# A section-based file names its record type in the first column, and the
# header for each type is a row whose first cell is that type's own name.
# This is the shape a scale return arrives in.
SECTION_HEADS = ("BOOM_HEADER", "LOGS", "SAMPLE_LOGS", "HEADER", "DETAIL")

MARK = re.compile(r"^[A-Z0-9][A-Z0-9/'\-]{2,11}$")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _column(header: list, kind: str):
    """Which column holds this, or None."""
    normed = [_norm(h) for h in header]
    for want in COLUMNS[kind]:
        w = _norm(want)
        if w in normed:
            return normed.index(w)
    for want in COLUMNS[kind]:
        w = _norm(want)
        for i, h in enumerate(normed):
            if w and w in h:
                return i
    return None


# ────────────────────────────── recognising ────────────────────────────────

def looks_like(path: str) -> bool:
    """Is this a log delivery record?

    Recognised by carrying a timber mark column and a date, the same way
    everything else in a drop is recognised - by what is in it rather than
    what it is called.
    """
    low = path.lower()
    if not low.endswith((".csv", ".txt", ".xlsx", ".xls")):
        return False
    try:
        for header in _headers(path):
            if (_column(header, "mark") is not None
                    and _column(header, "date") is not None):
                return True
            # A scale return splits them: marks on the detail rows, the date
            # on the header row. Either alone is enough to recognise it.
            if (_column(header, "mark") is not None
                    and str(header[0]).strip().upper() in SECTION_HEADS):
                return True
    except Exception:
        return False
    return False


def _headers(path: str):
    """Every row that could be a header - one for a plain table, several for
    a sectioned file."""
    if path.lower().endswith((".xlsx", ".xls")):
        import pandas as pd
        d = pd.read_excel(path, header=None, nrows=12)
        yield [str(c) for c in d.iloc[0].tolist()]
        for i in range(1, min(6, len(d))):
            row = [str(c) for c in d.iloc[i].tolist()]
            if str(row[0]).strip().upper() in SECTION_HEADS:
                yield row
        return
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.reader(fh)):
            if i > 40:
                break
            if not row:
                continue
            if i == 0 or str(row[0]).strip().upper() in SECTION_HEADS:
                yield row


# ─────────────────────────────── reading ───────────────────────────────────

def _rows(path: str):
    """Every data row, paired with the header that describes it.

    A sectioned file has a header per record type and rows that name their
    type in the first cell, sometimes abbreviated - `BOOM_HEADER` describes
    rows beginning `BH`, `LOGS` describes rows beginning `L`. The abbreviation
    is not stated anywhere, so it is matched on initials.
    """
    if path.lower().endswith((".xlsx", ".xls")):
        import pandas as pd
        d = pd.read_excel(path, header=0)
        header = [str(c) for c in d.columns]
        for _i, r in d.iterrows():
            yield header, [r[c] for c in d.columns]
        return

    with open(path, encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.reader(fh))
    heads, plain = {}, None
    for row in raw:
        if not row:
            continue
        first = str(row[0]).strip().upper()
        if first in SECTION_HEADS:
            heads[first] = row
            heads["".join(w[0] for w in first.split("_"))] = row
        elif plain is None and not heads:
            plain = row
    for row in raw:
        if not row:
            continue
        first = str(row[0]).strip().upper()
        if first in heads and row is not heads[first]:
            yield heads[first], row
        elif plain is not None and row is not plain and not heads:
            yield plain, row


def read(paths, month: str = "", log=print) -> tuple:
    """Marks that arrived, from one or more log delivery records.

    Returns (arrivals, report). Each arrival is a mark, when it came, how
    much, and where it was read from. `month` as YYYY-MM keeps only arrivals
    in it; without one, everything is returned.
    """
    if isinstance(paths, str):
        paths = [paths]

    found = defaultdict(lambda: {"volume": 0.0, "rows": 0, "first": "",
                                 "last": "", "supplier": "",
                                 "destination": "", "files": set()})
    read_files, outside = 0, 0

    for path in paths:
        seen_here = 0
        # A sectioned file carries the date on its header row and the marks
        # on its detail rows, so the date has to be remembered across them.
        current_date = ""
        for header, row in _rows(path):
            im = _column(header, "mark")
            idate = _column(header, "date")

            if idate is not None and idate < len(row):
                d = _iso(row[idate])
                if d:
                    current_date = d
            if im is None or im >= len(row):
                continue

            mark = str(row[im] or "").strip().upper()
            if not mark or not MARK.match(mark):
                continue

            when = current_date
            if month and when and when[:7] != month:
                outside += 1
                continue

            e = found[mark]
            e["rows"] += 1
            e["files"].add(os.path.basename(path))
            iv = _column(header, "volume")
            if iv is not None and iv < len(row):
                try:
                    v = float(str(row[iv]).replace(",", "") or 0)
                    e["volume"] += v if v == v else 0.0
                except (TypeError, ValueError):
                    pass
            for key in ("supplier", "destination"):
                ic = _column(header, key)
                if ic is not None and ic < len(row) and not e[key]:
                    v = str(row[ic] or "").strip()
                    # A numeric code is not a name, and `000` appears where a
                    # supplier field was left unset.
                    if v and not v.isdigit():
                        e[key] = v
            if when:
                if not e["first"] or when < e["first"]:
                    e["first"] = when
                if not e["last"] or when > e["last"]:
                    e["last"] = when
            seen_here += 1
        if seen_here:
            read_files += 1

    arrivals = []
    for mark, e in found.items():
        arrivals.append({
            "identifier": mark,
            "volume_m3": round(e["volume"], 3),
            "rows": e["rows"],
            "first_arrival": e["first"],
            "last_arrival": e["last"],
            "supplier": e["supplier"],
            "destination": e["destination"],
            "source_file": "; ".join(sorted(e["files"])),
        })
    arrivals.sort(key=lambda a: -a["volume_m3"])

    if arrivals:
        log("")
        log("{} log delivery record(s): {} mark(s) arrived{}".format(
            read_files, len(arrivals),
            " in {}".format(month) if month else ""))
        total = sum(a["volume_m3"] for a in arrivals)
        if total:
            log("  {:,.0f} m3 of log. Not added to the month's tonnage - "
                "these are chipped elsewhere and arrive again as chips."
                .format(total))
        for a in arrivals[:8]:
            log("    {:<14}{:>10,.1f} m3   {}".format(
                a["identifier"], a["volume_m3"], a["first_arrival"] or "-"))
        if len(arrivals) > 8:
            log("    ... and {} more".format(len(arrivals) - 8))
    if outside:
        log("  {:,} row(s) arrived outside {} and were left for that "
            "month".format(outside, month))

    return arrivals, {"files": read_files, "marks": len(arrivals),
                      "volume_m3": round(sum(a["volume_m3"]
                                             for a in arrivals), 3),
                      "outside_month": outside}


def _iso(v) -> str:
    """A date as YYYY-MM-DD, from whatever it was written as."""
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return "{}-{:02d}-{:02d}".format(m.group(1), int(m.group(2)),
                                         int(m.group(3)))
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        # Ambiguous between day-first and month-first. Day-first is assumed
        # because these come from Canadian and European systems, and where it
        # matters the value above twelve settles it.
        a, b = int(m.group(1)), int(m.group(2))
        day, mon = (a, b) if a > 12 else (b, a) if b > 12 else (a, b)
        return "{}-{:02d}-{:02d}".format(m.group(3), mon, day)
    try:
        import pandas as pd
        d = pd.to_datetime(s, errors="coerce")
        return "" if d is None or d != d else d.date().isoformat()
    except Exception:
        return ""
