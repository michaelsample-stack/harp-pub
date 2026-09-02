"""HARP command line.

Every step runs standalone. A Cloud Function is one of these commands with a
scheduler attached - which is why there is no separate cloud codepath.

    harp resolve data/inbox/SOURCE.xlsx --config harmac-dev
    harp ften clients --config harmac-dev
    harp ften pull --config harmac-dev --client 00158809 --since 2025-07-01
    harp ften regions --config harmac-dev
    harp register summary --config harmac-dev
    harp runs --config harmac-dev

--dry-run works on anything that writes. Use it.
"""

from __future__ import annotations

import argparse
import sys
import json
import os
from datetime import date, datetime, timedelta

from . import (adapters, assemble, cache, config, detect, detection_api,
               areas as areas_stage,
               eudr_schema,
               library as library_stage,
               lots as lots_stage,
               drop, identify, io,
               run as run_stage,
               manifest, normalise, package, router, validate as validate_stage)
from .sources import dmp as dmp_source
from .sources import private_marks
from .resolution import Tier
from .sources import ften, hbs


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ──────────────────────────────── commands ─────────────────────────────────

def cmd_ften_regions(cfg, args) -> int:
    rmap = ften.region_map()
    for region in sorted(rmap):
        print(f"{region}  ({len(rmap[region])} districts)")
        if args.verbose:
            for d in rmap[region]:
                print(f"    {d}")
    coastal = [r for r in rmap if "coast" in r.lower()]
    if coastal:
        print(f"\nCoastal = {', '.join(coastal)}")
    return 0


def cmd_ften_clients(cfg, args) -> int:
    with manifest.run(cfg, "ften-clients", where=args.where) as run:
        _log(f"Pulling tenure holders  ({args.where})")
        rows = ften.clients(args.where, log=_log)
        run.rows_out = len(rows)

        out = f"{cfg.paths.staging}/ften_clients.csv"
        if args.dry_run:
            _log(f"[dry run] would write {len(rows)} rows to {out}")
        else:
            io.write_csv_dicts(out, rows,
                               ["CLIENT_NUMBER", "CLIENT_NAME",
                                "CLIENT_LOCATION_CODE", "BLOCK_COUNT"])
            run.output(out)
            _log(f"{len(rows)} client-locations -> {out}")

        blocks = sum(r.get("BLOCK_COUNT") or 0 for r in rows)
        run.note(f"{blocks} cutblocks accounted for")
        _log(f"{blocks} cutblocks accounted for")
    return 0


def cmd_ften_pull(cfg, args) -> int:
    rule = ften.CompletionRule(
        start_after=args.since,
        require_end_date=not args.allow_open,
        require_start=not args.allow_missing_start,
    )
    where = ften.build_where(
        client_numbers=args.client or None,
        timber_marks=args.mark or None,
        districts=args.district or None,
        rule=rule,
    )

    with manifest.run(cfg, "ften-pull",
                      clients=args.client, marks=args.mark,
                      since=args.since, rule=rule.describe()) as run:
        _log(f"WHERE: {where}")
        n = ften.count(where)
        _log(f"{n} blocks match")
        run.rows_in = n

        if args.count_only:
            print(n)
            return 0
        if n == 0:
            run.note("nothing matched")
            return 0
        if args.dry_run:
            _log(f"[dry run] would pull {n} blocks")
            return 0

        feats = ften.features(where, log=_log)
        run.rows_out = len(feats)
        if len(feats) != n:
            run.note(f"expected {n}, got {len(feats)}")
            _log(f"! expected {n} but got {len(feats)}")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw = f"{cfg.paths.staging}/ften_blocks_{stamp}.geojson"
        io.write_json(raw, ften.collection(feats, where,
                                           {"completion_rule": rule.describe()}))
        run.output(raw)
        _log(f"{len(feats)} features -> {raw}")

        # normalise
        rows, dropped = [], 0
        for f in feats:
            row = normalise.from_ften(f, source=args.source or "unknown")
            problems = normalise.check(row)
            if problems:
                run.reject(f.get("properties", {}).get("CUT_BLOCK_SKEY"),
                           "; ".join(problems))
                dropped += 1
            else:
                rows.append(row)

        out = f"{cfg.paths.outbox}/sce_base_{stamp}.jsonl"
        io.write_jsonl(out, rows)
        run.output(out)
        _log(f"{len(rows)} sce_base rows -> {out}"
             + (f"  ({dropped} rejected)" if dropped else ""))

        marks = {f.get("properties", {}).get("TIMBER_MARK") for f in feats}
        marks.discard(None)
        run.note(f"{len(marks)} distinct timber marks")
        _log(f"{len(marks)} distinct timber marks across {len(feats)} blocks")
    return 0


def cmd_package(cfg, args) -> int:
    """Say what is in a monthly drop, without acting on any of it.

    Recognition is by columns, never by filename - the filenames in this data
    have already been shown wrong three separate ways.
    """
    sorted_items = package.sort_package(args.folder)
    if not sorted_items:
        _log(f"nothing readable in {args.folder}")
        return 1
    print(package.describe(sorted_items))
    unknown = sorted_items.get("unknown") or []
    if unknown:
        print(f"\n{len(unknown)} file(s) matched no signature. That is a "
              f"finding, not an error - a new kind of file has arrived and "
              f"needs a signature adding.")
    for kind, items in sorted_items.items():
        if kind == "unknown":
            continue
        note = next((n for k, _c, n in package.SIGNATURES if k == kind), "")
        print(f"\n  {kind}: {note}")
    return 0


def _latest(pattern: str) -> str:
    import glob as _glob
    hits = sorted(_glob.glob(pattern))
    return hits[-1] if hits else ""


def cmd_union(cfg, args) -> int:
    """One polygon to submit for detection.

    Built from the tenure blocks and the search areas together, so the
    submitted area covers both by construction.
    """
    src = []
    for pattern, label in ((args.tenure or
                            f"{cfg.paths.outbox}/tenure-blocks-*.geojson",
                            "tenure blocks"),
                           (args.search or
                            f"{cfg.paths.outbox}/search-areas-*.geojson",
                            "search areas")):
        path = _latest(pattern)
        if not path:
            _log(f"nothing matching {pattern}")
            continue
        with open(path, encoding="utf-8") as fh:
            feats = (json.load(fh).get("features") or [])
        _log(f"{len(feats):,} {label} from {os.path.basename(path)}")
        src.extend(feats)
    if not src:
        _log("nothing to union. Run `harp run` first.")
        return 1

    try:
        feat = detect.union(src, log=_log)
    except RuntimeError as exc:
        _log(str(exc))
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = io.write_json(
        f"{cfg.paths.outbox}/search-union-{stamp}.geojson",
        {"type": "FeatureCollection", "name": "harp_search_union",
         "metadata": {"generated": datetime.now().isoformat(timespec="seconds"),
                      "note": feat["properties"]["harp_note"]},
         "features": [feat]})
    _log("\nThis is what goes to detection. It is a submission artefact - "
         "never declare it.")
    _log("The per-supplier files it was built from are what make the return "
         "attributable.")
    _log(f"\n  {path}")
    return 0


