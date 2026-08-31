# HARP — changelog

The version lives in `pyproject.toml` and `harp/__init__.py`. This file records
what changed and why; it does not assert a version of its own, because a third
place to update is a third place to forget.

Documents version separately. `docs/HARP_Design_v0_7_0.md` and
`docs/HPA1_Decisions_Log_v1_2.md` carry their own numbers and are not expected
to match the package.

---

## 0.18.x — the whole month in one command

Detection folded into a run. `harp run --month YYYY-MM` now goes from the
client's drop to a staged library month: sort, resolve, search areas, split,
union, detect, join back, validate, clean, stage.

It used to stop after the split and leave detection to a second command. That
invited runs that looked finished and were not — and because `run-*.txt` only
covered the first four stages, a run that never reached detection looked
identical on disk to one that had. Everything now writes to the same log, and
the summary says where the run stopped.

`detect`, `enrich` and `union` remain, as ways to resume when a run got partway
and the service did not answer.

A drop's own files are used from the drop. The lot list, supplier register and
mill locations are recognised by their columns, so naming a file that sits in
the folder being read is no longer a step anybody has to remember.

## 0.17.x — stated areas, and a jurisdiction fix

`harp areas` records an operating area for a supplier nothing else can place —
a remanufacturer with no tenure, no facility, and no place name in their own
name. Every entry carries who stated it, when, and what it rests on. Only a
supplier's own words count as declared; anyone else stating it is inference
with an author.

`harp mills` no longer places a non-BC supplier in a BC district. Interfor and
Weyerhaeuser both hold BC facilities, so the name matched and a district came
back — for an operation the client does not buy from.

## 0.16.x — the library, and lots

A month's geometry is validated, cleaned and revalidated, then staged for a
person to approve before it becomes a library month. Four states: working,
pending, quarantine, and the shelf. Nothing is declared from pending, and a
month with Required findings still standing goes to quarantine rather than
being promoted quietly.

`harp lot` walks a production lot back through the delivery record. A lot's
pulp weight and species split become bone-dry tonnes of chips per species,
doubled, and the walk goes back until each species target is met. Every
supplier in that window is declared.

## 0.15.x — the detection round trip

Wired to the NGIS detection service. A run submits a union of the tenure blocks
and search areas, polls, and joins the return back to the per-supplier geometry
it was built from.

What comes back carries a date, an area and a feature type, and no supplier —
so attribution is recovered by spatial join, which is why the union is kept
apart from the geometry it was built from.

## 0.14.x — a search area is never declared

Titled parcels joined the search areas. Across one month they ran to 303,434 ha
against 71,274 ha of detected harvest, so declaring the parcel over-declared by
about four times. A parcel is the ownership boundary; the cut is somewhere
inside it.

The same rule already applied to tenure blocks. What is kept is the detection;
the area only says whose it was.

## 0.13.x — eight tiers, and traceability

`plot_claimable` replaced by `harp_traceability` — direct, indirect, inferred.
The flag asserted a regulatory position, and whether a tier satisfies a given
test is a judgement for whoever makes the declaration.

Tiers restructured to P1a, P1b, P1c, P2a, P2b, P3a, P3b and P4. P1a is the only
one needing no detection; every other letter pair separates before and after a
run.

## 0.12.x and earlier

Catchments, the supplier alias table, the US routes, and the BC resolver
ladder. See `docs/HPA1_Decisions_Log_v1_2.md` for the reasoning behind each,
with dates and reversals.

---

## Installing

    pip install -e .                 # harp itself
    pip install -e ../bcparcel       # private marks to titled parcels
    pip install -e ../eudr_geojson   # validating a month
    pip install -e ../eudr_clean     # cleaning what fails

Editable, because all four are moving. `eudr_geojson` and `eudr_clean` are
imported where they are used rather than at the top, so a run that stops before
staging needs neither.

`shapely` and `pyproj` are needed for geodesic area and for geometric
deduplication. Without them a run says what it could not do rather than doing
it wrongly.

## Not built

- **A prefetch for the tenure register.** Every source runs three separate
  queries before anything else, and nothing is cached between runs, so a
  re-run of the same month costs as much as the first. Batching the
  identifiers into one query per field would take roughly 660 requests down to
  15.
- **Append-only registry store.** The private mark registry rebuilds from the
  extracts folder each time.

## Known open

- `apply_completion_rule` is in both configs and read nowhere. It looks like it
  governs resolution and does not.
- A sub-four-hectare detection comes back as a point. It carries an area but no
  boundary, and whether a point is admissible as a plot is undecided.
- How current the detection table is. A Georgia control returned nothing after
  2 June while the Pacific Northwest ran to mid-August, which looks like a
  stale regional copy rather than a lag.
