# HARP — changelog

The version lives in `pyproject.toml` and `harp/__init__.py`. This file records
what changed and why; it does not assert a version of its own, because a third
place to update is a third place to forget.

Documents version separately. `docs/HARP_Design_v1_1_0.md` and
`docs/HPA1_Decisions_Log_v1_6.md` carry their own numbers and are not expected
to match the package.

---

## 0.35.x — a class with no name, and a list printed before it was tidied

**A class the raster carries and its own table names with an empty string.**
Not a lookup failure - an unknown code becomes "class 47" - but a published
name that is blank, which reached a real month on 31 features.

Such a class is now dropped from the mix and the rest reweighted. A
placeholder was considered and rejected: it reads as a species and is not
one. Where dropping it leaves a feature with nothing, the feature has no
species and the gaps stage estimates it from neighbours - which is a real
answer rather than a label for a hole.

The classes are named in the log with a count, because a class the raster
carries and its table does not name is a fault in the published asset and
saying which one is the only way to find out.

**And the written-file list is deduplicated before it is printed** rather than
after. The fix was there but sat below the loop that prints it, so the
returned value was right and the log still showed the month file twice.

## 0.36.x — the version is stated once and checked

The README now states the version at the top. It lives in three places -
the package, `pyproject.toml` and the README - and a test asserts they agree,
because a README that states a version is only useful if it is the right one.

## Documentation, as of 11 September 2026

`docs/HARP_Design_v1_1_0.md` and `docs/HPA1_Decisions_Log_v1_6.md`, with the
README beside them. All three describe what HARP does now, after four clean
monthly runs.

The design document gained the detection window, one-feature-per-detection,
log delivery records, the monthly input library, and the corrections to
ProductionPlace and the lot walkback. Its section letters were also put back
into reading order, having drifted through successive insertions.

## 0.35.x — the intake library, and three lot walkback faults

**The monthly input library gets the layout the cloud deployment specifies:**

    <intake>/YYYY-MM/<submission-id>/

`paths.intake` in config, `./data/intake/monthly` locally and a `gs://` path
in the cloud, with nothing else changing. The submission id is for
corrections: a month arrives as -001, and a corrected month arrives as -002
with the original left exactly as it was processed. The latest is used unless
one is named with `--submission`.

### Three faults in the walkback

**It walked back from when a lot started.** A lot can run for weeks - one ran
for seventy days - and fibre arriving partway through went into it. Starting
from the beginning excluded everything delivered during production. It now
walks back from when the lot finished.

**It read one delivery record.** Walking back from the finish date until the
mass is covered can reach through several months, and with one file a large
lot came back short - not because it was, but because the record was not
there. It now reads across the intake library.

**And it reversed a list that was no longer in date order.** One delivery
record is in date order and reversing it walked backwards correctly; a pool
of several months is in whatever order the months were read, and reversing
that walked forwards from the oldest. Every lot came back satisfied from the
start of the year, with identical delivery counts - which is what made it
visible. It sorts before it walks now.

**A CSV delivery record is also readable**, which two months of the year
needed.

Log delivery records are deliberately not read here. Those logs are the same
wood that arrives later as chips, and counting both would satisfy a lot twice
over.

## 0.34.x — log deliveries

The chip delivery record says how much fibre arrived. It does not say which
harvest it came from, because a chip carries no mark - which is why most of a
month resolves to a search area.

A log delivery record says the other half: **which marks arrived, and when**.
A mark on a real arrival names a specific harvest, so these resolve to a cut
block and are finished. The first such file carried nine marks, four of which
were not in the supply register at all.

**Two records of two different things, and neither replaces the other.** Log
arrivals never enter the tonnage arithmetic: those logs are chipped elsewhere
and arrive again later as chips, and counting both would count the same wood
twice. Their volume is cubic metres of log against bone-dry tonnes of chip,
carried as stated rather than converted - the factor varies by species and
moisture, and a wrong one is worse than two units side by side.

**More than one format will arrive**, because some suppliers export a scale
return from their system and others will send a spreadsheet somebody typed.
The reader looks for the three facts - which mark, when it came, how much -
rather than the layout, and a new supplier's spelling is a line in a table
rather than a new reader. Tested against both shapes.

**And DCT Chambers was 300 km inland.** The mill file had matched the
company's head office in Vernon rather than the operation the register's own
routing names, so DCT's chip deliveries were searching the Okanagan for
harvest that came off Vancouver Island. Now Ladysmith.

## 0.33.x — the four fields identify something

Validation on a real month reported 2,485 duplicate ProductionPlace, 440
missing, and 291 missing ProducerName. All three had the same root: the fields
described the area a detection was found in rather than the detection.

**ProductionPlace now identifies one harvest.** A timber mark is used as it
stands - it already names a specific cut. Everything else is built from where
it is and which harvest it is: `DCR-202605-0007`, the seventh harvest found in
that district that month. The district name stays on `harp_key_name`.

A field meant to identify a place that names the same place two thousand times
identifies nothing.

