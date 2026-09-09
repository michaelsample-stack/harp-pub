# HARP — changelog

The version lives in `pyproject.toml` and `harp/__init__.py`. This file records
what changed and why; it does not assert a version of its own, because a third
place to update is a third place to forget.

Documents version separately. `docs/HARP_Design_v0_9_0.md` and
`docs/HPA1_Decisions_Log_v1_4.md` carry their own numbers and are not expected
to match the package.

---

## 0.27.x — the reference data ships with the package

The supplier alias table, mill locations and stated operating areas now live
in `harp/reference/` rather than being pointed at by hand.

They are ours rather than the client's - a few hundred rows of decisions made
once and reviewed since - and requiring somebody to select them is how a run
quietly loses its mill locations and builds a month of mill-buffer search
areas without saying why.

**A local copy still wins.** The order is: an explicit path, then
`sources.reference.path`, then `data/registry` in the working folder, then the
packaged copy. A file somebody is editing locally keeps being used.

A run now lists all three and their row counts before it resolves anything,
and the Setup tab shows them alongside the libraries.

## 0.26.x — a run that says when the register stopped answering

*Revised after the register went down and made the first version of this
worse rather than better.*

**What went wrong.** BCGW's backend stopped serving queries - a bare
`where=1=1&returnCountOnly=true` failed from a browser, on two different
layers - and a run made during it resolved almost nothing. The first attempt
at this then halved each failed batch and halved again, retrying every piece
three times with backoff, which turned a five-minute outage into hours of
silent waiting. The outage was not ours; the freeze was.

**Probe, batch, fall back once.** A single identifier is tried before
committing: if it fails the field is abandoned in one request. A batch that
fails afterwards is asked identifier by identifier - once, not recursively -
which is what the ladder would have done anyway.

**The retry discriminates.** A 429, a timeout, or a server-side error is
worth trying again; a 400 is not, and retrying it three times with backoff
costs ninety seconds to learn what the first attempt already said.

**And lookups are cached between runs.** A week for a hit, two days for a
miss, in the store HBS already uses. The point is not speed: on the day the
register went down every source resolved to nothing and the whole run was
wasted. Now a re-run only asks about what it has not already got. A service
error is never cached - that would make an outage permanent.

Three changes, after a month where the BC register stopped answering partway
through and 190 of 221 sources resolved to nothing. Every failure was handled
correctly per source, so the run finished looking healthy and produced a month
that was mostly noise.

**It says so now.** Above 10% of sources failing on a service call rather than
on their data, the run shouts - and it compares the unresolved count against
the previous run, because 107 becoming 212 is more informative than either
number alone.

**A failed request is retried before it is believed.** Three attempts, two
then eight then twenty seconds. A rate-limited request answers perfectly well
half a minute later, and trying once cost a month.

**And it asks far less often.** The ladder used to make one request per
identifier per rung - 663 sequential requests for 221 identifiers, nothing
cached between runs, which is what got it throttled. A prefetch now asks in
batches of two hundred: **six requests instead of 663.** A field whose
prefetch failed still falls through to a live query, because an incomplete
index would read a question nobody asked as a miss.

## 0.25.x — filling the gaps, and a run that survives a flaky register

**One source no longer ends a month.** A transient FTEN failure at source 175
of 221 killed a whole run and lost the 174 that had already resolved. The
geometry fetch inside `_hit` was the one unguarded call - every rung above
already treated a service error as a rung that did not answer.

Now a source that raises is recorded as unresolved and the run carries on,
with the failures listed at the end and a note that they are worth re-running.
A source whose register answered but whose geometry did not keeps its tier and
attributes; only the shape is missing, and `geometry_error` says so.

## 0.25.x — filling the gaps

A stage that estimates what the detection service and the species rasters
could not answer, from the nearest features that could. About 7% of features
get no date and under 1% no species.

**On, and part of the pipeline rather than an option.** A deliverable with
blank fields is not a deliverable, and the services leave a few percent
unanswered every month.

Above 15% of features the run says so loudly and the stage goes amber. A month
where one feature in six had to be estimated is not a month with a few gaps -
it is one where something upstream did not answer, and the number is there so
nobody has to notice it themselves.

