# HARP — Harvest Area Resolution Pipeline

Resolves harvest area geometry from whatever evidence a supplier can provide — a
timber mark, a coordinate, a catchment boundary, or a shapefile — validates it,
and lands it in TraceMark as `sce_base` rows for EUDR due diligence.

Client-agnostic. **A client is a YAML file, not a code fork.**

---

## Why this exists separately

`tracemark-eo` is a satellite detection engine. HARP is an *acquisition and
resolution* layer that calls it as one of five paths. Keeping them apart means
the BC tenure work is reusable across clients rather than buried in a Harmac
folder.

---

## Entry points

| | |
|---|---|
| `harp resolve ...` | console script, after `pip install -e .` |
| `python -m harp resolve ...` | no install needed, from the repo root |
| `python tools/harp_gui.py` | desktop wrapper — see below |
| `from harp import router` | as a library |

## Desktop wrapper

```bash
python tools/harp_gui.py
```

A window over the library while it is being built out. Three tabs:

Five tabs, in the order a monthly run happens.

**Package** — point at the drop. Files are recognised by their columns, and the
supply list is picked up automatically. A file matching no signature is shown
with the columns it had rather than skipped.

**Resolve** — inputs, options, run. Live tier tallies and a progress bar; rows
appear as each source finishes, colour-coded. Stop works mid-run and partial
results still write. Nothing touches disk until *Write outputs*.

**Results** — filter by tier, search across identifier, supplier, holder and
district, sort by any column. Selecting a row shows every rung tried, hit or
miss, with the query used — the tab for asking "why did that come back P4".
*Probe one identifier* runs the full ladder on any value, whatever its class.

**Outputs** — previous runs, with buttons to open the outbox, rejects and
staging folders.

**Setup** — config paths, and what is installed. `bcparcel` missing means R5b
will not run and private marks stop at the district; the status bar says so.

Everything calls into `harp`. Nothing is reimplemented — two copies of the
ladder is how they drift apart.

## The private mark registry

BC scaled-timbermark extracts link a private mark to the parcels it was scaled
from. ParcelMap BC publishes those parcels. That is the only public route from a
private mark to a specific piece of land rather than a district.

```bash
harp resolve SOURCE.xlsx --private-marks ./data/registry/bc_private_timber_marks
```

It is a **registry, not client data** — the extracts hold 1,907 marks across 23
districts, of which a client uses a few dozen. Parcels are cached per PID and
shared: a mark resolved for one client is free for the next.

**A parcel is a search area, not an answer.** It is the ownership boundary; a
200 ha parcel behind a 12 ha cut over-declares by sixteen times. The harvest
inside it is found by change detection. Until that runs, a parcel result is P3.

Needs `bcparcel`, installed rather than vendored.

## The client's own declaration

A Digital Material Passport is a declaration the client has already filed. HARP
reads the download link out of each, fetches the geometry once, explodes
collections and multiparts, and sorts on area.

```bash
harp resolve SOURCE.xlsx --dmp ./data/inbox/2026-08
```

| | Tier | Where it goes |
|---|---|---|
| Cutblock, under 1,000 ha | P3 | the master collection |
| Regional polygon | P4 | `catchments-*.geojson`, for detection |

It is **not a rung** — a passport has no identifier to key on, so it runs after
the per-source loop. Every feature carries `harp_provenance: client_declaration`
and no `harp_source_id`.

Deduplication is geometric, since a declared polygon has no registry identity: a
declared cutblock with 50% or more of its area inside one we resolved ourselves
is dropped. Ours carries a timber mark and a tenure holder; theirs carries
neither.

Downloads happen once and are never discarded — the source retains DMP data for
roughly a week after delivery.

## The client's own declaration

A Digital Material Passport is a declaration the client has already filed. HARP
reads the download link out of each, fetches the geometry once, explodes
collections and multiparts, and sorts on area.

```bash
harp resolve SOURCE.xlsx --dmp ./data/inbox/2026-08
```

| | Tier | Where it goes |
|---|---|---|
| Cutblock, under 1,000 ha | P3 | the master collection |
| Regional polygon | P4 | `catchments-*.geojson`, for detection |

It is **not a rung** — a passport has no identifier to key on, so it runs after
the per-source loop. Every feature carries `harp_provenance: client_declaration`
and no `harp_source_id`.

Deduplication is geometric, since a declared polygon has no registry identity: a
declared cutblock with 50% or more of its area inside one we resolved ourselves
is dropped. Ours carries a timber mark and a tenure holder; theirs carries
neither.

Downloads happen once and are never discarded — the source retains DMP data for
roughly a week after delivery.

## Monthly drops

A LIMS export arrives on a cycle and mostly repeats itself. Resolving all of it
every month is wasteful and buries the rows that actually moved.