def cmd_detect(cfg, args) -> int:
    """The whole round trip: union, submit, wait, read, join back.

    The service takes crude bounding areas rather than a constellation of
    small polygons, so everything is unioned before submission. Attribution is
    recovered afterwards by spatial join - which is the reason the
    per-supplier files are kept untouched.
    """
    start, end = args.since, args.until
    if args.month:
        try:
            y, m = (int(x) for x in args.month.split("-")[:2])
            start = start or date(y, m, 1).isoformat()
            end = end or (date(y + (m == 12), (m % 12) + 1, 1)
                          - timedelta(days=1)).isoformat()
        except (ValueError, TypeError):
            _log("--month wants YYYY-MM")
            return 1
    if not (start and end):
        _log("give --month, or --since and --until")
        return 1

    def load(pattern, default):
        path = _latest(pattern or default)
        if not path:
            return [], ""
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh).get("features") or []), path

    tenure, t_path = load(args.tenure,
                          f"{cfg.paths.outbox}/tenure-blocks-*.geojson")
    search, s_path = load(args.search,
                          f"{cfg.paths.outbox}/search-areas-*.geojson")
    if not (tenure or search):
        _log("nothing to submit. Run `harp run` first.")
        return 1
    _log(f"{len(tenure):>7,} tenure block(s)   "
         f"{os.path.basename(t_path) if t_path else '(none)'}")
    _log(f"{len(search):>7,} search area(s)    "
         f"{os.path.basename(s_path) if s_path else '(none)'}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _log("")
    try:
        feat = detect.union(tenure + search, log=_log)
    except RuntimeError as exc:
        _log(str(exc))
        return 1

    # The submitted geometry is kept. If a result ever looks wrong the first
    # question is what was actually sent, and rebuilding it later from a
    # changed catchment layer would not answer that.
    union_path = io.write_json(
        f"{cfg.paths.outbox}/search-union-{stamp}.geojson",
        {"type": "FeatureCollection", "name": "harp_search_union",
         "metadata": {"generated": datetime.now().isoformat(timespec="seconds"),
                      "window": [start, end],
                      "note": feat["properties"]["harp_note"]},
         "features": [feat]})
    _log(f"  {union_path}")

    _log("")
    try:
        feats, raw, summary = detection_api.run(
            union_path, start, end, cfg.paths.outbox,
            base=args.api or detection_api.DEFAULT_BASE,
            token=args.token or "", log=_log)
    except detection_api.DetectionError as exc:
        _log("")
        _log(str(exc))
        return 1

    detections_path = io.write_json(
        f"{cfg.paths.outbox}/detections-{stamp}.geojson",
        {"type": "FeatureCollection", "name": "harp_detections",
         "metadata": {"job": summary["job"], "window": [start, end],
                      "raw": os.path.basename(summary["raw"]),
                      "features": len(feats)},
         "features": feats})
    _log(f"\n  {detections_path}")

    if args.no_enrich:
        _log("\nStopping before the join back, as asked.")
        _log(f"  harp enrich {detections_path} --since {start} --until {end}")
        return 0

    _log("")
    args.detections = detections_path
    return cmd_enrich(cfg, args)


def cmd_enrich(cfg, args) -> int:
    """What comes back, joined to what went out."""
    try:
        detections = detect.read_detections(args.detections, log=_log)
    except Exception as exc:
        _log(f"could not read {args.detections}: {exc}")
        return 1
    if not detections:
        _log("no detections in that file")
        return 1

    start = end = None
    if args.month:
        try:
            y, m = (int(x) for x in args.month.split("-")[:2])
            start = date(y, m, 1)
            end = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        except (ValueError, TypeError):
            _log("--month wants YYYY-MM")
            return 1
    if args.since:
        start = date.fromisoformat(args.since)
    if args.until:
        end = date.fromisoformat(args.until)
    if start or end:
        _log(f"window: {start or 'any'} to {end or 'any'}")

    def load(pattern, default):
        path = _latest(pattern or default)
        if not path:
            return [], ""
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh).get("features") or []), path

    tenure, t_path = load(args.tenure,
                          f"{cfg.paths.outbox}/tenure-blocks-*.geojson")
    search, s_path = load(args.search,
                          f"{cfg.paths.outbox}/search-areas-*.geojson")
    harvest, h_path = load(args.harvest,
                           f"{cfg.paths.outbox}/harvest-areas-*.geojson")
    for label, n, path in (("harvest areas", len(harvest), h_path),
                           ("tenure blocks", len(tenure), t_path),
                           ("search areas", len(search), s_path)):
        _log(f"{n:>7,} {label}" + (f"  {os.path.basename(path)}" if path else
                                   "  (none found)"))
    _log("")

    b_prime, c_prime, report = detect.enrich(tenure, search, detections,
                                             start, end, log=_log)
    merged = detect.merge(harvest, b_prime, c_prime)

    stamp = args.month or datetime.now().strftime("%Y%m%d-%H%M%S")
    note = ("One month of harvest areas. harp_evidence says what each rests "
            "on: an identifier, a government record confirmed by detection, "
            "or a detection alone. They are not equally strong.")
    path = io.write_json(
        f"{cfg.paths.outbox}/harvest-{stamp}.geojson",
        {"type": "FeatureCollection", "name": "harp_harvest",
         "metadata": {"generated": datetime.now().isoformat(timespec="seconds"),
                      "window": [str(start or ""), str(end or "")],
                      "features": len(merged), "note": note},
         "features": merged})

    _log("")
    for line in detect.summary(report).splitlines():
        _log("  " + line)
    from collections import Counter as _C
    kinds = _C(f["properties"].get("harp_evidence", "?") for f in merged)
    _log(f"\n{len(merged):,} feature(s) in the month:")
    for k, n in kinds.most_common():
        _log(f"  {n:>7,}  {k}")
    _log(f"\n  {path}")
    return 0


def cmd_deliver(cfg, args) -> int:
    """The four EUDR fields, and nothing else. For sending out.

    Everything the pipeline knows stays in the library month. This is the
    view of it a customer or a regulator sees.
    """
    src = args.source or _latest(
        f"{cfg.paths.outbox}/harvest-{args.month}.geojson"
        if args.month else f"{cfg.paths.outbox}/harvest-*.geojson")
    if args.month and not src:
        # Prefer the approved copy - a deliverable should come from a month
        # somebody signed off, not from whatever is in the working folder.
        opts = library_stage.settings(cfg)
        shelved = os.path.join(opts["path"], args.month, "harvest.geojson")
        if os.path.isfile(shelved):
            src = shelved
    if not src or not os.path.isfile(src):
        _log("nothing to deliver. Run the month first.")
        return 1

    _log(f"from {src}")
    with open(src, encoding="utf-8") as fh:
        feats = json.load(fh).get("features") or []
    view, report = eudr_schema.project(feats, log=_log)

    stamp = args.month or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = io.write_json(
        f"{cfg.paths.outbox}/eudr-{stamp}.geojson",
        {"type": "FeatureCollection", "name": "harp_eudr",
         "features": view["features"]})
    _log(f"\n  {path}")
    if report.get("missing"):
        _log("")
        _log("  Some features carry fewer than four fields. A field is "
             "omitted rather than sent blank, because a blank one fails "
             "validation where a missing one only warns.")
    return 0


