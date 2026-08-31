"""File access that does not care whether it is local or in Cloud Storage.

Everything in HARP reads and writes through here. `./data/inbox/x.csv` and
`gs://harmac-staging/inbox/x.csv` are handled the same way, so no pipeline code
needs to know where it is running.

Backed by fsspec, with gcsfs providing the gs:// implementation.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator

import fsspec


def _fs(path: str):
    return fsspec.core.url_to_fs(path)[0]


def exists(path: str) -> bool:
    return _fs(path).exists(path)


def mkdirs(path: str) -> None:
    fs = _fs(path)
    try:
        fs.makedirs(path, exist_ok=True)
    except (FileExistsError, NotImplementedError):
        pass          # object stores have no real directories


def ls(path: str, pattern: str = "*") -> list[str]:
    fs = _fs(path)
    if not fs.exists(path):
        return []
    return sorted(fs.glob(f"{path.rstrip('/')}/{pattern}"))


@contextmanager
def open_text(path: str, mode: str = "r") -> Iterator[Any]:
    if "w" in mode or "a" in mode:
        parent = path.rsplit("/", 1)[0]
        if parent and parent != path:
            mkdirs(parent)
    with fsspec.open(path, mode, encoding="utf-8", newline="") as fh:
        yield fh


def read_json(path: str) -> Any:
    with open_text(path) as fh:
        return json.load(fh)


def write_json(path: str, obj: Any, indent: int | None = None) -> str:
    with open_text(path, "w") as fh:
        json.dump(obj, fh, indent=indent, default=str)
    return path


def write_jsonl(path: str, rows: list[dict]) -> str:
    with open_text(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    return path


def append_jsonl(path: str, row: dict) -> None:
    """Append one record. Object stores cannot append, so we read-modify-write.

    Fine for a manifest of a few thousand rows. If it ever gets hot, move the
    manifest to BigQuery.
    """
    existing = []
    if exists(path):
        with open_text(path) as fh:
            existing = [line for line in fh.read().splitlines() if line.strip()]
    existing.append(json.dumps(row, default=str))
    with open_text(path, "w") as fh:
        fh.write("\n".join(existing) + "\n")


def read_csv_dicts(path: str) -> list[dict]:
    import csv
    with open_text(path) as fh:
        return list(csv.DictReader(fh))


def write_csv_dicts(path: str, rows: list[dict], fieldnames: list[str] | None = None) -> str:
    import csv
    if not rows:
        rows = []
    fieldnames = fieldnames or (list(rows[0].keys()) if rows else [])
    with open_text(path, "w") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path
