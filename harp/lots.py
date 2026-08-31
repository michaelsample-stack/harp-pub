"""From a production lot back to the deliveries that fed it.

    harp lot <lot list.xlsx> --deliveries <load summary.xlsx>
    harp lot <lot list.xlsx> --deliveries ... --lot HANK-96
    harp lot <lot list.xlsx> --deliveries ... --dry-run

WHAT THIS ANSWERS
-----------------
A pulp lot is made from chips that arrived over the preceding weeks, already
mixed. Nothing records which delivery went into which lot, and once wood is in
a pile the question has no exact answer.

So the answer is bounded rather than exact: walk back through the delivery
record from the lot's production date, accumulating by species, until twice the
lot's requirement is covered. Every supplier in that window is a possible
contributor and all of them are declared.

**Twice** is the safety margin. Piles do not empty cleanly - Harmac reclaims
from the top, so material at the bottom can sit a long time - and
over-declaring is the failure that survives an audit.

**Species are tracked separately but selection is not filtered by them.** Each
species has its own counter and its own target, and the walk continues until
the slowest is satisfied - so a lot that is mostly fir still reaches far enough
back to cover its hemlock. But a load is taken if it carries any outstanding
species at all, with no minimum share. A load that is 95% cedar enters a
no-cedar lot on the strength of its 2% fir, because that fir is real and
plausibly came off the same cut block as the cedar. A stand is rarely one
species.

THE ARITHMETIC
--------------
A lot's weight is finished pulp, not chips. Roughly half the wood dissolves in
the digester as lignin and leaves as black liquor, so a tonne of pulp took
something like two tonnes of dry chips. The conversion runs:

    air-dry tonnes of pulp
      x species share            per species, as measured, not the recipe
      x m3 of chips per Adt      5.00 fir, 5.50 hem, 7.75 cedar
      / m3 per BDU               chip volume to bone-dry units
      x tonnes per BDU           1.089, a BDU being 2,400 lb
      = bone-dry tonnes          the unit the delivery record uses

Every factor is in config. They came from the client's own master data table
and will be confirmed; none of them should need a code change.

ONE FACTOR IS RECORDED THE OTHER WAY UP
---------------------------------------
The client's table reads `3.375 BDU/m3`. Taken literally that is 3.7 tonnes of
dry fibre in a cubic metre of chips, which is denser than solid wood - chips
are mostly air. Read as `3.375 m3/BDU` it gives about 0.32 BDT/m3, which is
right for coastal softwood.

It is used as m3/BDU here. `chip_m3_per_bdu` in config, and a note in the
output saying so, because a silent unit inversion would send the walkback
eleven times too deep and still look plausible.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# The species columns, shared by the lot list and the delivery record. They
# match exactly, which is the one thing about these two files that was not a
# fight.
SPECIES = ("DFIR", "HEMBAL", "RCEDAR")

DEFAULT_FACTORS = {
    # Cubic metres of chips per air-dry tonne of pulp, by species. Cedar takes
    # over half again as much volume as fir for the same tonne of pulp.
    "chip_m3_per_adt": {"DFIR": 5.00, "HEMBAL": 5.50, "RCEDAR": 7.75},
    # See the note above. Recorded by the client as BDU/m3; used as m3/BDU.
    "chip_m3_per_bdu": 3.375,
    # A bone dry unit is 2,400 lb.
    "tonnes_per_bdu": 1.0886,
    # The walkback covers twice the lot's requirement.
    "walkback_multiple": 2.0,
}


def factors(cfg) -> dict:
    """Conversion factors, from config where present."""
    out = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in DEFAULT_FACTORS.items()}
    given = ((getattr(cfg, "sources", None) or {}).get("lots") or {})
    for k, v in given.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k].update(v)
        elif k in out:
            out[k] = v
    return out


@dataclass
class Lot:
    lot_id: str
    earliest: datetime
    latest: datetime
    adt: float                       # air-dry tonnes of pulp
    species: dict                    # share by species, as a fraction
    spec_name: str = ""
    customer: str = ""

    @property
    def span_days(self) -> float:
        try:
            return (self.latest - self.earliest).total_seconds() / 86400
        except Exception:
            return 0.0


@dataclass
class Walk:
    lot: Lot
    required_bdt: dict = field(default_factory=dict)   # per species, doubled
    covered_bdt: dict = field(default_factory=dict)
    deliveries: list = field(default_factory=list)
    suppliers: dict = field(default_factory=dict)      # code -> bdt
    months: set = field(default_factory=set)
    reached: datetime | None = None
    short: dict = field(default_factory=dict)

    @property
    def satisfied(self) -> bool:
        return not self.short

    @property
    def days_back(self) -> float:
        if not self.reached:
            return 0.0
        return (self.lot.earliest - self.reached).total_seconds() / 86400


# ─────────────────────────────── reading ───────────────────────────────────

def read_lots(path: str, log=print) -> list[Lot]:
    """A production lot list. Weight is finished pulp, in kilograms."""
    import pandas as pd
    d = pd.read_excel(path)
    d = d.drop(columns=[c for c in d.columns
                        if str(c).strip() == "" or str(c).startswith("[#")],
               errors="ignore")
    if "Lot ID" not in d.columns:
        raise RuntimeError(
            "no 'Lot ID' column in {} - is that a lot list?".format(path))
    d = d[d["Lot ID"].notna()]

    lots = []
    for _, r in d.iterrows():
        shares, total = {}, 0.0
        for s in SPECIES:
            try:
                v = float(r.get(s) or 0)
            except (TypeError, ValueError):
                v = 0.0
            shares[s] = v
            total += v
        if total <= 0:
            continue
        # Recorded as percentages summing to about 100. Normalising rather
        # than dividing by 100 absorbs the rounding - they run 99.9 to 100.08.
        shares = {k: v / total for k, v in shares.items()}
        try:
            kg = float(r.get("Inv Wt (kg)") or 0)
        except (TypeError, ValueError):
            kg = 0.0
        if kg <= 0:
            continue
        lots.append(Lot(
            lot_id=str(r["Lot ID"]).strip(),
            earliest=r.get("Earliest Prod Time"),
            latest=r.get("Latest Prod Time"),
            adt=kg / 1000.0,
            species=shares,
            spec_name=str(r.get("Spec Name") or "").strip(),
            customer=str(r.get("End User Name") or "").strip()))

    log("{} lot(s), {:,.0f} Adt of pulp".format(
        len(lots), sum(l.adt for l in lots)))
    if lots:
        spans = sorted(l.span_days for l in lots)
        log("  produced {} to {}".format(
            min(l.earliest for l in lots).date(),
            max(l.latest for l in lots).date()))
        log("  production spans a window, not a moment: median {:.1f} h, "
            "longest {:.1f} days".format(spans[len(spans) // 2] * 24,
                                         spans[-1]))
    return lots


def read_deliveries(path: str, log=print) -> list[dict]:
    """The load delivery record. Mass is bone-dry tonnes.

    One row per load, with a date, a supplier, a source, and the measured
    species split of that load.
    """
    import pandas as pd
    d = pd.read_excel(path, header=0, skiprows=[1])
    d = d.drop(columns=[c for c in d.columns if str(c).startswith("[#")],
               errors="ignore")

    date_col = next((c for c in d.columns
                     if "DATE" in str(c).upper()
                     or "TIME" in str(c).upper()), None)
    if not date_col:
        raise RuntimeError("no date column in the delivery record")
    if "BDT" not in d.columns:
        raise RuntimeError(
            "no BDT column - GROSS is truck weight, not fibre, and is not a "
            "substitute")

    rows = []
    for _, r in d.iterrows():
        when = r.get(date_col)
        try:
            bdt = float(r.get("BDT") or 0)
        except (TypeError, ValueError):
            bdt = 0.0
        if bdt <= 0 or when is None:
            continue
        shares, total = {}, 0.0
        for s in SPECIES:
            try:
                v = float(r.get(s) or 0)
            except (TypeError, ValueError):
                v = 0.0
            shares[s] = v
            total += v
        if total > 0:
            shares = {k: v / total for k, v in shares.items()}
        else:
            # No measured split on this load. It still carries fibre and
            # still has a supplier, so it is kept and flagged rather than
            # dropped - dropping it would quietly deepen the walkback.
            shares = {}
        rows.append({
            "when": when,
            "bdt": bdt,
            "supplier": str(r.get("SUPPID") or "").strip(),
            "supplier_name": str(r.get("SUPP_NAME") or "").strip(),
            "source": str(r.get("SOURCEID") or "").strip(),
            "species": shares,
            "unsplit": not shares,
        })

    rows.sort(key=lambda x: x["when"])
    unsplit = sum(1 for r in rows if r["unsplit"])
    log("{:,} delivery load(s), {:,.0f} BDT".format(
        len(rows), sum(r["bdt"] for r in rows)))
    if rows:
        log("  delivered {} to {}".format(rows[0]["when"].date(),
                                          rows[-1]["when"].date()))
    if unsplit:
        log("  {:,} load(s) carry no species split and can only be counted "
            "against the total".format(unsplit))
    return rows


# ─────────────────────────────── the walk ──────────────────────────────────

def chips_required(lot: Lot, f: dict) -> dict:
    """Bone-dry tonnes of chips per species for one lot, before the multiple.

    Pulp to volume to bone-dry units to tonnes. The species factors differ by
    more than half, so this cannot be done on the total and apportioned after.
    """
    per_adt = f["chip_m3_per_adt"]
    m3_per_bdu = f["chip_m3_per_bdu"]
    t_per_bdu = f["tonnes_per_bdu"]
    out = {}
    for s in SPECIES:
        adt = lot.adt * lot.species.get(s, 0.0)
        m3 = adt * per_adt.get(s, 0.0)
        out[s] = (m3 / m3_per_bdu) * t_per_bdu if m3_per_bdu else 0.0
    return out


def walk(lot: Lot, deliveries: list[dict], f: dict, log=None) -> Walk:
    """Back through the deliveries until twice the lot is covered.

    From the earliest production time, on the reasoning that chips consumed on
    the first day of a run must have arrived before it. For a lot that ran
    eleven days that is a materially earlier start than the last day, and it
    is the conservative end.
    """
    multiple = f["walkback_multiple"]
    need = {s: v * multiple for s, v in chips_required(lot, f).items()}
    w = Walk(lot=lot, required_bdt=dict(need))
    got = {s: 0.0 for s in SPECIES}

    before = [d for d in deliveries if d["when"] <= lot.earliest]
    for d in reversed(before):          # newest first
        outstanding = [s for s in SPECIES if got[s] < need[s] - 1e-9]
        if not outstanding:
            break
        # A load is taken if it carries ANY species still outstanding, with
        # no minimum share.
        #
        # This lets a load that is 95% cedar into a lot that used no cedar,
        # because it also carried 2% fir. That is deliberate. The 2% is real
        # fibre and it plausibly came off the same cut block as the cedar did
        # - a stand is rarely one species, and a load reflects what was
        # standing there. Excluding it would decline to declare ground the
        # wood may genuinely have come from.
        #
        # A minimum share was considered and rejected. Any threshold would be
        # arbitrary, and the direction of its error is under-declaration.
        #
        # The load is taken whole. Part of a truckload cannot be assigned to
        # one lot and part to another, and splitting it would imply a
        # precision the record does not have.
        if d["species"]:
            contributes = any(d["species"].get(s, 0) > 0 for s in outstanding)
        else:
            contributes = True          # unsplit: counts against everything
        if not contributes:
            continue
        for s in SPECIES:
            share = d["species"].get(s, 0.0) if d["species"] else 0.0
            got[s] += d["bdt"] * share
        w.deliveries.append(d)
        w.suppliers[d["supplier"]] = w.suppliers.get(d["supplier"], 0.0) + d["bdt"]
        w.months.add("{:04d}-{:02d}".format(d["when"].year, d["when"].month))
        w.reached = d["when"]

    w.covered_bdt = got
    w.short = {s: need[s] - got[s] for s in SPECIES
               if got[s] < need[s] - 1e-6 and need[s] > 0}
    return w


def describe(w: Walk, f: dict, log=print) -> None:
    lot = w.lot
    log("")
    log("{}  {}  {}".format(lot.lot_id, lot.spec_name,
                            ("to " + lot.customer) if lot.customer else ""))
    log("  produced {} to {}  ({:.1f} h)".format(
        lot.earliest, lot.latest, lot.span_days * 24))
    log("  {:,.0f} Adt of pulp".format(lot.adt))

    req = chips_required(lot, f)
    log("")
    log("  {:<10}{:>9}{:>14}{:>14}{:>14}".format(
        "", "share", "chips BDT", "at {:.0f}%".format(
            f["walkback_multiple"] * 100), "covered"))
    for s in SPECIES:
        log("  {:<10}{:>8.1f}%{:>14,.0f}{:>14,.0f}{:>14,.0f}".format(
            s, lot.species.get(s, 0) * 100, req[s],
            w.required_bdt.get(s, 0), w.covered_bdt.get(s, 0)))

    ratio = (sum(req.values()) / lot.adt) if lot.adt else 0
    log("")
    log("  {:,.0f} BDT of chips for {:,.0f} Adt of pulp - {:.2f}:1 by mass"
        .format(sum(req.values()), lot.adt, ratio))
    if not 1.5 <= ratio <= 3.0:
        # Roughly half the wood leaves as black liquor, so about two tonnes of
        # dry chips per tonne of pulp. Well outside that and a factor is wrong
        # or inverted, which is worth saying loudly rather than burying.
        log("  That is outside the 1.5 to 3 range a kraft mill should show. "
            "Check the conversion factors before trusting this.")

    log("")
    if w.satisfied:
        log("  {:,} load(s) from {} supplier(s), reaching back {:.1f} days "
            "to {}".format(len(w.deliveries), len(w.suppliers),
                           w.days_back, w.reached.date() if w.reached else "?"))
    else:
        log("  {:,} load(s) from {} supplier(s) - NOT ENOUGH".format(
            len(w.deliveries), len(w.suppliers)))
        for s, v in w.short.items():
            log("    {} short by {:,.0f} BDT".format(s, v))
        log("  The delivery record does not reach far enough back. Load "
            "earlier months before declaring this lot.")

    if w.months:
        log("  months touched: {}".format(", ".join(sorted(w.months))))

    top = sorted(w.suppliers.items(), key=lambda kv: -kv[1])[:8]
    if top:
        log("")
        log("  largest contributors:")
        for code, bdt in top:
            name = next((d["supplier_name"] for d in w.deliveries
                         if d["supplier"] == code and d["supplier_name"]), "")
            log("    {:<12}{:>10,.0f} BDT   {}".format(code, bdt, name[:40]))