```bash
harp resolve data/inbox/SOURCE.xlsx --config harmac-dev \
     --since data/outbox/resolution-20260812-142944.csv
```

### What is in a drop

```bash
harp package ./data/inbox/2026-08
```

Recognises files by their **columns, never their name**. Filenames in this data
have been wrong three separate ways — a workbook named "June 2026" whose sheet
is "January 2026" and whose records were processed in February, a "Calendar
Year" label on files that are not year-to-date, and a `ProcessedOn` that varies
per record. A file matching no signature is reported with the columns it had,
not skipped.

| Kind | Behaviour |
|---|---|
| job list | **replaces** — the current statement of what needs answering |
| delivery record | read for volume, never resolved |
| passport | fetched, exploded, sorted into cutblocks and regional areas |
| registry extract | **accumulates** — the newest private mark file alone covers 27.7% of the year |
| supplier geodata | attaches to one source |

### Comparing against last month

Compares against the previous manifest and reports:

| | |
|---|---|
| **new** | resolved |
| **changed** | re-resolved, with what moved |
| **gone** | written to rejects — a supplier who stopped delivering is a fact, not an absence |
| **unchanged** | previous answer carried forward |

Keyed on the client's own source id, falling back to the identifier. An
identifier alone is not unique — `PRINCETON` appears under both Gorman and
Weyerhaeuser — and keying on it reports dozens of spurious changes.

Combined with the cache, a monthly run touches the network only for what is
genuinely new. HBS records never expire; a mark resolved in August is not
re-queried in September.

## Resolving a raw client list

Hand it whatever the client sent. No curation, no register to maintain first —
the minimum viable input is a column of identifiers.

```bash
harp resolve data/inbox/SOURCE.xlsx --config harmac-dev --unique --no-geometry
harp resolve data/inbox/SOURCE.xlsx --config harmac-dev --class B
```

Every identifier goes down its jurisdiction's ladder. BC is implemented; the US
states fail loudly rather than quietly returning nothing, because a source never
attempted must not look like one attempted and missed.

### The BC ladder

| Rung | Query | Outcome |
|---|---|---|
| R1 | FTEN 340 `TIMBER_MARK` | P1 cut block |
| R2 | FTEN 340 `HARVEST_AUTH_FOREST_FILE_ID` | P1 |
| R3 | FTEN 340 `CUT_BLOCK_FOREST_FILE_ID` | P1 |
| R4 | FTEN 340 file id + cutting permit | P1 |
| R5 | HBS mark record | classify Crown or private |
| R6 | FTEN 340, the licence HBS named | P2 |
| R7 | FTEN 340, client number + district | P2 |
| R5b | private mark registry → PID → ParcelMap BC | P3 — private marks |
| R8 | district ∩ private forest ownership (layer 238) | P3 — opt-in |
| R9 | district only | P4 |

R1 does the work. Across 217 Harmac identifiers every resolution — 71 of them —
matched on `TIMBER_MARK`. R2–R4 have never fired and are cheap insurance.

R5 is the pivot: HBS holds a record for every mark issued in BC including the
private ones that appear in no tenure geometry, and gives back the holder, a
client number that is FTEN's own key, and the district.

R8 is opt-in — `--catchment`, or the tick box in the GUI. For a mark on private
land there is no harvest geometry anywhere public, so the best available answer
is a bounded area: the district it was issued in, narrowed to private forest
ownership. The intersect runs server-side. Layer 238's field names are read at
run time rather than hardcoded, because they are unverified and a wrong guess
would fail silently.

**A catchment bounds where the harvest could be, not where it was.** It is P3,
and the reduction it achieves should be measured on real data before anyone
relies on the tier.

### The EUDR libraries

Validation and cleaning need two NGIS packages that are not on PyPI. Install
them from their clones:

```bash
pip install -e ../eudr_geojson
pip install -e ../eudr_clean
```

Editable, so a change to either is picked up without reinstalling — which
matters while `eudr_clean` is being tuned for this data rather than for
supplier submissions.

Not vendored. `tracemark-eo` vendors `eudr_geojson`, and that is how copies
drift.

## From a lot back to its deliveries

```bash
harp lot June_Lot_List.xlsx --deliveries NFP_Load_Delivery_Summary.xlsx --dry-run
```

A pulp lot is made from chips that arrived over the preceding weeks, already
mixed. Nothing records which delivery went into which lot, so the answer is
bounded rather than exact: walk back from the lot's production date,
accumulating by species, until twice the lot's requirement is covered. Every
supplier in that window is declared.

The arithmetic runs pulp → chip volume → bone-dry tonnes, with a different
factor per species — cedar takes over half again as much volume as fir for the
same tonne of pulp, so it cannot be done on the total and apportioned after.

Every factor is in config under `sources.lots`.