def cmd_areas(cfg, args) -> int:
    """Operating areas stated by hand, for suppliers nothing else can place."""
    path = args.table or areas_stage.path_for(cfg)
    table = areas_stage.load(path)

    if args.set:
        who = args.who or os.environ.get("USERNAME") or \
            os.environ.get("USER") or ""
        if not who:
            _log("who is stating this? Pass --who. An area with no author is "
                 "a guess.")
            return 1
        try:
            e = areas_stage.set_area(
                path, args.set, who,
                districts=(args.districts or "").split(",") if args.districts
                else None,
                counties=(args.counties or "").split(",") if args.counties
                else None,
                state=args.state or "", basis=args.basis, note=args.note or "")
        except RuntimeError as exc:
            _log(str(exc))
            return 1
        _log(f"{args.set}: {', '.join(e['districts'] + e['counties'])}")
        _log(f"  stated by {who}, {areas_stage.BASIS[args.basis]}")
        _log(f"\n  {path}")
        return 0

    if args.load:
        who = args.who or os.environ.get("USERNAME") or "?"
        areas_stage.load_worksheet(args.load, path, who, log=_log)
        return 0

    if args.missing:
        # Everything the last run could not place. Written as a worksheet so
        # somebody who knows the region can fill it in.
        src = _latest(f"{cfg.paths.outbox}/search-areas-*.geojson")
        placed = set()
        if src:
            with open(src, encoding="utf-8") as fh:
                for ft in json.load(fh).get("features") or []:
                    p = ft.get("properties") or {}
                    placed.add(p.get("harp_supplier") or "")
        reg = args.register
        if not reg:
            _log("--missing wants --register, to know who the suppliers are")
            return 1
        try:
            pairs = mills.suppliers_with_jurisdiction(reg, None)
        except RuntimeError as exc:
            _log(str(exc))
            return 1
        gaps = [{"supplier": n, "jurisdiction": j,
                 "why": "no tenure, no register match, no mill town"}
                for n, j in pairs
                if n not in placed and n not in table]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = f"{cfg.paths.outbox}/supplier-areas-worksheet-{stamp}.csv"
        areas_stage.worksheet(gaps, out, log=_log)
        _log(f"\n  {out}")
        return 0

    _log(f"stated areas at {path}")
    _log("")
    if not table:
        _log("  nothing stated yet")
        _log("")
        _log("  harp areas --missing --register <register.xlsx>")
        _log("    writes a worksheet of the suppliers nothing could place")
        return 0
    _log("  {:<34}{:<24}{:<12}{}".format("supplier", "areas", "stated by",
                                         "basis"))
    for name in sorted(table):
        e = table[name]
        _log("  {:<34}{:<24}{:<12}{}".format(
            name[:34], ", ".join(e["districts"] + e["counties"])[:24],
            e["stated_by"][:12], e["basis"]))
    _log("")
    for line in areas_stage.summary(table).splitlines():
        _log("  " + line)
    return 0


def cmd_library(cfg, args) -> int:
    """The monthly shelf: what is on it, what is waiting, what needs hands."""
    opts = library_stage.settings(cfg)
    root = args.library or opts["path"]

    if args.action in (None, "list"):
        rows = library_stage.months(root, opts.get("quarantine", ""))
        _log(f"library at {root}")
        if not rows:
            _log("  nothing on the shelf yet")
            return 0
        _log("")
        _log("  {:<10}{:<12}{:>10}  {}".format("month", "state", "features",
                                               "approved by"))
        for r in rows:
            _log("  {:<10}{:<12}{:>10}  {}".format(
                r["month"], r["state"],
                "{:,}".format(r["features"]) if r["features"] else "-",
                r["approved_by"] or ("{} finding(s)".format(r["findings"])
                                     if r["findings"] else "")))
        pend = [r for r in rows if r["state"] == "pending"]
        quar = [r for r in rows if r["state"] == "quarantine"]
        if pend:
            _log(f"\n  {len(pend)} waiting on approval. Nothing is declared "
                 f"from pending.")
        if quar:
            _log(f"  {len(quar)} in quarantine with Required findings "
                 f"outstanding.")
        return 0

    if args.action == "build":
        if not args.month:
            _log("--month wants YYYY-MM")
            return 1
        src = _latest(args.source or
                      f"{cfg.paths.outbox}/harvest-{args.month}.geojson")
        if not src:
            src = _latest(args.source or f"{cfg.paths.outbox}/harvest-*.geojson")
        if not src:
            _log("no harvest collection to build from. Run `harp detect` "
                 "first.")
            return 1
        with open(src, encoding="utf-8") as fh:
            feats = json.load(fh).get("features") or []
        _log(f"from {os.path.basename(src)}")
        deliveries = args.deliveries or ""
        if not deliveries:
            _log("no --deliveries given. The walkback needs them, so the "
                 "month will be incomplete without.")
        try:
            library_stage.build(root, args.month, feats, deliveries, opts,
                                source_files=[os.path.basename(src)],
                                log=_log)
        except RuntimeError as exc:
            _log(str(exc))
            return 1
        return 0

    if args.action == "promote":
        if not args.month:
            _log("--month wants YYYY-MM")
            return 1
        who = args.who or os.environ.get("USERNAME") or \
            os.environ.get("USER") or "unknown"
        if opts["require_approval"] and not args.who and who == "unknown":
            _log("who is approving this? Pass --who.")
            return 1
        try:
            library_stage.promote(root, args.month, who, force=args.force,
                                  quarantine=opts.get("quarantine", ""),
                                  log=_log)
        except RuntimeError as exc:
            _log(str(exc))
            return 1
        return 0

    _log(f"unknown action '{args.action}'")
    return 1


