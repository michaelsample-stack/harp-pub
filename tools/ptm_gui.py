#!/usr/bin/env python3
"""Private timber mark -> parcel geometry, with a window.

A wrapper over ptm_parcels.py, which does the work. Nothing is reimplemented
here - the parsing, the union and the ParcelMap queries all live in that
module, so the two cannot drift.

    python tools/ptm_gui.py

Four panes:

    Extracts    load the ministry workbooks, see what is in them
    Marks       pick which marks to resolve, from a register or by hand
    Parcels     the result, one row per mark, with area and district
    Log         what it actually did

Built for isolating a few marks and eyeballing them before trusting the route.
Start with two you can check by hand.

Requires: the same as ptm_parcels.py, plus tkinter (ships with Python).
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
import ptm_parcels as ptm                              # noqa: E402

MUTED = "#5F6368"
ACCENT = "#1A73E8"
GOOD = "#137333"
WARN = "#B06000"
BAD = "#A50E0E"


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Private Timber Mark - parcel geometry")
        self.geometry("1180x900")
        self.minsize(980, 700)

        self.rows: list[dict] = []       # every extract row, unioned
        self.wanted: list[str] = []      # marks to resolve
        self.results: list[dict] = []    # per-mark summary
        self.features: list[dict] = []   # parcel polygons
        self.questions: list[dict] = []
        self.msgs: queue.Queue = queue.Queue()
        self.busy = False

        self._build()
        self.after(120, self._drain)

    # ---------------------------------------------------------------- layout

    def _build(self):
        pad = dict(padx=10, pady=5)

        head = ttk.Frame(self)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="Private Timber Marks",
                  font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(head, text="mark → PID → ParcelMap BC → polygon",
                  foreground=MUTED).pack(side="left", padx=12)

        split = ttk.PanedWindow(self, orient="vertical")
        split.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=4)
        split.add(bottom, weight=1)

        self._build_extracts(top, pad)
        self._build_marks(top, pad)
        self._build_results(top, pad)
        self._build_run(top, pad)
        self._build_log(bottom)

    def _build_extracts(self, parent, pad):
        g = ttk.LabelFrame(parent, text="1.  Ministry extracts")
        g.pack(fill="x", **pad)
        r = ttk.Frame(g); r.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Button(r, text="Load folder…", command=self.load_folder).pack(side="left")
        self.ex_lbl = ttk.Label(r, text="nothing loaded", foreground=MUTED)
        self.ex_lbl.pack(side="left", padx=10)

        cols = ("file", "sheet", "rows", "marks", "stray")
        heads = ("File", "Data sheet", "Rows", "Marks", "Stray cols")
        widths = (420, 130, 70, 70, 90)
        tf = ttk.Frame(g); tf.pack(fill="x", padx=8, pady=(0, 8))
        self.ex_tree = ttk.Treeview(tf, columns=cols, show="headings", height=6)
        for c, h, w in zip(cols, heads, widths):
            self.ex_tree.heading(c, text=h)
            self.ex_tree.column(c, width=w,
                                anchor="e" if c in ("rows", "marks", "stray") else "w")
        self.ex_tree.pack(fill="x")

    def _build_marks(self, parent, pad):
        g = ttk.LabelFrame(parent, text="2.  Marks to resolve")
        g.pack(fill="x", **pad)
        r = ttk.Frame(g); r.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Button(r, text="From register…",
                   command=self.load_register).pack(side="left")
        ttk.Label(r, text="class:").pack(side="left", padx=(10, 2))
        self.klass = tk.StringVar(value="B")
        ttk.Entry(r, textvariable=self.klass, width=5).pack(side="left")
        ttk.Button(r, text="All marks in the extracts",
                   command=self.use_all).pack(side="left", padx=10)
        ttk.Button(r, text="Clear", command=self.clear_marks).pack(side="left")

        r2 = ttk.Frame(g); r2.pack(fill="x", padx=8, pady=(2, 2))
        ttk.Label(r2, text="Or type them - commas, spaces or one per line. "
                           "Start with two you can check by hand.",
                  foreground=MUTED).pack(anchor="w")
        pf = ttk.Frame(g); pf.pack(fill="x", padx=8, pady=(0, 4))
        self.paste = tk.Text(pf, height=3, font=("Consolas", 9), wrap="word")
        sb = ttk.Scrollbar(pf, command=self.paste.yview)
        self.paste.config(yscrollcommand=sb.set)
        self.paste.pack(side="left", fill="x", expand=True)
        sb.pack(side="left", fill="y")
        self.paste.insert("1.0", "EDRWD, AA545")

        r3 = ttk.Frame(g); r3.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(r3, text="Use typed marks",
                   command=self.use_typed).pack(side="left")
        self.mk_lbl = ttk.Label(r3, text="", foreground=MUTED)
        self.mk_lbl.pack(side="left", padx=10)

    def _build_results(self, parent, pad):
        g = ttk.LabelFrame(parent, text="3.  Parcels")
        g.pack(fill="both", expand=True, **pad)
        cols = ("mark", "pids", "found", "missing", "area", "district", "legal")
        heads = ("Mark", "PIDs", "Parcels found", "Missing", "Area ha",
                 "District", "Legal description (first)")
        widths = (90, 60, 90, 70, 90, 90, 460)
        tf = ttk.Frame(g); tf.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=10)
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c in
                             ("pids", "found", "missing", "area") else "w")
        sb = ttk.Scrollbar(tf, command=self.tree.yview)
        self.tree.config(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("ok", foreground=GOOD)
        self.tree.tag_configure("partial", foreground=WARN)
        self.tree.tag_configure("none", foreground=BAD)

    def _build_run(self, parent, pad):
        g = ttk.LabelFrame(parent, text="4.  Run")
        g.pack(fill="x", **pad)
        r = ttk.Frame(g); r.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(r, text="Output folder:").pack(side="left")
        self.outdir = tk.StringVar(value=os.path.join(os.getcwd(), "ptm_output"))
        ttk.Entry(r, textvariable=self.outdir).pack(side="left", fill="x",
                                                    expand=True, padx=6)
        ttk.Button(r, text="Browse…", command=self.pick_dir).pack(side="left")

        r2 = ttk.Frame(g); r2.pack(fill="x", padx=8, pady=(4, 8))
        self.btn_run = ttk.Button(r2, text="Fetch parcels",
                                  command=self.do_run)
        self.btn_run.pack(side="left")
        self.btn_write = ttk.Button(r2, text="Write GeoJSON",
                                    command=self.do_write, state="disabled")
        self.btn_write.pack(side="left", padx=6)
        ttk.Label(r2, text="A parcel is the land the timber was scaled from, "
                           "not the cut block.",
                  foreground=WARN).pack(side="left", padx=12)
        self.status = ttk.Label(r2, text="", foreground=ACCENT)
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
        self.log(ptm.ATTRIBUTION)
        self.log("The source workbooks carry their own Legal Disclaimer sheet "
                 "- read it before publishing derived geometry.")

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
        self.btn_run.config(state="disabled" if busy else "normal")
        self.btn_write.config(
            state="normal" if (not busy and self.features) else "disabled")
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

    # -------------------------------------------------------------- extracts

    def load_folder(self):
        d = filedialog.askdirectory(title="Folder of ministry .xlsx extracts")
        if not d:
            return
        try:
            rows, report = ptm.load_extracts(d)
        except SystemExit as exc:
            messagebox.showerror("Could not read that folder", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("Could not read that folder", str(exc))
            return
        if not rows:
            messagebox.showwarning("Nothing found",
                                   "No timber mark rows in those files.")
            return

        self.rows = ptm.union(rows)
        self.ex_tree.delete(*self.ex_tree.get_children())
        for r in report:
            self.ex_tree.insert("", "end", values=(
                r["file"], r["sheet"], r["rows"], r["marks"],
                r["stray_columns"] or ""))
        marks = {r["TIMBER_MARK"] for r in self.rows}
        self.ex_lbl.config(text="{} files  ·  {} rows unioned  ·  {} marks".format(
            len(report), len(self.rows), len(marks)))

        self.log("\nloaded {} files from {}".format(len(report), d))
        self.log("  {} rows total, {} after deduplicating on (mark, PID)".format(
            len(rows), len(self.rows)))
        self.log("  {} distinct marks".format(len(marks)))
        dis = Counter(r.get("ORG_UNIT_CODE", "") for r in self.rows)
        self.log("  districts: " + ", ".join(
            "{} {}".format(k, v) for k, v in dis.most_common(8)))
        if any(r["stray_columns"] for r in report):
            self.log("  note: some files carry stray trailing columns - "
                     "stripped on load")
        sheets = {r["sheet"] for r in report}
        if len(sheets) > 1:
            self.log("  note: data sheets are not consistently named ({}), "
                     "so the filename is not a reliable period label".format(
                         ", ".join(sorted(sheets))))

    # ----------------------------------------------------------------- marks

    def _set_marks(self, marks, note):
        present = {r["TIMBER_MARK"] for r in self.rows}
        found = [m for m in marks if m in present]
        missing = [m for m in marks if m not in present]
        self.wanted = found
        self.mk_lbl.config(text="{} of {} present in the extracts".format(
            len(found), len(marks)))
        self.log("\n{}: {} of {} present".format(note, len(found), len(marks)))
        if missing:
            self.log("  not in the extracts: " + ", ".join(missing[:14])
                     + (" …" if len(missing) > 14 else ""))
        if found:
            pids = set()
            for r in self.rows:
                if r["TIMBER_MARK"] in found:
                    p, _n = ptm.parse_pids(r.get("PID"))
                    pids.update(p)
            self.log("  {} distinct PIDs to look up".format(len(pids)))

    def load_register(self):
        if not self.rows:
            messagebox.showwarning("Load the extracts first",
                                   "Load the ministry folder before choosing "
                                   "marks.")
            return
        path = filedialog.askopenfilename(
            title="HARP supplier register",
            filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")])
        if not path:
            return
        try:
            marks = ptm.marks_from_register(path, self.klass.get().strip() or None)
        except SystemExit as exc:
            messagebox.showerror("Could not read that register", str(exc))
            return
        self._set_marks(marks, "from register, class {}".format(
            self.klass.get().strip() or "all"))

    def use_typed(self):
        if not self.rows:
            messagebox.showwarning("Load the extracts first", "")
            return
        raw = self.paste.get("1.0", "end")
        marks = [m.strip().upper() for m in
                 raw.replace("\n", ",").replace(" ", ",").split(",") if m.strip()]
        if not marks:
            messagebox.showwarning("Nothing typed", "")
            return
        self._set_marks(list(dict.fromkeys(marks)), "typed")

    def use_all(self):
        if not self.rows:
            messagebox.showwarning("Load the extracts first", "")
            return
        marks = sorted({r["TIMBER_MARK"] for r in self.rows})
        self._set_marks(marks, "all marks in the extracts")

    def clear_marks(self):
        self.wanted = []
        self.mk_lbl.config(text="")
        self.tree.delete(*self.tree.get_children())
        self.features, self.results = [], []
        self.btn_write.config(state="disabled")

    # ------------------------------------------------------------------- run

    def do_run(self):
        if not self.wanted:
            messagebox.showwarning("No marks chosen",
                                   "Choose some marks to resolve first.")
            return
        wanted = list(self.wanted)
        rows = list(self.rows)
        self.tree.delete(*self.tree.get_children())
        self.features, self.results, self.questions = [], [], []

        def work():
            field = ptm.pid_field()
            if not field:
                self.log("ParcelMap layer 218 did not return a PID field - "
                         "cannot query. The service may be down.")
                return
            self.log("\nParcelMap PID field: {}".format(field))

            by_mark: dict[str, dict] = {}
            for r in rows:
                m = r["TIMBER_MARK"]
                if m not in wanted:
                    continue
                pids, note = ptm.parse_pids(r.get("PID"))
                if note and not pids:
                    self.questions.append({
                        "kind": "unparsable PID", "timber_mark": m,
                        "value": r.get("PID"), "note": note})
                e = by_mark.setdefault(m, {"pids": [], "districts": set(),
                                           "legals": []})
                e["pids"].extend(pids)
                if r.get("ORG_UNIT_CODE"):
                    e["districts"].add(r["ORG_UNIT_CODE"])
                if r.get("LEGAL"):
                    e["legals"].append(r["LEGAL"])

            all_pids = sorted({p for e in by_mark.values() for p in e["pids"]})
            self.log("{} marks  ·  {} distinct PIDs  ·  {} unparsable "
                     "cells".format(len(by_mark), len(all_pids),
                                    len(self.questions)))
            self.log("fetching parcels…")

            feats, not_found = ptm.fetch_parcels(all_pids, log=self.log)
            import re as _re
            by_pid = {_re.sub(r"[^0-9]", "",
                              str((f.get("properties") or {}).get(field, "")))
                      .zfill(9): f for f in feats}
            self.log("{} of {} PIDs returned a parcel".format(
                len(by_pid), len(all_pids)))
            for p in not_found:
                self.questions.append({"kind": "PID not in ParcelMap",
                                       "timber_mark": "", "value": p,
                                       "note": "parsed but no parcel returned"})

            for m in sorted(by_mark):
                e = by_mark[m]
                pids = sorted(set(e["pids"]))
                got = [by_pid[p] for p in pids if p in by_pid]
                total = round(sum(ptm.area_ha(f) for f in got), 1)
                districts = ",".join(sorted(e["districts"]))
                legal = e["legals"][0] if e["legals"] else ""

                for f in got:
                    props = dict(f.get("properties") or {})
                    props.update({
                        "harp_timber_mark": m,
                        "harp_pid": str(props.get(field, "")),
                        "harp_source": ("PMBC parcel via ministry "
                                        "scaled-timbermark extract"),
                        "harp_geometry_means": ("titled parcel the timber was "
                                                "scaled from - not the cut "
                                                "block boundary"),
                        "harp_districts": districts,
                        "harp_retrieved": datetime.now().isoformat(
                            timespec="seconds"),
                        "harp_licence": ptm.ATTRIBUTION,
                    })
                    self.features.append({"type": "Feature",
                                          "geometry": f.get("geometry"),
                                          "properties": props})

                row = {"timber_mark": m, "pids": len(pids),
                       "parcels_found": len(got),
                       "parcels_missing": len(pids) - len(got),
                       "area_ha": total, "districts": districts,
                       "example_legal": legal}
                self.results.append(row)
                tag = ("ok" if len(got) == len(pids) and got else
                       "none" if not got else "partial")
                self.after(0, self.tree.insert, "", "end", {
                    "values": (m, len(pids), len(got), len(pids) - len(got),
                               "{:,.1f}".format(total) if total else "",
                               districts, legal[:120]),
                    "tags": (tag,)})
                self.after(0, self.status.config,
                           {"text": "{} of {} marks".format(
                               len(self.results), len(by_mark))})
                self.log("  {:<10} {:>4} PIDs  {:>4} parcels  {:>10,.1f} ha  "
                         "{}".format(m, len(pids), len(got), total, districts))

            total_ha = round(sum(r["area_ha"] for r in self.results), 1)
            self.log("\n" + "-" * 62)
            self.log("{} marks  ·  {} parcels  ·  {:,.0f} ha of titled "
                     "land".format(len(self.results), len(self.features),
                                   total_ha))
            self.log("That area is the land the timber was scaled from. The "
                     "harvest is somewhere inside it - treat it as an upper "
                     "bound, not a boundary.")
            if self.questions:
                self.log("{} items need a look - see the questions file "
                         "on write".format(len(self.questions)))
            self.log("-" * 62)

        self.run_bg(work)

    # ---------------------------------------------------------------- output

    def do_write(self):
        if not self.features:
            return
        outdir = self.outdir.get().strip()
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Bad output folder", str(exc))
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        gj = {"type": "FeatureCollection", "name": "ptm_parcels",
              "metadata": {
                  "generated": datetime.now().isoformat(timespec="seconds"),
                  "marks": len(self.results), "parcels": len(self.features),
                  "licence": ptm.ATTRIBUTION,
                  "note": ("A parcel is the titled land the timber was scaled "
                           "from. The harvest is somewhere inside it. Do not "
                           "present this as a harvest boundary.")},
              "features": self.features}
        gpath = os.path.join(outdir, "ptm_parcels_{}.geojson".format(stamp))
        with open(gpath, "w", encoding="utf-8") as fh:
            json.dump(gj, fh)

        import csv
        spath = os.path.join(outdir, "ptm_summary_{}.csv".format(stamp))
        with open(spath, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.results[0].keys()))
            w.writeheader(); w.writerows(self.results)

        self.log("\nwrote {}".format(gpath))
        self.log("wrote {}".format(spath))

        if self.questions:
            qpath = os.path.join(outdir, "ptm_questions_{}.csv".format(stamp))
            with open(qpath, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=["kind", "timber_mark",
                                                   "value", "note"])
                w.writeheader(); w.writerows(self.questions)
            self.log("wrote {}".format(qpath))
        self.status.config(text="written")


if __name__ == "__main__":
    App().mainloop()