**A sanity check worth knowing about.** The output reports chips against pulp
by mass. A kraft mill should show near 2:1, because roughly half the wood
leaves the digester as black liquor. Well outside 1.5 to 3 and a factor is
wrong or inverted, which the run says out loud rather than burying.

## Precision tiers

Every resolution says how tightly its geometry is bounded. This is what a
consumer filters on.

| Tier | Geometry | Plot claimable |
|---|---|---|
| P1 | cut block polygon | yes |
| P2 | harvesting authority, licence, or holder tenure in a district | yes |
| P3 | constrained catchment | with a stated basis |
| P4 | administrative area only | **no** — an operating area |
| P5 | unresolved | no |

---

## The five paths

| Path | Jurisdiction | Input | `eudr_sub_type` | Quality |
|---|---|---|---|---|
| `supplier_geodata` | any | polygons supplied direct | `parcel` | 1 — best |
| `ften_public` | BC | timber mark / client number | `database_polygon` | 2 |
| `detect_point` | US | lat/long + window | `change_detection_polygon` | 3 |
| `detect_catchment` | US | boundary + timeline | `change_detection_polygon` | 4 |
| `unresolved` | — | nothing usable | — | — |

`unresolved` is a real outcome, not an error. It lands in rejects so a supplier
who can't provide anything is *visible* rather than silently absent.

**`land_type` is resolved, not declared.** The register once took it as input.
You cannot tell Crown from private by looking — FTEN holds 83,916 timber marks
beginning with `E` and Harmac's sixteen are not among them. HARP establishes it
via HBS and writes it to the manifest.

---

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

harp ften regions --config harmac-dev
harp ften pull --config harmac-dev --client 00158809 --since 2025-07-01
harp runs --config harmac-dev
```

No credentials needed for FTEN — BC's endpoints are open under the OGL.

`--dry-run` works on anything that writes. Use it.

---

## Local and cloud are the same code

Three decisions make this work, and none of them are clever:

**Paths are strings.** `fsspec` handles `./data/staging` and
`gs://bucket/staging` identically. No branching.

**Auth is ADC.** `gcloud auth application-default login` once. BigQuery and
Earth Engine then behave the same on your laptop and in a Cloud Function.

**Config, not environment.** `harmac-dev.yaml` points at local paths,
`harmac-prd.yaml` points at GCS. Same code reads both.

Nothing in the pipeline asks "am I in the cloud?" If you want to write that,
put it in the config instead.

---

## Layout

```
harp/
  cli.py             every step, runnable standalone
  config.py          YAML loading, path resolution
  io.py              local / gs:// shim
  manifest.py        run log and rejects
  router.py          register-driven path selection
  normalise.py       everything → sce_base
  sources/
    ften.py          BC forest tenure          ✅ working
    supplier_file.py direct geodata            ⬜ stub
    detection.py     tracemark-eo wrapper      ⬜ stub
  configs/
    harmac-dev.yaml
    harmac-prd.yaml
functions/           Cloud Function shim — calls cli.main()
terraform/           deployment
```

---

## Two rules

**Every run writes a manifest row.** What ran, when, against what, how many in
and out. This is what makes the pipeline replayable in year three when someone
challenges a polygon.

**Nothing is dropped silently.** If a record doesn't make it through, it lands
in rejects with a reason. The existing NGIS geofence join drops unmatched
polygons with no error and no log — we are not repeating that.

---

## Service quirks worth knowing

Both verified against the live BC endpoint:

**`resultOffset` is ignored on groupBy queries.** Every page returns identical
rows. A naive loop runs forever. HARP pages on a key instead — never offset.

**Name filtering doesn't work.** `CLIENT_NAME LIKE '%HARMAC%'` returns nothing;
the tenure is registered to Nanaimo Forest Products Ltd. Always filter on
`CLIENT_NUMBER`.

---

## Open questions

Tracked in `docs/HPA1_Decisions_Log_v1_2.md` and `docs/HARP_Design_v0_7_0.md`. The blocking ones:

- Write to `sce_base` directly, or a staging table TraceMark promotes?
- Who registers `sce_type` in `db_primary_sources`? Unregistered types make
  assessments silently skip every row.
- What exactly is the completion rule? `CompletionRule` holds the current
  definition in one place so it can be argued about rather than assumed.
- How does HARP call `tracemark-eo` — as a library, or the deployed service?

---

## Licence and attribution

BC data is used under the **Open Government Licence – British Columbia**.
Attribution is required and is written into every output file's metadata block.

Sources:
`WHSE_FOREST_TENURE.FTEN_CUT_BLOCK_POLY_SVW` (layer 340) ·
`WHSE_ADMIN_BOUNDARIES.ADM_NR_DISTRICTS_SPG` (layer 748)
