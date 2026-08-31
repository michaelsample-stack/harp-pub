# HARP — build state

**Version 0.8.0 · 18 August 2026**

## What runs

| | |
|---|---|
| Package sorting | by column signature, never filename. Four kinds recognised |
| Monthly diff | new / changed / gone / carried forward |
| BC ladder | R1–R8, including R5b private marks |
| HBS | land basis, evidence pages retained |
| Client declaration | DMPs downloaded, exploded, sorted, deduplicated |
| Assemble | merge, dedupe, tier reconciliation |
| Validate | eudr_geojson → eudr_clean → revalidate |
| Normalise | sce_base + provenance sidecar |
| CLI, and a five-tab desktop app | |

## Dependencies

    pip install -e .              # harp itself
    pip install -e ../bcparcel    # required for R5b

`eudr_geojson`, `eudr_clean` and `ngis-eo` are imported lazily via
`harp.adapters` — a BC-only run needs none of them. `shapely` and `pyproj` are
needed for geodesic area and for geometric deduplication; without them the run
says what it could not do rather than doing it wrongly.

## Not built

- **Validation and cleaning** — `eudr_geojson` and `eudr_clean` are
  specified in `docs/HARP_Design_v0_7_0.md` but not yet wired into a run.
  The regional polygons from a DMP are written to `catchments-*.geojson` ready
  for it.
- **US resolvers** — stubs that fail loudly rather than returning nothing
- **Append-only registry store** — the registry rebuilds from the extracts folder

## Known open

- `sce_base` scope: the 32-column list is in `billerud_prod_20251029.py`, but
  most are risk assessment results. Whether HARP runs the assessment or hands
  off a thinner row is undecided. Geometry column is `.geo`, we write `geom`.
- Declaration window assumed 24 months, unconfirmed with the client.
- Whether a detected polygon carries a different tier from a registry cut block.
- The provenance of the DMP harvest units. 26% overlap with our P1 blocks, 0%
  with our private parcels. A question for the client.

## Baseline — HPA1 Harmac, 279 sources

    P1  cut block geometry        87    140 blocks, 2,315 ha
    B   private, holder known     43    resolve to parcels via bcparcel
    C2  chip mills                85
    E   identifier unknown        22
    D   yards and reloads         23
    N/A internal                   9
    C1  custom chippers            3

Zero chip sources resolve from Harmac's records alone — a chip delivery names
the mill, not the forest. Nathan's decision is to draw a catchment around the
mill and run detection inside it.
