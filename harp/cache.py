"""
Cache with a different cadence per kind of fact.

Who holds a timber mark, and whether it sits on Crown or private land, are
registry facts that do not change. Cut block boundaries do. Caching both the
same way either serves stale geometry or hammers a government HTML screen for
answers it already gave.

Negative results expire faster than positive ones: a mark absent today may be
issued next month.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

# seconds; None means never expire
TTL = {
    "hbs": 365 * 24 * 3600,        # registry fact - re-check annually
    "hbs_miss": 30 * 24 * 3600,    # may be issued later
    "schema": 7 * 24 * 3600,       # field names have changed before
    "district": None,              # administrative boundaries are stable
    "geometry": 0,                 # never cached - blocks are added and retired
}


class Cache:
    """File-backed JSON cache. One file per key, namespaced by kind."""

    def __init__(self, root="harp_cache", enabled=True):
        self.root = root
        self.enabled = enabled
        if enabled:
            os.makedirs(root, exist_ok=True)

    def _path(self, kind: str, key: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))
        d = os.path.join(self.root, kind)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, safe[:120] + ".json")

    def get(self, kind: str, key: str) -> Any | None:
        if not self.enabled:
            return None
        ttl = TTL.get(kind, 0)
        if ttl == 0:
            return None
        p = self._path(kind, key)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as fh:
                blob = json.load(fh)
        except Exception:
            return None
        if ttl is not None and time.time() - blob.get("_at", 0) > ttl:
            return None
        return blob.get("value")

    def put(self, kind: str, key: str, value: Any) -> None:
        if not self.enabled or TTL.get(kind, 0) == 0:
            return
        try:
            with open(self._path(kind, key), "w", encoding="utf-8") as fh:
                json.dump({"_at": time.time(), "value": value}, fh)
        except Exception:
            pass    # a cache write failing must never break a run

    def put_raw(self, kind: str, key: str, text: str, ext=".html") -> str:
        """
        Store a raw source record and return its path.

        Retaining the page a verdict came from is what makes that verdict
        auditable rather than merely asserted.
        """
        if not self.enabled:
            return ""
        d = os.path.join(self.root, kind + "_raw")
        os.makedirs(d, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))
        p = os.path.join(d, safe[:120] + ext)
        try:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            return p
        except Exception:
            return ""

    def stats(self) -> dict:
        out = {}
        if not (self.enabled and os.path.isdir(self.root)):
            return out
        for kind in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, kind)
            if os.path.isdir(d):
                out[kind] = len(os.listdir(d))
        return out
