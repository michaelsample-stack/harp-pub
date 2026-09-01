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

- **A prefetch for the tenure register.** Every source runs three separate
  queries before anything else, and nothing is cached between runs, so a rerun
  of the same month costs as much as the first. Batching the identifiers into
  one query per field would take roughly 660 requests down to 15. The single
  biggest improvement available, and contained.
- **An append-only registry store.** The private mark registry rebuilds from
  the extracts folder each run.

## Known open

- **`apply_completion_rule`** is in both configs and read nowhere. It looks
  like it governs resolution and does not.
- **How current the detection table is.** A Georgia control returned nothing
  after 2 June while the Pacific Northwest ran to mid-August, which looks like
  a stale regional copy rather than a lag.
- **A sub-four-hectare detection comes back as a point.** It carries an area
  but no boundary, and whether a point is admissible as a plot is undecided.
- **Whether a lot's weight is air-dry**, and the direction of the client's
  `BDU/m3` factor. Both assumed and both defensible; neither confirmed. The
  chips-to-pulp ratio the run reports is the check on it.
- **Declaration window** assumed 24 months, unconfirmed with the client.
- **`COS` and `WEW`** remain unexplained supplier codes. `WWW` is resolved - it
  is a route rather than a company.

## Blocked on the client

- **June deliveries.** The lot list is June and the delivery record is July, so
  the walkback has never run against a matching month.
- **Six months of back data.** A lot reaches past the month it was made.
- **April 2026 scaled timbermarks**, never received. The extracts are per
  month, not cumulative, so a missing month is a gap.
