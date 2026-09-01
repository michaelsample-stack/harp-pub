#!/usr/bin/env python3
"""Who is WWW?

    python tools/name_the_code.py --sources SOURCE.xlsx
    python tools/name_the_code.py --sources SOURCE.xlsx --code WWW
    python tools/name_the_code.py --sources SOURCE.xlsx --min-share 0.6

THE PROBLEM
-----------
Four supplier codes in the client's system have no company name anywhere in
the data: WWW, WWK, COS and WEW. They are not a geometry gap - their sources
carry timber marks and resolve to cut blocks perfectly well - but a harvest
area attributed to "WWW" is not something anybody can act on.

THE IDEA
--------
A timber mark resolves to a cut block, and that block names its tenure holder.
If every mark under a code points at the same holder, that holder is almost
certainly who the code is.

    WWW ─► 16 sources ─► timber marks ─► FTEN blocks ─► CLIENT_NUMBER
                                                        CLIENT_NAME

WHAT THIS IS NOT
----------------
Not proof. A code could be a reload, a broker, or a haul contractor moving
another company's wood - in which case the holder is the company whose timber
it was, not the company the client is paying. A single clear answer is a strong
lead worth confirming; a split answer is a finding of its own and usually means
the code is not a company at all.

Nothing is written to the alias table from here. This prints a case; a person
decides.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harp.sources import ften  # noqa: E402

# A mark that is obviously not a mark. These appear in the same column and
# would waste a query each.
NOT_A_MARK = re.compile(r"^(HOG|CHIP|BULK|OTHER|TRADE|BLANK|N/?A|UNKNOWN)$",
                        re.I)


def looks_like_mark(v: str) -> bool:
    v = (v or "").strip().upper()
    if not v or NOT_A_MARK.match(v):
        return False
    # Crown marks run a letter then five digits, or two letters then four.
    # Private marks in these extracts run five letters. The slash form is
    # local to this client.
    return bool(re.fullmatch(r"[A-Z]\d{5}|[A-Z]{2}\d{4}|[A-Z]{5}|\d+/\d+", v))


def read_sources(path: str, log=print) -> dict:
    """Supplier code to the identifiers filed under it."""
    import pandas as pd
    d = pd.read_excel(path, header=0, skiprows=[1])
    d = d.drop(columns=[c for c in d.columns if str(c).startswith("[#")],
               errors="ignore")
    for col in ("SUPPID", "UNITID"):
        if col not in d.columns:
            raise SystemExit("no {} column in {}".format(col,
                                                         os.path.basename(path)))
    out = defaultdict(list)
    for _, r in d.iterrows():
        code = str(r.get("SUPPID") or "").strip()
        ident = str(r.get("UNITID") or "").strip()
        if code and ident:
            out[code].append(ident)
    log("{} supplier code(s) across {} source(s)".format(len(out), len(d)))
    return out


def unnamed_codes(sources: dict, names: dict) -> list:
    """Codes with no company name against them.

    A name that is just the code, or that says the name is unknown, is not a
    name.
    """
    out = []
    for code in sorted(sources):
        name = (names.get(code) or "").strip()
        if not name or name.upper() == code.upper() or \
                "full name un" in name.lower() or "unknown" in name.lower():
            out.append(code)
    return out


def holders_for(marks: list, log=print) -> tuple:
    """Every tenure holder behind a set of marks."""
    holders = Counter()
    resolved, missed = 0, []
    for mark in marks:
        rows, _where, err = ften.by_field("TIMBER_MARK", mark)
        if err:
            log("    {} — service error: {}".format(mark, err[:60]))
            continue
        if not rows:
            missed.append(mark)
            continue
        resolved += 1
        for r in rows:
            num = str(r.get("CLIENT_NUMBER") or "").strip()
            name = str(r.get("CLIENT_NAME") or "").strip()
            if num or name:
                holders["{}|{}".format(num, name)] += 1
    return holders, resolved, missed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sources", required=True, help="SOURCE.xlsx")
    ap.add_argument("--code", help="one code, or several comma separated")
    ap.add_argument("--names", metavar="XLSX",
                    help="a delivery summary, to read SUPP_NAME from")
    ap.add_argument("--min-share", type=float, default=0.7,
                    help="how much of the evidence must agree before this "
                         "calls it a lead")
    ap.add_argument("--limit", type=int, default=25,
                    help="marks to try per code")
    ap.add_argument("--out", default="name_out")
    args = ap.parse_args()

    sources = read_sources(args.sources)

    names = {}
    if args.names and os.path.isfile(args.names):
        import pandas as pd
        d = pd.read_excel(args.names, header=0, skiprows=[1])
        if "SUPPID" in d.columns and "SUPP_NAME" in d.columns:
            names = {str(a).strip(): str(b).strip()
                     for a, b in zip(d.SUPPID, d.SUPP_NAME)}

    if args.code:
        codes = [c.strip().upper() for c in args.code.split(",")]
    else:
        codes = unnamed_codes(sources, names)
        print("\n{} code(s) with no company name: {}".format(
            len(codes), ", ".join(codes)))

    rows = []
    for code in codes:
        idents = sources.get(code) or []
        marks = sorted({i for i in idents if looks_like_mark(i)})
        print("\n" + "=" * 70)
        print("{}   {} source(s), {} of them a usable identifier".format(
            code, len(idents), len(marks)))
        if not marks:
            # Nothing to look up. Worth saying which, because a code whose
            # identifiers are all mill towns is a different problem from one
            # whose marks simply do not resolve.
            print("  Nothing here looks like a timber mark:")
            print("    " + ", ".join(sorted(set(idents))[:10]))
            print("  This code cannot be named this way.")
            rows.append({"code": code, "sources": len(idents), "marks": 0,
                         "resolved": 0, "verdict": "no usable identifiers",
                         "holder": "", "client_number": "", "share": ""})
            continue

        holders, resolved, missed = holders_for(marks[:args.limit])
        if not holders:
            print("  {} mark(s) tried, none resolved to a tenure holder"
                  .format(len(marks[:args.limit])))
            rows.append({"code": code, "sources": len(idents),
                         "marks": len(marks), "resolved": 0,
                         "verdict": "no marks resolved", "holder": "",
                         "client_number": "", "share": ""})
            continue

        total = sum(holders.values())
        print("  {} of {} mark(s) resolved, naming {} holder(s)".format(
            resolved, len(marks[:args.limit]), len(holders)))
        print()
        for key, n in holders.most_common(6):
            num, name = key.split("|", 1)
            print("    {:>5}  {:>5.0f}%   {:<12}{}".format(
                n, n / total * 100, num, name[:46]))
        if missed:
            print("\n  {} mark(s) not in the register: {}".format(
                len(missed), ", ".join(missed[:6])))

        top, n = holders.most_common(1)[0]
        num, name = top.split("|", 1)
        share = n / total
        print()
        if share >= args.min_share and len(holders) == 1:
            verdict = "one holder"
            print("  Every mark points at the same holder.")
            print("  {} is very likely {}.".format(code, name))
        elif share >= args.min_share:
            verdict = "one holder dominant"
            print("  {:.0f}% of the evidence points at {}.".format(
                share * 100, name))
            print("  A strong lead. The rest may be wood bought in, or the "
                  "code may cover more than one company.")
        else:
            verdict = "split"
            print("  No holder accounts for {:.0f}% of the marks.".format(
                args.min_share * 100))
            print("  That is a finding rather than a failure: a code drawing "
                  "from many holders is probably a reload, a broker or a haul "
                  "contractor rather than a company that cuts timber.")

        rows.append({"code": code, "sources": len(idents),
                     "marks": len(marks), "resolved": resolved,
                     "verdict": verdict, "holder": name,
                     "client_number": num,
                     "share": "{:.0%}".format(share)})

    if rows:
        os.makedirs(args.out, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(args.out, "code_names_{}.csv".format(stamp))
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print("\n" + "=" * 70)
        print("  {}".format(path))

    print()
    print("None of this is written anywhere. A holder behind a mark is who "
          "held the tenure, which is not always who the client paid - a "
          "reload or a haul contractor would show somebody else's name. "
          "Confirm a lead with the client before using it.")


if __name__ == "__main__":
    main()
