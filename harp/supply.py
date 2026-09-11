"""What arrived this month, and how each of it should be resolved.

    from harp import supply
    plan, report = supply.plan_month(deliveries, register, month, log=log)

WHY THIS EXISTS
---------------
The pipeline used to take its work from the supply source register: every
identifier in it, resolved, searched and declared. A July run resolved 217
identifiers to declare a month in which 41 sources delivered.

The register is a master list of everything the client might buy from. Most of
it did not arrive this month, and a good deal of it is not a delivery at all -
159 of its 279 rows are log purchases sent out for chipping, which arrive back
later under a different source entirely.

So the month's work comes from the delivery record, and the register is a
lookup for what each delivered source is.

FOUR KINDS OF ARRIVAL
---------------------
`ORIGIN_TYPE` in the register says which, and they are not equally traceable.
Across 2026 to date, by mass rather than by count:

    merchant residual   304,547 BDT   71%   chips bought from a sawmill
    toll chipped        121,092 BDT   28%   own logs, chipped by somebody else
    yard                  4,892 BDT    1%   own material
    trade                   103 BDT    0%

**Merchant residual is the ceiling case and it is most of the fibre.** A
sawmill selling its residual chips has no timber mark to give: the logs were
its own purchase, cut under marks it does not pass on and often does not keep.
A search area is the honest answer and no amount of asking will improve it.

**Toll chipped is the opposite.** The client bought those logs themselves,
under marks that are already in the register, and sent them to DCT or Mid
Island to be chipped. The marks exist and resolve; what is missing is only
which of them fed which shipment. The pool of blocks routed to that chipper
is a search area of real registered geometry, and detection narrows it to the
window - the same treatment a supplier's own tenure already gets.

MEASURED IN TONNES, NOT FEATURES
--------------------------------
A month reported as "3,781 features across nine tiers" says nothing about how
much of the fibre is placed. Reported as "71% of this month is merchant
residual and bounded at district level", it is something the client can act
on - or decline to, knowingly.

Every figure this module produces is bone-dry tonnes.
"""

from __future__ import annotations

from collections import defaultdict

# How a delivered source is treated. The register's own ORIGIN_TYPE decides,
# with the supplier code distinguishing a toll chipper from any other yard
# receipt.
MERCHANT = "merchant residual"
TOLL = "toll chipped"
OWN_TENURE = "own tenure"
YARD = "own yard"
TRADE = "trade"
UNKNOWN = "not in the register"

# Toll chippers, by the supplier code they receive under. A chipper takes the
# client's own logs and returns chips, so what arrives is a receipt rather
# than a purchase - which is why these are YARD in the register despite being
# somebody else's premises.
#
# Config can add to this; the names here are the ones seen in the data.
TOLL_CHIPPERS = {
    "MIDISL": ["MIDISL", "MID ISLAND"],
    "DCT": ["DCT", "CHAMBERS", "LADYSMITH", "JORDAN RIVER"],
    "CWI-CC": ["COASTLAND", "CWI-CC"],
    "FFP": ["FRANKLIN CUSTOM"],
    "CAHOY": ["CHIPS AHOY"],
    "LONGHOH": ["LONGHOH"],
}


def settings(cfg) -> dict:
    s = ((getattr(cfg, "sources", None) or {}).get("supply") or {})
    chippers = dict(TOLL_CHIPPERS)
    for code, names in (s.get("toll_chippers") or {}).items():
        chippers[str(code).upper()] = [str(n).upper() for n in names]
    return {"toll_chippers": chippers,
            # Below this share of a month, a kind is not worth its own line in
            # the summary - it goes in with the rest.
            "report_floor": float(s.get("report_floor", 0.005))}


# ──────────────────────────────── the plan ─────────────────────────────────

def _kind(row: dict, chippers: dict) -> str:
    """What kind of arrival this source is."""
    if not row:
        return UNKNOWN
    origin = str(row.get("ORIGIN_TYPE") or "").strip().upper()
    supp = str(row.get("SUPPID") or "").strip().upper()
    name = str(row.get("NAME") or "").strip().upper()

    if origin == "TIMBER_LAND":
        return OWN_TENURE
    if origin in ("TRADE", "TRADE_PARTNER"):
        return TRADE
    if origin == "YARD":
        for code, needles in chippers.items():
            if supp.startswith(code) or any(n in name for n in needles):
                return TOLL
        # A yard receipt that is not from a chipper is the client's own
        # material moving about. It arrived, but it did not come from a
        # forest this month.
        return YARD
    # PURCHASE, and anything unrecognised. A purchased chip is a sawmill's
    # residual unless the source itself carries a harvest identifier.
    return MERCHANT


