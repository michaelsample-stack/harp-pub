"""What a supplier says about where their wood came from.

    from harp import declarations
    feats, report = declarations.read(paths, month="2026-07", log=log)

THE POINT OF THIS MODULE
------------------------
Two suppliers already declare their origins and neither does it the same way.
Mosaic exports GeoJSON, one file per contract and boom, with the harvest
boundary in it. Willis prints a table and scans it, with a state permit number
per entry and no geometry at all.

Both are the same act: a supplier telling us where their wood came from. What
differs is only what they hand over.

    geometry     taken at their word - the boundary is the evidence
    identifiers  resolved in a public register - the register is the evidence

So this is the roof rather than a third reader. A new supplier's format is a
new reader underneath it, not a new path through the pipeline, and a supplier
who switches from a scan to a spreadsheet changes nothing downstream.

WHAT COMES OUT
--------------
Features, whichever way they arrived, each carrying who declared it and what
the claim rests on. A declaration that ships geometry is P1d and is finished.
A declaration that ships identifiers resolves to register geometry, and that
is P2a - a permit covers several units and the supplier has not said which of
them fed a delivery, so it is a place to search and detection narrows it.

That difference is the whole reason for keeping the two apart rather than
calling everything declared.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter

# A Washington Forest Practices number: seven digits, or a tribal or regional
# prefix followed by digits.
PERMIT = re.compile(r"^(?:\d{7}|[A-Z]{2,5}\d{6,9})$")

# Column names a tabular declaration might use. Matched case-insensitively on
# a normalised header, because a supplier's spreadsheet will not use ours.
COLUMNS = {
    "permit": ("wa state fpa number", "fpa number", "fpa", "permit",
               "permit number", "application number", "notification number"),
    "supplier": ("supplier", "landowner", "vendor", "seller", "source"),
    "county": ("county",),
    "state": ("state",),
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).strip()


def _column(header: list, kind: str):
    want = COLUMNS[kind]
    normed = [_norm(h) for h in header]
    for w in want:
        if w in normed:
            return normed.index(w)
    for i, h in enumerate(normed):
        if any(w in h for w in want):
            return i
    return None


# ─────────────────────────── recognising one ───────────────────────────────

def looks_like(path: str) -> str:
    """What kind of declaration this file is, or an empty string.

    Returns "geometry", "identifiers", or "". Recognised by structure rather
    than by name or extension, the same as everything else in a drop.
    """
    low = path.lower()
    if low.endswith(".json"):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                doc = json.load(fh)
        except Exception:
            return ""
        feats = doc.get("features") if isinstance(doc, dict) else doc
        if isinstance(feats, list) and feats:
            props = (feats[0].get("properties") or feats[0]) or {}
            if "Originator" in props and "Products" in props:
                return "geometry"
        return ""

    if low.endswith((".csv", ".txt")):
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                header = next(csv.reader(fh), [])
        except Exception:
            return ""
        return "identifiers" if _column(header, "permit") is not None else ""

    if low.endswith(".pdf"):
        # A scan carries no text, so the only way to know is to read it - and
        # reading it is expensive. The name is a weak signal but it is the
        # only one available before OCR.
        return "identifiers" if any(w in os.path.basename(low)
                                    for w in ("eudr", "fpa", "declaration",
                                              "fiber", "fibre")) else ""
    return ""


# ────────────────────────────── the readers ────────────────────────────────

def read_table(path: str, log=print) -> list:
    """A declaration that lists identifiers - CSV, or a scan read by OCR."""
    rows = []
    if path.lower().endswith(".pdf"):
        rows = _read_pdf(path, log=log)
    else:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            r = list(csv.reader(fh))
        if not r:
            return []
        header, body = r[0], r[1:]
        ip = _column(header, "permit")
        if ip is None:
            return []
        isup, ico, ist = (_column(header, "supplier"),
                          _column(header, "county"), _column(header, "state"))
        for line in body:
            if ip >= len(line):
                continue
            permit = str(line[ip]).strip().upper()
            if not PERMIT.match(permit):
                continue
            rows.append({
                "permit": permit,
                "supplier": line[isup].strip() if isup is not None
                and isup < len(line) else "",
                "county": line[ico].strip() if ico is not None
                and ico < len(line) else "",
                "state": (line[ist].strip().upper() if ist is not None
                          and ist < len(line) else ""),
            })
    return rows


def _read_pdf(path: str, log=print) -> list:
    """A scanned declaration, read by OCR.

    The suppliers who send these are loggers, and a printed table scanned to
    PDF is what arrives. It is a better target than it sounds: the layout is
    a plain grid, and nothing here has to be trusted - a permit that misreads
    will not resolve in the register, and a permit that resolves is right.

    Read by word position rather than as text. Tesseract reads this table
    column by column otherwise, which puts every supplier name in one block
    and every permit in another.
    """
    try:
        import pdfplumber
        import pytesseract
    except ImportError:
        log("  reading a scanned declaration needs pdfplumber and "
            "pytesseract, and tesseract itself")
        return []

    rows = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            try:
                im = page.to_image(resolution=300).original
            except Exception:
                continue
            try:
                d = pytesseract.image_to_data(
                    im, output_type=pytesseract.Output.DATAFRAME)
            except Exception as exc:
                log("  page {}: {}".format(page_no,
                                           str(exc).splitlines()[0][:70]))
                continue
            d = d[(d.conf > 30) & d.text.notna()
                  & (d.text.astype(str).str.strip() != "")]
            if d.empty:
                continue
            d = d.assign(cy=d.top + d.height / 2).sort_values("cy")

            # Words on the same printed line, clustered on their centre. The
            # gap is generous: a table row is a good deal taller than the
            # jitter within one.
            lines, cur, last = [], [], None
            for _i, w in d.iterrows():
                if last is not None and abs(w.cy - last) > 18:
                    lines.append(cur)
                    cur = []
                cur.append(w)
                last = w.cy
            lines.append(cur)

            for line in lines:
                line = sorted(line, key=lambda w: w.left)
                toks = [str(w.text).strip() for w in line]
                permit = next((t for t in toks if PERMIT.match(t.upper())), "")
                if not permit:
                    continue
                i = toks.index(permit)
                # Numbers after the permit are volumes and totals. They are
                # dropped first, because a trailing volume otherwise hides
                # the state - "Grays Harbor WA 97.21" leaves the state in
                # with the county and the county reading as three words.
                rest = [t for t in toks[i + 1:]
                        if not re.match(r"^[\d,]+\.?\d*$", t)]
                # The state is a two-letter code somewhere after the permit,
                # not necessarily last: a marginal note like "FPA copy
                # attached" sits to the right of it on some rows and would
                # otherwise be read as the county.
                si = next((j for j in range(len(rest) - 1, -1, -1)
                           if len(rest[j]) == 2 and rest[j].isalpha()
                           and rest[j].isupper()), None)
                state = rest[si].upper() if si is not None else ""
                words = rest[:si] if si is not None else rest
                rows.append({"permit": permit.upper(),
                             "supplier": " ".join(toks[:i]),
                             "county": " ".join(words),
                             "state": state})
            if page_no == 1 and rows:
                # The table is on the first page. Later pages are the
                # supporting permits themselves, which are forms rather than
                # tables and have nothing to add.
                break
    if rows:
        log("  {} entr(ies) read from the scan".format(len(rows)))
    return rows


# ────────────────────────────── the roof ───────────────────────────────────

def read(paths, month: str = "", mark_lookup=None, log=print) -> tuple:
    """Every supplier declaration in these files, whatever shape they are in.

    Returns (features, report). Geometry-bearing declarations come back as
    P1d and are finished. Identifier-bearing ones are resolved against their
    register and come back as P2a, to be searched.
    """
    if isinstance(paths, str):
        paths = [paths]

    by_kind = {"geometry": [], "identifiers": []}
    for p in paths:
        kind = looks_like(p)
        if kind:
            by_kind[kind].append(p)

    feats, report = [], {"geometry_files": len(by_kind["geometry"]),
                         "identifier_files": len(by_kind["identifiers"])}

    if by_kind["geometry"]:
        from .sources import producer_geodata
        log("")
        log("{} file(s) of producer geodata".format(len(by_kind["geometry"])))
        got, rep = producer_geodata.read(by_kind["geometry"], month=month,
                                         mark_lookup=mark_lookup, log=log)
        feats.extend(got)
        report["declared_areas"] = len(got)
        report.update({"geodata_" + k: v for k, v in (rep or {}).items()})

    if by_kind["identifiers"]:
        log("")
        log("{} supplier declaration(s) listing permit numbers".format(
            len(by_kind["identifiers"])))
        entries = []
        for p in by_kind["identifiers"]:
            got = read_table(p, log=log)
            for e in got:
                e["file"] = os.path.basename(p)
            entries.extend(got)
        if entries:
            feats.extend(_resolve_permits(entries, log=log))
            report["declared_permits"] = len({e["permit"] for e in entries})

    return feats, report


def _resolve_permits(entries: list, log=print) -> list:
    """Permit numbers to the register geometry they name."""
    from .sources import fpars

    wa = [e for e in entries
          if not e.get("state") or e["state"] in ("WA", "WASHINGTON")]
    other = [e for e in entries if e not in wa]
    if other:
        # Named rather than dropped. A permit filed with a tribal nation or
        # another state is a real declaration this pipeline cannot resolve
        # yet, and it should not vanish into a count.
        log("  {} permit(s) outside Washington are not resolved: {}".format(
            len(other), ", ".join(sorted({e["permit"] for e in other})[:6])))

    if not wa:
        return []
    by_permit = {}
    for e in wa:
        by_permit.setdefault(e["permit"], e)

    feats, _rep = fpars.geometry(list(by_permit), log=log)
    for f in feats:
        e = by_permit.get(f["properties"].get("harp_key"), {})
        p = f["properties"]
        p["harp_supplier"] = e.get("supplier") or p.get("harp_supplier", "")
        p["ProducerName"] = e.get("supplier") or ""
        p["harp_producer_source"] = ("named on the supplier's own monthly "
                                     "declaration")
        if e.get("county") and not p.get("harp_county"):
            p["harp_county"] = e["county"]
        p["harp_source_file"] = e.get("file", "")
        # A permit covers several approved units and the supplier has not
        # said which of them fed a delivery. Real register geometry, more
        # than they cut for us, narrowed by detection - which is P2a.
        p["harp_tier"] = "P2a"
        p["harp_traceability"] = "indirect"
        p["harp_note"] = (
            "a harvest unit under a permit this supplier declared. They "
            "named the permit, not which of its units the wood came off, so "
            "what is declared is the harvest detection finds inside it.")
    if feats:
        who = Counter(f["properties"].get("ProducerName") or "?"
                      for f in feats)
        log("  {} unit(s) across {} supplier(s)".format(len(feats), len(who)))
    return feats
