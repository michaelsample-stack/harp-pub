#!/usr/bin/env python3
"""Washington FPA extract and DIST detection — a window over fpars_detect.py.

All the work lives in that module; nothing is reimplemented here, so the two
cannot drift.

    python tools/fpars_gui.py

Three tabs, following the test:

    1 Extract    pull the application polygons for chosen suppliers
    2 Detect     run DIST inside them and see what was actually cut
    3 Result     the comparison — applications against detected harvest

WHAT THE TEST IS FOR
--------------------
A Forest Practices Application is permission to cut, not evidence of a cut.
Matching Harmac's suppliers by name returned roughly 86,000 hectares of
applications, against maybe 50 to 100 hectares of actual harvest behind a
month's intake. Detection is what separates ground that was disturbed from
ground that merely had approval.

The number worth watching is on the Result tab: how much of the application
area comes back with disturbance. If it is a small fraction, the approach
works. If most of it lights up, it does not, and that is worth knowing before
a methodology is built on it.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timedelta

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpars_detect as fp                                  # noqa: E402

MUTED = "#5F6368"
ACCENT = "#1A73E8"
GOOD = "#137333"
WARN = "#B06000"
BAD = "#A50E0E"

SETTINGS = os.path.join(os.path.expanduser("~"), ".harp_fpars_gui.json")


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Washington FPA — extract and detect")
        self.geometry("1180x900")
        self.minsize(980, 700)

        self.polygons: list = []
        self.poly_path = ""
        self.detected: list = []
        self.summary: list = []
        self.msgs: queue.Queue = queue.Queue()
        self.busy = False

        self._vars()
        self._build()
        self._load_settings()
        self.after(120, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _vars(self):
        self.outdir = tk.StringVar(value=os.path.join(os.getcwd(), "fpars_out"))
        self.since = tk.StringVar(value="2021")
        self.suppliers = tk.StringVar(value=", ".join(fp.DEFAULT_TERMS))
        self.poly_file = tk.StringVar(value="")
        end = datetime.now().date()
        self.start = tk.StringVar(value=str(end - timedelta(days=730)))
        self.end = tk.StringVar(value=str(end))
        self.confidence = tk.StringVar(value="6")
        self.project = tk.StringVar(value="")
        self.teo = tk.StringVar(value=os.path.abspath("../tracemark-eo"))
        self.limit = tk.StringVar(value="50")

    # ═════════════════════════════════════════════════════════ layout

    def _build(self):
        pad = dict(padx=10, pady=6)

        head = ttk.Frame(self)
        head.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(head, text="Washington FPA",
                  font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(head, text="applications → detection → harvest",
                  foreground=MUTED).pack(side="left", padx=12)
        self.dep_lbl = ttk.Label(head, text="", foreground=MUTED)
        self.dep_lbl.pack(side="right")

        split = ttk.PanedWindow(self, orient="vertical")
        split.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=4)
        split.add(bottom, weight=1)

        self.nb = ttk.Notebook(top)
        self.nb.pack(fill="both", expand=True)
        t1, t2, t3 = ttk.Frame(self.nb), ttk.Frame(self.nb), ttk.Frame(self.nb)
        self.nb.add(t1, text="  1  Extract  ")
        self.nb.add(t2, text="  2  Detect  ")
        self.nb.add(t3, text="  3  Result  ")
        self._build_extract(t1, pad)
        self._build_detect(t2, pad)
        self._build_result(t3, pad)
        self._build_log(bottom)

        bar = ttk.Frame(self); bar.pack(fill="x", side="bottom")
        self.status = ttk.Label(bar, text="ready", foreground=ACCENT, anchor="w")
        self.status.pack(side="left", padx=14, pady=4)
        self.check_deps()

    # ──────────────────────────────────────────────────────── extract

    def _build_extract(self, parent, pad):
        g = ttk.LabelFrame(parent, text="Which suppliers, and how far back")
        g.pack(fill="x", **pad)

        r = ttk.Frame(g); r.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(r, text="Suppliers", width=12).pack(side="left")
        ttk.Entry(r, textvariable=self.suppliers).pack(side="left", fill="x",
                                                       expand=True, padx=6)
        ttk.Button(r, text="Reset",
                   command=lambda: self.suppliers.set(
                       ", ".join(fp.DEFAULT_TERMS))).pack(side="left")

        r2 = ttk.Frame(g); r2.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Label(r2, text="From year", width=12).pack(side="left")
        ttk.Entry(r2, textvariable=self.since, width=8).pack(side="left")
        ttk.Label(r2, text="2021 is the floor — DIST alerts do not go back "
                           "further, so anything earlier cannot be verified",
                  foreground=MUTED).pack(side="left", padx=10)

        r3 = ttk.Frame(g); r3.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(r3, text="Output", width=12).pack(side="left")
        ttk.Entry(r3, textvariable=self.outdir).pack(side="left", fill="x",
                                                     expand=True, padx=6)
        ttk.Button(r3, text="…", width=3,
                   command=self.pick_out).pack(side="left")

        r4 = ttk.Frame(parent); r4.pack(fill="x", **pad)
        self.btn_extract = ttk.Button(r4, text="Extract application polygons",
                                      command=self.do_extract)
        self.btn_extract.pack(side="left")
        ttk.Label(r4, text="talks only to Washington DNR — no Earth Engine "
                           "needed for this step",
                  foreground=MUTED).pack(side="left", padx=10)

        g2 = ttk.LabelFrame(parent, text="What came back")
        g2.pack(fill="both", expand=True, **pad)
        cols = ("term", "fp_ids", "polys", "acres", "ha", "roles")
        heads = ("Supplier term", "FP_IDs", "Polygons", "Acres", "Hectares",
                 "As landowner / operator / timber owner")
        widths = (220, 90, 90, 110, 110, 280)
        tf = ttk.Frame(g2); tf.pack(fill="both", expand=True, padx=10, pady=10)
        self.ex_tree = ttk.Treeview(tf, columns=cols, show="headings", height=10)
        for c, h, w in zip(cols, heads, widths):
            self.ex_tree.heading(c, text=h)
            self.ex_tree.column(c, width=w,
                                anchor="w" if c in ("term", "roles") else "e")
        sb = ttk.Scrollbar(tf, command=self.ex_tree.yview)
        self.ex_tree.config(yscrollcommand=sb.set)
        self.ex_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        self.ex_note = ttk.Label(parent, text="", foreground=MUTED,
                                 wraplength=1080, justify="left")
        self.ex_note.pack(anchor="w", padx=14, pady=(0, 8))

    # ───────────────────────────────────────────────────────── detect

    def _build_detect(self, parent, pad):
        g = ttk.LabelFrame(parent, text="Input")
        g.pack(fill="x", **pad)
        r = ttk.Frame(g); r.pack(fill="x", padx=10, pady=10)
        ttk.Label(r, text="Polygons", width=12).pack(side="left")
        ttk.Entry(r, textvariable=self.poly_file).pack(side="left", fill="x",
                                                       expand=True, padx=6)
        ttk.Button(r, text="…", width=3, command=self.pick_polys).pack(side="left")
        self.poly_lbl = ttk.Label(g, text="nothing loaded", foreground=MUTED)
        self.poly_lbl.pack(anchor="w", padx=10, pady=(0, 10))

        g2 = ttk.LabelFrame(parent, text="Detection window")
        g2.pack(fill="x", **pad)
        r = ttk.Frame(g2); r.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(r, text="From", width=12).pack(side="left")
        ttk.Entry(r, textvariable=self.start, width=14).pack(side="left")
        ttk.Label(r, text="To").pack(side="left", padx=(14, 4))
        ttk.Entry(r, textvariable=self.end, width=14).pack(side="left")
        ttk.Label(r, text="YYYY-MM-DD", foreground=MUTED).pack(side="left", padx=10)
        r2 = ttk.Frame(g2); r2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(r2, text="Confidence", width=12).pack(side="left")
        ttk.Combobox(r2, textvariable=self.confidence, width=5, state="readonly",
                     values=[str(i) for i in range(1, 9)]).pack(side="left")
        ttk.Label(r2, text="minimum DIST status, 1–8. Higher is stricter; 6 is "
                           "what the production scripts use",
                  foreground=MUTED).pack(side="left", padx=10)

        g3 = ttk.LabelFrame(parent, text="Earth Engine")
        g3.pack(fill="x", **pad)
        for label, var, browse in (("Project", self.project, None),
                                   ("tracemark-eo", self.teo, self.pick_teo)):
            r = ttk.Frame(g3); r.pack(fill="x", padx=10, pady=3)
            ttk.Label(r, text=label, width=12).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x",
                                                expand=True, padx=6)
            if browse:
                ttk.Button(r, text="…", width=3, command=browse).pack(side="left")
        r = ttk.Frame(g3); r.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Label(r, text="Limit", width=12).pack(side="left")
        ttk.Entry(r, textvariable=self.limit, width=8).pack(side="left")
        ttk.Label(r, text="first N polygons. Leave 50 for a first run — if it "
                          "works, clear it", foreground=MUTED).pack(side="left",
                                                                    padx=10)

        r = ttk.Frame(parent); r.pack(fill="x", **pad)
        self.btn_detect = ttk.Button(r, text="Run detection",
                                     command=self.do_detect)
        self.btn_detect.pack(side="left")
        ttk.Label(r, text="submits to Earth Engine and waits — minutes, not "
                          "seconds", foreground=WARN).pack(side="left", padx=10)

    # ───────────────────────────────────────────────────────── result

    def _build_result(self, parent, pad):
        g = ttk.LabelFrame(parent, text="Applications against detected harvest")
        g.pack(fill="x", **pad)
        self.cards = ttk.Frame(g); self.cards.pack(fill="x", padx=10, pady=12)
        self.card_lbl = {}
        for key, title in (("apps", "Applications"),
                           ("app_ha", "Application ha"),
                           ("hit", "With disturbance"),
                           ("miss", "Nothing detected"),
                           ("ratio", "Share that lit up")):
            f = ttk.Frame(self.cards, relief="solid", borderwidth=1)
            f.pack(side="left", padx=(0, 10), ipadx=16, ipady=8)
            ttk.Label(f, text=title, foreground=MUTED,
                      font=("Segoe UI", 8)).pack()
            lbl = ttk.Label(f, text="—", font=("Segoe UI", 16))
            lbl.pack()
            self.card_lbl[key] = lbl

        self.verdict = ttk.Label(parent, text="", wraplength=1080,
                                 justify="left")
        self.verdict.pack(anchor="w", padx=14, pady=(0, 8))

        g2 = ttk.LabelFrame(parent, text="Per application")
        g2.pack(fill="both", expand=True, **pad)
        cols = ("fp", "supplier", "app_ac", "detected", "note")
        heads = ("FP_ID", "Supplier", "Application acres", "Disturbance", "")
        widths = (130, 200, 130, 120, 400)
        tf = ttk.Frame(g2); tf.pack(fill="both", expand=True, padx=10, pady=10)
        self.res_tree = ttk.Treeview(tf, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.res_tree.heading(c, text=h)
            self.res_tree.column(c, width=w,
                                 anchor="e" if "ac" in c else "w")
        sb = ttk.Scrollbar(tf, command=self.res_tree.yview)
        self.res_tree.config(yscrollcommand=sb.set)
        self.res_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.res_tree.tag_configure("hit", foreground=GOOD)
        self.res_tree.tag_configure("miss", foreground=MUTED)

        r = ttk.Frame(parent); r.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(r, text="Open output folder",
                   command=lambda: self._open(self.outdir.get())).pack(side="left")

    def _build_log(self, parent):
        f = ttk.LabelFrame(parent, text="Log")
        f.pack(fill="both", expand=True, pady=(4, 0))
        lf = ttk.Frame(f); lf.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_txt = tk.Text(lf, height=9, font=("Consolas", 9), wrap="none")
        sb = ttk.Scrollbar(lf, command=self.log_txt.yview)
        self.log_txt.config(yscrollcommand=sb.set)
        self.log_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.log("An application is permission to cut, not evidence of a cut. "
                 "Detection is what tells the two apart.")

    # ═════════════════════════════════════════════════════ plumbing

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
        state = "disabled" if busy else "normal"
        self.btn_extract.config(state=state)
        self.btn_detect.config(state=state)
        self.status.config(text=note or ("working…" if busy else "ready"))

    def run_bg(self, fn, note=""):
        if self.busy:
            return
        self.set_busy(True, note)

        def wrap():
            try:
                fn()
            except Exception as exc:
                self.log("ERROR: {}".format(exc))
                self.log(traceback.format_exc())
                self.after(0, lambda e=exc: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self.set_busy(False))

        threading.Thread(target=wrap, daemon=True).start()

    def check_deps(self):
        missing = []
        for mod, label in (("requests", "requests"), ("ee", "earthengine-api")):
            try:
                __import__(mod)
            except ImportError:
                missing.append(label)
        teo = self.teo.get()
        if not os.path.isdir(teo):
            missing.append("tracemark-eo path")
        elif not os.path.isfile(os.path.join(teo, "pointtopoly",
                                             "pointtopoly.py")):
            missing.append("pointtopoly/pointtopoly.py under that path")
        self.dep_lbl.config(
            text=("missing: " + ", ".join(missing)) if missing
            else "requests, earthengine-api and tracemark-eo all present",
            foreground=WARN if missing else GOOD)

    def _open(self, path):
        if not path or not os.path.isdir(path):
            messagebox.showinfo("Not there yet", "Nothing written to {} yet."
                                .format(path or "the output folder"))
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Could not open", str(exc))

    def pick_out(self):
        d = filedialog.askdirectory(title="Output folder",
                                    initialdir=self.outdir.get())
        if d:
            self.outdir.set(d)

    def pick_teo(self):
        d = filedialog.askdirectory(title="tracemark-eo repository")
        if d:
            self.teo.set(d)
            self.check_deps()

    def pick_polys(self):
        p = filedialog.askopenfilename(
            title="Application polygons",
            initialdir=self.outdir.get(),
            filetypes=[("GeoJSON", "*.geojson *.json"), ("All files", "*.*")])
        if p:
            self.poly_file.set(p)
            self._load_polys(p)

    def _load_polys(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                gj = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Could not read that file", str(exc))
            return
        self.polygons = gj.get("features") or []
        self.poly_path = path
        acres = 0.0
        for f in self.polygons:
            try:
                acres += float((f.get("properties") or {}).get(
                    "TIMHARV_RPT_AREA") or 0)
            except (TypeError, ValueError):
                pass
        self.poly_lbl.config(text="{}  ·  {} polygons  ·  {:,.0f} acres "
                                  "({:,.0f} ha)".format(
                                      os.path.basename(path),
                                      len(self.polygons), acres,
                                      acres * 0.404686))
        self.log("loaded {} polygons from {}".format(len(self.polygons),
                                                     os.path.basename(path)))

    # ─────────────────────────────────────────────────────── extract

    def do_extract(self):
        terms = [t.strip() for t in self.suppliers.get().replace("\n", ",")
                 .split(",") if t.strip()]
        if not terms:
            messagebox.showwarning("No suppliers", "Enter at least one name.")
            return
        try:
            since = int(self.since.get().strip())
        except ValueError:
            since = 2021
        outdir = self.outdir.get().strip()
        self.ex_tree.delete(*self.ex_tree.get_children())
        self.summary = []

        def work():
            s = fp._session()
            layer = fp.pick_layer(s)
            self.log("\nlayer {}  {}".format(layer["id"], layer["name"]))
            self.log("applications from {} onward\n".format(since))
            os.makedirs(outdir, exist_ok=True)

            all_feats = []
            for term in terms:
                self.log("  {}…".format(term))
                ids, by_role = fp.fp_ids_for(s, term)
                feats = fp.polygons_for(s, sorted(ids), layer, since,
                                        log=self.log) if ids else []
                acres = 0.0
                for f in feats:
                    p = f.setdefault("properties", {})
                    p["harp_supplier_term"] = term
                    try:
                        acres += float(p.get("TIMHARV_RPT_AREA") or 0)
                    except (TypeError, ValueError):
                        pass
                all_feats.extend(feats)
                row = {"term": term, "fp_ids": len(ids), "polygons": len(feats),
                       "acres": round(acres, 1), **by_role}
                self.summary.append(row)
                self.after(0, self.ex_tree.insert, "", "end", {
                    "values": (term, len(ids), len(feats),
                               "{:,.0f}".format(acres),
                               "{:,.0f}".format(acres * 0.404686),
                               "{} / {} / {}".format(
                                   by_role.get("landowner", 0),
                                   by_role.get("operator", 0),
                                   by_role.get("timberowner", 0)))})
                self.log("    {:>6} FP_IDs   {:>6} polygons   {:>10,.0f} ac"
                         .format(len(ids), len(feats), acres))

            total_ac = sum(r["acres"] for r in self.summary)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(outdir,
                                "fpars_polygons_{}.geojson".format(stamp))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"type": "FeatureCollection",
                           "name": "fpars_applications",
                           "metadata": {
                               "layer": layer["name"], "since": since,
                               "generated": datetime.now().isoformat(
                                   timespec="seconds"),
                               "by_supplier": self.summary,
                               "note": ("Forest Practices Applications matched "
                                        "by company name. Permission to cut, "
                                        "not evidence of a cut.")},
                           "features": all_feats}, fh)
            self.log("\n{:,.0f} acres  ·  {:,.0f} hectares  ·  {} polygons"
                     .format(total_ac, total_ac * 0.404686, len(all_feats)))
            self.log("  {}".format(path))

            def done():
                self.poly_file.set(path)
                self._load_polys(path)
                self.ex_note.config(
                    text="{:,.0f} hectares of applications across {} "
                         "polygons. That is everywhere these companies had "
                         "permission to cut — not where they did. Move to "
                         "Detect.".format(total_ac * 0.404686, len(all_feats)))
                self.nb.select(1)
            self.after(0, done)

        self.run_bg(work, "extracting…")

    # ──────────────────────────────────────────────────────── detect

    def do_detect(self):
        if not self.polygons:
            messagebox.showwarning("Nothing loaded",
                                   "Extract or open a polygon file first.")
            return
        try:
            import ee  # noqa: F401
        except ImportError:
            messagebox.showerror(
                "Earth Engine not installed",
                "pip install earthengine-api\n\nYou will also need a GCP "
                "project with Earth Engine enabled.")
            return
        teo_dir = self.teo.get()
        if not os.path.isfile(os.path.join(teo_dir, "pointtopoly",
                                           "pointtopoly.py")):
            messagebox.showerror(
                "tracemark-eo not found",
                "Expected pointtopoly/pointtopoly.py under:\n\n{}\n\n"
                "Point the box at the repository root, not at the "
                "pointtopoly folder.".format(teo_dir or "(empty)"))
            return

        feats = list(self.polygons)
        if self.limit.get().strip().isdigit():
            feats = feats[:int(self.limit.get().strip())]
        start, end = self.start.get().strip(), self.end.get().strip()
        conf = int(self.confidence.get())
        project = self.project.get().strip() or None
        teo = self.teo.get()
        outdir = self.outdir.get().strip()

        def work():
            import ee
            self.log("importing tracemark-eo from {}…".format(teo))
            ptp = fp.load_pointtopoly(teo, log=self.log)
            convert_ee_feature_collection_to_geojson = \
                ptp.convert_ee_feature_collection_to_geojson
            get_harvestable_forest_img = ptp.get_harvestable_forest_img
            polyToChangeDetectionPoly_DIST = ptp.polyToChangeDetectionPoly_DIST

            self.log("\n{} polygon(s), {} to {}, confidence >= {}".format(
                len(feats), start, end, conf))
            self.log("initialising Earth Engine…")
            try:
                ee.Initialize(project=project) if project else ee.Initialize()
            except Exception:
                self.log("  not authenticated — a browser window will open")
                ee.Authenticate()
                ee.Initialize(project=project) if project else ee.Initialize()

            # FP_ID is the join back to the application, so a detected harvest
            # can be traced to the permission it sits inside.
            for i, f in enumerate(feats):
                f.setdefault("properties", {})["joinID"] = str(
                    f["properties"].get("FP_ID") or i)
            fc = ee.FeatureCollection({"type": "FeatureCollection",
                                       "features": feats})

            self.log("building the forest mask…")
            harvestable = get_harvestable_forest_img()
            now = ee.Date(end)
            dw = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
                  .filterDate(now.advance(-8, "months"), now)
                  .select("crops").mean())
            dw_mask = dw.lt(0.5).rename("dw")

            self.log("running detection — this is the slow part…")
            detected = polyToChangeDetectionPoly_DIST(
                start, end, fc, "joinID", harvestable, dw_mask, conf,
                "harp-fpars-test")

            self.log("collecting…")
            result = json.loads(convert_ee_feature_collection_to_geojson(detected))
            os.makedirs(outdir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = os.path.join(outdir,
                                "fpars_detected_{}.geojson".format(stamp))
            result.setdefault("metadata", {}).update({
                "source": os.path.basename(self.poly_path),
                "window": [start, end], "confidence": conf,
                "note": "One feature per application, joined on FP_ID."})
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump(result, fh)
            self.detected = result.get("features") or []
            self.log("  {}".format(dest))
            self.after(0, self._show_result, feats)

        self.run_bg(work, "detecting — minutes, not seconds…")

    # ──────────────────────────────────────────────────────── result

    def _show_result(self, sent):
        self.res_tree.delete(*self.res_tree.get_children())
        by_id = {}
        for f in sent:
            p = f.get("properties") or {}
            by_id[str(p.get("FP_ID"))] = p

        hit = miss = 0
        app_ac = det_ac = 0.0
        for f in self.detected:
            p = f.get("properties") or {}
            fpid = str(p.get("joinID") or p.get("FP_ID") or "")
            src = by_id.get(fpid, {})
            try:
                a = float(src.get("TIMHARV_RPT_AREA") or 0)
            except (TypeError, ValueError):
                a = 0.0
            app_ac += a
            alerted = str(p.get("alert")).lower() == "true"
            try:
                d = float(p.get("area") or p.get("AREA") or 0)
            except (TypeError, ValueError):
                d = 0.0
            if alerted:
                hit += 1
                det_ac += d
            else:
                miss += 1
            self.res_tree.insert("", "end", tags=("hit" if alerted else "miss",),
                                 values=(fpid,
                                         src.get("harp_supplier_term", ""),
                                         "{:,.0f}".format(a) if a else "",
                                         "yes" if alerted else "—",
                                         "" if alerted else
                                         "approved, nothing detected in window"))

        total = hit + miss
        share = (hit / total * 100) if total else 0
        self.card_lbl["apps"].config(text="{:,}".format(total))
        self.card_lbl["app_ha"].config(
            text="{:,.0f}".format(app_ac * 0.404686))
        self.card_lbl["hit"].config(text="{:,}".format(hit))
        self.card_lbl["miss"].config(text="{:,}".format(miss))
        self.card_lbl["ratio"].config(text="{:.0f}%".format(share))

        if share < 40:
            v = ("Most applications show no disturbance in this window. That is "
                 "the result the approach needs: permission is much broader "
                 "than what was actually cut, and detection is doing the "
                 "narrowing.")
        elif share < 75:
            v = ("A little over half the applications show disturbance. "
                 "Detection is narrowing the area, but not dramatically — "
                 "worth trying a tighter window before drawing conclusions.")
        else:
            v = ("Nearly every application shows disturbance. Detection is not "
                 "narrowing anything here, so the declared area stays close to "
                 "the full company footprint. That is worth knowing before a "
                 "methodology is built on it.")
        self.verdict.config(text=v, foreground=GOOD if share < 40 else
                            WARN if share < 75 else BAD)
        self.log("\n{} of {} applications showed disturbance ({:.0f}%)".format(
            hit, total, share))
        self.nb.select(2)

    # ─────────────────────────────────────────────────────── settings

    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                s = json.load(fh)
        except Exception:
            return
        for key, var in (("out", self.outdir), ("project", self.project),
                         ("teo", self.teo), ("since", self.since),
                         ("suppliers", self.suppliers)):
            if s.get(key):
                var.set(s[key])

    def _on_close(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump({"out": self.outdir.get(),
                           "project": self.project.get(),
                           "teo": self.teo.get(),
                           "since": self.since.get(),
                           "suppliers": self.suppliers.get()}, fh)
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
