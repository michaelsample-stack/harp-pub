#!/usr/bin/env python3
"""DMP harvest units — a window over dmp_harvest_units.py.

Harmac Pacific's own Digital Material Passports carry a
HarvestUnitsDownloadURL. The BC one returns three features and fifteen
megabytes: each is a GeometryCollection holding hundreds or thousands of
individual harvest polygons, grouped up and labelled only by region.

This unpacks them. All the work lives in dmp_harvest_units.py — nothing is
reimplemented here, so the two cannot drift.

    python tools/dmp_gui.py

Three panes:

    File        pick the download, see what is inside each declared unit
    Compare     check our resolved blocks against theirs
    Log

WHAT IT CAN AND CANNOT ANSWER
-----------------------------
There is no timber mark, source id or supplier in the file, so it cannot
attribute a harvest to one of Harmac's supply sources. What it can do is say
how much detail sits under the declaration, and prompt the question of where
that detail came from.

Requires: shapely and pyproj for areas (optional), tkinter.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from collections import Counter
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dmp_harvest_units as hu                          # noqa: E402

MUTED = "#5F6368"
ACCENT = "#1A73E8"
GOOD = "#137333"
WARN = "#B06000"
BAD = "#A50E0E"


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("DMP harvest units")
        self.geometry("1120x860")
        self.minsize(940, 660)

        self.path = ""
        self.summary: list[dict] = []
        self.parts: dict[str, list] = {}
        self.msgs: queue.Queue = queue.Queue()
        self.busy = False

        self._build()
        self.after(120, self._drain)

    # ---------------------------------------------------------------- layout

    def _build(self):
        pad = dict(padx=10, pady=5)

        head = ttk.Frame(self)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="DMP harvest units",
                  font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(head, text="what sits under a declared region",
                  foreground=MUTED).pack(side="left", padx=12)

        split = ttk.PanedWindow(self, orient="vertical")
        split.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=4)
        split.add(bottom, weight=1)

        self._build_file(top, pad)
        self._build_results(top, pad)
        self._build_actions(top, pad)
        self._build_log(bottom)

    def _build_file(self, parent, pad):
        g = ttk.LabelFrame(parent, text="1.  The download")
        g.pack(fill="x", **pad)
        r = ttk.Frame(g); r.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Button(r, text="Open passports…",
                   command=self.open_dmps).pack(side="left")
        ttk.Button(r, text="Open a downloaded file…",
                   command=self.open_file).pack(side="left", padx=6)
        self.file_lbl = ttk.Label(r, text="nothing loaded", foreground=MUTED)
        self.file_lbl.pack(side="left", padx=10)

        r2 = ttk.Frame(g); r2.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(r2, text="Point at the folder of passports Harmac sent. The "
                           "download link is inside each one; downloads are "
                           "cached and never re-fetched.",
                  foreground=MUTED).pack(side="left")
        if not hu.HAVE_SHAPELY:
            ttk.Label(r2, text="shapely not installed — counts only, no areas",
                      foreground=WARN).pack(side="right")

        r3 = ttk.Frame(g); r3.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(r3, text="Cutblock if under").pack(side="left")
        self.threshold = tk.StringVar(value=str(int(hu.CUTBLOCK_MAX_HA)))
        e = ttk.Entry(r3, textvariable=self.threshold, width=8)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda _e: self.reclassify())
        ttk.Label(r3, text="hectares, otherwise a catchment").pack(side="left")
        ttk.Button(r3, text="Apply", command=self.reclassify).pack(side="left",
                                                                  padx=10)
        ttk.Label(r3, text="check the size spread in the log before trusting "
                           "the default", foreground=MUTED).pack(side="left")

    def _build_results(self, parent, pad):
        g = ttk.LabelFrame(parent, text="2.  What is inside")
        g.pack(fill="both", expand=True, **pad)
        cols = ("unit", "parts", "cutblocks", "catchments", "total",
                "median", "smallest", "largest", "reading")
        heads = ("Passport · declared unit", "Polygons", "Cutblocks",
                 "Catchments", "Total ha", "Median ha", "Smallest", "Largest",
                 "Reading")
        widths = (330, 70, 75, 80, 90, 85, 80, 90, 190)
        tf = ttk.Frame(g); tf.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=9)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c not in
                             ("unit", "reading") else "w")
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.config(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("units", foreground=GOOD)
        self.tree.tag_configure("boundary", foreground=BAD)
        self.tree.tag_configure("mixed", foreground=WARN)

        note = ttk.Frame(g); note.pack(fill="x", padx=8, pady=(0, 8))
        self.reading_lbl = ttk.Label(note, text="", foreground=MUTED,
                                     wraplength=1040, justify="left")
        self.reading_lbl.pack(anchor="w")

    def _build_actions(self, parent, pad):
        g = ttk.LabelFrame(parent, text="3.  Do something with it")
        g.pack(fill="x", **pad)

        r = ttk.Frame(g); r.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(r, text="Output folder:").pack(side="left")
        self.outdir = tk.StringVar(value=os.path.join(os.getcwd(), "dmp_output"))
        ttk.Entry(r, textvariable=self.outdir).pack(side="left", fill="x",
                                                    expand=True, padx=6)
        ttk.Button(r, text="Browse…", command=self.pick_dir).pack(side="left")

        r2 = ttk.Frame(g); r2.pack(fill="x", padx=8, pady=(4, 2))
        self.btn_explode = ttk.Button(r2, text="Write cutblocks and catchments",
                                      command=self.do_explode, state="disabled")
        self.btn_explode.pack(side="left")
        ttk.Label(r2, text="two files: every cutblock in one, every regional "
                           "polygon in the other",
                  foreground=MUTED).pack(side="left", padx=10)

        r3 = ttk.Frame(g); r3.pack(fill="x", padx=8, pady=(4, 8))
        self.btn_compare = ttk.Button(r3, text="Compare with our blocks…",
                                      command=self.do_compare, state="disabled")
        self.btn_compare.pack(side="left")
        ttk.Label(r3, text="pick a HARP areas geojson — the useful answer is "
                           "which of ours fall outside theirs",
                  foreground=MUTED).pack(side="left", padx=10)
        self.status = ttk.Label(r3, text="", foreground=ACCENT)
        self.status.pack(side="right")

    def _build_log(self, parent):
        f = ttk.LabelFrame(parent, text="Log")
        f.pack(fill="both", expand=True, pady=(4, 0))
        lf = ttk.Frame(f); lf.pack(fill="both", expand=True, padx=8, pady=8)
        self.log_txt = tk.Text(lf, height=10, font=("Consolas", 9), wrap="none")
        sb = ttk.Scrollbar(lf, command=self.log_txt.yview)
        self.log_txt.config(yscrollcommand=sb.set)
        self.log_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.log("Collections are opened and multiparts exploded, so every "
                 "output feature is a single polygon.")
        self.log("No timber mark, source id or supplier is present in these "
                 "files, so they cannot attribute a harvest to one of Harmac's "
                 "supply sources.")

    # -------------------------------------------------------------- plumbing

    def log(self, msg):
        self.msgs.put(str(msg))

    def _drain(self):
        wrote = False
        while not self.msgs.empty():
            self.log_txt.insert("end", self.msgs.get() + "\n")
            wrote = True
        if wrote:
            self.log_txt.see("end")
        self.after(120, self._drain)

    def set_busy(self, busy, note=""):
        self.busy = busy
        state = "disabled" if (busy or not self.summary) else "normal"
        self.btn_explode.config(state=state)
        self.btn_compare.config(state=state)
        self.status.config(text=note)

    def run_bg(self, fn):
        if self.busy:
            return
        self.set_busy(True, "working…")

        def wrap():
            try:
                fn()
            except Exception as exc:
                self.log("ERROR: {}".format(exc))
                self.log(traceback.format_exc())
                self.after(0, lambda e=exc: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self.set_busy(False, ""))

        threading.Thread(target=wrap, daemon=True).start()

    def pick_dir(self):
        d = filedialog.askdirectory(title="Output folder",
                                    initialdir=self.outdir.get())
        if d:
            self.outdir.set(d)

    # ------------------------------------------------------------------ open

    def open_dmps(self):
        """A folder of passports: find the links, fetch, unpack, merge."""
        d = filedialog.askdirectory(title="Folder of Digital Material Passports")
        if not d:
            return
        found = hu.find_dmps(d)
        if not found:
            messagebox.showwarning(
                "No passports there",
                "No file in that folder is a Digital Material Passport.\n\n"
                "A passport is a .json with a DigitalMaterialPassport key.")
            return
        self.path = d
        self.tree.delete(*self.tree.get_children())
        self.parts, self.summary = {}, []
        self.reading_lbl.config(text="")
        cache = os.path.join(self.outdir.get().strip() or os.getcwd(),
                             "downloads")

        def work():
            self.log("\n{} passport(s) in {}".format(len(found), d))
            for info in found:
                self.log("\n{}   {} {}   {} declared unit(s)".format(
                    info["id"], info["country"], info["state"],
                    len(info["units"])))
                if not info["url"]:
                    self.log("  no download link in this passport")
                    continue
                dest = os.path.join(cache, "{}.geojson".format(
                    "".join(c if c.isalnum() or c in "-_" else "_"
                            for c in info["id"])))
                got = hu.download(info["url"], dest, log=self.log)
                if got:
                    self._ingest(got, source_label=info["id"])
            total = sum(len(v[0]) for v in self.parts.values())
            self.after(0, self.file_lbl.config, {
                "text": "{} passport(s)  ·  {} declared unit(s)  ·  {} "
                        "polygons".format(len(found), len(self.parts), total)})
            self.after(0, self.reclassify)

        self.run_bg(work)

    def _ingest(self, path: str, source_label: str = ""):
        """Read one downloaded harvest units file into the working set.

        Keyed on the declared unit and which passport it came from, so several
        passports merge without one overwriting another - Washington has a
        county called Clallam and British Columbia does not, but there is no
        guarantee two passports never share a unit name.
        """
        gj = hu.load(path)
        feats = gj.get("features") or []
        self.log("  {} feature(s) in {}".format(len(feats),
                                                os.path.basename(path)))
        if "crs" in gj:
            self.log("  a crs member is present - EUDR forbids it")
        for i, f in enumerate(feats):
            props = f.get("properties") or {}
            parts = list(hu.walk(f.get("geometry") or {}))
            areas = [hu.area_ha(p) for p in parts] if hu.HAVE_SHAPELY else []
            name = (props.get("UserDefinedId") or props.get("name")
                    or "feature {}".format(i))
            key = "{} · {}".format(source_label, name) if source_label else name
            props = {**props, "declared_by": source_label}
            self.parts[key] = (parts, props, areas)
            good = sorted(a for a in areas if a > 0)
            self.log("    {:<48} {:>5} polygons  {:>13}".format(
                key[:48], len(parts),
                "{:,.0f} ha".format(sum(good)) if good else ""))
            if good:
                for line in hu.distribution(good).splitlines():
                    self.log("  " + line)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Harvest units download",
            filetypes=[("GeoJSON", "*.geojson *.json"), ("All files", "*.*")])
        if not path:
            return
        self.path = path
        self.tree.delete(*self.tree.get_children())
        self.summary, self.parts = [], {}
        self.reading_lbl.config(text="")

        def work():
            size = os.path.getsize(path) / 1024 / 1024
            self.log("\nopening {} ({:.1f} MB)".format(
                os.path.basename(path), size))
            self._ingest(path, source_label=os.path.basename(path))
            feats = self.parts
            total = sum(len(v[0]) for v in self.parts.values())
            self.after(0, self.file_lbl.config, {
                "text": "{}  ·  {:.1f} MB  ·  {} declared unit(s)  ·  {} "
                        "polygons".format(os.path.basename(path), size,
                                          len(self.parts), total)})
            self.after(0, self.reclassify)

        self.run_bg(work)

    def _threshold(self) -> float:
        try:
            v = float(self.threshold.get().strip())
            return v if v > 0 else hu.CUTBLOCK_MAX_HA
        except ValueError:
            return hu.CUTBLOCK_MAX_HA

    def reclassify(self):
        """Rebuild the table at the current threshold.

        Separate from loading, because the threshold is a judgement about this
        data rather than a fact - the size spread in the log is what should
        decide it. Changing it re-sorts without re-reading the file.
        """
        if not self.parts:
            return
        t = self._threshold()
        self.tree.delete(*self.tree.get_children())
        self.summary = []
        total_cut = total_cat = 0

        for name, (parts, props, all_areas) in self.parts.items():
            areas = sorted(a for a in all_areas if a > 0)
            cut = sum(1 for a in areas if a <= t)
            cat = len(areas) - cut
            total_cut += cut
            total_cat += cat
            med = areas[len(areas) // 2] if areas else 0

            if not areas:
                reading, tag = "no areas - install shapely", "mixed"
            elif cut and cat:
                reading, tag = "both kinds present", "mixed"
            elif cut:
                reading, tag = "cutblocks only", "units"
            else:
                reading, tag = "regional areas only", "boundary"

            self.tree.insert("", "end", tags=(tag,), values=(
                name, len(parts), cut, cat,
                "{:,.0f}".format(sum(areas)) if areas else "",
                "{:,.1f}".format(med) if areas else "",
                "{:,.2f}".format(areas[0]) if areas else "",
                "{:,.0f}".format(areas[-1]) if areas else "",
                reading))
            self.summary.append({"name": name, "parts": len(parts),
                                 "cutblocks": cut, "catchments": cat,
                                 "area_ha": round(sum(areas), 1) if areas else None,
                                 "threshold_ha": t, "properties": props})

        self.reading_lbl.config(
            text="{} polygons under {} declared unit(s): {} look like cutblocks "
                 "and {} like regional areas, at a {:,.0f} ha threshold. The "
                 "cutblocks are already harvest areas. The regional ones are "
                 "search areas — change detection has to run inside them before "
                 "they mean anything. There is no timber mark or source id "
                 "here, so neither kind can be attributed to a Harmac supply "
                 "source.".format(
                     sum(s["parts"] for s in self.summary), len(self.summary),
                     total_cut, total_cat, t))
        self.set_busy(False, "{} cutblocks, {} catchments".format(
            total_cut, total_cat))

    # --------------------------------------------------------------- explode

    def do_explode(self):
        if not self.parts:
            return
        outdir = self.outdir.get().strip()
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Bad output folder", str(exc))
            return

        def work():
            self.log("\nexploding to {}".format(outdir))
            t = self._threshold()
            # Two files, not one per unit. The declared unit rides along as a
            # property, so merging loses nothing.
            buckets: dict[str, list] = {"cutblock": [], "catchment": [],
                                        "unknown": []}
            for name, (parts, props, all_areas) in self.parts.items():
                for n, p in enumerate(parts):
                    a = all_areas[n] if n < len(all_areas) else 0.0
                    kind = hu.classify(a, t)
                    buckets[kind].append({
                        "type": "Feature", "geometry": p,
                        "properties": {**props, "declared_unit": name,
                                       "part_index": n,
                                       "part_area_ha": round(a, 2),
                                       "harvest_area_kind": kind,
                                       "kind_basis": ("area {:,.1f} ha against "
                                                      "a {:,.0f} ha threshold"
                                                      .format(a, t))}})
            written = []
            for kind in ("cutblock", "catchment", "unknown"):
                out_feats = buckets[kind]
                if not out_feats:
                    continue
                dest = os.path.join(outdir, "{}s.geojson".format(kind))
                with open(dest, "w", encoding="utf-8") as fh:
                    json.dump({"type": "FeatureCollection",
                               "name": "dmp_harvest_{}s".format(kind),
                               "features": out_feats}, fh)
                written.append(dest)
                total = sum(f["properties"]["part_area_ha"] for f in out_feats)
                self.log("  {:<24} {:>6} features  {:>14,.0f} ha".format(
                    os.path.basename(dest), len(out_feats), total))
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sp = os.path.join(outdir, "summary_{}.json".format(stamp))
            with open(sp, "w", encoding="utf-8") as fh:
                json.dump(self.summary, fh, indent=1)
            self.log("summary: {}".format(sp))
            self.after(0, lambda: self.status.config(
                text="{} files written".format(len(written))))

        self.run_bg(work)

    # --------------------------------------------------------------- compare

    def do_compare(self):
        if not self.path:
            return
        if not hu.HAVE_SHAPELY:
            messagebox.showwarning(
                "shapely needed",
                "Comparing needs shapely and pyproj:\n\n"
                "  pip install shapely pyproj")
            return
        ours = filedialog.askopenfilename(
            title="A HARP areas geojson",
            filetypes=[("GeoJSON", "*.geojson *.json"), ("All files", "*.*")])
        if not ours:
            return

        def work():
            from shapely.geometry import shape
            from shapely.ops import unary_union
            self.log("\ncomparing against {}".format(os.path.basename(ours)))

            theirs = []
            for parts, _props, _areas in self.parts.values():
                for p in parts:
                    try:
                        theirs.append(shape(p))
                    except Exception:
                        pass
            if not theirs:
                self.log("no usable geometry in the harvest units file")
                return
            self.log("their parts: {}".format(len(theirs)))
            merged = unary_union(theirs)

            from collections import defaultdict
            with open(ours, encoding="utf-8") as fh:
                mine = (json.load(fh).get("features") or [])

            stats = defaultdict(lambda: {"in": 0, "out": 0,
                                         "in_ids": set(), "out_ids": set()})
            skipped = 0
            for f in mine:
                g = f.get("geometry")
                if not g:
                    skipped += 1
                    continue
                try:
                    geom = shape(g)
                except Exception:
                    skipped += 1
                    continue
                p = f.get("properties") or {}
                tier = p.get("harp_tier") or "?"
                ident = (p.get("harp_identifier") or p.get("TIMBER_MARK")
                         or "?")
                key = "in" if merged.intersects(geom) else "out"
                stats[tier][key] += 1
                stats[tier][key + "_ids"].add(ident)

            total_in = sum(v["in"] for v in stats.values())
            total = total_in + sum(v["out"] for v in stats.values())
            self.log("\nour features      : {}".format(total))
            if skipped:
                self.log("  no geometry     : {}".format(skipped))
            self.log("\n{:<6}{:>10}{:>10}{:>9}   {}".format(
                "tier", "inside", "outside", "overlap", "marks in / out"))
            self.log("-" * 62)
            for tier in sorted(stats):
                v = stats[tier]
                n = v["in"] + v["out"]
                self.log("{:<6}{:>10}{:>10}{:>8.0f}%   {} / {}".format(
                    tier, v["in"], v["out"], (v["in"] / n * 100) if n else 0,
                    len(v["in_ids"]), len(v["out_ids"])))
            self.log("-" * 62)
            self.log("{:<6}{:>10}{:>10}{:>8.0f}%".format(
                "all", total_in, total - total_in,
                (total_in / total * 100) if total else 0))

            p1 = stats.get("P1a", 0) + stats.get("P1b", 0)
            if p1 and (p1["in"] + p1["out"]):
                pct = p1["in"] / (p1["in"] + p1["out"]) * 100
                self.log("\nP1 is the number that matters: {:.0f}% of blocks "
                         "matched to a specific mark fall inside what they "
                         "declared.".format(pct))
                if p1["out_ids"]:
                    ids = sorted(p1["out_ids"])
                    self.log("marks with no overlap: " + ", ".join(ids[:18])
                             + (" and {} more".format(len(ids) - 18)
                                if len(ids) > 18 else ""))
            self.log("\nBefore reading anything into a low figure: the "
                     "periods may differ, ours carries every block ever "
                     "recorded under a mark, and a chip passport declares a "
                     "different supply stream from a log purchase.")
            self.after(0, lambda: self.status.config(
                text="{} in, {} out".format(total_in, total - total_in)))

        self.run_bg(work)


if __name__ == "__main__":
    App().mainloop()
