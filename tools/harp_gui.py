#!/usr/bin/env python3
"""HARP — a window for the whole thing.

    python tools/harp_gui.py

Four tabs, in the order the work happens:

    The month     a client drop in, a harvest collection out
    Library       take a month through validation and onto the shelf
    Lots          walk a production lot back to the ground it came from
    Setup         config, paths, and what is installed

The older per-stage tabs are gone. They existed when the pipeline was four
separate commands; it is one now, and a tab per internal stage was a menu of
things nobody chose. What they were for - seeing where a run stopped - the
progress lamps do better.
"""

from __future__ import annotations

import glob
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from collections import Counter
from datetime import date, datetime, timedelta

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import harp                                                    # noqa: E402
from harp import (assemble, config, detect as detect_stage,    # noqa: E402
                  detection_api, io, library as library_stage,
                  eudr_schema, lots as lots_stage, mills as mills_mod,
                  package,
                  run as run_stage)
from harp.resolution import Tier                               # noqa: E402

MUTED = "#5F6368"
GOOD = "#137333"
WARN = "#B06000"
BAD = "#A50E0E"
SETTINGS = os.path.join(os.path.expanduser("~"), ".harp_gui.json")

TIER_COLOUR = {"P1a": GOOD, "P1b": WARN, "P1c": GOOD, "P2a": WARN,
               "P2b": GOOD, "P3a": WARN, "P3b": WARN, "P4": BAD}

# The stages, in order. Two are placeholders: validation and cleaning happen
# in the Library tab rather than here, and showing them greyed is more honest
# than leaving them off and adding them silently later.
STAGES = [
    ("sort",     "Sort",         "files read by their columns"),
    ("resolve",  "Resolve",      "every source down the ladder"),
    ("search",   "Search areas", "for whatever did not resolve"),
    ("split",    "Split",        "harvest, tenure, search"),
    ("union",    "Union",        "one polygon to submit"),
    ("detect",   "Detect",       "submit and wait"),
    ("enrich",   "Join back",    "attribution recovered"),
    ("write",    "The month",    "one collection"),
    ("eudr",     "EUDR fields",  "added, not substituted"),
    ("validate", "Validate",     "eudr_geojson, then clean"),
    ("stage",    "Stage",        "pending, awaiting approval"),
]

