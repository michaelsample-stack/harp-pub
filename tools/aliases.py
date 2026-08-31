#!/usr/bin/env python3
"""Review the supplier alias table.

The matcher proposes; a person decides. This is where the deciding happens.

    python tools/aliases.py review                 what needs a ruling
    python tools/aliases.py accept "Teal Jones" 00010199 --who MB \\
           --reason "Teal-Jones Group owns Teal Cedar Products"
    python tools/aliases.py reject "Chips Ahoy Fibre Supplier Ltd" 00107744 \\
           --who MB --reason "unrelated - Fraser Pulp Chips"
    python tools/aliases.py list --state accepted
    python tools/aliases.py stats

WHY A TABLE RATHER THAN A BETTER MATCHER
----------------------------------------
Some of the answer is not in the names. Teal-Jones Group owns Teal Cedar
Products and no amount of string comparison will discover that. Kruger Inc.
and Kruger Products 2010 LP might be one firm. A matcher asked to rule on
these will reach the same uncertain conclusion every month; a person asked
once will not.

So a decision made today holds. Tightening the matcher later does not change
a historical answer, because the answer no longer comes from the matcher.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harp.aliases import ACCEPTED, PROPOSED, REJECTED, AliasTable  # noqa: E402

DEFAULT = "./data/registry/supplier_aliases.csv"


def cmd_review(t: AliasTable, args) -> int:
    open_rows = t.open_questions()
    if not open_rows:
        print("Nothing waiting. {}".format(t.summary().splitlines()[0]))
        return 0

    print("{} proposal(s) need a ruling.\n".format(len(open_rows)))
    print("These are NOT being used. A supplier's tenure is thousands of "
          "blocks,\nso an unconfirmed holder stays out of any declaration "
          "until someone says.\n")
    current = None
    for a in open_rows:
        if a.supplier != current:
            current = a.supplier
            print("\n{}".format(a.supplier))
            accepted = t.for_supplier(a.supplier, a.jurisdiction, ACCEPTED)
            if accepted:
                print("   already accepted: {}".format(
                    ", ".join(x.client_name for x in accepted)[:80]))
        print("   {:<40} {:<10} {}".format(
            a.client_name[:40], a.client_number, a.match_basis[:40]))

    print("\n" + "-" * 70)
    print("To rule on one:")
    print('  python tools/aliases.py accept "{}" {} --who YOU --reason "..."'
          .format(open_rows[0].supplier, open_rows[0].client_number))
    print('  python tools/aliases.py reject "{}" {} --who YOU --reason "..."'
          .format(open_rows[0].supplier, open_rows[0].client_number))
    print("\nOr edit {} directly - set the state column to accepted or "
          "rejected.".format(t.path))
    return 0


def cmd_decide(t: AliasTable, args, accept: bool) -> int:
    a = t.decide(args.supplier, args.client_number, accept,
                 who=args.who or os.environ.get("USERNAME", "unknown"),
                 reason=args.reason or "", jurisdiction=args.jurisdiction)
    t.save()
    print("{}: {} -> {} ({})".format(
        "accepted" if accept else "rejected", a.supplier,
        a.client_name or a.client_number, a.decided_by))
    if a.reason:
        print("  reason: {}".format(a.reason))
    return 0


def cmd_list(t: AliasTable, args) -> int:
    rows = [a for a in t.rows.values()
            if not args.state or a.state == args.state]
    if not rows:
        print("nothing matching")
        return 0
    rows.sort(key=lambda a: (a.state, a.supplier.upper()))
    print("{:<32}{:<38}{:<11}{:<10}{}".format(
        "SUPPLIER", "CLIENT", "NUMBER", "STATE", "WHO / WHY"))
    print("-" * 116)
    for a in rows:
        who = "{} {}".format(a.decided_by, a.decided_on).strip()
        print("{:<32}{:<38}{:<11}{:<10}{}".format(
            a.supplier[:32], a.client_name[:38], a.client_number,
            a.state, (who + "  " + a.reason).strip()[:34]))
    print("-" * 116)
    print(t.summary())
    return 0


def cmd_stats(t: AliasTable, args) -> int:
    print(t.summary())
    acc = [a for a in t.rows.values() if a.state == ACCEPTED]
    if acc:
        undecided = {a.supplier for a in t.rows.values()
                     if a.state == PROPOSED} - {a.supplier for a in acc}
        if undecided:
            print("\n{} supplier(s) have proposals but nothing accepted:"
                  .format(len(undecided)))
            for s in sorted(undecided)[:20]:
                print("  {}".format(s))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", default=DEFAULT, help="the alias csv")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="what needs a ruling")
    r.set_defaults(fn=lambda t, a: cmd_review(t, a))

    for name, accept in (("accept", True), ("reject", False)):
        p = sub.add_parser(name, help="{} a proposed match".format(name))
        p.add_argument("supplier")
        p.add_argument("client_number")
        p.add_argument("--who", help="who is deciding")
        p.add_argument("--reason", help="why - this matters later")
        p.add_argument("--jurisdiction", default="BC")
        p.set_defaults(fn=lambda t, a, acc=accept: cmd_decide(t, a, acc))

    li = sub.add_parser("list", help="everything in the table")
    li.add_argument("--state", choices=[ACCEPTED, PROPOSED, REJECTED])
    li.set_defaults(fn=cmd_list)

    st = sub.add_parser("stats", help="counts")
    st.set_defaults(fn=cmd_stats)

    args = ap.parse_args()
    table = AliasTable(args.table)
    return args.fn(table, args)


if __name__ == "__main__":
    raise SystemExit(main())