**And a bare supplier code is now named rather than left blank.** The client
buys under codes it has not explained - COS, RYK, WWW - each carrying real
tonnage, and every detection in their search areas inherited an empty
producer.

    Supplier RYK (name not provided by the client)

Better than blank and much better than "Unknown": it says precisely what is
missing and who can supply it, and it keeps the gap countable. A real name
still wins wherever there is one.

## 0.32.x — one feature per detection, with the uncertainty on it

**And one tier per detection.** A detection inside a registered cut block is
usually inside somebody's district as well, and both were emitted: the same
ground appearing twice, once as a harvest attributable to the tenure holder
and once as an inferred find in a district, sometimes naming two different
suppliers.

The weaker claim adds nothing - knowing a harvest sits in a registered block
already places it more tightly than a district can - so a detection now
appears once, under the strongest area that contained it. On one real month
that was 421 features, and more to the point it stops the file contradicting
itself.

Things resolved from an identifier pass through untouched. They are not
detections and do not compete: two registered blocks can legitimately share a
boundary.


A detection can fall inside several suppliers' search areas at once - two
sawmills in one natural resource district, and nothing says which of them cut
that patch. The pipeline emitted a copy per area, which quadrupled a month:
**3,254 detections became 14,348 features**, most of them the same polygon
under different names.

**One feature now, and the uncertainty recorded rather than removed.** Where
one area contains a detection, its supplier is named as before. Where several
do:

    ProducerName              Sierra Pacific Industries
    harp_producer_source      the largest of 4 candidate(s) by this month's
                              delivered tonnage - not established as the
                              producer of this area
    harp_producer_candidates  Sierra Pacific 7,512 BDT; Willis 5,660;
                              Manke 4,652; Aspen 1,991
    harp_producer_count       4

The candidate fields are absent where one supplier's area contained it and no
judgement was needed, so a reader can tell the firm rows from the ranked ones
at a glance.

**The alternative considered was keeping one at random.** That replaces "we do
not know which of these four" with "it was this one" - which looks certain and
is wrong most of the time. Ranking by tonnage is also a guess, but it is a
stated guess with its reasoning attached and its alternatives beside it.

**And declared areas now take their jurisdiction from their geometry.** They
arrived with none, so the EUDR country field fell back to whatever the config
said - right for a BC producer by luck, wrong the first time a US one
declares.

## 0.31.x — the window reaches back, and the delivery path actually runs

**The prefetch was looking at the wrong half of the month.** It covered the
delivered sources and not the pooled ones - and in a chip-heavy month most of
a delivered source's identifier is a mill town name that was never going to
match a timber mark. The marks that matter are the pooled ones, the logs sent
out for chipping, and 131 of those were being looked up one at a time.

It now takes both sets.

**And a prefetch batch had no fallback to WFS.** `attributes()` fell through
when the REST service failed; the batch path did not, so a REST outage made
every batch fail and the whole thing degrade to one query per identifier -
paying in full the cost the prefetch exists to avoid, with the fallback
sitting unused one function away.


**Detection now searches the two months before the declared month as well as
the month itself.** A chip delivered in May came from a log cut before May -
felled, hauled, chipped, delivered - so searching only May found harvest that
had not been delivered yet and missed the harvest that fed the month. Wrong
ground twice over.

The window ends at the month's end rather than shifting wholesale, because
some of what a month delivers really was cut within it. `lag_months` in
config; two is a working assumption about turnaround rather than a
measurement, and if a month's detections cluster at the start of its window
it is too short.

The desktop window now asks which month to declare and how far back to look,
and shows the window that follows - it no longer takes a start and an end,
which was a way to declare for a period nobody asked about. It calls the same
`_window` the run uses, so what it shows is what will be searched.

**And the delivery-driven month had never actually run.** Two mistakes in the
same block - a module used without being imported, and a variable used before
it existed - meant every month fell back to resolving the whole register. The
handler caught both alongside genuine file problems and reported "could not
read the delivery record", which sounded like the client's fault.

Both fixed. The handler now lets a programming mistake through rather than
dressing it as bad input, and a real fallback says plainly that the month
will be far larger than it should be.

Found by a review, not by us. There are tests now that exercise the path
rather than its parts: removing the import fails them.

## 0.30.x — documentation brought up to date

The design document reaches 1.0.0 and gains two sections: what arrived and
what kind it is, and supplier declarations. The decisions log reaches 1.5 with
the three decisions of the last two days and six corrections. The README gains
the delivery-driven month, declarations, `harp supply`, and what happens when
a register is unavailable.

No code changed.

## 0.29.x — what a read through the whole codebase turned up

**The EUDR lamp never lit.** Declared in the desktop window, the stage ran,
and nothing signalled it - so it sat grey through every run, reading as a
stage that had not happened.

**The toll-chipper pool swallowed its failures.** Any exception per record
was caught and skipped with no count, so a register that stopped answering
could take every pooled identifier with it and the run would report "0 added"
without a reason. The main resolve loop already counted and named its
failures; this path had not been given the same treatment.

