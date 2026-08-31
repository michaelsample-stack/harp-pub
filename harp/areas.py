"""Operating areas stated by hand, for suppliers nothing else can place.

    harp areas                          what has been stated, and what is missing
    harp areas --missing                just the gaps, as a worksheet
    harp areas --set "Waldun Forest Products" --districts DCK,DSI --who MB
    harp areas --set "Gorman Group" --counties 53009 --who MB --note "..."

WHY THIS EXISTS
---------------
Every automated route can fail at once. A remanufacturer holds no tenure, buys
logs on the open market, has no mill in the facility list and no place name in
their own name. Nothing in the public record places them anywhere.

Those suppliers have three possible ends. Somebody asks them and they answer -
which is the good one. Somebody who knows the region writes down where they
work. Or they stay a gap forever.

The second is what this is. It is weaker than a supplier's own declaration and
stronger than nothing, and the difference is recorded rather than blurred:
every entry carries who stated it, when, and why.

WHAT IT IS NOT
--------------
Not a place to put a guess to make a coverage figure look better. An entry
here is a claim that a person will stand behind, and it is attributed. If
nobody knows, the honest entry is no entry - the supplier stays in the gap
list where somebody might ask them.

HOW IT IS USED
--------------
The catchment builder reads this before falling back to a mill buffer, and
after every register route. So a stated area never overrides a tenure record,
and always beats a circle drawn round a mill.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime

FIELDS = ["supplier", "districts", "counties", "state", "stated_by",
          "stated_at", "basis", "note"]

# What a stated area rests on, best first. Recorded per entry because the
# difference between a supplier telling us and somebody inferring it is the
# difference between P3a declared and P3a inferred.
BASIS = {
    "supplier": "the supplier said so",
    "client": "the client's own staff said so",
    "local": "local knowledge of where they work",
    "inferred": "reasoned from something else",
}


def path_for(cfg) -> str:
    base = ((getattr(cfg, "sources", None) or {}).get("areas") or {}).get(
        "path")
    if base:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(base)))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "registry", "supplier_areas.csv")


def load(path: str) -> dict:
    """Stated areas, keyed on supplier name."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("supplier") or "").strip()
            if not name:
                continue
            out[name] = {
                "districts": [d.strip().upper() for d in
                              (row.get("districts") or "").split(",")
                              if d.strip()],
                "counties": [c.strip() for c in
                             (row.get("counties") or "").split(",")
                             if c.strip()],
                "state": (row.get("state") or "").strip().upper(),
                "stated_by": (row.get("stated_by") or "").strip(),
                "stated_at": (row.get("stated_at") or "").strip(),
                "basis": (row.get("basis") or "").strip().lower(),
                "note": (row.get("note") or "").strip(),
            }
    return out


def save(path: str, table: dict) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for name in sorted(table):
            e = table[name]
            w.writerow({
                "supplier": name,
                "districts": ",".join(e.get("districts") or []),
                "counties": ",".join(e.get("counties") or []),
                "state": e.get("state", ""),
                "stated_by": e.get("stated_by", ""),
                "stated_at": e.get("stated_at", ""),
                "basis": e.get("basis", ""),
                "note": e.get("note", ""),
            })
    return path


def set_area(path: str, supplier: str, who: str, districts=None,
             counties=None, state: str = "", basis: str = "local",
             note: str = "") -> dict:
    """State where a supplier operates. Overwrites any previous entry."""
    if not (districts or counties):
        raise RuntimeError(
            "give --districts or --counties. An entry with neither says "
            "nothing, and a supplier with nothing stated is better left in "
            "the gap list where somebody might ask them.")
    if basis not in BASIS:
        raise RuntimeError("basis wants one of: {}".format(
            ", ".join(sorted(BASIS))))
    table = load(path)
    table[supplier.strip()] = {
        "districts": [d.strip().upper() for d in (districts or [])],
        "counties": [c.strip() for c in (counties or [])],
        "state": state.strip().upper(),
        "stated_by": who,
        "stated_at": datetime.now().date().isoformat(),
        "basis": basis,
        "note": note,
    }
    save(path, table)
    return table[supplier.strip()]


def summary(table: dict) -> str:
    if not table:
        return "nothing stated yet"
    by = {}
    for e in table.values():
        by[e.get("basis") or "?"] = by.get(e.get("basis") or "?", 0) + 1
    lines = ["{} supplier(s) with a stated area".format(len(table))]
    for b, n in sorted(by.items(), key=lambda kv: -kv[1]):
        lines.append("  {:>4}  {}".format(n, BASIS.get(b, b)))
    return "\n".join(lines)


def worksheet(gaps: list[dict], path: str, log=print) -> str:
    """A file to fill in, for the suppliers nothing could place.

    Deliberately a worksheet rather than a form: the useful columns are the
    ones a person can actually answer, and the rest are left for the tool to
    fill when the answer comes back.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["supplier", "code", "jurisdiction", "july_bdt",
                    "what_is_known", "districts", "counties", "state",
                    "basis", "stated_by", "note"])
        for g in gaps:
            w.writerow([g.get("supplier", ""), g.get("code", ""),
                        g.get("jurisdiction", ""), g.get("bdt", ""),
                        g.get("why", ""), "", "", "", "", "", ""])
    log("{} supplier(s) with nothing to go on".format(len(gaps)))
    log("")
    log("Fill in districts (DCK, DSI) or counties (a 5-digit FIPS) for any "
        "you know, then load it back:")
    log("  harp areas --load {}".format(os.path.basename(path)))
    log("")
    log("Leave a row blank if nobody knows. A blank row keeps the supplier "
        "in the gap list where somebody might ask them; a guess buries them.")
    return path


def load_worksheet(path: str, table_path: str, who: str, log=print) -> int:
    """Take a filled-in worksheet back into the table."""
    table = load(table_path)
    added = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("supplier") or "").strip()
            d = [x.strip().upper() for x in
                 (row.get("districts") or "").split(",") if x.strip()]
            c = [x.strip() for x in (row.get("counties") or "").split(",")
                 if x.strip()]
            if not name or not (d or c):
                continue
            table[name] = {
                "districts": d, "counties": c,
                "state": (row.get("state") or "").strip().upper(),
                "stated_by": (row.get("stated_by") or "").strip() or who,
                "stated_at": datetime.now().date().isoformat(),
                "basis": (row.get("basis") or "local").strip().lower(),
                "note": (row.get("note") or "").strip(),
            }
            added += 1
    save(table_path, table)
    log("{} supplier(s) taken from the worksheet".format(added))
    return added
