"""Configuration loading.

A HARP run is described entirely by a YAML file. The same code runs locally or
in Cloud Functions - the only difference is which config it is handed.

    harp/configs/harmac-dev.yaml    paths point at ./data
    harp/configs/harmac-prd.yaml    paths point at gs://...

Nothing in the pipeline should branch on "am I local or not". If you find
yourself wanting to, put it in the config instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).parent / "configs"


@dataclass
class Paths:
    inbox: str            # supplier drops, LIMS extracts
    staging: str          # intermediate artefacts
    outbox: str           # finished GeoJSON / sce_base rows
    rejects: str          # anything the pipeline refused
    manifest: str         # run log


@dataclass
class BigQuery:
    project: str | None = None
    dataset: str | None = None
    enabled: bool = False


@dataclass
class Config:
    client: str
    environment: str
    paths: Paths
    bigquery: BigQuery = field(default_factory=BigQuery)
    sources: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.client}-{self.environment}"


def _expand(value: str, root: Path) -> str:
    """Resolve ./relative paths against the repo; leave gs:// alone."""
    value = os.path.expandvars(value)
    if "://" in value:
        return value
    return str((root / value).resolve()) if value.startswith(".") else value


def load(name_or_path: str, repo_root: Path | None = None) -> Config:
    """Load a config by name ('harmac-dev') or by path."""
    repo_root = repo_root or Path.cwd()

    path = Path(name_or_path)
    if not path.exists():
        candidate = CONFIG_DIR / f"{name_or_path}.yaml"
        if not candidate.exists():
            raise FileNotFoundError(
                f"No config '{name_or_path}'. Looked in {CONFIG_DIR}. "
                f"Available: {', '.join(sorted(p.stem for p in CONFIG_DIR.glob('*.yaml')))}"
            )
        path = candidate

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    p = data.get("paths", {})
    paths = Paths(
        inbox=_expand(p.get("inbox", "./data/inbox"), repo_root),
        staging=_expand(p.get("staging", "./data/staging"), repo_root),
        outbox=_expand(p.get("outbox", "./data/outbox"), repo_root),
        rejects=_expand(p.get("rejects", "./data/rejects"), repo_root),
        manifest=_expand(p.get("manifest", "./data/manifest"), repo_root),
    )

    bq_raw = data.get("bigquery", {}) or {}
    bq = BigQuery(
        project=bq_raw.get("project"),
        dataset=bq_raw.get("dataset"),
        enabled=bool(bq_raw.get("enabled", False)),
    )

    return Config(
        client=data.get("client", "unknown"),
        environment=data.get("environment", "dev"),
        paths=paths,
        bigquery=bq,
        sources=data.get("sources", {}) or {},
        raw=data,
    )


def from_environment() -> Config:
    """Used by the Cloud Function shim.

    Reads HARP_CONFIG, or derives one from the GCP project id following the
    ngis-{client}-tms-{env}-{region} convention.
    """
    explicit = os.environ.get("HARP_CONFIG")
    if explicit:
        return load(explicit)

    project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("Set HARP_CONFIG, or run where GOOGLE_CLOUD_PROJECT is set.")

    parts = project.split("-")
    if len(parts) >= 4 and parts[0] == "ngis":
        return load(f"{parts[1]}-{parts[3]}")

    raise RuntimeError(f"Cannot derive a config from project id '{project}'.")