def cmd_lot(cfg, args) -> int:
    """From a production lot back to the deliveries that fed it."""
    lot_path, deliveries = args.lots, args.deliveries

    if os.path.isdir(lot_path):
        # A folder is the friendlier thing to hand it, and the drop already
        # knows which file is which.
        sorted_items = package.sort_package(lot_path)
        found = run_stage._first(sorted_items, "lot_list")
        if not found:
            _log(f"no lot list in {lot_path}")
            _log("  a lot list is recognised by a 'Lot ID' column")
            return 1
        lot_path = found
        deliveries = deliveries or run_stage._first(sorted_items,
                                                    "delivery_record")
        _log(f"lot list     {os.path.basename(lot_path)}")
        _log(f"deliveries   {os.path.basename(deliveries) if deliveries else '(none found)'}")
        _log("")
    if not deliveries:
        _log("--deliveries wants the load summary, or give the drop folder "
             "and it will be found")
        return 1
    args.lots, args.deliveries = lot_path, deliveries

    f = lots_stage.factors(cfg)
    _log("conversion factors:")
    for sp, v in f["chip_m3_per_adt"].items():
        _log(f"  {sp:<8}{v:>6.2f} m3 per air-dry tonne of pulp")
    _log(f"  {f['chip_m3_per_bdu']:.3f} m3 per bone-dry unit, "
         f"{f['tonnes_per_bdu']:.4f} t per BDU")
    _log(f"  walkback covers {f['walkback_multiple'] * 100:.0f}% of the lot")
    _log("")
    _log("  The client's table records the second of those as BDU per m3. "
         "Taken that way it makes chips denser than solid wood, so it is used "
         "inverted. Change chip_m3_per_bdu in config if that is wrong.")
    _log("")

    try:
        all_lots = lots_stage.read_lots(args.lots, log=_log)
    except Exception as exc:
        _log(str(exc))
        return 1
    if args.lot:
        wanted = {x.strip().upper() for x in args.lot.split(",")}
        all_lots = [l for l in all_lots if l.lot_id.upper() in wanted]
        if not all_lots:
            _log(f"no lot matching {args.lot}")
            return 1

    _log("")
    try:
        deliveries = lots_stage.read_deliveries(args.deliveries, log=_log)
    except Exception as exc:
        _log(str(exc))
        return 1
    if not deliveries:
        _log("no usable deliveries")
        return 1

    walks, rows = [], []
    for lot in all_lots:
        w = lots_stage.walk(lot, deliveries, f)
        walks.append(w)
        if args.lot or len(all_lots) <= 6:
            lots_stage.describe(w, f, log=_log)
        rows.append({
            "lot_id": lot.lot_id, "spec": lot.spec_name,
            "customer": lot.customer,
            "produced_from": lot.earliest, "produced_to": lot.latest,
            "pulp_adt": round(lot.adt, 1),
            **{f"share_{s}": round(lot.species.get(s, 0) * 100, 2)
               for s in lots_stage.SPECIES},
            **{f"required_bdt_{s}": round(w.required_bdt.get(s, 0), 1)
               for s in lots_stage.SPECIES},
            **{f"covered_bdt_{s}": round(w.covered_bdt.get(s, 0), 1)
               for s in lots_stage.SPECIES},
            "loads": len(w.deliveries), "suppliers": len(w.suppliers),
            "reached_back_to": w.reached, "days_back": round(w.days_back, 1),
            "months_touched": " ".join(sorted(w.months)),
            "satisfied": w.satisfied,
            "shortfall_bdt": round(sum(w.short.values()), 1) if w.short else 0,
        })

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = io.write_csv_dicts(f"{cfg.paths.outbox}/lot-walkback-{stamp}.csv",
                              rows)

    short = [w for w in walks if not w.satisfied]
    spanning = [w for w in walks if len(w.months) > 1]
    _log("")
    _log("=" * 66)
    _log(f"{len(walks)} lot(s)")
    if spanning:
        _log(f"  {len(spanning)} reach back into an earlier month - those "
             f"need the earlier month's geometry too")
    if short:
        _log(f"  {len(short)} could not be covered by this delivery record:")
        for w in short[:8]:
            _log(f"    {w.lot.lot_id:<12}short {sum(w.short.values()):>10,.0f} BDT")
        _log("  Nothing has been declared for those. Load earlier months.")
    else:
        _log("  all covered by the delivery record given")
    _log(f"\n  {path}")

    if args.dry_run:
        _log("\nStopped before pulling geometry, as asked.")
        return 0

    # ---- geometry ----------------------------------------------------
    opts = library_stage.settings(cfg)
    root = args.library or opts["path"]
    wanted = sorted({m for w in walks for m in w.months})
    if not wanted:
        _log("\nno months to pull geometry from")
        return 1

    _log("")
    _log(f"pulling geometry for {len(wanted)} month(s): {', '.join(wanted)}")
    shelf, missing = {}, []
    for m in wanted:
        try:
            shelf[m] = library_stage.read_month(root, m,
                                               opts.get("quarantine", ""),
                                               log=_log)
        except FileNotFoundError as exc:
            missing.append(str(exc))
    if missing:
        _log("")
        for msg in missing:
            _log(f"  {msg}")
        _log("")
        _log("A lot cannot be declared from months that are not on the shelf.")
        _log("  harp library                      to see what is there")
        _log("  harp library build --month YYYY-MM   to prepare one")
        return 1

    written = []
    for w in walks:
        if not w.satisfied:
            # Nothing is written for a lot the delivery record could not
            # cover. A partial answer that looks complete is worse than none.
            continue
        feats = []
        for m in sorted(w.months):
            for f in shelf.get(m, []):
                sup = (f.get("properties") or {}).get("harp_supplier_code") \
                    or (f.get("properties") or {}).get("harp_supplier")
                if sup in w.suppliers:
                    g = {"type": "Feature", "geometry": f.get("geometry"),
                         "properties": {**(f.get("properties") or {}),
                                        "harp_lot": w.lot.lot_id,
                                        "harp_lot_month": m,
                                        "harp_supplier_bdt": round(
                                            w.suppliers.get(sup, 0), 1)}}
                    feats.append(g)
        if not feats:
            _log(f"  {w.lot.lot_id}: no geometry for any of its suppliers")
            continue
        path = io.write_json(
            f"{cfg.paths.outbox}/lot-{w.lot.lot_id}.geojson",
            {"type": "FeatureCollection", "name": f"harp_lot_{w.lot.lot_id}",
             "metadata": {
                 "lot": w.lot.lot_id, "customer": w.lot.customer,
                 "produced": [str(w.lot.earliest), str(w.lot.latest)],
                 "pulp_adt": round(w.lot.adt, 1),
                 "walked_back_to": str(w.reached),
                 "months": sorted(w.months),
                 "suppliers": len(w.suppliers),
                 "loads": len(w.deliveries),
                 "features": len(feats),
                 "note": ("every harvest area for every supplier who "
                          "delivered into this lot's walkback window. The "
                          "chips were mixed before use, so which of these "
                          "fed this lot cannot be narrowed further.")},
             "features": feats})
        written.append(path)
        _log(f"  {w.lot.lot_id:<12}{len(feats):>7,} feature(s)  "
             f"{len(w.suppliers)} supplier(s)")

    _log("")
    _log(f"{len(written)} lot package(s) written to {cfg.paths.outbox}")
    return 0


def cmd_forget_parcels(cfg, args) -> int:
    """Make every PID askable again.

    A PID lands on the not-found list when ParcelMap has nothing for it, and
    stays there so it is not re-requested every month. That is right until
    something changes - the fabric is republished, or a PID-reading bug is
    fixed - at which point an old absence is no longer true.
    """
    from .sources.private_marks import Registry
    reg = Registry(cache_dir=f"{cfg.paths.staging}/cache/bcparcel", log=_log)
    n = reg.forget_missing()
    _log(f"{n} PID(s) cleared. They will be requested again on the next run.")
    return 0