def _destination(name: str, chippers: dict) -> str:
    """Which chipper a log purchase was sent to, from its NAME.

    The register records where logs went - "Direct Delivery MIDISL", "SB4
    LADYSMITH-DCT". That routing is the only link between a log purchase and
    the chips that come back from it, and it is what makes a toll-chipped
    delivery traceable at all.
    """
    n = str(name or "").strip().upper()
    if not n:
        return ""
    for code, needles in chippers.items():
        if any(needle in n for needle in needles):
            return code
    return ""


def mark_pools(register: list[dict], chippers: dict) -> dict:
    """The identifiers routed to each chipper.

    {chipper code: [identifier, ...]}. These are log purchases, so their
    identifiers are timber marks that resolve in the usual way. The pool is
    every block the client sent to that chipper - more than fed any one
    shipment, which is what detection is for.
    """
    pools = defaultdict(list)
    for row in register:
        if str(row.get("PRODUCT_TYPE") or "").strip().upper() != "LOG":
            continue
        code = _destination(row.get("NAME"), chippers)
        ident = str(row.get("UNITID") or "").strip()
        if code and ident:
            pools[code].append(ident)
    return {k: sorted(set(v)) for k, v in pools.items()}


def plan_month(deliveries: list[dict], register: list[dict], month: str = "",
               cfg=None, log=print) -> tuple:
    """What arrived, grouped by source, with how each should be resolved.

    `deliveries` is the load record - one row per load, from
    `lots.read_deliveries`. `register` is the supply source register.

    Returns (plan, report). The plan is one entry per delivered source, in
    descending order of mass, because that is the order the work matters in.
    """
    opts = settings(cfg)
    chippers = opts["toll_chippers"]
    by_source = {str(r.get("SOURCEID") or "").strip(): r for r in register}
    pools = mark_pools(register, chippers)

    arrived = defaultdict(lambda: {"bdt": 0.0, "loads": 0, "first": "",
                                   "last": ""})
    for load in deliveries:
        sid = str(load.get("source") or load.get("SOURCEID") or "").strip()
        if not sid:
            continue
        e = arrived[sid]
        try:
            v = float(load.get("bdt") or load.get("BDT") or 0)
            # A NaN survives float() and poisons every total downstream, so
            # the sum comes out as nan and every share with it.
            e["bdt"] += v if v == v else 0.0
        except (TypeError, ValueError):
            pass
        e["loads"] += 1
        when = str(load.get("date") or load.get("DATE_IN") or "")[:10]
        if when:
            if not e["first"] or when < e["first"]:
                e["first"] = when
            if not e["last"] or when > e["last"]:
                e["last"] = when

    plan = []
    for sid, e in arrived.items():
        row = by_source.get(sid) or {}
        kind = _kind(row, chippers)
        entry = {
            "source_id": sid,
            "identifier": str(row.get("UNITID") or "").strip(),
            "supplier": str(row.get("SUPPID") or "").strip(),
            "supplier_name": str(row.get("NAME") or "").strip(),
            "jurisdiction": str(row.get("STATEID") or "").strip().upper(),
            "product": str(row.get("PRODUCT_TYPE") or "").strip().upper(),
            "origin": str(row.get("ORIGIN_TYPE") or "").strip().upper(),
            "kind": kind,
            "bdt": round(e["bdt"], 2),
            "loads": e["loads"],
            "first_load": e["first"],
            "last_load": e["last"],
            "pool": [],
        }
        if kind == TOLL:
            code = ""
            supp = entry["supplier"].upper()
            for c in chippers:
                if supp.startswith(c):
                    code = c
                    break
            if not code:
                code = _destination(entry["supplier_name"], chippers)
            entry["chipper"] = code
            entry["pool"] = pools.get(code, [])
        plan.append(entry)

    plan.sort(key=lambda e: -e["bdt"])
    report = summarise(plan, month, opts, log=log)

    # Does this record belong to the month being declared?
    #
    # Nothing checked. A drop folder for one month run with another month's
    # label produced a file named for the second and filled with the first's
    # deliveries, and said nothing - which is the worst kind of wrong output,
    # because it looks right.
    if month:
        seen = sorted({e["first_load"][:7] for e in plan if e["first_load"]}
                      | {e["last_load"][:7] for e in plan if e["last_load"]})
        if seen and month not in seen:
            report["wrong_month"] = seen
            log("")
            log("!" * 66)
            log("This delivery record is for {}, and the run is declaring "
                "for {}.".format(" and ".join(seen), month))
            log("")
            log("  The month would be named {} and contain {}'s deliveries. "
                "Nothing downstream would say so.".format(
                    month, seen[0]))
            log("")
            log("  Either the wrong folder was given, or the wrong month.")
            log("!" * 66)
    return plan, report


