"""Run manifest and rejects.

Two rules that everything else in HARP depends on:

  1. Every run writes a manifest row. What ran, when, against what, how many
     records in and out. This is what makes a pipeline replayable three years
     later when someone challenges a polygon.

  2. Nothing is ever dropped silently. If a record does not make it through,
     it lands in rejects with a reason. The existing NGIS geofence join drops
     unmatched polygons with no error and no log - we are not repeating that.

Both write to files now and can move to BigQuery later without changing callers.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import io
from .config import Config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Run:
    """One execution of one step."""

    def __init__(self, cfg: Config, step: str, params: dict[str, Any] | None = None):
        self.cfg = cfg
        self.step = step
        self.params = params or {}
        self.run_id = f"{step}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.started = _now()
        self.rows_in = 0
        self.rows_out = 0
        self.rejects: list[dict] = []
        self.notes: list[str] = []
        self.outputs: list[str] = []

    # ---------------------------------------------------------------- record

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def output(self, path: str) -> None:
        self.outputs.append(path)

    def reject(self, record: Any, reason: str, **extra: Any) -> None:
        """Refuse a record, loudly and on the record."""
        self.rejects.append({
            "run_id": self.run_id,
            "step": self.step,
            "reason": reason,
            "record": record,
            **extra,
        })

    # ----------------------------------------------------------------- write

    def finish(self, status: str = "ok", error: str | None = None) -> dict:
        row = {
            "run_id": self.run_id,
            "client": self.cfg.client,
            "environment": self.cfg.environment,
            "step": self.step,
            "params": self.params,
            "started": self.started,
            "finished": _now(),
            "status": status,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_rejected": len(self.rejects),
            "outputs": self.outputs,
            "notes": self.notes,
            "error": error,
        }

        io.append_jsonl(f"{self.cfg.paths.manifest}/manifest.jsonl", row)

        if self.rejects:
            path = f"{self.cfg.paths.rejects}/{self.run_id}.jsonl"
            io.write_jsonl(path, self.rejects)
            row["rejects_file"] = path

        return row


@contextmanager
def run(cfg: Config, step: str, **params: Any) -> Iterator[Run]:
    """Wrap a step so the manifest is written whatever happens.

        with manifest.run(cfg, "ften-pull", client="00158809") as r:
            r.rows_in = len(marks)
            ...
            r.rows_out = len(features)
    """
    r = Run(cfg, step, params)
    try:
        yield r
    except Exception as exc:
        r.finish(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    else:
        r.finish(status="ok")


def history(cfg: Config, limit: int = 20) -> list[dict]:
    """Recent runs, newest first."""
    import json
    path = f"{cfg.paths.manifest}/manifest.jsonl"
    if not io.exists(path):
        return []
    with io.open_text(path) as fh:
        rows = [json.loads(line) for line in fh.read().splitlines() if line.strip()]
    return list(reversed(rows))[:limit]