Every filled feature is marked on `harp_estimated` and its basis says so, in
the same field the real values use. `Estimated` is carried into the delivered
view, so a file containing estimates can be told from one that does not after
the `harp_` fields are stripped.

`harp gaps` fills an existing month without running everything else.

Also: `is_us` read `harp_jurisdiction` and `ProducerCountry` as one field.
They disagree about CA - California in the first, Canada in the second - so a
feature with no jurisdiction and a Canadian country went to the US raster.

## 0.24.x — species

What was growing on each harvest area, from the Canadian annual species raster
and the US forest type raster, read through Earth Engine after the month is
assembled.

Four fields on every feature, and a `Species` array in the delivered view. On
by default; a run without Earth Engine reachable says so and carries on.

The two rasters are not like for like - Canada classifies species per pixel,
TreeMap classifies forest type - and the design document says so, because the
percentages do not mean the same thing on each side of the border.

`harp species` re-reads an existing month without running everything else.

## 0.23.x — harvest dates, and a jurisdiction fix

`HarvestStartDate` and `HarvestEndDate` on every feature that can carry them,
bracketed a month either side of a detection, with `harp_harvest_basis` saying
how each was arrived at. A second detection call dates the blocks that resolved
without one.

And `catchments.py` was setting no jurisdiction on any search area, so every
detection inherited a blank and the EUDR country fell back to the config
default - which declared Washington and Californian harvest as Canadian.

## 0.22.x — output structure, and a census before submission

Each run writes to its own folder under the outbox, named for the month and the
run, with the stages in numbered subfolders. Every run is kept.

Quarantine moved out of the library to its own configurable path, keyed by run
because a month can fail more than once. Files there are named for their state,
so one moved out of context still says what it is, and a plain-language note
records what stopped it. The library holds finished months only.

A census prints before anything is submitted: everything the run holds, grouped
by whether it is finished, will be searched, or did not resolve, each with a
line saying what that category means. It is the last point at which the shape of
a month can be reviewed before the numbers change.

The desktop detection window is a month-to-month range rather than a single
month. A backwards range is refused rather than silently swapped.

## 0.21.x — producer-declared harvest areas

**P1d**, and a fourth traceability value, **declared**. A supplier exports their
own harvest areas and they are taken at their word.

Checking them against a register was tried and abandoned: of 63 distinct timber
marks in one batch, 21 appeared in the BC tenure register, because the largest
suppliers work private fee-simple land outside Crown tenure by definition. Where
a mark does resolve the geometry matches almost exactly — 38.01 ha against
38.01 ha, centroids 1.3 m apart — so the register is kept only for finding a
producer name better than a placeholder.

Read with the rest of the drop and passed through the split like everything
else. Deduplicated: 1,450 features became 370 distinct, because a block feeding
several booms is exported once per boom. Longitude given in 0–360 convention is
normalised. Points without boundaries, slivers and reversed dates are annotated
rather than dropped.

A feature belongs to every month it had production in — from the production
dates, which are complete, rather than the harvest dates, which are half
populated and include one placeholder reading 2001-12-31.

## 0.20.x — the EUDR projection

The four regulated fields — `ProducerName`, `ProducerCountry`,
`ProductionPlace`, `Area` — added to a month before validation, and stripped to
just those four at delivery by `harp deliver`.

Added rather than substituted, because the validator ignores extra fields and a
production lot is resolved against `harp_supplier`. Building it the other way
round would have put a four-field collection into the library and broken lot
resolution; that was caught before it shipped.

A field with no value is omitted rather than emitted empty. A missing field is
Recommended; a blank one is Required.

`Area` is measured from the geometry being shipped, never inherited from a
parent. `ProducerCountry` maps `BC` to `CA` and everything else to `US` —
including our `CA`, which is California.

## 0.19.x — the producer name comes from the register

`ProducerName` carried from the tenure register at the point of resolution, with
the client's alias kept beside it.

Two provenance files had gone to a customer naming the producer as `WWW`, a code
in the client's system that turned out to cover six unrelated holders. The
register name was already being fetched and discarded.