def summarise(plan: list[dict], month: str, opts: dict, log=print) -> dict:
    """What arrived, by mass, and how much of it can be placed."""
    total = sum(e["bdt"] for e in plan) or 1.0
    by_kind = defaultdict(float)
    for e in plan:
        by_kind[e["kind"]] += e["bdt"]

    log("")
    log("{:,} source(s) delivered {}{:,.0f} BDT".format(
        len(plan), "in {}, ".format(month) if month else "", total))
    log("")
    for kind, bdt in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        log("  {:<22}{:>10,.0f} BDT{:>7.0f}%   {}".format(
            kind, bdt, 100 * bdt / total, _explain(kind)))

    ceiling = by_kind.get(MERCHANT, 0.0)
    if ceiling / total > 0.25:
        log("")
        log("  {:.0f}% of this month is residual chips bought from a sawmill. "
            "Those carry no harvest identifier and a search area is the best "
            "answer available for them - not a gap to be chased."
            .format(100 * ceiling / total))

    toll = [e for e in plan if e["kind"] == TOLL]
    if toll:
        pooled = sum(len(e["pool"]) for e in
                     {e.get("chipper"): e for e in toll}.values())
        log("")
        log("  {:.0f}% was chipped under toll from the client's own logs, "
            "across {} identifier(s) already in the register.".format(
                100 * by_kind[TOLL] / total, pooled))

    unknown = [e for e in plan if e["kind"] == UNKNOWN]
    if unknown:
        log("")
        log("  {} source(s) delivered {:,.0f} BDT and are not in the "
            "register:".format(len(unknown),
                               sum(e["bdt"] for e in unknown)))
        for e in unknown[:6]:
            log("    {:<28}{:>9,.0f} BDT".format(e["source_id"][:28],
                                                 e["bdt"]))

    return {"sources": len(plan), "bdt": round(total, 2),
            "by_kind": {k: round(v, 2) for k, v in by_kind.items()},
            "shares": {k: round(v / total, 4) for k, v in by_kind.items()},
            "unknown": len(unknown)}


def _explain(kind: str) -> str:
    return {
        MERCHANT: "sawmill chips - no mark exists to ask for",
        TOLL: "own logs chipped out - the marks are in the register",
        OWN_TENURE: "the client's own tenure",
        YARD: "own material moving, not a harvest this month",
        TRADE: "traded volume",
        UNKNOWN: "delivered but not in the register",
    }.get(kind, "")


def to_records(plan: list[dict], log=print) -> tuple:
    """The plan as things to resolve, plus what needs a search area instead.

    Returns (identifiers, pooled, unresolvable). The first resolve normally.
    The second are toll-chipper pools, which become search areas of real
    registered blocks. The third have no identifier at all and get a
    catchment.
    """
    identifiers, pooled, none = [], {}, []
    for e in plan:
        if e["kind"] == TOLL and e["pool"]:
            pooled.setdefault(e.get("chipper") or e["supplier"],
                              {"pool": e["pool"], "bdt": 0.0,
                               "sources": []})
            slot = pooled[e.get("chipper") or e["supplier"]]
            slot["bdt"] += e["bdt"]
            slot["sources"].append(e["source_id"])
            continue
        if e["kind"] in (YARD,):
            # Own material moving between yards. It arrived, but it did not
            # come from a forest this month and declaring it would double
            # count the wood that did.
            continue
        if e["identifier"]:
            identifiers.append(e)
        else:
            none.append(e)

    log("")
    log("  {} source(s) with an identifier to resolve".format(
        len(identifiers)))
    if pooled:
        log("  {} toll chipper(s), {} identifier(s) between them".format(
            len(pooled), sum(len(p["pool"]) for p in pooled.values())))
    if none:
        log("  {} source(s) with no identifier - a search area is the "
            "ceiling".format(len(none)))
    return identifiers, pooled, none