def cmd_mills(cfg, args) -> int:
    """Mill location and district per supplier - the input to search areas."""
    from . import mills
    try:
        pairs = mills.suppliers_with_jurisdiction(args.register, args.klass)
    except RuntimeError as exc:
        _log(str(exc))
        return 1
    _log("{} facilities, {} districts loaded".format(
        len(mills.facilities()), len(mills.districts())))
    _log("")
    _log("The facility list and the district layer are both British "
         "Columbia. A supplier the register places elsewhere is skipped "
         "here and picked up by the US routes instead.")
    _log("")
    rows, skipped = [], 0
    for name, jur in pairs:
        if not mills.is_bc(jur):
            # Weyerhaeuser and Interfor both hold BC facilities, so the name
            # matches and a BC district comes back - for an operation Harmac
            # does not buy from. Skipping is the whole fix.
            skipped += 1
            rows.append({"supplier": name, "facility": "", "city": "",
                         "latitude": "", "longitude": "", "district": "",
                         "district_code": "",
                         "how_established": "not British Columbia ({}) - "
                                            "placed by the US routes"
                                            .format(jur or "?"),
                         "confirmed_by_harmac": ""})
            _log("  {:<32}{:<28}{}".format(name[:32], "-", "not BC"))
            continue
        fac, how = mills.match_facility(name)
        dist = mills.district_at(fac["lat"], fac["lon"]) if fac else None
        if not dist and not fac:
            dist = mills.district_from_name(name)
            how = "place name in the supplier's own name" if dist else how
        rows.append({
            "supplier": name,
            "facility": fac["label"] if fac else "",
            "city": fac["city"] if fac else "",
            "latitude": fac["lat"] if fac else "",
            "longitude": fac["lon"] if fac else "",
            "district": dist["name"] if dist else "",
            "district_code": dist["code"] if dist else "",
            "how_established": how or "no facility, no place name",
            "confirmed_by_harmac": ""})
        _log("  {:<32}{:<28}{}".format(
            name[:32], (fac["label"] if fac else "-")[:28],
            dist["code"] if dist else "-"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = io.write_csv_dicts(
        f"{cfg.paths.outbox}/supplier_locations-{stamp}.csv", rows)
    placed = sum(1 for r in rows if r["district_code"])
    bc = len(rows) - skipped
    _log(f"\n{placed} of {bc} British Columbia supplier(s) placed in a "
         f"district")
    if skipped:
        _log(f"  {skipped} are outside BC and go down the US routes instead")
    unplaced = bc - placed
    if unplaced:
        _log(f"\n  {unplaced} BC supplier(s) could not be placed from their "
             f"name alone.")
        _log("  Most will still resolve: the catchment builder also reads the "
             "mill town out of each source identifier, which this pass does "
             "not see. Anything still unplaced after a full run is a real "
             "gap and shows in the run summary.")
    _log("\nA mill is not a harvest area. The district narrows a search; it "
         "does not answer one.")
    _log(f"\n  {path}")
    return 0


def cmd_run(cfg, args) -> int:
    """Everything, in one step: sort the drop, resolve, split.

    The stages have always existed; driving them by hand meant four commands
    in the right order with the right paths, and the tools disagreeing about
    where things live.
    """
    out = run_stage.run(
        cfg, args.folder,
        month=args.month or "",
        detect=not args.no_detect,
        stage=not args.no_stage,
        api_base=args.api or "",
        private_marks_dir=args.private_marks or "",
        register=args.register or "",
        mills_csv=args.mills or "",
        alias_override=args.aliases or "",
        radius_km=args.radius_km,
        max_block_ha=args.max_block_ha,
        fetch_geometry=not args.no_geometry,
        unique=not args.no_unique,
        limit=args.limit or 0,
        log=_log)
    return 0 if out.get("ok") else 1


def cmd_resolve(cfg, args) -> int:
    """Hand HARP a raw supply list and resolve every identifier it contains.

    This is the entry point for a client's own export - SOURCE.xlsx, a LIMS
    dump, a CSV somebody typed. No curation required: the minimum viable input
    is an identifier column.
    """
    try:
        records = identify.load(args.input, sheet=args.sheet,
                                only_class=args.klass,
                                default_jurisdiction=args.jurisdiction)
    except RuntimeError as exc:
        _log(str(exc))
        return 1
    if not records:
        _log(f"No identifiers found in {args.input}")
        return 1

    _log("columns mapped: " + identify.describe_mapping())

    carried, gone = [], []
    if args.since:
        try:
            prev = io.read_csv_dicts(args.since) if args.since.endswith(".csv") \
                else io.read_json(args.since)
        except Exception as exc:
            _log(f"could not read {args.since}: {exc}")
            return 1
        d = drop.compare(records, prev)
        _log("against previous drop: " + d.summary())
        carried = drop.carry_forward(d, prev)
        gone = drop.gone_report(d)
        for g in gone[:10]:
            _log("  gone: {:<14} {} (was {})".format(
                g["identifier"], g["supplier_name"][:30], g["last_tier"]))
        if len(gone) > 10:
            _log(f"  ... and {len(gone) - 10} more")
        records = d.to_resolve
        if not records:
            _log("nothing new or changed - previous answers still stand")

    if args.unique:
        before = len(records)
        records = identify.dedupe(records)
        if before != len(records):
            _log(f"{before} rows -> {len(records)} distinct identifiers")

    if args.limit:
        records = records[:args.limit]

    records = [identify.identify(r) for r in records]

    by_jur: dict[str, int] = {}
    for r in records:
        by_jur[r.jurisdiction or "?"] = by_jur.get(r.jurisdiction or "?", 0) + 1
    _log(f"{len(records)} sources: "
         + ", ".join(f"{k} {v}" for k, v in sorted(by_jur.items())))

    libs = adapters.available()
    missing = [k for k, ok in libs.items() if not ok]
    if missing:
        _log("libraries not installed (not needed for BC): " + ", ".join(missing))

    if args.dry_run:
        for r in records[:20]:
            _log(f"  would resolve {r.identifier:<14} {r.jurisdiction:<3} "
                 f"class={r.klass.value if r.klass else '?'}")
        if len(records) > 20:
            _log(f"  ... and {len(records) - 20} more")
        return 0

    rule = None
    if not args.no_window:
        cr = (cfg.sources.get("ften") or {}).get("completion_rule") or {}
        if cr:
            rule = ften.CompletionRule(**cr)
            _log(f"completion rule: {rule.describe()}")

    store = cache.Cache(args.cache, enabled=not args.no_cache)
    hbs_client = hbs.Client(cache=store)

    # The private mark registry. Shared across clients, not client data: the
    # extracts are a BC-wide scaling return of which a client uses a few dozen
    # marks. Built once, consulted per source, parcels cached per PID.
    registry = None
    marks_dir = args.private_marks or (cfg.sources.get("private_marks") or {}
                                       ).get("extracts_dir")
    if marks_dir:
        registry = private_marks.Registry(
            marks_dir, cache_dir=f"{args.cache}/bcparcel", log=_log)
        try:
            n = registry.build()
            _log(f"private mark registry: {n} marks indexed")
        except private_marks.NotInstalled as exc:
            _log(str(exc))
            registry = None

    run = manifest.Run(cfg, "resolve", {
        "input": args.input, "sources": len(records),
        "geometry": not args.no_geometry, "class_filter": args.klass,
    })
    run.rows_in = len(records)

    results = []
    for i, rec in enumerate(records, 1):
        res = router.resolve(rec, hbs_client=hbs_client,
                             fetch_geometry=not args.no_geometry,
                             rule=rule, log=None,
                             catchment=args.catchment, registry=registry)
        results.append(res)
        note = res.tenure_holder or res.unresolved_reason
        _log(f"[{i:>4}/{len(records)}] {res.identifier:<14} {res.tier.value:<3} "
             f"{(res.klass.value if res.klass else '?'):<4} "
             f"{note[:44]}"
             + (f"  {len(res.features)} blocks" if res.features else ""))
        if not res.resolved:
            run.reject({"source_id": res.source_id,
                        "identifier": res.identifier},
                       res.unresolved_reason or "unresolved",
                       supplier=res.supplier_name, verdict=res.verdict,
                       tenure_holder=res.tenure_holder,
                       district=res.district_name or res.district_code)

    run.rows_out = sum(1 for r in results if r.resolved)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    rows = [r.row() for r in results] + carried
    if carried:
        run.note(f"{len(carried)} answers carried forward unchanged")
    if gone:
        run.output(io.write_csv_dicts(
            f"{cfg.paths.rejects}/gone-{stamp}.csv", gone))
        run.note(f"{len(gone)} sources present last drop, absent this one")
    run.output(io.write_csv_dicts(
        f"{cfg.paths.outbox}/resolution-{stamp}.csv", rows))
    run.output(io.write_json(
        f"{cfg.paths.outbox}/resolution-{stamp}.json",
        [r.full() for r in results], indent=1))

    # ---- the client's own declaration ----
    #
    # After the per-source loop, because a passport has no identifier to key
    # on and so cannot be a rung on the ladder. Cutblocks join the master
    # collection; regional areas go to the pool that change detection runs
    # inside.
    # Off unless asked for. The passports are a prior effort by an unknown
    # third party, and how they produced their geometry is not recorded
    # anywhere in the files. Ingesting that by default would put polygons of
    # unestablished provenance into a declaration alongside our own.
    declared = {"cutblock": [], "catchment": []}
    passports = dmp_source.find(args.dmp) if args.dmp else []
    if passports:
        _log(f"\n{len(passports)} Digital Material Passport(s) found")
        declared = dmp_source.ingest(
            passports, f"{args.cache}/dmp", log=_log)

    # ---- assemble ----
    collection, report = assemble.assemble(results)

    if declared["cutblock"]:
        kept, dropped = dmp_source.drop_duplicates(
            declared["cutblock"], collection["features"], log=_log)
        collection["features"].extend(kept)
        run.note(f"{len(kept)} declared cutblocks added, {dropped} dropped as "
                 f"already resolved")
        _log(f"  {len(kept)} declared cutblock(s) added to the collection")

    if declared["catchment"]:
        run.output(io.write_json(
            f"{cfg.paths.outbox}/catchments-{stamp}.geojson",
            {"type": "FeatureCollection", "name": "harp_search_areas",
             "features": declared["catchment"]}))
        _log(f"  {len(declared['catchment'])} regional area(s) written for "
             f"change detection")
    for line in assemble.summary(report).splitlines():
        _log("  " + line)
    run.note(assemble.summary(report).replace("\n", "; "))

    if collection["features"]:
        # ---- validate, clean, revalidate ----
        outcome = None
        if not args.no_validate:
            libs = adapters.available()
            if not libs.get("eudr_geojson"):
                _log("eudr_geojson not installed - validation skipped")
            else:
                _log("validating…")
                outcome = validate_stage.run(
                    collection, country_iso2=args.country,
                    max_rounds=args.max_clean_rounds, log=_log)
                for line in outcome.summary().splitlines():
                    _log("  " + line)
                run.note(outcome.summary().replace("\n", "; "))
                collection = outcome.collection

                if outcome.review.get("features"):
                    run.output(io.write_json(
                        f"{cfg.paths.rejects}/review-{stamp}.geojson",
                        assemble.stamp(outcome.review,
                                       {"reason": "failed validation"})))
                    rows = validate_stage.review_rows(outcome)
                    if rows:
                        run.output(io.write_csv_dicts(
                            f"{cfg.paths.rejects}/review-findings-{stamp}.csv",
                            rows))
                if outcome.findings:
                    run.output(io.write_csv_dicts(
                        f"{cfg.paths.outbox}/findings-{stamp}.csv",
                        outcome.findings))

        run.output(io.write_json(
            f"{cfg.paths.outbox}/areas-{stamp}.geojson",
            assemble.stamp(collection, {
                "sources": len(results), "resolved": run.rows_out,
                "validation": outcome.status if outcome else "not run"})))

        # A pooled commodity can mix precisions, but a DDS and a risk
        # screening want different subsets. Splitting them is cheaper than
        # explaining later why a district polygon shared a file with a block.
        direct, indirect = assemble.split_by_traceability(collection)
        if direct["features"] and indirect["features"]:
            run.output(io.write_json(
                f"{cfg.paths.outbox}/areas-direct-{stamp}.geojson",
                assemble.stamp(direct)))
            run.output(io.write_json(
                f"{cfg.paths.outbox}/areas-indicative-{stamp}.geojson",
                assemble.stamp(indirect)))

    # ---- normalise to sce_base ----
    sce, prov = [], []
    for res in results:
        sce.extend(normalise.from_resolution(
            res, dissolve_envelopes=not args.no_dissolve))
        prov.extend(normalise.provenance_rows(res))
    if sce:
        run.output(io.write_jsonl(f"{cfg.paths.staging}/sce_base-{stamp}.jsonl",
                                  sce))
        run.note(f"{len(sce)} sce_base rows staged")
        env = sum(1 for r in sce
                  if (r.get("_harp") or {}).get("dissolved_from_blocks"))
        if env:
            _log(f"  {env} operating envelope(s) dissolved to one row each")
    if prov:
        # The record as pulled, kept for audit but out of the payload.
        run.output(io.write_jsonl(
            f"{cfg.paths.staging}/provenance-{stamp}.jsonl", prov))

    row = run.finish()

    tiers: dict[str, int] = {}
    klasses: dict[str, int] = {}
    for res in results:
        tiers[res.tier.value] = tiers.get(res.tier.value, 0) + 1
        k = res.klass.value if res.klass else "?"
        klasses[k] = klasses.get(k, 0) + 1

    print(f"\n{len(results)} sources  ·  {run.rows_out} resolved  ·  "
          f"{sum(len(r.features) for r in results)} blocks  ·  "
          f"{sum(r.area_ha for r in results):,.0f} ha\n")
    for t in Tier:
        if tiers.get(t.value):
            print(f"  {t.value:<4} {t.label:<28} {tiers[t.value]}")
    print()
    for k in sorted(klasses):
        print(f"  class {k:<4} {klasses[k]}")

    holders = {}
    for res in results:
        if res.tenure_holder:
            holders[res.tenure_holder] = holders.get(res.tenure_holder, 0) + 1
    if holders:
        print(f"\n  {len(holders)} tenure holders")
        for h in sorted(holders, key=lambda x: -holders[x])[:8]:
            print(f"    {h[:46]:<46} {holders[h]}")

    print(f"\n  run {row['run_id']}")
    for out in run.outputs:
        print(f"    {out}")
    if run.rejects:
        print(f"    {len(run.rejects)} unresolved -> {cfg.paths.rejects}")
        print("    That file is a client question list, not an error log.")
    return 0


def cmd_register_summary(cfg, args) -> int:
    path = args.path or f"{cfg.paths.inbox}/supplier_register.csv"
    if not io.exists(path):
        _log(f"No register at {path}")
        _log("Expected columns: supplier_id, name, jurisdiction, land_type, tier,")
        _log("  client_number, client_locations, geodata_format,")
        _log("  has_coordinates, has_catchment, contact, notes")
        return 1

    suppliers = router.load_register(path)
    print(f"{len(suppliers)} suppliers\n")
    for path_name, n in router.summarise(suppliers).items():
        print(f"  {path_name:<20} {n}")

    unresolved = [s for s in suppliers if router.choose(s) is router.Path.UNRESOLVED]
    if unresolved:
        print(f"\n{len(unresolved)} unresolved - no usable input:")
        for s in unresolved:
            print(f"  {s.supplier_id}  {s.name}")
    return 0


def cmd_runs(cfg, args) -> int:
    rows = manifest.history(cfg, args.limit)
    if not rows:
        print("no runs recorded")
        return 0
    for r in rows:
        flag = " " if r["status"] == "ok" else "!"
        print(f"{flag} {r['finished']}  {r['step']:<16} "
              f"in={r['rows_in']:<6} out={r['rows_out']:<6} "
              f"rej={r['rows_rejected']:<4} {r['status']}")
    return 0


# ──────────────────────────────── argparse ─────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="harp", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="harmac-dev", help="config name or path")
    p.add_argument("--dry-run", action="store_true", help="say what would happen")

    # Grouped, because fifteen commands listed alphabetically does not tell a
    # reader which five matter.
    p.epilog = """
the pipeline
  run             the whole month: a client drop in, a staged library month out
  library         the monthly shelf, and getting a month onto it
  lot             a production lot back to the ground it came from
  areas           operating areas stated by hand, where nothing else can place
                  a supplier
  mills           mill location and district per supplier

when a run stops halfway
  detect          resume at detection, without re-resolving
  enrich          resume at the join, with a detection file you already have
  union           just the polygon, without submitting it

looking at things
  summary         what a run produced
  runs            run history, and what changed between drops
  package         sort a drop and report, without resolving
  resolve         resolve a supply list on its own
  register        supplier register operations
  ften            query the tenure register directly
  forget-parcels  clear the list of PIDs known to have no parcel

A month is not declarable until it is on the shelf. `harp run --month YYYY-MM`
takes it the whole way; without a month it stops after the split, which leaves
search areas nobody can declare.
"""

    sub = p.add_subparsers(dest="group", required=True)

    ften_p = sub.add_parser("ften", help="BC forest tenure")
    ften_sub = ften_p.add_subparsers(dest="cmd", required=True)

    r = ften_sub.add_parser("regions", help="NR regions and their districts")
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(fn=cmd_ften_regions)

    c = ften_sub.add_parser("clients", help="every tenure holder with a cutblock")
    c.add_argument("--where", default="1=1")
    c.set_defaults(fn=cmd_ften_clients)

    q = ften_sub.add_parser("pull", help="pull cutblock geometry")
    q.add_argument("--client", action="append", help="client number, repeatable")
    q.add_argument("--mark", action="append", help="timber mark, repeatable")
    q.add_argument("--district", action="append", help="district name, repeatable")
    q.add_argument("--since", default=None, help="harvest start after YYYY-MM-DD")
    q.add_argument("--source", default=None, help="supplier id for sce_source")
    q.add_argument("--allow-open", action="store_true",
                   help="include blocks with no disturbance end date")
    q.add_argument("--allow-missing-start", action="store_true",
                   help="include blocks with no disturbance start date")
    q.add_argument("--count-only", action="store_true")
    q.set_defaults(fn=cmd_ften_pull)

    rn = sub.add_parser("run",
                        help="the whole month: sort, resolve, search areas, "
                             "split, detect, join back, validate, stage")
    rn.add_argument("folder", help="the monthly drop")
    rn.add_argument("--private-marks", metavar="DIR",
                    help="timber mark extracts, if not in the drop folder")
    rn.add_argument("--register", metavar="XLSX",
                    help="supplier register - says which suppliers still need "
                         "a search area. Without it, none are built")
    rn.add_argument("--mills", metavar="CSV",
                    help="mill locations, from `harp mills`")
    rn.add_argument("--aliases", metavar="CSV",
                    help="supplier alias table, if not in data/registry")
    rn.add_argument("--radius-km", type=float, default=150.0,
                    help="mill buffer radius, the last-resort search area")
    rn.add_argument("--max-block-ha", type=float, default=2000.0,
                    help="above this a polygon is treated as a broad area")
    rn.add_argument("--no-geometry", action="store_true",
                    help="resolve without fetching polygons")
    rn.add_argument("--no-unique", action="store_true",
                    help="resolve every row, not every distinct identifier")
    rn.add_argument("--month", metavar="YYYY-MM",
                    help="the detection window. Without it the run stops "
                         "after the split, which leaves nothing declarable")
    rn.add_argument("--no-detect", action="store_true",
                    help="stop after the split on purpose")
    rn.add_argument("--no-stage", action="store_true",
                    help="write the month but do not validate or stage it")
    rn.add_argument("--api", metavar="URL",
                    help="the detection service, if not the default")
    rn.add_argument("--limit", type=int, help="stop after N sources")
    rn.set_defaults(fn=cmd_run)

    un = sub.add_parser("union",
                        help="just the polygon, without submitting it")
    un.add_argument("--tenure", metavar="GLOB")
    un.add_argument("--search", metavar="GLOB")
    un.set_defaults(fn=cmd_union)

    dt = sub.add_parser("detect",
                        help="resume at detection, when a run got that far "
                             "and the service did not answer")
    dt.add_argument("--month", help="YYYY-MM")
    dt.add_argument("--since", help="YYYY-MM-DD")
    dt.add_argument("--until", help="YYYY-MM-DD")
    dt.add_argument("--tenure", metavar="GLOB")
    dt.add_argument("--search", metavar="GLOB")
    dt.add_argument("--harvest", metavar="GLOB")
    dt.add_argument("--api", metavar="URL",
                    help="the detection service, if not the default")
    dt.add_argument("--token", help="bearer token, if the service wants one")
    dt.add_argument("--no-enrich", action="store_true",
                    help="stop after downloading, before the join back")
    dt.set_defaults(fn=cmd_detect)

    en = sub.add_parser("enrich",
                        help="resume at the join, with a detection file you "
                             "already have")
    en.add_argument("detections", help="what the service returned")
    en.add_argument("--month", help="YYYY-MM, the window to keep")
    en.add_argument("--since", help="YYYY-MM-DD")
    en.add_argument("--until", help="YYYY-MM-DD")
    en.add_argument("--tenure", metavar="GLOB")
    en.add_argument("--search", metavar="GLOB")
    en.add_argument("--harvest", metavar="GLOB")
    en.set_defaults(fn=cmd_enrich)

    dl = sub.add_parser("deliver",
                        help="the four EUDR fields and nothing else, for "
                             "sending out")
    dl.add_argument("--month", help="YYYY-MM")
    dl.add_argument("--source", metavar="GEOJSON")
    dl.set_defaults(fn=cmd_deliver)

    ar = sub.add_parser("areas",
                        help="operating areas stated by hand, for suppliers "
                             "nothing else can place")
    ar.add_argument("--set", metavar="SUPPLIER")
    ar.add_argument("--districts", help="BC district codes, comma separated")
    ar.add_argument("--counties", help="US county FIPS, comma separated")
    ar.add_argument("--state", help="the state, for US counties")
    ar.add_argument("--basis", default="local",
                    choices=sorted(areas_stage.BASIS),
                    help="what the statement rests on")
    ar.add_argument("--who", help="who is stating it")
    ar.add_argument("--note")
    ar.add_argument("--missing", action="store_true",
                    help="write a worksheet of everything unplaced")
    ar.add_argument("--register", metavar="XLSX")
    ar.add_argument("--load", metavar="CSV", help="take a filled-in worksheet")
    ar.add_argument("--table", metavar="CSV", help="a different table")
    ar.set_defaults(fn=cmd_areas)

    lb = sub.add_parser("library",
                        help="the monthly shelf - what is on it, and getting "
                             "a month onto it")
    lb.add_argument("action", nargs="?", default="list",
                    choices=["list", "build", "promote"])
    lb.add_argument("--month", help="YYYY-MM")
    lb.add_argument("--source", metavar="GLOB",
                    help="the harvest collection to build from")
    lb.add_argument("--deliveries", help="the load summary for that month")
    lb.add_argument("--library", metavar="DIR",
                    help="the shelf, if not the configured one")
    lb.add_argument("--who", help="who is approving this")
    lb.add_argument("--force", action="store_true",
                    help="promote despite Required findings - recorded in "
                         "the manifest")
    lb.set_defaults(fn=cmd_library)

    lt = sub.add_parser("lot",
                        help="walk a production lot back to the deliveries "
                             "that fed it")
    lt.add_argument("lots",
                    help="a production lot list, or the drop folder holding "
                         "one")
    lt.add_argument("--deliveries",
                    help="the load delivery summary. Found in the drop if "
                         "you give a folder")
    lt.add_argument("--lot", help="one lot id, or several comma separated")
    lt.add_argument("--library", metavar="DIR",
                    help="the shelf, if not the configured one")
    lt.add_argument("--dry-run", action="store_true",
                    help="the mass walkback only, without pulling geometry")
    lt.set_defaults(fn=cmd_lot)

    fp = sub.add_parser("forget-parcels",
                        help="clear the list of PIDs known to have no parcel, "
                             "so they are tried again")
    fp.set_defaults(fn=cmd_forget_parcels)

    ml = sub.add_parser("mills",
                        help="mill location and district per supplier")
    ml.add_argument("register", help="a supplier register xlsx")
    ml.add_argument("--class", dest="klass", help="class filter")
    ml.set_defaults(fn=cmd_mills)

    r = sub.add_parser("resolve", help="resolve a raw supply list to harvest areas")
    r.add_argument("input", help="CSV or XLSX from the client")
    r.add_argument("--sheet", help="worksheet name for xlsx input")
    r.add_argument("--class", dest="klass", help="only rows with this class")
    r.add_argument("--jurisdiction", default="BC",
                   help="default when the input does not say (BC)")
    r.add_argument("--unique", action="store_true",
                   help="resolve each identifier once, not once per source")
    r.add_argument("--no-geometry", action="store_true",
                   help="attributes only - much faster for a first pass")
    r.add_argument("--no-window", action="store_true",
                   help="ignore the completion rule in the config")
    r.add_argument("--private-marks", metavar="DIR",
                   help="folder of BC scaled-timbermark extracts. Enables R5b "
                        "- private mark to parcel geometry")
    r.add_argument("--catchment", action="store_true",
                   help="build a P3 catchment for private marks - district "
                        "narrowed to private forest ownership")
    r.add_argument("--cache", default="./data/cache")
    r.add_argument("--no-cache", action="store_true")
    r.add_argument("--limit", type=int)
    r.add_argument("--dmp", metavar="DIR",
                   help="folder of Digital Material Passports. Off unless "
                        "given: their provenance is unestablished, so they are "
                        "not ingested by default")
    r.add_argument("--no-dissolve", action="store_true",
                   help="emit one sce_base row per polygon for an operating "
                        "envelope, rather than one row per source")
    r.add_argument("--no-validate", action="store_true",
                   help="skip the validate and clean stage")
    r.add_argument("--country", metavar="ISO2",
                   help="fallback ProducerCountry for validation, e.g. CA")
    r.add_argument("--max-clean-rounds", type=int, default=3,
                   help="cap on validate/clean rounds (default 3)")
    r.add_argument("--since", metavar="MANIFEST",
                   help="a previous resolution CSV or JSON - resolve only what "
                        "is new or changed, carry the rest forward, and report "
                        "anything that has gone")
    r.set_defaults(fn=cmd_resolve)

    pk = sub.add_parser("package", help="what is in a monthly drop?")
    pk.add_argument("folder")
    pk.set_defaults(fn=cmd_package)

    reg = sub.add_parser("register", help="supplier register")
    reg_sub = reg.add_subparsers(dest="cmd", required=True)
    s = reg_sub.add_parser("summary", help="how many suppliers on each path")
    s.add_argument("--path", default=None)
    s.set_defaults(fn=cmd_register_summary)

    runs = sub.add_parser("runs", help="recent runs from the manifest")
    runs.add_argument("--limit", type=int, default=20)
    runs.set_defaults(fn=cmd_runs)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.load(args.config)
    _log(f"config: {cfg.label}")
    return args.fn(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