LAMP = {"idle": ("#D8D8D6", "#B0B0AE"), "running": ("#F0B429", "#C08A15"),
        "done": ("#4E9B84", "#3A7A66"), "empty": ("#C9A227", "#9C7C1A"),
        "failed": ("#C4705A", "#9C5546"), "skipped": ("#EDEDEB", "#CFCFCC")}


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("HARP — Harvest Area Resolution Pipeline")
        self.geometry("1280x900")
        self.minsize(1040, 720)
        self.busy = False
        self.msgs: queue.Queue = queue.Queue()
        self.cfg = None

        self.config_name = tk.StringVar(value="harmac-dev")
        self.drop_dir = tk.StringVar(value="")
        self.register_file = tk.StringVar(value="")
        self.mills_file = tk.StringVar(value="")
        self.month = tk.StringVar(value="")
        self.max_block = tk.StringVar(value="2000")
        self.api_base = tk.StringVar(value=detection_api.DEFAULT_BASE)
        self.library_dir = tk.StringVar(value="")
        self.who = tk.StringVar(value=os.environ.get("USERNAME")
                                or os.environ.get("USER") or "")
        self.lot_file = tk.StringVar(value="")
        self.deliveries_file = tk.StringVar(value="")
        self.lot_filter = tk.StringVar(value="")

        self._build()
        self._load_settings()
        self._reload_config()
        if self.drop_dir.get().strip() and os.path.isdir(self.drop_dir.get()):
            self.read_drop(self.drop_dir.get().strip())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(120, self._drain)

    # ───────────────────────────────────────────────────────── chrome

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=14, pady=(10, 4))
        ttk.Label(top, text="HARP",
                  font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(top, text="harvest area resolution",
                  foreground=MUTED).pack(side="left", padx=10)
        ttk.Label(top, text="config").pack(side="left", padx=(24, 4))
        box = ttk.Combobox(top, textvariable=self.config_name, width=18,
                           values=self._configs())
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda _e: self._reload_config())
        self.cfg_note = ttk.Label(top, text="", foreground=MUTED)
        self.cfg_note.pack(side="left", padx=10)
        ttk.Label(top, text="harp " + harp.__version__,
                  foreground=MUTED).pack(side="right")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=4)
        tabs = {}
        for key, label in (("month", "  The month  "),
                           ("library", "  Library  "),
                           ("lots", "  Lots  "),
                           ("setup", "  Setup  ")):
            tabs[key] = ttk.Frame(self.nb)
            self.nb.add(tabs[key], text=label)
        self._build_month(tabs["month"])
        self._build_library(tabs["library"])
        self._build_lots(tabs["lots"])
        self._build_setup(tabs["setup"])

        lf = ttk.LabelFrame(self, text="Log")
        lf.pack(fill="both", expand=False, padx=14, pady=(0, 4))
        self.txt = tk.Text(lf, height=11, font=("Consolas", 9), wrap="word")
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.config(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True, padx=(6, 0),
                      pady=6)
        sb.pack(side="left", fill="y", padx=(0, 6), pady=6)

        self.status = ttk.Label(self, text="ready", foreground=MUTED,
                                anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 8))

    def _configs(self):
        d = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "harp", "configs")
        return sorted(os.path.splitext(f)[0] for f in os.listdir(d)
                      if f.endswith(".yaml")) if os.path.isdir(d) else []

    # ────────────────────────────────────────────────────── the month

    def _build_month(self, parent):
        pad = dict(padx=12, pady=6)
        g = ttk.LabelFrame(parent, text="Inputs")
        g.pack(fill="x", **pad)
        for label, var, cmd, hint in (
                ("Client drop", self.drop_dir, self.pick_drop,
                 "the folder they sent"),
                ("Supplier register", self.register_file, self.pick_register,
                 "says who still needs a search area"),
                ("Mill locations", self.mills_file, self.pick_mills,
                 "optional — Find mills makes it")):
            r = ttk.Frame(g)
            r.pack(fill="x", padx=10, pady=3)
            ttk.Label(r, text=label, width=17).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x",
                                                expand=True, padx=6)
            ttk.Button(r, text="…", width=3, command=cmd).pack(side="left")
            ttk.Label(r, text=hint, foreground=MUTED,
                      width=36).pack(side="left", padx=6)

        self.drop_note = ttk.Label(g, text="", foreground=MUTED)
        self.drop_note.pack(anchor="w", padx=10, pady=(2, 0))

        r = ttk.Frame(g)
        r.pack(fill="x", padx=10, pady=(6, 10))
        ttk.Label(r, text="Detection window", width=17).pack(side="left")
        ttk.Entry(r, textvariable=self.month, width=12).pack(side="left")
        ttk.Label(r, text="YYYY-MM", foreground=MUTED).pack(side="left",
                                                            padx=6)
        for label, back in (("this", 0), ("last", 1), ("−2", 2), ("−3", 3)):
            ttk.Button(r, text=label, width=6,
                       command=lambda b=back: self.set_month(b)).pack(
                           side="left", padx=2)
        ttk.Label(r, text="blank stops after the split",
                  foreground=MUTED).pack(side="left", padx=12)
        ttk.Label(r, text="blocks over").pack(side="left", padx=(20, 4))
        ttk.Entry(r, textvariable=self.max_block, width=7).pack(side="left")
        ttk.Label(r, text="ha are search areas",
                  foreground=MUTED).pack(side="left", padx=4)

        r = ttk.Frame(parent)
        r.pack(fill="x", **pad)
        self.btn_month = ttk.Button(r, text="Run the month",
                                    command=self.do_month)
        self.btn_month.pack(side="left")
        ttk.Button(r, text="Find mills",
                   command=self.do_mills).pack(side="left", padx=8)
        ttk.Button(r, text="Open outbox",
                   command=lambda: self.open_dir(
                       self.cfg.paths.outbox if self.cfg else "")).pack(
                           side="left", padx=4)

        g2 = ttk.LabelFrame(parent, text="Progress")
        g2.pack(fill="x", **pad)
        tree = ttk.Frame(g2)
        tree.pack(fill="x", padx=10, pady=10)
        self.lamps = {}
        for key, label, hint in STAGES:
            col = ttk.Frame(tree)
            col.pack(side="left", fill="y", padx=(0, 6))
            c = tk.Canvas(col, width=118, height=16, highlightthickness=0,
                          bg=self["bg"])
            c.pack()
            dot = c.create_oval(52, 3, 66, 17, fill=LAMP["idle"][0],
                                outline=LAMP["idle"][1])
            ttk.Label(col, text=label, font=("Segoe UI", 8, "bold"),
                      foreground=MUTED, width=16,
                      anchor="center").pack(pady=(2, 0))
            note = ttk.Label(col, text=hint, font=("Segoe UI", 7),
                             foreground=MUTED, width=19, anchor="center")
            note.pack()
            self.lamps[key] = {"c": c, "dot": dot, "note": note, "hint": hint}

        self.bar = ttk.Progressbar(parent, mode="indeterminate")
        self.bar.pack(fill="x", padx=12, pady=(0, 6))

        g3 = ttk.LabelFrame(parent, text="What came out")
        g3.pack(fill="both", expand=True, **pad)
        cards = ttk.Frame(g3)
        cards.pack(fill="x", padx=10, pady=10)
        self.cards = {}
        for key, label in (("sources", "Sources"), ("detections", "Detections"),
                           ("harvest", "Harvest areas"), ("direct", "Direct"),
                           ("indirect", "Indirect"), ("inferred", "Inferred")):
            f = ttk.Frame(cards, relief="solid", borderwidth=1)
            f.pack(side="left", padx=(0, 8), ipadx=13, ipady=7)
            ttk.Label(f, text=label, foreground=MUTED,
                      font=("Segoe UI", 8)).pack()
            lbl = ttk.Label(f, text="—", font=("Segoe UI", 15))
            lbl.pack()
            self.cards[key] = lbl
        self.month_note = ttk.Label(g3, text="", foreground=MUTED,
                                    wraplength=1120, justify="left")
        self.month_note.pack(anchor="w", padx=10, pady=(0, 6))

        tf = ttk.Frame(g3)
        tf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.files = ttk.Treeview(tf, columns=("f",), show="headings",
                                  height=5)
        self.files.heading("f", text="Written")
        self.files.column("f", width=1000, anchor="w")
        sb = ttk.Scrollbar(tf, command=self.files.yview)
        self.files.config(yscrollcommand=sb.set)
        self.files.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

    # ───────────────────────────────────────────────────────── library

    def _build_library(self, parent):
        pad = dict(padx=12, pady=6)
        ttk.Label(parent, text="A month is validated, cleaned and validated "
                              "again. If it comes out clean it waits in "
                              "pending for you; if Required findings remain "
                              "it goes to quarantine, which needs hands "
                              "rather than another pass. Nothing is declared "
                              "from either until it is on the shelf.",
                  foreground=MUTED, wraplength=1160,
                  justify="left").pack(anchor="w", padx=16, pady=(10, 2))
        ttk.Label(parent, text="The deliverable carries only ProducerName, "
                               "ProducerCountry, ProductionPlace and Area. "
                               "Everything else the pipeline knows stays on "
                               "the shelf.",
                  foreground=MUTED, wraplength=1160,
                  justify="left").pack(anchor="w", padx=16, pady=(0, 4))

        g = ttk.LabelFrame(parent, text="Shelf")
        g.pack(fill="x", **pad)
        r = ttk.Frame(g)
        r.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(r, text="Location", width=12).pack(side="left")
        ttk.Entry(r, textvariable=self.library_dir).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(r, text="…", width=3,
                   command=self.pick_library).pack(side="left")
        ttk.Button(r, text="Refresh",
                   command=self.refresh_library).pack(side="left", padx=6)
        ttk.Button(r, text="Open",
                   command=lambda: self.open_dir(
                       self.library_dir.get())).pack(side="left")

        tf = ttk.Frame(g)
        tf.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        cols = ("month", "state", "features", "passes", "findings", "approved")
        self.shelf = ttk.Treeview(tf, columns=cols, show="headings", height=9)
        for c, h, w in zip(cols, ("Month", "State", "Features", "Clean passes",
                                  "Findings left", "Approved by"),
                           (100, 110, 100, 110, 110, 160)):
            self.shelf.heading(c, text=h)
            self.shelf.column(c, width=w,
                              anchor="e" if c in ("features", "passes",
                                                  "findings") else "w")
        sb = ttk.Scrollbar(tf, command=self.shelf.yview)
        self.shelf.config(yscrollcommand=sb.set)
        self.shelf.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for state, colour in (("pending", WARN), ("quarantine", BAD),
                              ("library", GOOD)):
            self.shelf.tag_configure(state, foreground=colour)

        g2 = ttk.LabelFrame(parent, text="Get a month onto the shelf")
        g2.pack(fill="x", **pad)
        r = ttk.Frame(g2)
        r.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(r, text="Month", width=12).pack(side="left")
        self.lib_month = tk.StringVar(value="")
        ttk.Entry(r, textvariable=self.lib_month, width=12).pack(side="left")
        ttk.Label(r, text="YYYY-MM", foreground=MUTED).pack(side="left",
                                                            padx=6)
        ttk.Label(r, text="Approved by").pack(side="left", padx=(24, 4))
        ttk.Entry(r, textvariable=self.who, width=16).pack(side="left")

        r = ttk.Frame(g2)
        r.pack(fill="x", padx=10, pady=(4, 10))
        self.btn_build = ttk.Button(r, text="Validate and clean",
                                    command=self.do_library_build)
        self.btn_build.pack(side="left")
        self.btn_promote = ttk.Button(r, text="Approve and shelve",
                                      command=self.do_promote)
        self.btn_promote.pack(side="left", padx=8)
        self.btn_deliver = ttk.Button(r, text="Build the deliverable",
                                      command=self.do_deliver)
        self.btn_deliver.pack(side="left", padx=8)
        self.force = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="even with findings outstanding",
                        variable=self.force).pack(side="left", padx=6)
        self.lib_note = ttk.Label(parent, text="", foreground=MUTED,
                                  wraplength=1160, justify="left")
        self.lib_note.pack(anchor="w", padx=16, pady=(0, 8))

    # ──────────────────────────────────────────────────────────── lots

    def _build_lots(self, parent):
        pad = dict(padx=12, pady=6)
        ttk.Label(parent, text="A lot's chips arrived over the preceding "
                              "weeks, already mixed. The walkback goes back "
                              "from the production date, accumulating by "
                              "species, until twice the lot's requirement is "
                              "covered — and every supplier in that window is "
                              "declared.",
                  foreground=MUTED, wraplength=1160,
                  justify="left").pack(anchor="w", padx=16, pady=(10, 2))

        g = ttk.LabelFrame(parent, text="Inputs")
        g.pack(fill="x", **pad)
        for label, var, cmd, hint in (
                ("Lot list", self.lot_file, self.pick_lots,
                 "production lots for a month"),
                ("Deliveries", self.deliveries_file, self.pick_deliveries,
                 "the load summary covering the period")):
            r = ttk.Frame(g)
            r.pack(fill="x", padx=10, pady=3)
            ttk.Label(r, text=label, width=12).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x",
                                                expand=True, padx=6)
            ttk.Button(r, text="…", width=3, command=cmd).pack(side="left")
            ttk.Label(r, text=hint, foreground=MUTED,
                      width=36).pack(side="left", padx=6)
        ttk.Label(g, text="Both are filled in from the drop when you choose "
                          "one on the first tab.",
                  foreground=MUTED).pack(anchor="w", padx=10, pady=(2, 0))
        r = ttk.Frame(g)
        r.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Label(r, text="Only lot", width=12).pack(side="left")
        ttk.Entry(r, textvariable=self.lot_filter, width=28).pack(side="left")
        ttk.Label(r, text="one id, or several comma separated — blank does "
                          "all of them", foreground=MUTED).pack(side="left",
                                                                padx=8)

        r = ttk.Frame(parent)
        r.pack(fill="x", **pad)
        self.btn_walk = ttk.Button(r, text="Walk back",
                                   command=lambda: self.do_lots(False))
        self.btn_walk.pack(side="left")
        self.btn_pkg = ttk.Button(r, text="Walk back and build packages",
                                  command=lambda: self.do_lots(True))
        self.btn_pkg.pack(side="left", padx=8)
        ttk.Label(r, text="packages need the months on the shelf",
                  foreground=MUTED).pack(side="left", padx=8)

        g2 = ttk.LabelFrame(parent, text="Lots")
        g2.pack(fill="both", expand=True, **pad)
        tf = ttk.Frame(g2)
        tf.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("lot", "adt", "chips", "loads", "sup", "back", "months", "ok")
        self.lot_tree = ttk.Treeview(tf, columns=cols, show="headings",
                                     height=12)
        for c, h, w in zip(cols, ("Lot", "Pulp Adt", "Chips BDT at 200%",
                                  "Loads", "Suppliers", "Days back",
                                  "Months touched", "Covered"),
                           (110, 90, 140, 80, 90, 90, 150, 90)):
            self.lot_tree.heading(c, text=h)
            self.lot_tree.column(c, width=w,
                                 anchor="w" if c in ("lot", "months", "ok")
                                 else "e")
        sb = ttk.Scrollbar(tf, command=self.lot_tree.yview)
        self.lot_tree.config(yscrollcommand=sb.set)
        self.lot_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.lot_tree.tag_configure("short", foreground=BAD)
        self.lot_tree.bind("<<TreeviewSelect>>", self._lot_selected)

        self.lot_note = ttk.Label(g2, text="", foreground=MUTED,
                                  wraplength=1160, justify="left")
        self.lot_note.pack(anchor="w", padx=10, pady=(0, 8))
        self._walks = {}

    def _lot_selected(self, _e=None):
        sel = self.lot_tree.selection()
        if not sel:
            return
        w = self._walks.get(self.lot_tree.item(sel[0])["values"][0])
        if not w:
            return
        top = sorted(w.suppliers.items(), key=lambda kv: -kv[1])[:6]
        self.lot_note.config(text="{}: {}".format(
            w.lot.lot_id,
            ",  ".join("{} {:,.0f} BDT".format(c, b) for c, b in top)))

    # ─────────────────────────────────────────────────────────── setup

    def _build_setup(self, parent):
        pad = dict(padx=12, pady=6)
        g = ttk.LabelFrame(parent, text="Paths")
        g.pack(fill="x", **pad)
        self.paths = ttk.Treeview(g, columns=("k", "v"), show="headings",
                                  height=7)
        for c, h, w in (("k", "What", 160), ("v", "Where", 900)):
            self.paths.heading(c, text=h)
            self.paths.column(c, width=w, anchor="w")
        self.paths.pack(fill="x", padx=10, pady=10)

        g2 = ttk.LabelFrame(parent, text="Components")
        g2.pack(fill="both", expand=True, **pad)
        self.deps = ttk.Treeview(g2, columns=("n", "s", "w"), show="headings",
                                 height=9)
        for c, h, w in (("n", "Component", 200), ("s", "State", 140),
                        ("w", "What it is for", 640)):
            self.deps.heading(c, text=h)
            self.deps.column(c, width=w, anchor="w")
        self.deps.pack(fill="both", expand=True, padx=10, pady=10)
        self.deps.tag_configure("ok", foreground=GOOD)
        self.deps.tag_configure("no", foreground=BAD)
        ttk.Button(parent, text="Re-check",
                   command=self.refresh_setup).pack(anchor="w", padx=16,
                                                    pady=(0, 10))

    # ───────────────────────────────────────────────────────── plumbing

    def log(self, msg=""):
        self.msgs.put(str(msg))

    def _drain(self):
        try:
            while True:
                self.txt.insert("end", self.msgs.get_nowait() + "\n")
                self.txt.see("end")
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def lamp(self, key, state, note=""):
        def apply():
            l = self.lamps.get(key)
            if not l:
                return
            fill, outline = LAMP.get(state, LAMP["idle"])
            l["c"].itemconfig(l["dot"], fill=fill, outline=outline)
            l["note"].config(text=note or (l["hint"] if state == "idle" else ""))
        self.after(0, apply)

    def reset_lamps(self):
        for key, _l, hint in STAGES:
            self.lamp(key, "idle", hint)

    def set_busy(self, on, note=""):
        self.busy = on
        state = "disabled" if on else "normal"
        for name in ("btn_month", "btn_build", "btn_promote", "btn_deliver",
                     "btn_walk", "btn_pkg"):
            if hasattr(self, name):
                getattr(self, name).config(state=state)
        self.status.config(text=note or ("working…" if on else "ready"))
        if on:
            self.bar.start(12)
        else:
            self.bar.stop()

    def run_bg(self, fn, note=""):
        if self.busy:
            return
        self.set_busy(True, note)

        def wrap():
            try:
                fn()
            except Exception as exc:
                self.log("")
                self.log("failed: {}".format(exc))
                self.log(traceback.format_exc())
            finally:
                self.after(0, self.set_busy, False)

        threading.Thread(target=wrap, daemon=True).start()

    def open_dir(self, d):
        if not d or not os.path.isdir(d):
            return
        try:
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────── pickers

    def _ask(self, var, title, kind="file", **kw):
        if kind == "dir":
            p = filedialog.askdirectory(title=title)
        else:
            p = filedialog.askopenfilename(title=title, **kw)
        if p:
            var.set(p)
        return p

    def pick_drop(self):
        p = self._ask(self.drop_dir, "The client's monthly drop", "dir")
        if p:
            self.read_drop(p)

    def read_drop(self, folder):
        """Fill in whatever the drop already contains.

        The files are recognised by their columns, so making somebody point
        at a file sitting in the folder they just chose is a step with no
        purpose. Anything already filled in is left alone - a deliberate
        choice beats an automatic one.
        """
        try:
            items = package.sort_package(folder)
        except Exception as exc:
            self.log("could not read that folder: {}".format(exc))
            return
        found = []
        for kind, var, label in (
                ("lot_list", self.lot_file, "lot list"),
                ("delivery_record", self.deliveries_file, "deliveries"),
                ("supplier_register", self.register_file, "supplier register"),
                ("mill_locations", self.mills_file, "mill locations")):
            path = run_stage._first(items, kind)
            if path:
                found.append(label)
                if not var.get().strip():
                    var.set(path)
        missing = [k for k in ("job_list", "delivery_record")
                   if not items.get(k)]
        self.log("")
        self.log("{}: {} file(s)".format(os.path.basename(folder),
                                         sum(len(v) for v in items.values())))
        if found:
            self.log("  found: " + ", ".join(found))
        if missing:
            # The supply list is what a run resolves. Without it there is
            # nothing to do, and saying so now beats saying so after a click.
            self.log("  missing: " + ", ".join(
                {"job_list": "a supply list (SOURCEID, no LOADID)",
                 "delivery_record": "a delivery record (SOURCEID + LOADID)"}[k]
                for k in missing))
        unknown = items.get("unknown") or []
        if unknown:
            self.log("  {} file(s) matched no signature - a finding, not an "
                     "error".format(len(unknown)))
        self.drop_note.config(
            text=("found " + ", ".join(found)) if found else
                 "nothing recognised in that folder",
            foreground=MUTED if found else WARN)

    def pick_register(self):
        self._ask(self.register_file, "Supplier register",
                  filetypes=[("Excel", "*.xlsx"), ("All", "*.*")])

    def pick_mills(self):
        self._ask(self.mills_file, "Mill locations",
                  filetypes=[("CSV", "*.csv"), ("All", "*.*")])

    def pick_library(self):
        self._ask(self.library_dir, "The library", "dir")
        self.refresh_library()

    def pick_lots(self):
        self._ask(self.lot_file, "Production lot list",
                  filetypes=[("Excel", "*.xlsx"), ("All", "*.*")])

    def pick_deliveries(self):
        self._ask(self.deliveries_file, "Load delivery summary",
                  filetypes=[("Excel", "*.xlsx"), ("All", "*.*")])

    def set_month(self, back):
        t = date.today()
        y, m = t.year, t.month - back
        while m < 1:
            m += 12
            y -= 1
        self.month.set("{}-{:02d}".format(y, m))
        self.lib_month.set(self.month.get())

    def _window(self):
        m = self.month.get().strip()
        if not m:
            return "", ""
        try:
            y, mo = (int(x) for x in m.split("-")[:2])
        except ValueError:
            return "", ""
        first = date(y, mo, 1)
        last = date(y + (mo == 12), (mo % 12) + 1, 1) - timedelta(days=1)
        return first.isoformat(), last.isoformat()

    # ──────────────────────────────────────────────────────── the work

    def _reload_config(self):
        from pathlib import Path
        root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            self.cfg = config.load(self.config_name.get(), repo_root=root)
            self.cfg_note.config(text=self.cfg.label, foreground=MUTED)
            if not self.library_dir.get():
                self.library_dir.set(library_stage.settings(self.cfg)["path"])
        except Exception as exc:
            self.cfg = None
            self.cfg_note.config(text=str(exc)[:60], foreground=BAD)
        self.refresh_setup()
        self.refresh_library()

    def refresh_setup(self):
        self.paths.delete(*self.paths.get_children())
        if self.cfg:
            for k in ("inbox", "staging", "outbox", "rejects", "manifest"):
                self.paths.insert("", "end",
                                  values=(k, getattr(self.cfg.paths, k)))
            self.paths.insert("", "end", values=("library",
                                                 self.library_dir.get()))
        self.deps.delete(*self.deps.get_children())
        for name, why in (("requests", "every registry call"),
                          ("shapely", "geometry, unions, spatial joins"),
                          ("pyproj", "area measured on the ellipsoid"),
                          ("pandas", "reading the client's spreadsheets"),
                          ("openpyxl", "the same, for xlsx"),
                          ("eudr_geojson", "validating a month"),
                          ("eudr_clean", "cleaning what fails"),
                          ("bcparcel", "private marks to titled parcels")):
            try:
                __import__(name)
                ok = True
            except ImportError:
                ok = False
            self.deps.insert("", "end",
                             values=(name, "present" if ok else "missing",
                                     why), tags=("ok" if ok else "no",))

    def do_mills(self):
        reg = self.register_file.get().strip()
        if not os.path.isfile(reg):
            messagebox.showwarning("No register", "Choose one first.")
            return

        def work():
            self.log("\nlocating mills…")
            rows, skipped = [], 0
            for name, jur in mills_mod.suppliers_with_jurisdiction(reg, None):
                if not mills_mod.is_bc(jur):
                    # The facility list and district layer are BC only.
                    skipped += 1
                    rows.append({"supplier": name, "facility": "", "city": "",
                                 "latitude": "", "longitude": "",
                                 "district": "", "district_code": "",
                                 "how_established": "not British Columbia "
                                                    "({})".format(jur or "?")})
                    self.log("  {:<32}not BC".format(name[:32]))
                    continue
                fac, how = mills_mod.match_facility(name)
                dist = (mills_mod.district_at(fac["lat"], fac["lon"])
                        if fac else None)
                if not dist and not fac:
                    dist = mills_mod.district_from_name(name)
                    how = "place name in the supplier's own name" if dist \
                        else how
                rows.append({"supplier": name,
                             "facility": fac["label"] if fac else "",
                             "city": fac["city"] if fac else "",
                             "latitude": fac["lat"] if fac else "",
                             "longitude": fac["lon"] if fac else "",
                             "district": dist["name"] if dist else "",
                             "district_code": dist["code"] if dist else "",
                             "how_established": how or "nothing found"})
                self.log("  {:<32}{}".format(name[:32],
                                             dist["code"] if dist else "-"))
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = io.write_csv_dicts(
                "{}/supplier_locations-{}.csv".format(self.cfg.paths.outbox,
                                                     stamp), rows)
            placed = sum(1 for r in rows if r["district_code"])
            bc = len(rows) - skipped
            self.log("\n{} of {} BC supplier(s) placed".format(placed, bc))
            if skipped:
                self.log("  {} outside BC, handled by the US routes".format(
                    skipped))
            if bc - placed:
                self.log("  {} unplaced here - the catchment builder also "
                         "reads the mill town from each source identifier, "
                         "which this pass does not see".format(bc - placed))
            self.log("  " + path)
            self.after(0, self.mills_file.set, path)

        self.run_bg(work, "locating mills…")

    def do_month(self):
        folder = self.drop_dir.get().strip()
        if not os.path.isdir(folder):
            messagebox.showwarning("No drop", "Choose the client's folder.")
            return
        if not self.cfg:
            messagebox.showwarning("No config", "Choose a config first.")
            return
        month = self.month.get().strip()
        start, end = self._window()
        if month and not start:
            messagebox.showwarning("Check the month", "Wants YYYY-MM.")
            return

        try:
            max_block = float(self.max_block.get())
        except ValueError:
            max_block = 2000.0

        self.files.delete(*self.files.get_children())
        for lbl in self.cards.values():
            lbl.config(text="—")
        self.month_note.config(text="", foreground=MUTED)
        self.reset_lamps()
        written = []

        def work():
            try:
                self._month(folder, month, start, end, max_block, written)
            finally:
                def show():
                    for w in written:
                        self.files.insert("", "end", values=(w,))
                self.after(0, show)

        self.run_bg(work, "running the month…")

    def _month(self, folder, month, start, end, max_block, written):
        """One call. The stages, the detection and the staging all happen
        inside run(), which writes them all to the same log."""
        out = run_stage.run(
            self.cfg, folder, month=month, log=self.log, on_stage=self.lamp,
            register=self.register_file.get().strip(),
            mills_csv=self.mills_file.get().strip(),
            max_block_ha=max_block,
            api_base=self.api_base.get().strip())
        written.extend(out.get("written", []))

        if not out.get("ok"):
            for k, _l, _h in STAGES:
                self.lamp(k, "failed")
            self.after(0, lambda: self.month_note.config(
                text="The run did not complete — see the log.",
                foreground=BAD))
            return

        trace = out.get("traceability") or {}
        stopped = out.get("stopped_at", "")
        why = out.get("why", "")
        lib = out.get("library_state", "")

        def done():
            self.cards["sources"].config(
                text="{:,}".format(out.get("sources", 0)))
            self.cards["detections"].config(
                text="{:,}".format(out.get("detections", 0))
                if out.get("detections") else "—")
            self.cards["harvest"].config(
                text="{:,}".format(out.get("month_features", 0))
                if out.get("month_features") else "—")
            for k in ("direct", "indirect", "inferred"):
                self.cards[k].config(
                    text="{:,}".format(trace.get(k, 0)) if trace else "—")

            if stopped == "staged":
                if lib == "pending":
                    txt = ("{:,} harvest area(s) for {}, validated and waiting "
                           "on approval. Go to the Library tab to shelve it."
                           .format(out.get("month_features", 0), month))
                    colour = MUTED
                else:
                    txt = ("{} went to quarantine — Required findings are "
                           "still standing after cleaning. It needs hands on "
                           "it, not another pass.".format(month))
                    colour = WARN
                self.lib_month.set(month)
                self.refresh_library()
            elif stopped == "month written":
                txt = ("{:,} harvest area(s) written but not staged. {}"
                       .format(out.get("month_features", 0), why))
                colour = WARN
            elif stopped == "detection":
                txt = "Stopped at detection: {}".format(why)
                colour = WARN
            elif stopped == "split":
                txt = ("Stopped after the split — {}. The search areas are "
                       "places to look, not answers; nothing here is "
                       "declarable.".format(why))
                colour = MUTED
            else:
                txt = "Stopped at {}: {}".format(stopped, why)
                colour = WARN
            self.month_note.config(text=txt, foreground=colour)
        self.after(0, done)

    # ──────────────────────────────────────────────────── library work

    def refresh_library(self):
        if not hasattr(self, "shelf"):
            return
        self.shelf.delete(*self.shelf.get_children())
        root = self.library_dir.get().strip()
        if not root or not os.path.isdir(root):
            return
        rows = library_stage.months(root)
        for r in sorted(rows, key=lambda x: (x["month"], x["state"]),
                        reverse=True):
            m = r["manifest"] or {}
            self.shelf.insert("", "end", tags=(r["state"],), values=(
                r["month"], r["state"],
                "{:,}".format(r["features"]) if r["features"] else "",
                m.get("clean_passes", ""),
                r["findings"] if r["findings"] else "",
                r["approved_by"] or ""))
        pend = sum(1 for r in rows if r["state"] == "pending")
        quar = sum(1 for r in rows if r["state"] == "quarantine")
        bits = []
        if pend:
            bits.append("{} waiting on approval".format(pend))
        if quar:
            bits.append("{} in quarantine".format(quar))
        self.lib_note.config(text=("  ·  ".join(bits)
                                   + ". Nothing is declared from either."
                                   if bits else ""))

    def do_library_build(self):
        month = self.lib_month.get().strip()
        if not month:
            messagebox.showwarning("No month", "Which month?")
            return
        if not self.cfg:
            return
        opts = library_stage.settings(self.cfg)
        root = self.library_dir.get().strip() or opts["path"]
        src = "{}/harvest-{}.geojson".format(self.cfg.paths.outbox, month)
        if not os.path.isfile(src):
            hits = sorted(glob.glob("{}/harvest-*.geojson".format(
                self.cfg.paths.outbox)))
            if not hits:
                messagebox.showwarning(
                    "Nothing to build from",
                    "No harvest collection in the outbox. Run the month "
                    "first.")
                return
            src = hits[-1]

        def work():
            self.log("\nfrom {}".format(os.path.basename(src)))
            with open(src, encoding="utf-8") as fh:
                feats = json.load(fh).get("features") or []
            library_stage.build(root, month, feats,
                                self.deliveries_file.get().strip(), opts,
                                source_files=[os.path.basename(src)],
                                log=self.log)
            self.after(0, self.refresh_library)

        self.run_bg(work, "validating and cleaning…")

    def do_deliver(self):
        """The four EUDR fields, and nothing else.

        Taken from the shelf rather than the outbox: a file going to a
        customer should come from a month somebody approved, not from
        whatever the last run happened to leave behind.
        """
        month = self.lib_month.get().strip()
        if not month:
            messagebox.showwarning("No month", "Which month?")
            return
        root = self.library_dir.get().strip()
        src = os.path.join(root, month, "harvest.geojson")
        approved = os.path.isfile(src)
        if not approved:
            src = os.path.join(self.cfg.paths.outbox,
                               "harvest-{}.geojson".format(month))
            if not os.path.isfile(src):
                messagebox.showwarning(
                    "Nothing to deliver",
                    "No {} on the shelf and none in the outbox.".format(month))
                return
            if not messagebox.askyesno(
                    "Not approved",
                    "{} is not on the shelf.\n\nBuilding from the outbox "
                    "copy instead. That month has not been approved by "
                    "anybody.\n\nCarry on?".format(month)):
                return

        def work():
            self.log("")
            self.log("from {}{}".format(os.path.basename(src),
                                        "" if approved else "  (NOT APPROVED)"))
            with open(src, encoding="utf-8") as fh:
                feats = json.load(fh).get("features") or []
            view, report = eudr_schema.project(feats, log=self.log)
            path = io.write_json(
                "{}/eudr-{}.geojson".format(self.cfg.paths.outbox, month),
                {"type": "FeatureCollection", "name": "harp_eudr",
                 "features": view["features"]})
            self.log("\n  {}".format(path))
            miss = report.get("missing") or {}

            def done():
                note = "{:,} feature(s) written to eudr-{}.geojson.".format(
                    len(view["features"]), month)
                if miss:
                    note += (" Some carry fewer than four fields — a field is "
                             "omitted rather than sent blank, because a blank "
                             "one fails validation where a missing one only "
                             "warns.")
                if not approved:
                    note = "Built from an unapproved month. " + note
                self.lib_note.config(text=note,
                                     foreground=WARN if not approved else MUTED)
            self.after(0, done)

        self.run_bg(work, "building the deliverable…")

    def do_promote(self):
        month = self.lib_month.get().strip()
        who = self.who.get().strip()
        if not month:
            messagebox.showwarning("No month", "Which month?")
            return
        if not who:
            messagebox.showwarning("Who?", "Say who is approving this.")
            return
        root = self.library_dir.get().strip()
        try:
            library_stage.promote(root, month, who, force=self.force.get(),
                                  log=self.log)
        except RuntimeError as exc:
            messagebox.showwarning("Not promoted", str(exc))
            self.log(str(exc))
        self.refresh_library()

    # ─────────────────────────────────────────────────────── lot work

    def do_lots(self, with_geometry):
        lot_path = self.lot_file.get().strip()
        dels = self.deliveries_file.get().strip()
        if not os.path.isfile(lot_path) or not os.path.isfile(dels):
            messagebox.showwarning("Missing input",
                                   "Choose a lot list and a delivery "
                                   "summary.")
            return
        if not self.cfg:
            return
        self.lot_tree.delete(*self.lot_tree.get_children())
        self._walks = {}

        def work():
            f = lots_stage.factors(self.cfg)
            self.log("")
            all_lots = lots_stage.read_lots(lot_path, log=self.log)
            want = {x.strip().upper()
                    for x in self.lot_filter.get().split(",") if x.strip()}
            if want:
                all_lots = [l for l in all_lots if l.lot_id.upper() in want]
            self.log("")
            deliveries = lots_stage.read_deliveries(dels, log=self.log)

            walks = [lots_stage.walk(l, deliveries, f) for l in all_lots]
            for w in walks:
                self._walks[w.lot.lot_id] = w

            def show():
                for w in walks:
                    self.lot_tree.insert(
                        "", "end", tags=() if w.satisfied else ("short",),
                        values=(w.lot.lot_id, "{:,.0f}".format(w.lot.adt),
                                "{:,.0f}".format(sum(w.required_bdt.values())),
                                "{:,}".format(len(w.deliveries)),
                                len(w.suppliers),
                                "{:.1f}".format(w.days_back),
                                " ".join(sorted(w.months)),
                                "yes" if w.satisfied else "SHORT"))
                short = [w for w in walks if not w.satisfied]
                if short:
                    self.lot_note.config(
                        text="{} lot(s) could not be covered by this delivery "
                             "record. Nothing is declared for those — load "
                             "earlier months.".format(len(short)),
                        foreground=WARN)
                else:
                    self.lot_note.config(
                        text="All covered. Select a lot to see its largest "
                             "contributors.", foreground=MUTED)
            self.after(0, show)

            if not with_geometry:
                return

            root = self.library_dir.get().strip()
            wanted = sorted({m for w in walks for m in w.months})
            self.log("")
            self.log("pulling geometry for {}".format(", ".join(wanted)))
            shelf, missing = {}, []
            for m in wanted:
                try:
                    shelf[m] = library_stage.read_month(root, m, log=self.log)
                except FileNotFoundError as exc:
                    missing.append(str(exc))
            if missing:
                self.log("")
                for msg in missing:
                    self.log("  " + msg)
                self.after(0, lambda: self.lot_note.config(
                    text="Some months are not on the shelf. A lot cannot be "
                         "declared from a month that has not been approved.",
                    foreground=BAD))
                return

            n = 0
            for w in walks:
                if not w.satisfied:
                    continue
                feats = []
                for m in sorted(w.months):
                    for ft in shelf.get(m, []):
                        p = ft.get("properties") or {}
                        sup = p.get("harp_supplier_code") or p.get(
                            "harp_supplier")
                        if sup in w.suppliers:
                            feats.append({"type": "Feature",
                                          "geometry": ft.get("geometry"),
                                          "properties": {
                                              **p, "harp_lot": w.lot.lot_id,
                                              "harp_lot_month": m}})
                if not feats:
                    continue
                path = io.write_json(
                    "{}/lot-{}.geojson".format(self.cfg.paths.outbox,
                                               w.lot.lot_id),
                    {"type": "FeatureCollection", "features": feats,
                     "metadata": {"lot": w.lot.lot_id,
                                  "customer": w.lot.customer,
                                  "months": sorted(w.months),
                                  "suppliers": len(w.suppliers),
                                  "features": len(feats)}})
                self.log("  {:<12}{:>7,} feature(s)".format(w.lot.lot_id,
                                                            len(feats)))
                n += 1
            self.log("\n{} package(s) written".format(n))

        self.run_bg(work, "walking back…")

    # ──────────────────────────────────────────────────────── settings

    def _load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                s = json.load(fh)
        except Exception:
            return
        for k, var in (("config", self.config_name), ("drop", self.drop_dir),
                       ("register", self.register_file),
                       ("mills", self.mills_file),
                       ("library", self.library_dir), ("who", self.who),
                       ("lots", self.lot_file),
                       ("deliveries", self.deliveries_file)):
            if s.get(k):
                var.set(s[k])

    def _close(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump({"config": self.config_name.get(),
                           "drop": self.drop_dir.get(),
                           "register": self.register_file.get(),
                           "mills": self.mills_file.get(),
                           "library": self.library_dir.get(),
                           "who": self.who.get(),
                           "lots": self.lot_file.get(),
                           "deliveries": self.deliveries_file.get()}, fh)
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
