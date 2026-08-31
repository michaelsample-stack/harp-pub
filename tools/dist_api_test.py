#!/usr/bin/env python3
"""Submit a GeoJSON to the harvest detection API and see what comes back.

    python dist_api_test.py

Standalone - no HARP import, nothing to install beyond `requests`.

WHAT IT IS FOR
--------------
Working out what the service actually does, rather than what the spec says it
does. It submits, polls until the job finishes, downloads the result, and
reports what came back - including the awkward cases:

    a file named .geojson that is really a CSV
    a header row with no rows under it
    an empty FeatureCollection
    a job that fails with a message worth reading

Each of those has already happened once, and each looked like something else
at first glance.

THE TWO ENDPOINTS
    POST /upload/process        file, startDate, endDate
    GET  /upload/status/{id}    poll until completed, then a signed URL

The signed URL expires after an hour. Re-polling gets a fresh one, which is
why this never stores it.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests

BASE = "https://harmac-api-test-183302365043.us-west1.run.app"
MUTED = "#5F6368"
GOOD = "#137333"
WARN = "#B06000"
BAD = "#A50E0E"

SETTINGS = os.path.join(os.path.expanduser("~"), ".dist_api_test.json")


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Harvest detection API — test")
        self.geometry("1060x760")
        self.minsize(880, 620)
        self.busy = False
        self.last_job = ""

        today = date.today()
        self.api = tk.StringVar(value=BASE)
        self.infile = tk.StringVar(value="")
        self.outdir = tk.StringVar(value=os.getcwd())
        self.start = tk.StringVar(value=str(today - timedelta(days=730)))
        self.end = tk.StringVar(value=str(today))
        self.jobid = tk.StringVar(value="")

        self._build()
        self._load()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ─────────────────────────────────────────────────────────── ui

    def _build(self):
        pad = dict(padx=12, pady=6)

        head = ttk.Frame(self)
        head.pack(fill="x", padx=14, pady=(12, 2))
        ttk.Label(head, text="Harvest detection API",
                  font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(head, text="submit · poll · read what came back",
                  foreground=MUTED).pack(side="left", padx=12)

        g = ttk.LabelFrame(self, text="Request")
        g.pack(fill="x", **pad)

        r = ttk.Frame(g); r.pack(fill="x", padx=10, pady=(10, 3))
        ttk.Label(r, text="File", width=10).pack(side="left")
        ttk.Entry(r, textvariable=self.infile).pack(side="left", fill="x",
                                                    expand=True, padx=6)
        ttk.Button(r, text="…", width=3, command=self.pick_in).pack(side="left")

        r = ttk.Frame(g); r.pack(fill="x", padx=10, pady=3)
        ttk.Label(r, text="Save to", width=10).pack(side="left")
        ttk.Entry(r, textvariable=self.outdir).pack(side="left", fill="x",
                                                    expand=True, padx=6)
        ttk.Button(r, text="…", width=3,
                   command=self.pick_out).pack(side="left")

        r = ttk.Frame(g); r.pack(fill="x", padx=10, pady=3)
        ttk.Label(r, text="From", width=10).pack(side="left")
        ttk.Entry(r, textvariable=self.start, width=14).pack(side="left")
        ttk.Label(r, text="To").pack(side="left", padx=(16, 4))
        ttk.Entry(r, textvariable=self.end, width=14).pack(side="left")
        ttk.Label(r, text="YYYY-MM-DD", foreground=MUTED).pack(side="left",
                                                               padx=10)
        for label, days in (("last 2 months", 60), ("last year", 365),
                            ("since 2024", 0)):
            ttk.Button(r, text=label, width=13,
                       command=lambda d=days: self.set_window(d)).pack(
                           side="left", padx=3)

        r = ttk.Frame(g); r.pack(fill="x", padx=10, pady=(3, 10))
        ttk.Label(r, text="API", width=10).pack(side="left")
        ttk.Entry(r, textvariable=self.api).pack(side="left", fill="x",
                                                 expand=True, padx=6)

        r = ttk.Frame(self); r.pack(fill="x", **pad)
        self.btn_go = ttk.Button(r, text="Submit and wait", command=self.go)
        self.btn_go.pack(side="left")
        ttk.Label(r, text="job id", foreground=MUTED).pack(side="left",
                                                           padx=(18, 4))
        ttk.Entry(r, textvariable=self.jobid, width=40).pack(side="left")
        self.btn_poll = ttk.Button(r, text="Poll this one",
                                   command=self.poll_only)
        self.btn_poll.pack(side="left", padx=6)
        ttk.Button(r, text="Open folder",
                   command=self.open_out).pack(side="left", padx=6)

        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=14, pady=(0, 4))

        lf = ttk.Frame(self)
        lf.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.txt = tk.Text(lf, font=("Consolas", 9), wrap="word")
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.config(yscrollcommand=sb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for name, colour in (("good", GOOD), ("warn", WARN), ("bad", BAD),
                             ("muted", MUTED)):
            self.txt.tag_configure(name, foreground=colour)

        self.log("Choose a GeoJSON, set a window, and submit.")
        self.log("")
        self.log("The result arrives as a signed URL that expires after an "
                 "hour. Polling again gets a fresh one, so a stale link is "
                 "never a problem.", "muted")

    def set_window(self, days):
        today = date.today()
        self.end.set(str(today))
        self.start.set(str(today - timedelta(days=days)) if days
                       else "2024-01-01")

    def pick_in(self):
        p = filedialog.askopenfilename(
            title="GeoJSON to submit",
            filetypes=[("GeoJSON", "*.geojson *.json"), ("All files", "*.*")])
        if p:
            self.infile.set(p)
            self.describe(p)

    def pick_out(self):
        d = filedialog.askdirectory(title="Where to save the result",
                                    initialdir=self.outdir.get())
        if d:
            self.outdir.set(d)

    def open_out(self):
        import subprocess
        import sys
        d = self.outdir.get()
        if not os.path.isdir(d):
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

    def log(self, msg="", tag=None):
        self.txt.insert("end", str(msg) + "\n", tag or ())
        self.txt.see("end")
        self.update_idletasks()

    def describe(self, path):
        try:
            size = os.path.getsize(path) / (1024 * 1024)
            with open(path, encoding="utf-8") as fh:
                gj = json.load(fh)
            feats = gj.get("features") or []
            self.log("")
            self.log("{}   {:.2f} MB   {:,} feature(s)".format(
                os.path.basename(path), size, len(feats)))
            kinds = {}
            for f in feats:
                g = (f.get("geometry") or {}).get("type", "none")
                kinds[g] = kinds.get(g, 0) + 1
            if kinds:
                self.log("   " + ", ".join("{} {}".format(n, k)
                                           for k, n in kinds.items()), "muted")
        except Exception as exc:
            self.log("could not read that file: {}".format(exc), "bad")

    # ───────────────────────────────────────────────────────── work

    def run_bg(self, fn):
        if self.busy:
            return
        self.busy = True
        self.btn_go.config(state="disabled")
        self.btn_poll.config(state="disabled")
        self.bar.start(12)

        def wrap():
            try:
                fn()
            except Exception as exc:
                self.log("")
                self.log("failed: {}".format(exc), "bad")
                self.log(traceback.format_exc(), "muted")
            finally:
                self.bar.stop()
                self.btn_go.config(state="normal")
                self.btn_poll.config(state="normal")
                self.busy = False

        threading.Thread(target=wrap, daemon=True).start()

    def go(self):
        path = self.infile.get().strip()
        if not os.path.isfile(path):
            messagebox.showwarning("No file", "Choose a GeoJSON to submit.")
            return
        self.txt.delete("1.0", "end")
        self.run_bg(lambda: self._submit_and_wait(path))

    def poll_only(self):
        jid = self.jobid.get().strip()
        if not jid:
            messagebox.showwarning("No job id", "Paste a job id to poll.")
            return
        self.run_bg(lambda: self._wait(jid))

    def _submit_and_wait(self, path):
        base = self.api.get().rstrip("/")
        self.log("")
        self.log("submitting {}".format(os.path.basename(path)))
        self.log("window {} to {}".format(self.start.get(), self.end.get()))
        t0 = time.time()
        with open(path, "rb") as fh:
            r = requests.post(
                base + "/upload/process",
                files={"file": (os.path.basename(path), fh,
                                "application/geo+json")},
                data={"startDate": self.start.get().strip(),
                      "endDate": self.end.get().strip()},
                timeout=300)
        self.log("HTTP {}".format(r.status_code),
                 "good" if r.ok else "bad")
        try:
            body = r.json()
        except ValueError:
            self.log(r.text[:600], "bad")
            return
        self.log(json.dumps(body, indent=2), "muted")
        jid = body.get("jobId")
        if not jid:
            self.log("no jobId in the response", "bad")
            return
        self.jobid.set(jid)
        self._wait(jid, t0)

    def _wait(self, jid, t0=None):
        base = self.api.get().rstrip("/")
        t0 = t0 or time.time()
        self.log("")
        self.log("polling…")
        last = ""
        for attempt in range(240):
            r = requests.get(base + "/upload/status/" + jid, timeout=60)
            try:
                s = r.json()
            except ValueError:
                self.log("status returned non-JSON: " + r.text[:300], "bad")
                return
            state = s.get("status", "?")
            if state != last:
                self.log("  {:>6.1f}s  {}".format(time.time() - t0, state))
                last = state
            if state == "completed":
                # Timing is diagnostic in itself. A job that finishes almost
                # instantly usually found nothing; one that takes ten seconds
                # or more usually found something.
                self.log("")
                self.log("completed in {:.1f}s".format(time.time() - t0),
                         "good")
                urls = s.get("downloadUrls") or []
                if not urls:
                    self.log("completed with no download URL", "warn")
                    return
                self._fetch(urls[0], jid)
                return
            if state == "failed":
                self.log("")
                self.log("the job failed", "bad")
                msg = s.get("errorMessage") or "(no message)"
                self.log("  " + msg, "bad")
                self._explain_failure(msg)
                return
            time.sleep(2)
        self.log("gave up waiting after 8 minutes", "warn")

    def _explain_failure(self, msg: str):
        """Say what a known error actually means.

        These are cheap to add and save an hour each time one recurs.
        """
        m = msg.lower()
        if "db-dtypes" in m:
            self.log("")
            self.log("That is a missing Python package on the service, not a "
                     "problem with the file. google-cloud-bigquery needs "
                     "db-dtypes to read date columns - which is also why an "
                     "earlier result came back with no date field.", "muted")
        elif "timeout" in m or "deadline" in m:
            self.log("")
            self.log("The query ran too long. A smaller area or a narrower "
                     "window would confirm it.", "muted")
        elif "memory" in m or "resources" in m:
            self.log("")
            self.log("The service ran out of room. Worth trying fewer "
                     "features.", "muted")

    def _fetch(self, url, jid):
        out_dir = self.outdir.get().strip() or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        r = requests.get(url, timeout=600)
        raw = r.content
        self.log("")
        self.log("{:,} bytes downloaded".format(len(raw)))

        # The service names everything .geojson. It is not always GeoJSON -
        # a CSV with a `geo` column has come back under that name, and
        # trusting the extension made it look like a parse failure.
        head = raw[:400].decode("utf-8", "replace").lstrip()
        is_json = head.startswith("{") or head.startswith("[")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        ext = "geojson" if is_json else "csv"
        path = os.path.join(out_dir, "detections-{}-{}.{}".format(
            stamp, jid[:8], ext))
        with open(path, "wb") as fh:
            fh.write(raw)

        if is_json:
            self._read_geojson(raw, path)
        else:
            self._read_csv(raw, path)
        self.log("")
        self.log("  " + path)

    def _read_geojson(self, raw, path):
        try:
            gj = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self.log("looked like JSON but would not parse: {}".format(exc),
                     "bad")
            return
        feats = gj.get("features") or []
        self.log("GeoJSON · {:,} feature(s)".format(len(feats)),
                 "good" if feats else "warn")
        if not feats:
            self.log("")
            self.log("An empty collection is a real answer, not an error. "
                     "Either nothing was detected in that window, or the "
                     "detection table does not cover this area.", "muted")
            return
        keys = set()
        for f in feats:
            keys.update((f.get("properties") or {}).keys())
        self.log("properties: " + ", ".join(sorted(keys)))
        self._check_fields(keys)
        self.log("")
        self.log("first feature:")
        self.log(json.dumps(feats[0].get("properties"), indent=2)[:900],
                 "muted")

    def _read_csv(self, raw, path):
        text = raw.decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(text)))
        cols = rows[0].keys() if rows else []
        if not cols:
            first = text.splitlines()[0] if text.strip() else ""
            cols = [c.strip() for c in first.split(",")] if first else []
        self.log("CSV · {:,} row(s)".format(len(rows)),
                 "good" if rows else "warn")
        self.log("columns: " + ", ".join(cols))
        self._check_fields(set(cols))
        if not rows:
            self.log("")
            self.log("A header with no rows under it. The query ran and found "
                     "nothing - which is a coverage or window answer, not a "
                     "failure.", "muted")
            return
        self.log("")
        self.log("first row:")
        for k, v in list(rows[0].items())[:8]:
            self.log("  {:<14} {}".format(k, str(v)[:110]), "muted")
        dates = sorted(r.get("date", "") for r in rows if r.get("date"))
        if dates:
            self.log("")
            self.log("dated {} to {}".format(dates[0][:10], dates[-1][:10]))
        kinds = {}
        for r in rows:
            k = r.get("feature_type") or "unspecified"
            kinds[k] = kinds.get(k, 0) + 1
        if kinds:
            self.log("types: " + ", ".join("{} {}".format(n, k)
                                           for k, n in kinds.items()))

    def _check_fields(self, keys: set):
        """Say what is missing, because two fields carry the whole method."""
        lower = {str(k).lower() for k in keys}
        if not any("date" in k for k in lower):
            self.log("")
            self.log("No date field. Without one there is no way to tell "
                     "whether a harvest falls inside a delivery window, which "
                     "is what ties a production lot to its sources.", "warn")
        if not any("type" in k for k in lower):
            self.log("No feature type. Points and polygons cannot be told "
                     "apart.", "warn")

    # ─────────────────────────────────────────────────────── settings

    def _load(self):
        try:
            with open(SETTINGS, encoding="utf-8") as fh:
                s = json.load(fh)
            for k, var in (("api", self.api), ("out", self.outdir),
                           ("file", self.infile)):
                if s.get(k):
                    var.set(s[k])
        except Exception:
            pass

    def _close(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump({"api": self.api.get(), "out": self.outdir.get(),
                           "file": self.infile.get()}, fh)
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