**Three constants had two copies each.** `BRACKET_DAYS` in both the dates and
gaps stages, along with two different implementations of `bracket()`;
`MIN_SHARE` in gaps and species; `POINT_AREA_HA` in normalise and species.

That matters more than tidiness. The point of filling a gap is that an
estimated value reads exactly like an observed one - and two copies of the
number applying it is how that quietly stops being true. Each now has one
definition and the others import it.

**Features dropped from the union in silence** are now counted. A geometry
that cannot be read is a smaller submission than intended, and a smaller
submission finds less, which looks like a quiet month rather than a fault.
The same for detections that cannot be read - each is harvest that will be
attributed to nobody.

**And the district lookup could not tell an outage from a missing district.**
It tries three field names because the code lives in a different one
depending on the layer; if all three fail nothing was actually asked, and
returning "no such district" would build a search area on a hole. It raises
now.

**The completion rule stays out of `harp run`, and the config says why.** It
requires disturbance start and end dates that the register barely populates -
filtering 3,339 blocks to since-2024 left fifteen - so applying it to a month
would drop most of it. Detection answers the same question with evidence
rather than a null field.

## 0.29.x — a second door to the tenure register

The ArcGIS REST service went down for a day and took every run with it. The
same feature class is published as WFS on a different host, and it answered
throughout - identical fields, identical records. `GR2106` gives `A94731`,
`BLK227` and Cape Mudge Forestry either way.

REST is tried first; WFS is tried when REST fails outright. A run no longer
depends on one server being up.

**The distinction that matters is preserved.** An outage must never read as
"no such record" - a blip on the first rung once demoted a cut block to a
district envelope, which is a wrong answer wearing the shape of a right one.
So the fallback is tried before concluding anything, and if both doors are
shut the run says so.

## 0.29.x — supplier declarations, whatever shape they arrive in

Two suppliers declare where their wood came from and neither does it the same
way. Mosaic exports GeoJSON with the boundary in it. Willis prints a table of
Washington permit numbers and scans it.

**Both are the same act**, so there is now one roof - `harp/declarations.py` -
and a new supplier's format is a reader underneath it rather than a new path
through the pipeline.

| | | |
|---|---|---|
| geometry | taken at their word | P1d, finished |
| identifiers | resolved in a public register | P2a, searched |

**A scan is read by OCR, and nothing about it has to be trusted.** A permit
that misreads will not resolve in the register; one that resolves is right.
Read by word position rather than as text, because tesseract reads a printed
table column by column otherwise. All twenty-one rows of one real declaration
came out clean.

**`harp/sources/fpars.py`** resolves a Washington Forest Practices permit to
the units it covers. Eighteen of twenty-one permits on one declaration
resolved, to fifty-four units, every one carrying an area.

**One layer, not eight.** The service publishes the same features under eight
filters, and querying all of them returned eighteen permits as two hundred and
eighteen rows. `FPA - All Harvest by Classification` carries every polygon the
others do. `Not Digitized` is not read at all - it records applications the
state has no boundary for, which is worth knowing and is not a harvest area.

**Why P2a rather than P1a.** A permit covers several approved units and the
supplier named the permit, not which unit fed a delivery. Real register
geometry, more than they cut for us, narrowed by detection - which is what
P2a means.

## 0.28.x — the month is what arrived

**The pipeline used to take its work from the supply source register.** A July
run resolved 217 identifiers to declare a month in which 41 sources delivered.
The register is a master list of everything the client might buy from; 159 of
its 279 rows are log purchases that arrive back later as chips under an
entirely different source.

**The month now comes from the delivery record**, and the register is a lookup
for what each delivered source is. Without a delivery record the old
behaviour stands, and says so.

### Four kinds of arrival

`ORIGIN_TYPE` says which, and by mass rather than by count they are not
close to equal. Across 2026 to date:

| | | |
|---|---|---|
| merchant residual | 304,547 BDT | 71% |
| toll chipped | 121,092 BDT | 28% |
| own yard | 4,892 BDT | 1% |
| trade | 103 BDT | 0% |

**Seventy-one percent is residual chips bought from a sawmill.** No timber
mark exists to be asked for - the logs were the sawmill's own purchase. A
search area is the honest ceiling, and a run now says so in tonnes rather
than leaving it to be inferred from a feature count.

**Twenty-eight percent was chipped under toll from the client's own logs.**
Those marks are already in the register: the register records where each log
purchase was sent, and a chip receipt from DCT or Mid Island now resolves to
the pool of blocks routed there as a P2a search area, narrowed by detection
to the window. The same treatment a supplier's own tenure already gets.

**Own yard material is no longer resolved at all.** It arrived, but it did not
come from a forest this month, and declaring it double counted the wood that
did.

### The join that was being thrown away

`harp_source_id` was set at assembly and dropped by the schema filter one step
later. It is kept now, which means a lot walkback selects geometry by the
deliveries it identified rather than by supplier - a supplier can deliver from
several sources and only some of them fed a given lot.

`harp supply` reports what arrived and how much of it can be placed, without
making a single query.

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
See `docs/HPA1_Decisions_Log_v1_6.md` for the reasoning behind each, with dates
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