A bare supplier code is never used as a producer name. Where nothing named a
holder, the field is left empty.

## 0.18.x — the whole month in one command

Detection folded into a run. `harp run --month YYYY-MM` goes from the client's
drop to a staged library month.

It used to stop after the split and leave detection to a second command. That
invited runs that looked finished and were not — and because the run log only
covered the first four stages, a run that never reached detection looked
identical on disk to one that had. Everything now writes to the same log, and
the summary says where the run stopped.

`detect`, `enrich` and `union` remain, as ways to resume when a run got partway
and the service did not answer.

## 0.17.x — stated areas, and a jurisdiction fix

`harp areas` records an operating area for a supplier nothing else can place.
Every entry carries who stated it, when, and what it rests on. Only a supplier's
own words count as declared.

`harp mills` no longer places a non-BC supplier in a BC district.

## 0.16.x — the library, and lots

A month is validated, cleaned and revalidated, then staged for approval. Nothing
is declared from an unapproved month.

`harp lot` walks a production lot back through the delivery record: pulp weight
and species split become bone-dry tonnes of chips, doubled, and the walk goes
back until each species target is met.

## 0.15.x — the detection round trip

Wired to the NGIS detection service. What comes back carries a date, an area and
a feature type, and no supplier — so attribution is recovered by spatial join
against the per-supplier geometry the union was built from.

## 0.14.x — a search area is never declared

Titled parcels joined the search areas. Across one month they ran to 303,434 ha
against 71,274 ha of detected harvest. A parcel is the ownership boundary; the
cut is somewhere inside it.

## 0.13.x — tiers and traceability

`plot_claimable` replaced by `harp_traceability`. The flag asserted a regulatory
position, and whether a tier satisfies a given test is a judgement for whoever
makes the declaration.

## 0.12.x and earlier

Catchments, the supplier alias table, the US routes, and the BC resolver ladder.
See `docs/HPA1_Decisions_Log_v1_4.md` for the reasoning behind each, with dates
and reversals.

---

## Installing

    pip install -e .                 # harp itself
    pip install -e ../bcparcel       # private marks to titled parcels
    pip install -e ../eudr_geojson   # validating a month
    pip install -e ../eudr_clean     # cleaning what fails

Editable, because all four are moving. The EUDR libraries are imported where
they are used, so a run that stops before staging needs neither.

`shapely` and `pyproj` are needed for geodesic area and geometric deduplication.
Without them a run says what it could not do rather than doing it wrongly.

## Not built

- **A prefetch for the tenure register.** Every source runs three separate
  queries before anything else, and nothing is cached between runs, so a rerun
  of the same month costs as much as the first. Batching the identifiers into
  one query per field would take roughly 660 requests down to 15. The single
  biggest improvement available, and contained.
- **An append-only registry store.** The private mark registry rebuilds from the
  extracts folder each run.
- **`sce_base` output.** TraceMark wants rows rather than GeoJSON. The schema is
  recorded in Appendix B of the design document.

## Known open

- **`apply_completion_rule`** is in both configs and read nowhere. It looks like
  it governs resolution and does not.
- **How current the detection table is.** A Georgia control returned nothing
  after 2 June while the Pacific Northwest ran to mid-August, which looks like a
  stale regional copy rather than a lag.
- **A sub-four-hectare detection comes back as a point.** It carries an area but
  no boundary, and whether a point is admissible as a plot is undecided.
- **Whether a lot's weight is air-dry**, and the direction of the client's
  `BDU/m3` factor. Both assumed and both defensible; neither confirmed. The
  chips-to-pulp ratio the run reports is the check on it.
- **Declaration window** assumed 24 months, unconfirmed with the client.
- **`COS` and `WEW`** remain unexplained supplier codes. `WWW` is resolved — a
  log broker rather than a harvester.

## Blocked on the client

- **June deliveries.** The lot list is June and the delivery record is July, so
  the walkback has never run against a matching month.
- **Six months of back data.** A lot reaches past the month it was made.
- **April 2026 scaled timbermarks**, never received. The extracts are per month,
  not cumulative, so a missing month is a gap.
