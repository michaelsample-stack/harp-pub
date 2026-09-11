# HARP — Harvest Area Resolution Pipeline

**Design document**

| | |
|---|---|
| Version | 1.1.0 |
| Status | End to end, from a client drop to a staged library month and a lot package. |
| Date | 9 September 2026 |
| Owner | M. — NGIS |
| Engagement | HPA1 Harmac Pacific (first client), intended to be client-independent |

---

## Changelog

| Version | Date | Change |
|---|---|---|
| 0.1.0 | 2026-08-12 | First draft. Four-stage architecture, precision tiers, BC resolver ladder fully specified from evidence, US and non-forestry paths stubbed. |
| 0.2.0 | 2026-08-12 | Assembly and validation stages specified against the real `eudr_geojson` 0.4.0 and `eudr_clean` 0.5.5 interfaces. Monthly-drop comparison added. Merged into the existing HARP repo — YAML config, run manifest, `sce_base` normalisation. R8 catchment built. Two field-discovered bugs recorded in §5.4. |
| 0.3.0 | 2026-08-13 | R5b — the BC private mark registry, via the new `bcparcel` package. Package sorting by column signature. Detection placement decided against the real `tracemark-eo` functions. Preemptive filtering removed. Operating envelopes dissolved to one row. |
| 0.4.0 | 2026-08-17 | Detection specified in full — search area sources, the two-run monthly cycle, submit/collect. History and retention decided: 24-month declaration window, archive the inputs. Overlap handling stated. |
| 1.1.0 | 2026-09-11 | Detection reaches back two months. Log delivery records read. One feature per detection, at its strongest tier, with candidate producers named. ProductionPlace identifies a harvest rather than a district. The monthly input library. Section letters put back in order. |
| 1.0.0 | 2026-09-09 | The month's work taken from the delivery record rather than the supply register. Toll-chipped receipts resolved to the pool of marks routed to the chipper. Supplier declarations brought under one roof, whether they carry geometry or identifiers. Washington Forest Practices permits resolved to harvest units. A second route to the tenure register. |
| 0.9.0 | 2026-09-09 | Harvest dates and species added as stages, and a gap-filling stage after them. The reference data - supplier register, alias table, mill locations, stated areas - moved inside the package. The tenure register asked in batches and cached between runs. Producer name set on every search area route, after a month went out with 95% of features unnamed. |
| 0.8.0 | 2026-09-01 | Detection folded into a run - `harp run --month` now goes from the drop to a staged month, and everything writes to one log. The monthly library added, with pending, quarantine and an approval gate. Lot walkback added. `ProducerName` carried from the register rather than the client's code, after a supplier code covering six unrelated companies reached a customer deliverable. The EUDR projection added ahead of validation. |
| 0.7.0 | 2026-08-28 | Detection wired to the NGIS service, so a run goes end to end. `plot_claimable` replaced by `harp_traceability` — direct, indirect, inferred — because whether a tier satisfies a regulatory test is not ours to assert. Tiers restructured to eight. **Search areas are no longer declared: what is kept is the ground detection found inside them, carrying whatever the area could tell us.** Titled parcels joined the search areas — 303,000 ha of parcel against 71,000 ha of detected harvest in one month made declaring them indefensible. |
| 0.6.0 | 2026-08-24 | Catchment layer built — six methods, following the Domtar, Enviva and Billerud precedents in `tracemark-eo`. Persistent supplier alias table added, so a company-name decision survives a rerun. US routes added: Washington FPARS by company name, US counties, national forest boundaries. Block cap removed. |
| 0.5.0 | 2026-08-18 | The client's own declaration ingested — Digital Material Passports, after the per-source loop. Declared cutblocks join the collection at P3, regional areas go to the detection pool at P4, and geometric deduplication drops anything already resolved. |
| 0.5.1 | 2026-08-18 | Passport ingest made opt-in. Their provenance is unestablished, so they are recognised and reported but not consumed unless asked for. |

Version policy: minor bump for a new resolver, a new precision tier, or a change
to the data contract. Patch bump for corrections and registry detail. Major bump
when the stage model or the output contract changes.

---

## 1. What HARP is

HARP turns a supplier's own record of where fibre came from into a harvest area
geometry, or into a clear statement of why no geometry exists and who holds it.

It exists because EUDR requires the plot of land a commodity was produced on,
and a client's operational system holds an identifier — not a boundary.

**HARP does two things, repeatedly:**

1. Turn an identifier into a polygon.
2. Get an identifier from whoever holds it.

Every class of source is a description of how many times step 2 must happen
before step 1 becomes possible. That framing is the whole pipeline; the rest is
plumbing.

### What HARP is not

- Not a chain-of-custody system. It resolves origin, not custody.
- Not a supply chain model. Aggregation, recipes and lot apportionment sit
  upstream in TraceMark.
- Not a substitute for supplier engagement. Where the geometry is privately
  held, HARP identifies the holder and stops.

---

## 2. Design principles

**Precision is an output, not an assumption.** Every resolved source carries a
tier saying how precise its geometry is. A district boundary and a cut block are
both valid outputs; conflating them is not.

**Evidence travels with the answer.** Which registry, which field matched, when
it was queried, what the record said. A DDS built on HARP output must be
defensible without re-running the pipeline.

**Registry-first, then declaration.** Query what is public before asking a
supplier. But do not assume the register is the normal case — for Harmac's BC log
supply it covers 74% of identifiers and 0% of the private ones.

**Fail into a client deliverable.** An unresolvable identifier is not a pipeline
error. It is a finding that goes back to the client as a question.

**Jurisdiction is a lookup, not a branch.** The resolver ladder is configuration.
Adding Washington should be a config file, not a rewrite.

**Cache registry facts, refresh geometry.** Who holds a mark and what land basis
it sits on do not change. Cut block boundaries do.

---

## 3. Architecture

One command does the month.

```
harp run <drop folder> --month 2026-07
```

Sort, resolve, build search areas, split, union, submit for detection, join the
result back, add the EUDR fields, validate, clean, stage. The desktop window
does the same from one button and lights each stage as it goes.

```
  monthly drop
       │
       ▼
  ┌──────────────┐
  │ 1 SORT       │  files recognised by their columns,      local
  │              │  never their names
  └──────────────┘
       │
       ▼
  ┌──────────────┐
  │ 2 RESOLVE    │  per-jurisdiction ladder, first          registry queries
  │              │  success wins. A tier is attached here   per source
  └──────────────┘
       │
       ▼
  ┌──────────────┐
  │ 3 SEARCH     │  a bounded area for whatever did not     registry queries
  │   AREAS      │  resolve                                 per supplier
  └──────────────┘
       │
       ▼
  ┌──────────────┐
  │ 4 SPLIT      │  three files, one schema                 local
  └──────────────┘
       │
       ├─────────► harvest-areas-*.geojson    P1a. Finished.
       ├─────────► tenure-blocks-*.geojson    P2a. To be searched.
       └─────────► search-areas-*.geojson     P1b, P3a. To be searched.
                              │
                              ▼
                     ┌──────────────┐
                     │ 5 UNION      │  one polygon to submit   local
                     └──────────────┘
                              ▼
                     ┌──────────────┐
                     │ 6 DETECT     │  NGIS service            remote
                     └──────────────┘
                              │  dated geometry, no attribution
                              ▼
                     ┌──────────────┐
                     │ 7 JOIN BACK  │  spatial join to what    local
                     └──────────────┘
                              ▼
                     ┌──────────────┐
                     │ 8 EUDR       │  four fields added,      local
                     │   FIELDS     │  nothing replaced
                     └──────────────┘
                              ▼
                     ┌──────────────┐
                     │ 9 VALIDATE   │  eudr_geojson
                     │ 10 CLEAN     │  eudr_clean, then again
                     └──────────────┘
                              ▼
                     ┌──────────────┐
                     │ 11 STAGE     │  pending, or quarantine
                     └──────────────┘
                              ▼
                        a person approves
                              ▼
                     the library month
```

**Why one command.** Detection used to be a second step, and that produced
runs which looked finished and were not. The run log covered only the first
four stages, so a run that never reached detection was indistinguishable on
disk from one that had. Everything now writes to the same log, and the summary
says where the run stopped: `split`, `union`, `detection`, `month written` or
`staged`.

`detect`, `enrich` and `union` survive as ways to resume when a run got partway
and the service did not answer. They are recovery, not workflow.

**Why three files at stage 4.** They need different handling, not because
three stages produced them. The first is the answer; the second and third are
places to look.

**Why the union.** The service takes crude, large bounding areas. Handing it a
constellation of small polygons is not what it is for, so everything is
dissolved into one shape before submission. The union is a submission artefact
and is never declared - the per-supplier geometry it was built from is kept
untouched, and that is what makes the return attributable.

**Diff is gone as a stage.** It was designed to send only what moved to the
resolvers. In practice a monthly drop replaces the supply list wholesale and
the cache makes a full pass cheap, so a separate diff was machinery without a
job. `harp runs` still shows what changed between drops.

**Qualify is gone too.** A tier is attached at the moment of resolution, by the
rung that produced the geometry. A separate pass to decide it later invited the
tier and the evidence to drift apart.

---

## 3A. The detection round trip

Detection is not something HARP performs. NGIS runs a weekly HLS-DIST job whose
output is a maintained table of harvest polygons, and HARP submits an area to a
service that queries it.

### The contract

```
POST /upload/process        multipart: file, startDate, endDate
GET  /upload/status/{id}    poll until completed, then a signed URL
```

The URL expires after an hour; polling again returns a fresh one, so a stale
link is never a problem.

### What comes back

A file named `.geojson` that is usually a CSV:

| | |
|---|---|
| `geo` | the geometry as WKT |
| `date` | when the disturbance was first detected |
| `area_ha` | its size |
| `feature_type` | `polygon`, or `point` below four hectares |
| `geo_json_str` | the same geometry, already as GeoJSON |
| `sce_id` | a batch id for the whole job — **not** a per-feature id |

Three things are worth knowing before writing against it. `geo_json_str` means
no WKT parsing is needed. `sce_id` is one value across every row, so it is not
something to join on however much it looks like an identifier. And **the
filename is not to be trusted**: a CSV arriving as `.geojson` looked like a
corrupt file for twenty minutes before anyone read the first line, so the
content is sniffed instead.

### What it does not carry

**No supplier and no mark.** Attribution is recovered afterwards by spatial
join against the per-supplier areas that were submitted. That is the whole
reason the union is kept separate from the geometry it was built from.

### The join back

Every submitted area is a place to look, and in every case **the detection is
what is kept**. The area only says whose it was and what else is known.

| Detection falls inside | Becomes | Inherits |
|---|---|---|
| a titled parcel | P1c, direct | the timber mark from the delivery record |
| a tenure block | P2b, indirect | the mark and holder from the tenure register |
| a district or county | P3b, inferred | the supplier, and nothing else |

**Where two suppliers' areas overlap, both get a copy.** The geometry repeats
and the attribution does not, because a harvest has to be declarable against
whoever supplied the fibre.

**Where one supplier's areas overlap** — a detection inside both their tenure
block and their district — the better parent wins and the weaker copy is
dropped. One detection, one feature, the best attribution available.

---

### The window a month searches

A month searches the two months before it as well as itself. A May run covers
1 March to 31 May and declares for May.

A chip delivered in May came from a log cut before May - felled, hauled,
chipped, delivered. Searching only May finds harvest that has not been
delivered yet and misses the harvest that fed the month: the wrong ground
twice over.

The window ends at the month's end rather than shifting wholesale, because
some of what a month delivers really was cut within it. A shifted window
would say none of it was.

**Two months is a working assumption about turnaround, not a measurement.**
`lag_months` in config. If a month's detections cluster at the very start of
its window, the lag is too short and that will be visible immediately.

A consequence worth stating: the same cut block appears in several months. A
block cut in March feeds March, April and May deliveries and turns up in all
three. That is correct rather than duplication - each copy is tied to the
deliveries that drew it in, through `harp_source_id`.

---

## 3B. The monthly library

A lot's chips reach back past the month it was made - a June lot has been seen
reaching twelve days into May. So resolving a lot means opening several months,
and that only works if each month has one obvious file rather than five
timestamped candidates.

The outbox is a working area. The library holds the month that was right.

```
<library>/2026-05/harvest.geojson     the geometry
<library>/2026-05/deliveries.csv      the loads, for the walkback
<library>/2026-05/manifest.json       what it was built from, and by whom
```

### Four states

| | |
|---|---|
| working | `data/outbox` - runs, reruns, experiments |
| pending | been through validation, waiting on a person |
| quarantine | Required findings still standing after the pass limit |
| library | approved |

**Nothing is declared from pending or quarantine.** A lot resolved against an
unapproved month would look identical to one resolved against an approved
month, so `read_month` refuses to read from either.

**Quarantine is an admission, not a failure.** Early months will contain
geometry the automated cleaner cannot fix, and a month sitting visibly in
quarantine with its findings beside it is more useful than one that failed
quietly or, worse, was promoted while still broken.

### The validate and clean cycle

Validate, clean, validate again, up to `max_passes`. Required findings block;
Recommended findings are reported and carried through, because a warning that
stops a month is a warning somebody switches off.

**One early exit.** If a pass produces the same Required count as the previous
one and drops nothing, the cleaner has nothing further to offer and two more
rounds will not change that. It stops and says so.

### Approval

`require_approval` in config, defaulting to on. Promotion records who approved
it and when. Promoting a quarantined month needs `--force`, and the manifest
records how many findings were accepted and by whom.

The intent is a service that promotes clean months by itself. Until the
cleaning parameters are settled for this data rather than for supplier
submissions, a person looks first.

---

## 3C. One detection, one feature

A detection can fall inside several suppliers' search areas at once - two
sawmills in one natural resource district, and nothing says which of them cut
that patch. It can also fall inside a registered cut block and a district at
the same time.

Emitting a feature for each combination quadrupled a month: 3,254 detections
became 14,348 features, most of them the same polygon under different names
and different tiers.

### One tier

A detection appears under the strongest area that contained it. Knowing a
harvest sits in a registered block already places it more tightly than a
district can, so the district claim adds nothing and its presence made the
file contradict itself - the same ground asserted twice at two strengths,
sometimes naming two different suppliers.

Areas resolved from an identifier are not affected. They are not detections
and do not compete: two registered blocks can legitimately share a boundary.

### One supplier, with the others named

Where several suppliers' areas contain a detection, the largest by that
month's delivered tonnage is named, and the ranking is recorded rather than
hidden:

| | |
|---|---|
| `ProducerName` | the largest candidate |
| `harp_producer_source` | that it is a ranking, not an establishment |
| `harp_producer_candidates` | every candidate with the tonnage that ranked them |
| `harp_producer_count` | how many there were |

The candidate fields are absent where one supplier's area contained it, so a
reader can tell the firm rows from the ranked ones at a glance.

**Keeping one at random was considered and rejected.** It replaces "we do not
know which of these four" with "it was this one" - which looks certain and is
wrong most of the time. Ranking by tonnage is also a guess, but it is a stated
guess with its reasoning attached and its alternatives beside it: anyone can
see it and disagree.

---

## 3D. The EUDR projection

Two functions, and the order matters.

    add(features)      the four EUDR fields alongside everything else
    project(features)  only the four, for a customer

**`add` runs before validation. `project` runs at delivery.**

The validator inspects only the four named fields - both the blank check and
the capitalisation check work from a known list, and anything else is ignored.
So a month carries its `harp_` fields through validation, cleaning and into the
library, and is stripped only at the point somebody outside sees it.

That ordering is not cosmetic. **A production lot is resolved against
`harp_supplier`.** Strip early and lot resolution has nothing to match on.

### The rule that shapes it

| | |
|---|---|
| 1.1.12 | Missing ProducerName — **Recommended** |
| 1.1.7 | Blank required fields — **Required** |

A blank field is worse than a missing one. Every field here is omitted when it
has no value, never emitted empty - the opposite of how the rest of the
pipeline fills fields, and deliberate.

### The four

**`ProducerName`** — carried from the point of resolution in EUDR casing, so
it survives the eventual refactor unrenamed. See §4.

**`ProductionPlace`** — the timber mark where there is one, otherwise the area
name, otherwise the district. A client number is skipped: it identifies a
company, not a place.

**`Area`** — measured from the geometry being shipped, so declared and computed
agree; a mismatch is Required. Never carried from a parent - a detection inside
a two hundred hectare block is forty hectares, and that difference is the whole
point of the round trip. A point is the exception, having no area to measure,
so the service's stated figure is used.

**`ProducerCountry`** — ISO2 from the jurisdiction, **and there is a trap in
it.** Our `CA` means California, whose country is `US`. Only `BC` maps to `CA`.
Getting that backwards would file Californian harvest as Canadian and nothing
downstream would notice.

---

### ProductionPlace identifies a harvest

A timber mark is used as it stands: it already names a specific cut.

Everything else is built from where it is and which harvest it is -
`DCR-202605-0007`, the seventh harvest found in that district that month. The
district name stays on `harp_key_name`.

It named the area before, so every harvest in one district carried the same
string and a month came back with two and a half thousand features called
"Campbell River Natural Resource District". A field meant to identify a place
that names the same place two thousand times identifies nothing.

### A supplier known only by a code

The client buys under purchasing codes it has not explained - five of them,
covering fifty-seven sources and real tonnage. Every detection in their search
areas inherited an empty producer.

    Supplier RYK (name not provided by the client)

Better than blank and better than "Unknown": it says precisely what is missing
and who can supply it, and it keeps the gap countable. A real name wins
wherever there is one.

---

## 3E. From a lot back to its deliveries

A pulp lot is made from chips that arrived over the preceding weeks, already
mixed. Nothing records which delivery went into which lot, so the answer is
bounded rather than exact.

### The arithmetic

    air-dry tonnes of pulp
      x species share            measured, not the recipe
      x m3 of chips per Adt      5.00 fir, 5.50 hem, 7.75 cedar
      / m3 per BDU               chip volume to bone-dry units
      x tonnes per BDU           1.089, a BDU being 2,400 lb
      = bone-dry tonnes          the unit the delivery record uses

Every factor is in config under `sources.lots`.

**One factor is recorded the other way up.** The client's table reads
`3.375 BDU/m3`. Taken literally that is 3.7 tonnes of dry fibre in a cubic
metre of chips - denser than solid wood, and chips are mostly air. Used
inverted it gives a chips-to-pulp ratio of 1.72:1 across a month, where a kraft
mill should show near 2:1 because roughly half the wood leaves as black liquor.
A run reports that ratio, and says so loudly outside 1.5 to 3.

### The walk

From the moment the lot finished, backwards through the delivery record,
accumulating **per species** until each has reached twice its requirement.

**From the finish, not the start.** A lot can run for weeks - one ran for
seventy days - and fibre that arrived partway through went into it. Walking
back from the start would exclude everything delivered during production.

**Twice** is the margin. Piles are reclaimed from the top, so material at the
bottom can sit a long time, and over-declaring is the failure that survives an
audit.

**Species are tracked separately but selection is not filtered by them.** Each
has its own counter and target, and the walk continues until the slowest is
satisfied. But a load is taken if it carries any outstanding species at all,
with no minimum share - a load that is 95% cedar enters a no-cedar lot on the
strength of its 2% fir, because that fir is real and plausibly came off the
same cut block. A stand is rarely one species.

### Reading across months

Walking back until the mass is covered can reach through several months of
deliveries - a large lot took 168 loads spanning two. So the walk reads the
monthly input library rather than one file, which is what the library is for.

Reading a single record made a large lot look short: not because it was, but
because the record was not there.

### What comes out

Every source in that window, every supplier, and every month it touched. Then
every feature for those sources, from those library months.

**Selected by source rather than by supplier.** A supplier can deliver from
several sources and only some of them fed a given lot; `harp_source_id` is on
every feature and says which. Months built before that field was kept fall
back to the supplier, which is the older and looser answer.
the output.

**A lot the delivery record cannot cover produces nothing.** A partial answer
that looks complete is worse than none.

---

## 3F. Producer-declared harvest areas

A supplier exports their own harvest areas as GeoJSON. They are taken at their
word: the producer asserts they harvested here, and that assertion is the
evidence. **P1d, traceability `declared`.**

### Why not checked against a register

It was tried. Of 63 distinct timber marks in one batch, 21 resolved in the BC
tenure register and 6% of features produced a good geometric match.

**Not a data quality failure.** The two largest suppliers work private
fee-simple land on Vancouver Island - the old E&N Railway grant - which sits
outside Crown tenure by definition. Their marks were never going to be there.

Where a mark does resolve the match is exact: 38.01 ha against 38.01 ha,
centroids 1.3 m apart. So the register is used for one thing only - finding a
better producer name than a placeholder.

### The producer name

Three rungs, and `harp_producer_source` records which answered.

| | |
|---|---|
| the supplier's own name | `producer declaration` |
| where that is a placeholder, the timber mark's holder | `forest register, via the declared timber mark` |
| failing both, whoever declared the file | `originator - the declaring party, not the harvester` |

`"PURCHASE Name"` is a literal placeholder a supplier exports where they will
not identify their upstream. It is never shipped. What ships is the declaring
party's name, with the field beside it saying that is who declared it rather
than who cut it.

### What the reader fixes, and what it only records

**Longitude in 0-360.** Some records give Vancouver Island as 235.5 rather
than -124.5 - the same place counted eastward. Normalised before anything
touches the geometry, because everything downstream produces confident nonsense
otherwise. 119 of 1,450 in one batch, almost all in the placeholder-identity
population.

**Duplication.** 1,450 features, 370 distinct. A block feeding several booms is
exported once per boom. Deduplicated on source id and geometry, or area is
overcounted four times over.

**Self-intersections.** Repaired with `buffer(0)`. Three in one batch.

**Recorded but not fixed**, on `harp_data_note`: points with no boundary,
slivers under a twentieth of a hectare, harvest dates running backwards, and
placeholder dates. Kept and annotated rather than dropped - a feature quietly
discarded is one nobody can ask about later.

### Which month a feature belongs to

Its **production** dates, not its harvest dates.

Harvest dates are 65% populated and one reads `2001-12-31`. Production dates
are complete across every line item. They record when the wood ran at the mill
rather than when it was cut - a different thing, but the right one for
assembling a month, because a month of harvest areas is a month of what was
used.

A block consumed over three months belongs to all three. Not duplication: the
same ground feeding three months of production.

---

## 3G. What arrived, and what kind it is

**The month's work comes from the delivery record.** The supply source
register is a master list of everything the client might buy from; a July run
once resolved 217 identifiers to declare a month in which 41 sources
delivered, and 159 of those identifiers are log purchases that arrive back
later as chips under an entirely different source.

Without a delivery record there is no way to know what arrived, so the
register is resolved instead and the run says so.

### Four kinds of arrival

`ORIGIN_TYPE` in the register says which. By mass rather than by count they
are not close to equal - across 2026 to date:

| | | |
|---|---|---|
| merchant residual | 304,547 BDT | 71% |
| toll chipped | 121,092 BDT | 28% |
| own yard | 4,892 BDT | 1% |
| trade | 103 BDT | 0% |

**Merchant residual is the ceiling case and it is most of the fibre.** A
sawmill selling its residual chips has no timber mark to give: the logs were
its own purchase, cut under marks it does not pass on. A search area is the
honest answer and no amount of asking the client will improve it.

That said, it is a ceiling where suppliers will not or cannot provide, not
where nothing exists - one merchant supplier declares Washington permit
numbers voluntarily, and those resolve. See 3H.

**Toll chipped is the opposite.** The client bought those logs, under marks
already in the register, and sent them to a chipper. The register records
where each log purchase went - "Direct Delivery MIDISL", "SB4 LADYSMITH-DCT" -
and that routing is the only link between a log purchase and the chips that
come back from it.

So a chip receipt from a toll chipper resolves to the pool of blocks routed
to that chipper. Real registered geometry, more than fed any one shipment,
narrowed by detection to the window: **P2a**, the same treatment a supplier's
own tenure already gets.

**Own yard material is not resolved at all.** It arrived, but it did not come
from a forest this month, and declaring it double counts the wood that did.

### Reported in tonnes

A month described as "3,781 features across nine tiers" says nothing about
how much of the fibre is placed. Described as "71% of this month is merchant
residual and bounded at district level", it is something the client can act on
- or decline to, knowingly.

`harp supply` reports this without making a single query.

---

## 3H. Supplier declarations

Two suppliers declare where their wood came from and neither does it the same
way. One exports GeoJSON, one file per contract and boom, with the harvest
boundary in it. The other prints a table of state permit numbers and scans it.

**Both are the same act.** What differs is only what they hand over.

| | | |
|---|---|---|
| geometry | taken at their word | P1d, finished |
| identifiers | resolved in a public register | P2a, searched |

A new supplier's format is a reader under the same roof, not a new path
through the pipeline, and a supplier who switches from a scan to a
spreadsheet changes nothing downstream.

### Reading a scan

The suppliers who send these are loggers, and a printed table scanned to PDF
is what arrives. It is a better target than it sounds, and **nothing about it
has to be trusted**: a permit that misreads will not resolve in the register,
and a permit that resolves is right.

Read by word position rather than as text - the OCR reads such a table column
by column otherwise, putting every supplier name in one block and every permit
in another. All twenty-one rows of one real declaration came out clean.

### Washington Forest Practices

A permit number is Washington's equivalent of a timber mark: a supplier files
an application before harvesting, the state approves it, and the approved unit
is published with its boundary.

Eighteen of twenty-one permits on one declaration resolved, to fifty-four
units, every one carrying an area. The three that did not are filed with a
tribal nation's own department and are not in the state register.

**One layer, not eight.** The service publishes the same features under eight
filters, and querying all of them returned eighteen permits as two hundred and
eighteen rows. `FPA - All Harvest by Classification` carries every polygon the
others do. `Not Digitized` is not read at all: it records applications the
state has no boundary for, which is worth knowing and is not a harvest area.

**Why P2a and not P1a.** A permit covers several approved units, and the
supplier named the permit rather than which unit fed a delivery.

---

### Species a producer stated

A producer's file names a species per product with its volume, so the mix and
the dominant both come from what they said rather than from a raster, ordered
by declared volume.

Without a dominant these counted as a blank species name in the month's tally
- forty-three of them in one month - which read as a raster class that could
not be named and was nothing of the kind.

---

## 3I. Log delivery records

The chip delivery record says how much fibre arrived. It does not say which
harvest it came from, because a chip carries no mark - which is why most of a
month resolves to a search area.

A log delivery record says the other half: **which timber marks arrived, and
when.** A mark on a real arrival names a specific harvest, so these resolve
to a cut block and are finished.

The first such file carried nine marks, four of which were not in the supply
register at all.

### Two records of two different things

Neither replaces the other, and log arrivals never enter the tonnage
arithmetic. Those logs are chipped elsewhere and arrive again later as chips;
counting both would count the same wood twice.

Their volume is cubic metres of log against bone-dry tonnes of chip, and it is
carried as stated rather than converted. The factor varies by species and
moisture, and a wrong one is worse than two units side by side.

### More than one format

Some suppliers export a scale return from their system - a sectioned file with
a header row per record type, the date on the header row and the marks on the
detail rows. Others send a spreadsheet somebody typed.

The reader looks for three facts rather than a layout:

    which mark      TIMBER_MARK, MARK, Timber Mark
    when it came    Arrival_Date, DATE_IN, Received, Delivery Date
    how much        METRIC_NET, Volume, m3, Net

A new supplier's spelling is a line in that table, not a new reader.

### What is not yet known

The file does not say where the logs were headed. A mark's entry in the supply
register records its chipper, but only for marks the register holds - and four
of the first nine were not in it. Without that link, a block resolved from a
log arrival sits in the library unreachable by a lot walkback, which selects
geometry by the chip deliveries that fed a lot.

---

## 3J. Harvest dates

Every feature that can carry one gets `HarvestStartDate` and `HarvestEndDate`,
and `harp_harvest_basis` says how each was arrived at.

### Three sources, best first

| | |
|---|---|
| the producer's own dates | where a producer's file states them |
| the feature's own detection | P1c, P2b and P3b arrive with one |
| a second detection call | for P1a, which needed no detection to resolve |

The last of those buffers every P1a block by 250 m, dissolves the buffers into
**one** MultiPolygon, and submits that once. A block whose register answered
but which no detection covers keeps empty dates and a basis saying so.

### The bracket, and what it is not

A detection date is the day a satellite observed a change. It is not the day
cutting started and not the day it finished: cloud can delay an observation by
weeks, and an operation may have run for a month before anything was visible.

**The client's convention is one month either side**, and that is what these
fields carry. The names are what was asked for; the basis field is what makes
them defensible, because `HarvestStartDate` reads as a fact and this one is
derived. The raw observation stays on `harp_detected_first` beside it.

### Matching

    a polygon    a detection covering at least 30% of it dates it
    a point      a detection inside the circle the point stands for

A polygon is the better observation and wins where both hit the same feature.
Where several hit one, the earliest wins - a block cut over three weeks shows
several, and the first is when cutting started.

### One thing that is never done

A pair that runs backwards is withheld rather than swapped or shipped.
Swapping asserts an order the producer did not state; shipping puts an end
date before a start date in front of a regulator. The basis records what was
stated instead.

---

## 3K. Species

What was growing on each harvest area, read from national rasters after the
month is assembled - so every route gets it the same way regardless of how its
geometry was reached.

| | |
|---|---|
| Canada | annual leading species, 30 m, 1984-2022, from Landsat time series trained on National Forest Inventory plots |
| United States | FIA forest type, 30 m, imputed from FIA plots (TreeMap) |

Each feature is turned into a polygon and the raster is counted by class over
it; the histogram is the composition. A point has no area, so it is read as a
circle of the area it states - read as a point it lands on one 30 m cell and
returns that cell's class at a hundred percent.

### The two are not like for like

Canada classifies the leading **species** per pixel. TreeMap has no species
band at all - it carries the forest **type**, which is named for its leading
species but classifies the stand.

Two features of the same size and pixel count:

    Canada   Western hemlock 79%; Douglas-fir 21%
    US       California mixed conifer 62%; California laurel 13%; Tanoak 9%;
             Red alder 8%; California black oak 7%

Neither is wrong. Adjacent pixels within one stand get the same species in
Canada, so a small area reads uniform; neighbouring FIA plots get imputed
different forest types, so the same ground reads mixed. **The percentages do
not mean the same thing on each side of the border and should not be averaged
together.**

A few FIA types are group labels rather than species - California mixed
conifer, misc western softwoods, other hardwoods. They carry no genus, because
there is not one, and their HS code follows whether the group is coniferous.

### Why a raster rather than the provincial inventory

The BC vegetation inventory was tried first and works. Where it has attributes
it is finer - a surveyed stand boundary with a percentage per species beats a
pixel count.

It failed on the case that matters most. Of 171 harvest polygons, 48 overlapped
inventory carrying no species at all: they were detections of recent harvest,
and the inventory had already cleared those polygons because it knew the ground
had been cut. The one thing we want to know is the thing it blanks.

### The fields

| | |
|---|---|
| `harp_species_dominant` | the single largest, for anyone wanting the simple answer |
| `harp_species` | the mix as readable text, which shows in a GIS attribute table |
| `harp_species_json` | the structured version with genus, species and HS code |
| `harp_species_basis` | source, year, pixel count, and any caveat |

A read resting on fewer than ten pixels says so. Anything under five percent is
dropped and the rest renormalised, because a list ending in four species at one
percent each is noise.

**Earth Engine is required and the stage is optional.** A run without it says
so and carries on; species is an addition to a month, not a precondition for
one.

---

## 3L. Filling the gaps

The detection service and the species rasters leave a few percent of a month
unanswered. Across two real lot files it was about 7% for dates and under 1%
for species. Those are estimated from the nearest features that do have a
value, so a month goes out complete.

**Part of the pipeline, not an option.** A deliverable with blank fields is not
a deliverable.

### How

| | |
|---|---|
| dates | the median observation of the five nearest, bracketed the same thirty days |
| species | their composition, weighted by proximity |

**Nearest by distance, not by supplier.** Two blocks a kilometre apart came off
the same forest whoever bought them; two blocks of one supplier two hundred
kilometres apart did not. Beyond twenty kilometres a feature is not a
neighbour, and where nothing is close enough the month's own median is used -
the basis says which of the two happened.

### The threshold

**Above 15% of features, the run says so loudly and the stage goes amber.**

That is not a rule about what is acceptable. It is the point at which somebody
should look, because a month where one feature in six had to be estimated is
not a month with a few gaps. The usual causes:

- the detection window does not cover when the wood was cut
- the detection record has a floor - nothing before 2025-12-01 exists to be
  found, and asking for two years returns exactly what one year returns
- a jurisdiction is unset, sending features to the wrong raster

### Saying so

Every filled feature carries `harp_estimated` and a basis in the same field
the real values use:

    estimated from the 5 nearest dated area(s), median 2026-03-15, all
    within 5.3 km. Not observed on this area.

Every other feature carries a basis naming where its value came from. An
estimate that looked like an observation would be the only one that did not,
and this is a regulatory deliverable.

`Estimated` is carried into the delivered view, so a file containing estimates
can be told from one that does not after the `harp_` fields are stripped.

---

## 3M. Library interfaces

Confirmed against the real packages, not inferred.

### eudr_geojson 0.4.0

```python
findings = validate_file(collection_dict, country_iso2="CA")
```

Returns a flat `list[dict]`; empty means clean. Never raises — internal errors
come back as 1.1.1 findings. Each finding carries `feature_id`, `sub_index`,
`production_place`, `error_code`, `error_type`, `label`, `notes`,
`geometry_type`, `wkt`.

`feature_id` is the index in the *original* features array, which is what makes
the split-and-clean design possible.

### eudr_clean 0.5.5

```python
result = clean_file(collection_dict, verbose=False)
```

Returns `valid_features`, `failed_features`, `stats`, `warnings`, `log`,
`failures`. Raises `ValidationError` on bad input.

**The feature count changes.** MultiPolygon explosion and bow-tie splitting
both turn one input feature into several, so positional indices are meaningless
afterwards. Track features by a property, never by position.

**Everything destructive is opt-in and left off.** Hole filling, vertex
collapsing, small-polygon-to-point conversion and property purging all change
what is being asserted about a plot. Those are decisions for a caller who knows
the client's position — not defaults. Spike removal and self-intersection
repair are mandatory in the library and not configurable.

`verbose` is forced off: the library logs to stdout, which would bury a run.

### ngis-eo / tracemark-eo

Placement confirmed against the source; see Stage 3c. Two functions, chosen by
the size of the search area, not by preference.

```python
# bounded - a parcel. Dissolves per search polygon, keeps joinID.
from pointtopoly import polyToChangeDetectionPoly_DIST
fc = polyToChangeDetectionPoly_DIST(cdSearchPolys, startDate, endDate, ...)

# large - a district catchment. Grids at 200 km, separate polygons,
# exports to BigQuery and returns nothing to the caller.
from harvest_generation import generate_change_detection_polys
generate_change_detection_polys(region, startDate, endDate, ...)
```

Still open: whether HARP holds the Earth Engine service account itself or posts
to a service that does. HARP will run on GCP and will have its own service
account, so the library route is viable; the argument for a service is that
Earth Engine quota is per project and shared across NGIS engagements.

---

## 4. Precision tiers

Two axes, and they answer different questions. **The tier** says how tightly
the harvest is bounded. **Traceability** says how the geometry was reached.
Both travel on every feature.

### The tiers

| Tier | What it is | Detection |
|---|---|---|
| **P1a** | the harvest block itself, from a public forest register | not needed |
| **P1b** | the titled parcel a mark was scaled from — a place to look | — |
| **P1c** | a harvest detected within one, carrying that mark | yes |
| **P1d** | a harvest area the producer declared, in their own file | not needed |
| **P2a** | a registered harvest area attributable to a supplier | — |
| **P2b** | a harvest detected within one, carrying its mark and holder | yes |
| **P3a** | a search area — district, county, national forest | — |
| **P3b** | a harvest detected within one, attributed to that supplier | yes |
| **P4** | nothing resolved | — |

**P1a is the only tier that needs no detection.** It is the harvest, named by
an identifier on the delivery. Every other letter pair separates before and
after a detection run.

**P1 stays apart from P2 because of where the mark came from.** A parcel's mark
is on the client's own delivery record — they bought timber under it, and it
was scaled from that parcel. A tenure block's mark came from querying a company
matched by name, and nothing says the client bought any of it. The geometry is
the same shape; the chain of evidence is not.

**P4 is the grease trap.** Identifiers nobody has explained, supplier codes
with no company behind them, blanket authorities covering a whole class of
land, and material that is out of scope. Sourcing for all of it is to be
determined. It stays visible in the reporting rather than being quietly
dropped, because an unresolved source is a client question rather than a
pipeline failure.

### Traceability

| | |
|---|---|
| **direct** | tied to the fibre by an identifier on the delivery itself — P1a, P1b, P1c |
| **declared** | the producer gave us this boundary and stands behind it — P1d |
| **indirect** | attributable to the supplier, but reached through the company rather than the delivery — P2a, P2b |
| **inferred** | an area, or a detection within one. Nothing links this ground to this supplier except overlap — P3a, P3b |

This replaced a `plot_claimable` flag. That flag asserted a regulatory
position, and whether a tier satisfies a given test is a judgement for whoever
makes the declaration. The pipeline records the method and does not pre-empt
it.

**One case where the two axes disagree.** A P1a block reached through a tenure
holder rather than through a delivery is *indirect*: the tier describes the
geometry, traceability describes how we got to it, and they are allowed to
differ.

### Rules

- A tier is assigned by what actually constrained the geometry, never by what
  was hoped for.
- Aggregated commodities may legitimately carry mixed tiers. The mix is
  reported, never averaged away.
- **A search area is never declared.** P1b, P2a and P3a exist to be searched.
  What reaches a declaration is the detection found inside them.

### What reaches a declaration

The rule above has a mechanical consequence: a tier that exists to be searched
emits no `sce_base` row.

| Tier | `eudr_sub_type` |
|---|---|
| P1a | `database_polygon` |
| P1c, P2b, P3b | `catchment_polygon` |
| P1b, P2a, P3a | *no row — a place to look* |
| P4 | *no row — nothing resolved* |

**All three detected tiers share a sub type** because it describes where a
polygon came from, not how good it is. Every one is ours, derived from imagery,
whatever the strength of the identifier that led us to the area. The tier and
the traceability value carry the difference; the sub type should not try to.

**Two absences, one outcome.** A search area produces no row because it is
waiting on detection; an unresolved source produces none because there is
nothing to report. `normalise.why_no_row` tells them apart, because reporting
them as one number would hide that the first is working as intended.

**A consequence worth stating to a client.** A supplier whose only resolution
is a search area, and inside whose area no detection was found in the window,
contributes no declared geometry for that month. That is correct - nothing
places a harvest there in the period - but it reads oddly against a coverage
table showing them resolved.

---

## 4A. Sorting a package

A client sends a folder holding a job list, one or more registry extracts, and
in time other things nobody has described yet.

**Recognise by columns, never by filename.** Filenames in this data have been
proven wrong three separate ways: a workbook named "June 2026" whose data sheet
is "January 2026" and whose records were processed in February; a "Calendar
Year" label on files that are demonstrably not year-to-date; and a
`ProcessedOn` that varies per record rather than per file.

| Kind | Signature | Behaviour |
|---|---|---|
| job list | `SOURCEID` | **replaces** — the current statement of what needs answering |
| registry extract | `TIMBER_MARK` + `PID` | **accumulates** — never replaced |
| supplier geodata | `geometry` | attaches to one source |
| unknown | — | reported with the columns it had |

A file matching no signature is a finding, not an error: a new kind of file has
arrived and needs a signature adding.

---

## 5. Resolver — British Columbia

Fully specified. Every rung below has been executed against real client data.

### 5.1 Registries

| Registry | Endpoint | Notes |
|---|---|---|
| FTEN cutblock polygons | `mpcm/bcgwpub/MapServer/340` — `FTEN_CUT_BLOCK_POLY_SVW` | Crown tenure only |
| FTEN harvesting authority | `.../383` | Permit-level outline |
| Timber licence | `.../427` | |
| Consolidated cutblocks | `.../543` | Harvested areas |
| RESULTS openings | `.../442` | Silviculture; longer memory than FTEN |
| NR districts | `.../748` | Carries `REGION_ORG_UNIT_NAME` |
| Forest cover ownership | `.../238` | Ownership class — used for P3 |
| ParcelMap BC | `.../218` | Tested, no usable key. See §5.5 |
| Harvest Billing System | `a100.gov.bc.ca/pub/hbs/opq/timberMarkQuery.do` | HTML screen, public, no login |

Base: `https://delivery.maps.gov.bc.ca/arcgis/rest/services/`

### 5.2 The ladder

```
R1  FTEN 340  TIMBER_MARK = <id>                        → P1
R2  FTEN 340  HARVEST_AUTH_FOREST_FILE_ID = <id>        → P1
R3  FTEN 340  CUT_BLOCK_FOREST_FILE_ID = <id>           → P1
R4  FTEN 340  file ID + cutting permit (split on '/')   → P1
R5  HBS       timber mark query                         → classify, do not stop
      ├─ CROWN   → R6
      ├─ PRIVATE → R8
      └─ absent  → P5, client question
R6  FTEN 383  licence from HBS                          → P2
R7  FTEN 340  CLIENT_NUMBER + GEOGRAPHIC_DISTRICT_CODE  → P2
R8  catchment: district ∩ ownership ∩ holder footprint  → P3
R9  district only                                       → P4
```

**R1 does the work.** Across 217 Harmac identifiers, every single resolution —
71 of them — matched on `TIMBER_MARK`. Licence numbers, cutting-permit-shaped
codes and alphanumeric marks all live in that one field. R2–R4 have never fired
and are retained only as cheap insurance.

**R5 is the pivot.** HBS holds a record for every mark issued in BC, including
private ones absent from all tenure geometry. It returns holder name, client
number (the same key FTEN uses), natural resource district, region, file type,
quota type, managed unit and validity dates.

**R7 is keyed, not fuzzy.** HBS `Client No` = FTEN `CLIENT_NUMBER`. Do not match
tenure holders by name — see §5.4.

### 5.3 Land basis classification

Decided from the file type and managed unit text on the HBS record, quoted
verbatim into the evidence manifest.

| Verdict | Signals | Route |
|---|---|---|
| PRIVATE | `B08 Exportable Crown Grant`, `B09 Non-Exportable Crown Grant`, `Private Timber Mark`, `Z Outside Managed Units` | R8 |
| CROWN | `U Timber Supply Area`, TFL, community forest, woodlot, `B04 Forestry Licence to Cut` | R6 / R7 |
| UNCLEAR | neither | manual review |

### 5.4 Traps — all found the hard way

**Name matching fails on legacy entities.** A LIMS supplier name is not the FTEN
client name. Harmac buys from "Mosaic Forest Management"; the marks are held by
TimberWest Forest I, TimberWest Forest II, TimberWest Forest Corp and Island
Timberlands GP. Searching FTEN for "Mosaic" returns nothing and produces a false
conclusion that the supplier holds no tenure. **Always route through HBS to get
the client number.**

**Generic words match hundreds of holders.** `CEDAR`, `VALLEY`, `ISLAND`,
`PACIFIC` returned the same few dozen unrelated clients for several different
suppliers. Name search must be tiered — full name, then leading words, then
distinctive words, then generic — stopping at the first tier that hits, and
labelling anything found on a generic word as low confidence.

**Partial keys are not unique.** Matching a cutting permit number alone
(`243`) returned hundreds of blocks across unrelated licensees province-wide.
Never accept a partial-key match as a resolution.

**`resultOffset` and `returnDistinctValues` are unreliable.** The service ignores
both on some queries — pagination silently returns the same page, and distinct
queries return duplicated rows. Page using `OBJECTID` as a cursor; deduplicate
client-side.

**Do not put date filters in the WHERE clause.** `DISTURBANCE_END_DATE IS NOT
NULL` forces a full scan of 222,129 blocks and turns a sub-second lookup into
tens of seconds. Query on the identifier, filter the handful of returned rows
locally.

**The identifier field carries things that are not identifiers.** Company names,
delivery modes (`TRUCKED`, `WATERED`), dryland sort IDs (`DSI…`). These are a
client data quality finding, not a resolver failure. The `0R1` suffixes turned
out to be real Crown marks held by First Nations and community forest entities —
FTEN simply files their blocks under the holder rather than the mark, so R7
resolves them.

**A miss and an outage are not the same thing.** A transient FTEN failure
returning an empty list once demoted a P1 cut block to a P2 district envelope,
silently. The geometry was fine; the tier was wrong, and tier is what decides
whether a result may stand as a plot claim. Queries now retry, then raise, and
the ladder stops rather than falling through to a weaker rung.

**Do not reuse a key name that a parser also writes.** A provenance string was
stored under `licence`, which was also the parsed HBS licence number. R6 spent
several runs querying FTEN for `TIMBER_MARK = 'CONTAINS INFORMATION LICENSED
UNDER THE OPEN GOVERNMENT…'` and could never have worked. Values sent to a
registry are now sanity-checked for shape before use.

**A single page is not a count.** A holder query returned exactly 1,000 blocks —
the page size, not the total. Anything holder-scoped must page on `OBJECTID`;
the true figure for that holder was 2,283.

### 5.5 Ruled out

**ParcelMap BC.** Holds parcel geometry, PID and owner *type*, but the public
release excludes owner names. No key links a timber mark to a parcel. Tested
against numeric identifiers including zero-padded and dashed PID forms — no hits.

**Managed Forest Council.** Collects annual harvest declarations from private
managed forest owners, but publishes aggregates, not geometry.

**Conclusion:** there is no public register of private BC harvest areas. Private
geometry exists only with the landowner.

### 5.6 P3 catchment construction *(design — not yet built)*

For a PRIVATE verdict, intersect:

1. NR district from HBS — the outer bound
2. Private forest ownership class (layer 238) — removes Crown land, which is
   most of the district by area
3. E&N Land Belt where the HBS managed unit reports it — a mapped historical
   grant boundary, present on 7 of Harmac's 30 private marks
4. The holder's known Crown footprint in that district, where they have one, as
   an indicator of operating area

Expected reduction: district alone is roughly 10⁶ ha; district ∩ private forest
should be an order of magnitude smaller. **To be measured, not assumed** — the
tier assignment depends on it.

> **Open:** does a defensible P3 require constraint 2 as a minimum? Current
> thinking is yes, and district-only output is P4.

---

## 6. Resolver — other jurisdictions

> **Placeholder.** Structure agreed, content not researched beyond feasibility.
> Each will need its own trap list, and none should be assumed to work like BC.

### 6.1 United States — general

**Every US jurisdiction is a separate route.** There is no federal equivalent of
FTEN, no national timber mark, and no single identifier that crosses state
lines. State registers differ in coverage, in what they publish, and in whether
harvest areas are spatial at all. Federal land is a third system again.

For Harmac this is 11 sources in Washington and one each in Alaska, Oregon and
California — small by count, material by volume (Washington was 23% of July
intake).

### 6.2 Washington

Confirmed to exist, not yet queried.

- **FPARS** — Forest Practices Application Review System, WA DNR. GIS downloads
  as shapefile, geodatabase or KML from the DNR open data portal, with ArcGIS
  REST services for some layers.
- Covers private and state forest land under Ch. 76.09 RCW.
- **Known limits:** excludes applications older than 10 years, and road-only
  activity.
- **Expected shape:** no equivalent of a timber mark. Likely two-step — obtain
  the mill's log purchase list, then resolve each entry.

### 6.3 Oregon

- **FERNS** — ODF notification polygons, public ArcGIS FeatureServer at
  `gis.odf.oregon.gov`, queryable by year and geometry.
- Structurally closest to the BC pattern of the US options.

### 6.4 Alaska

- Tongass National Forest data via USFS and the Alaska Geoportal.
- **Major gap:** Native corporation, State of Alaska and Mental Health Trust
  lands have no equivalent public harvest-unit register.
- Lowest confidence of any jurisdiction encountered.

### 6.5 California

- CAL FIRE Timber Harvesting Plans. Not researched.

### 6.6 Non-forestry commodities

> **Placeholder.** EUDR covers cattle, cocoa, coffee, oil palm, rubber and soy
> alongside wood. The stage model should hold; the resolvers will not. No work
> done.

---

## 6A. The monthly input library

A run reads a folder. The library is how those folders are organised, and it
uses the layout the cloud deployment specifies:

    <intake>/YYYY-MM/<submission-id>/
        SOURCE.xlsx
        <delivery record>
        <private mark extract>
        <producer declarations>
        <log delivery record>
        <lot list>

`paths.intake` in config - a local path or a `gs://` one, with nothing else
changing.

**The submission id is for corrections.** A month arrives as `-001`. If a file
is later found to be wrong, the corrected month arrives as `-002` and the
original stays exactly as it was processed. Without it a correction either
overwrites what was actually used or sits beside it with nothing to say which
is which. The latest is used unless one is named.

It is not for batching: a delivery spread over two days is still one
submission.

**The library is read across months, not just for the month being run.** A lot
walkback reaches back until the mass is covered, and that can span several
months of deliveries.

### What a month needs

| | |
|---|---|
| delivery record | required - the month's scope comes from it |
| supply source register | required - what each delivered source is |
| private mark extracts | strongly wanted - without them private marks stay at a weaker tier |
| producer declarations | whatever suppliers sent, in whatever form |
| log delivery record | which marks arrived, where available |
| lot list | for `harp lot`, not for the run |

Files are recognised by their columns, never their names. Anything
unrecognised is reported rather than rejected.

**A stale extract reads as valid.** The mark extracts are snapshots rather
than cumulative, so a mark scaled in June can be absent from a file exported
in August - the current one is needed each month.

---

## 6B. Monthly drops

The client's LIMS export arrives on a cycle and mostly repeats itself.
Resolving all of it every month is wasteful and buries the rows that moved.

`drop.compare()` takes this month's records and last month's *manifest* — not
last month's spreadsheet, because the manifest carries what we concluded rather
than only what we were sent.

| | |
|---|---|
| **new** | resolve |
| **changed** | re-resolve, and say what moved |
| **gone** | report. A supplier who stopped delivering is a fact about the supply chain, not an absence of data |
| **unchanged** | carry the previous answer forward |

Only `identifier`, `jurisdiction`, `product_type` and `supplier_id` count as
material changes. A tidied supplier name does not justify a query.

**Keyed on the client's own source id**, falling back to the identifier. An
identifier alone is not unique — `PRINCETON` appears under both Gorman and
Weyerhaeuser in the Harmac data, and keying on it reported 55 spurious changes
where nothing had moved.

Combined with the cache, a monthly run touches the network only for what is
genuinely new: an HBS record resolved in August is not re-queried in September.

---

## 6C. History, retention and the declaration window

Two different questions that get confused with each other.

### What we declare — a rolling window

A declaration covers the harvest areas behind the fibre in a shipment. Not
everywhere a supplier has ever cut.

**The window is 24 months by default**, applied as a filter at query time.

The right length is a client question, not a design decision. A chip delivered
in August was not cut in August: the tree was felled, the logs sat in a yard,
went to a chipper, and the chips sat in a pile. Harmac reclaims its piles LIFO,
so material at the bottom can be old. The window has to cover stump to digester,
and only the client knows that number.

### What we keep — everything, archived

Raw extracts are archived as received and never deleted. Not queried, not
declared — just kept.

If someone asks in 2028 why a particular parcel was declared in March 2026, the
answer is the file we used. EUDR requires five years of due diligence records,
so this is not optional, and a roll-off is irreversible in a way a filter is not.

**The two are independent.** The store grows; the window is a view of it. Nothing
is lost by keeping a file, and nothing is over-declared by keeping it either.

### Overlap between drops

Already the normal case, not a future risk.

**Registry extracts** overlap heavily — any two of the six private mark files
share 15–50% of their marks. Deduplicated on **(mark, PID)**: 18,347 raw rows
become 6,676 pairs. Same mark in three files, same parcel, one row.

**The job list** overlaps almost entirely — it is the same suppliers each month.
That is what the diff is for: new, changed, gone, carried forward.

### The gap in the job list

`SOURCE.xlsx` is a snapshot of who supplies the client *today*. It carries no
dates and no history, so a supplier who delivered in early 2025 and stopped may
simply be absent.

That matters when declaring against fibre already in the pile. Either the client
can produce a historical version, or the delivery records are the better source.
**Raised with Harmac; unresolved.**

---

## 7. Source classification

Stage 1 assigns a class. The class determines which ladder runs and how many
supplier interactions stand between HARP and an answer.

| Class | Meaning | Action | Automatable |
|---|---|---|---|
| A | Harvest identifier, public register | Query it | Yes |
| B | Harvest identifier, no public geometry | Request from holder | No |
| C1 | Intermediary processing the client's own material | Resolve via client's own records | Yes, once linked |
| C2 | Third-party processor, one tier back | Request their purchase list, then re-classify | No |
| D | Aggregation point, two or more tiers back | Unwind the chain, then treat as C2 | No |
| E | Not a harvest identifier | Establish what the field means | No |
| N/A | Internal to the client | No external acquisition | — |

Two questions decide the class, and neither is client-specific:

1. Does the record carry a harvest identifier?
2. If so, does a public register hold geometry for that tenure type?

Jurisdiction answers question 2. It is not itself a class.

### Class B is not predictable from the string

FTEN holds 83,916 timber marks beginning with `E`. Harmac's sixteen `E` marks are
simply not among them. Crown and private marks are indistinguishable by format —
only the query separates them.

---

## 7A. Search areas

Where no identifier resolves to a harvest area — and where one resolves only to
the land a mark was scaled from — HARP produces a **search area**: a bounded
region within which the harvest lies. Change detection then finds the ground
that was actually disturbed inside it.

**Nothing here is ever declared.** What reaches a declaration is the detection,
carrying whatever the area could tell us about it.

### Six methods, applied in order

A supplier gets the best available; weaker methods run only where stronger ones
produce nothing.

| | Method | Source | What it is |
|---|---|---|---|
| 1 | Harvest area | FTEN 340 | the cut block itself. Not a search area |
| 2 | Titled parcel | private mark extracts → ParcelMap 218 | the land the mark was scaled from |
| 3 | Operator tenure | FTEN 340 by client number | every block the company holds |
| 4 | State register | Washington FPARS by company name | every application filed under that name |
| 5 | Named area | BC districts 748, US Census counties, USFS | the administrative area the mill sits in |
| 6 | None | — | no geometry created; the gap is recorded |

### Why parcels are here

A titled parcel was originally treated as an answer, at P3. It is not. Across
one month the parcels ran to **303,434 ha against 71,274 ha of detected
harvest** — a median of 41 ha with a tail to 1,926 — so declaring the parcel
over-declares by roughly four times.

The parcel is the ownership boundary; the cut is somewhere inside it. So the
parcel is submitted for detection like any other area, **and its timber mark
travels with it**, so a detection inside inherits a mark that came off the
client's own delivery record. That is what keeps it at P1c and directly
traceable rather than dropping to an inference.

### Where these came from

Not invented here. All three live NGIS deployments solve the same problem and
were read before this was built.

**Domtar** — `domtar/logical/supplier_geofence.py`. A supplier declares named
administrative areas; those join to published boundaries. Several areas per
supplier explode into separate records. A supplier answering *"potentially all
counties"* gets **null geometry** — an unbounded answer is recorded as no
answer. That rule is copied directly.

**Enviva** — `Tracemark_API/Enviva/pipeline.py`. A mill point buffered by a
radius held **per mill** in config, then intersected against harvest polygons.
The buffer is a query filter, never the declared area.

**Billerud** — `create_buffers` sizes a buffer by the **volume** a source
produced rather than by an assumed haul distance. Better than a fixed radius
where per-source volumes exist, which for this client they do.

### Declared against inferred

Every feature carries `harp_declared_by_supplier`. An area a supplier *told us*
and one *we inferred from their mill's location* are both usable and are not
equivalent. At the time of writing every area in the layer is inferred: four
suppliers returned unmarked road maps of an entire state, one a sentence naming
no district.

### The mill is not the forest

Where an area came from a mill location, it says where the operation is based,
not where the wood grew. Two examples that make the point:

- **Richmond Plywood** holds 270 cut blocks in BC and none in the district its
  mill occupies. An earlier build filtered operator tenure *by* the mill's
  district and silently produced zero — under-declaration, which is the failure
  mode that does not survive an audit. The district is now a flag, not a filter.
- **Roseburg's Coos Bay** site is a chip export terminal, not a mill. The chips
  are made at Coquille, Dillard and Riddle, so the terminal's county alone
  would miss where the wood comes from.

### The mill town route

A source identifier usually carries the mill town — `PARKSVILLE`, `MERRITT`,
`CASTLEGAR`. Mapped to a district or county, that places ten suppliers who had
no other route, including one for whom no company name exists anywhere in the
client's data.

Weaker than Domtar's, whose areas were supplier-declared. Recorded as inference.

### No cap on operator tenure

An earlier version stopped at 3,000 blocks per supplier. That is
under-declaration by accident, and it was silent — the output said *"2,459
blocks"* whether that was all of them or a truncated set. There is no cap now;
if one is set, the true total is fetched first so the shortfall is visible.

---

## 7B. The supplier alias table

`harp/aliases.py`, `data/registry/supplier_aliases.csv`.

Matching a supplier name to a tenure holder is not solvable by a better
algorithm, because part of the answer is not in the names. Teal-Jones Group
owns Teal Cedar Products; no string comparison discovers that.

**So the matcher proposes and the table decides.** Three states: `accepted`,
`rejected`, `proposed`. A proposal is **not** a weak acceptance — it is absent
from any output until someone rules, because a supplier's whole tenure is
thousands of blocks and attaching the wrong company would be wrong rather than
merely broad.

Only an exact name match is auto-accepted. Re-proposing a decided row is a
no-op, so tightening the matcher later cannot silently change a historical
answer.

**Shared, not client data.** Weyerhaeuser will appear on other engagements.

### What the matcher learned

Every rule below was added because a specific wrong match got through.

| Rule | The match it stopped |
|---|---|
| Industry words carry no identity | `Cedar` matched Aquila, G&R and Teal Jones all to Teal Cedar Products |
| Verification is bidirectional | `Imperial Fibre` → `IMPERIAL OIL RESOURCES`; every supplier word present, but `OIL` unexplained |
| Prefixes are not matches | `Alta` → `ALTAGAS HOLDINGS` |
| A single identifying word must start the client name | `Star Lumber` → `NORTH STAR PLANING`; the company is North Star |
| Abbreviations expand | `Coastland Wood Ind.` failed against `COASTLAND WOOD INDUSTRIES` |
| Geography is not always noise | `PACIFIC` in the noise list matched `Nicola Post & Rail` to `NICOLA PACIFIC FOREST PRODUCTS`, five times its size |

Anything that shares a supplier's name but carries an identifying word of its
own is reported as **possible** and never used — `Gorman Group` against
`GORMAN BROS. LUMBER` may be one firm, and the names do not prove it.

---

## 7C. Reference data

Four files the pipeline reads on every run, shipped inside the package rather
than pointed at by hand.

| | |
|---|---|
| `supplier_register.csv` | who has what, and who still needs a search area |
| `supplier_aliases.csv` | supplier to tenure client number, with who decided |
| `supplier_locations.csv` | mill location and district per supplier |
| `supplier_areas.csv` | operating areas stated by hand |

They are ours rather than the client's - a few hundred rows of decisions made
once and reviewed since - and they change rarely.

**Requiring somebody to select them is itself a failure mode.** A run without
mill locations falls back to a circle round a town name for every supplier and
says nothing; a run without the register builds no search areas at all. Both
have happened.

### Which copy is used

    1  whatever the caller passed in            an explicit choice wins
    2  `sources.reference.path` in config       a client with different data
    3  data/registry in the working folder      what a dev checkout has
    4  the copy inside the package              always present

Three exists so a file somebody is editing locally keeps being used. Four is
the floor, not an override.

A run lists all four and their row counts before resolving anything, and the
Setup tab shows them beside the libraries.

### A register that asks for nothing

A report *about* the suppliers reads perfectly well as a register - the right
sheet, the right column names, eighty-five rows - and reports that nobody
needs a search area. The run then builds none and says nothing.

So both the run and the desktop window check for that specifically: a register
with suppliers but no outstanding sources is either a finished month or the
wrong file, and it is worth saying which before two hundred sources are
resolved against it.

---

## 7D. Asking the tenure register

The ladder used to ask one question per identifier per rung: 221 identifiers
across three fields is 663 sequential requests, with nothing kept between
runs. That is enough to get throttled, and a re-run asked every one again.

**Batched.** `TIMBER_MARK IN (...)` in groups of fifty, before the ladder
starts, and the ladder reads the result. Six requests instead of 663.

**Probed first.** A single identifier is tried before committing. If it fails
the field is abandoned in one request rather than discovering the same thing
fifty times.

**One fallback level.** A batch that fails afterwards is asked identifier by
identifier - once, not recursively. An earlier version halved a failed batch
and halved again while retrying every piece with backoff, which turned a short
outage into hours of silent waiting.

**Cached between runs.** A week for a hit, two days for a miss. The point is
not speed: a run that gets partway before something stops answering now keeps
what it got, and a re-run asks only for the rest. A service error is never
cached - that would make an outage permanent.

**And the retry discriminates.** A 429, a timeout or a server-side error is
worth trying again. A 400 is not, and retrying it three times with backoff
costs ninety seconds to learn what the first attempt already said.

### A second door

The tenure register is published twice: an ArcGIS REST service and a WFS
endpoint on a different host. They return identical records - one timber mark
gives the same licence, block and client name either way.

REST is tried first; WFS is tried when REST fails outright. A run no longer
depends on one server being up, which it did on the day the REST service was
unavailable and every run resolved almost nothing.

**The distinction that matters is preserved.** An outage must never read as
"no such record" - a blip on the first rung once demoted a cut block to a
district envelope, which is a wrong answer wearing the shape of a right one.
The fallback is tried before concluding anything, and if both doors are shut
the run says so.

### An index that knows what it was not asked

`by_field` gets one of three answers from the index:

| | |
|---|---|
| rows | a hit, no request |
| empty | a real miss, no request |
| `None` | nobody asked - query it live |

The third is the one that matters. A field whose prefetch failed, or an
identifier the service refused, was never answered. Reading either as a miss
would say the register does not have something it was never given.

---

## 8. Data contract

### 8.1 Input

| Field | Required | Notes |
|---|---|---|
| `source_id` | yes | Client's own key |
| `identifier` | yes | The raw value from the client system |
| `supplier_id` / `supplier_name` | yes | For fallback and reporting |
| `jurisdiction` | yes | Country + state/province |
| `product_type` | yes | Log, chip, other — drives class assignment |
| `delivery_window` | no | Filters returned geometry by disturbance date |
| `class` | no | If pre-assigned; otherwise Stage 1 assigns it |

**On evolving supplier lists.** A client's supplier register changes constantly.
HARP must not depend on a curated list. The minimum viable input is identifier +
jurisdiction. Classification is derived, cached, and re-derived when the
identifier changes — not maintained by hand.

### 8.2 Output — geometry

Three files from a run, then one from the join back. **All four carry the same
schema**, so a consumer can read any of them without knowing which it opened
and tell them apart by `harp_geometry_kind`.

| | |
|---|---|
| `harvest-areas-*.geojson` | P1a. The harvest itself. Finished. |
| `tenure-blocks-*.geojson` | P2a. Real blocks, wrong scope. To be searched. |
| `search-areas-*.geojson` | P1b and P3a. Places to look. |
| `harvest-YYYY-MM.geojson` | the month, after detection |

GeoJSON, WGS84. Per feature:

```
harp_supplier            who supplied the fibre
harp_supplier_code       their code in the client's system
harp_jurisdiction        BC, WA, OR, CA, AK
harp_geometry_kind       cut_block | parcel | tenure_block | district |
                         county | national_forest | mill_buffer |
                         detected_block
harp_method              the rung or method that produced it
harp_source_system       the register it came from
harp_key                 the identifier it was found by
harp_key_name            that identifier spelled out
harp_timber_mark         where one exists
harp_district            where one is known
harp_area_ha             measured on the ellipsoid, not in degrees
harp_tier                P1a … P4
harp_traceability        direct | indirect | inferred
harp_is_envelope         whether the geometry is broader than the purchase
harp_declared_by_supplier  whether they told us, or we inferred it
harp_basis               how the area was arrived at, in words
harp_note                what it does and does not represent
```

After a detection run, a feature adds:

```
harp_detected            true
harp_detected_first      the date the disturbance was first seen
harp_detection_type      polygon, or point below four hectares
harp_parent_kind         the kind of area it was found inside
harp_parent_area_ha      how large that area was
harp_evidence            what this rests on, in words
```

**`harp_parent_area_ha` is worth carrying.** It is the difference between what
was searched and what was found, and that ratio is the honest measure of how
much the round trip narrowed things.

### ProducerName

Every feature carries `ProducerName`, in EUDR casing rather than the `harp_`
prefix, because it survives the eventual schema refactor unrenamed.

| | |
|---|---|
| `ProducerName` | who cut the wood, as the register spells it |
| `harp_producer_number` | their client number in that register |
| `harp_producer_source` | `forest register` or `client record` |

**The client's own alias is kept alongside it** on `harp_supplier` and
`harp_supplier_code`, so a name that changed can always be traced back to the
code it came from.

**A bare supplier code is never used as a producer name.** A code identifies a
purchasing arrangement rather than a company, and may not be one company at
all: `WWW` reached a customer deliverable once and turned out to cover six
unrelated holders, including a regional district clearing an airport runway.
Where no register named a holder and the client's own name for the supplier is
just the code, the field is left empty. Visibly empty beats plausibly wrong.

**One caution worth stating.** A tenure holder is who held the tenure, which is
not always who the client paid. Wood bought through a broker or a reload names
the holder in the register and somebody else on the invoice. `ProducerName`
answers the question EUDR asks - who produced it - and should not be read as
the counterparty.

**Geometry repeats where two suppliers share an area.** Each carries its own
copy so a detection can be attributed. The geometry repeating is not
duplication to be cleaned up; it is the attribution doing its job.

### 8.3 Output — evidence manifest

One record per source, resolved or not:

```
source_id, identifier, class, jurisdiction, rungs_attempted[],
matched_rung, precision_tier, verdict, verdict_basis,
tenure_holder, client_number, district, land_basis,
raw_record_ref, retrieved_at, unresolved_reason
```

`raw_record_ref` points at a stored copy of the source record — for HBS, the
saved HTML page. Retaining raw records is what makes a verdict auditable rather
than assertable.

---

## 9. Caching

| Data | Cadence | Reason |
|---|---|---|
| HBS mark record | Permanent, re-check annually | Registry fact — holder, land basis, district do not change |
| FTEN geometry | Per run | Blocks are added and retired |
| Layer schemas | Per session | Field names have changed before |
| District/region lookups | Permanent | Administrative boundaries are stable |
| Negative results | 30 days | A mark absent today may be added |

The HBS cache doubles as the evidence store. 30 Harmac records already exist and
seed it.

---

## 10. Operational constraints

**HBS is scraped, not an API.** A public HTML screen with no contract behind it.
It will break. Mitigate: cache aggressively, save every raw page, fail soft,
alert on parse failure rather than silently emitting empty fields.

**Be polite to government services.** Rate limit. Current practice is 0.4–0.5s
between requests, single-threaded.

**Attribution is mandatory.** BC data carries the Open Government Licence —
British Columbia; attribution must appear on every derived output.

**Privacy.** Some HBS holder records return "Not Releasable" — individual persons
rather than companies. Three of Harmac's thirty. Do not attempt to circumvent;
record as unavailable.

---

## 11. Current baseline — HPA1 Harmac

*Updated 1 September 2026.*

**Resolution.** 280 sources, 221 distinct identifiers. 140 cut blocks from
timber marks; 1,830 titled parcels from private marks, which are search areas
rather than answers.

**Search areas submitted.** Around 9,600 features unioned into 41 parts,
spanning British Columbia, Washington, Oregon, California and Alaska.

**Detection.** Confirmed working over the Pacific Northwest: 664 detections
across an 80 km test square on north Vancouver Island, dated to mid-August,
378 of them points below four hectares.

**The number that matters.** Search areas totalled 303,434 ha of parcel and
143,837 ha of tenure. What is declared is 71,274 ha of detected harvest. The
difference is the whole point of the round trip.

**Lots.** June's 69 lots needed 48,687 BDT of chips against July's 59,350 BDT
of deliveries - a month's production consuming about 82% of a month's intake,
which is what inventory looks like. Chips to pulp came out at 1.72:1.

**Producer names.** `WWW` was found to be not a supplier at all: sixteen marks
resolving to six unrelated holders, including a regional district clearing an
airport runway and a power company. `WWK` and `WEW` both resolve cleanly to
Cape Mudge Forestry Ltd. That is why `ProducerName` now comes off the register.

### Superseded

Where the first client stands. Useful as a reality check on any design claim.

| Status | Sources | Share |
|---|---|---|
| P1 — cut block geometry | 87 | 31% |
| Identified, private, holder known | 31 | 11% |
| Chip supply base, not yet started | 108 | 39% |
| Identifier meaning unknown | 41 | 15% |
| Internal | 9 | 3% |

**Resolved geometry:** 140 cut blocks, 2,315 ha.
**Identifier resolution rate:** 71 of 217 (33%) province-wide; 74% among
identifiers that were genuine harvest authorities.
**HBS coverage:** 30 of 30 unresolved marks found, 29 private, 1 Crown.

**What this says about the design.** The registry path is real and automatable,
but it covers a minority of sources. Most of the remaining work is supplier
engagement, not engineering. HARP's job is to make that work systematic and to
be unambiguous about what it has and has not established.

---

## 12. Open questions

**Answered since v0.7.0:**

- *Should detection be a separate command?* No. A run that stops before it has
  produced places to look rather than a month.
- *Where does the declared producer name come from?* The register, not the
  client's code. A code identifies a purchasing arrangement and may cover
  several companies.
- *Does the EUDR projection happen before or after validation?* The fields are
  added before, the stripping happens at delivery. Validation ignores extra
  fields, and a lot is resolved against them.

**Still open:**

1. **How long is the declaration window?** 24 months is the default; the right
   answer is stump-to-digester residence time and only the client knows it.
2. **Which `eudr_clean` opt-ins should be on?** Hole filling, vertex collapsing
   and small-polygon-to-point conversion each change what is being asserted
   about a plot. All off.
3. **A sub-four-hectare detection comes back as a point.** It carries an area
   but no boundary. Admissible as a plot, or does it need a buffer of its own
   stated area? Not the 1 km buffer `pointtopoly` applies, which would
   over-declare by a hundredfold and breach the four hectare ceiling.
4. **How current is the detection table?** A Georgia control returned nothing
   after 2 June while the Pacific Northwest ran to mid-August. That looks like
   a stale regional copy rather than a lag, and a month cannot be declared
   against a table that stops before it.
5. **Whether a lot's weight is air-dry**, and the direction of the
   `BDU/m3` factor. Both assumed and both defensible; neither confirmed.
6. **`apply_completion_rule`** is in both configs and read nowhere. It looks
   like it governs resolution and does not.
7. **Where HARP ends and TraceMark begins** — geometry only, or also the risk
   assessment against it?
8. **A mixed-tier collection** reports the full distribution and can be split
   on traceability. Is that the right contract, or does TraceMark want one
   number?

---

## 13. Roadmap

**Built since v0.7.0:** detection inside a run, the monthly library and its
approval gate, the lot walkback, stated operating areas, `ProducerName` from
the register, and the EUDR projection.

**Next, in order of value:**

**A prefetch for the tenure register.** Every source runs three separate
queries before anything else, and nothing is cached between runs - so a rerun
of the same month costs as much as the first. Batching the identifiers into one
query per field would take roughly 660 requests down to 15, and caching would
make a second run near-instant. This is the single biggest improvement
available and it is contained.

**Supplier-provided harvest references.** Willis Enterprises have offered
per-load Washington permit numbers, which resolve straight to harvest geometry.
Any supplier who can do the same moves from a search area to method 1. This is
worth more than anything the pipeline can do on its own.

**An append-only registry store.** The private mark registry rebuilds from the
extracts folder each run.

**Naming the unnamed codes.** `COS` and `WEW` remain unexplained. `WWW` is
resolved - it is a route rather than a company.

**Then:** whatever the third-party review raises.

---

## Appendix A — glossary

| Term | Meaning |
|---|---|
| Timber mark | BC log-movement control under Part 5 of the Forest Act. Required to move timber off private land as well as Crown. Not proof of tenure. |
| Crown grant | Historic freehold grant of Crown land. Now private title, outside the tenure system. Exportable (B08) or non-exportable (B09). |
| E&N Land Belt | Esquimalt & Nanaimo Railway grant. Origin of most private timberland on east Vancouver Island. A mapped boundary. |
| Dryland sort | Log sorting yard. A location, not a harvest area. |
| MGU | Management unit — TSA, TFL, or `Z Outside Managed Units` for private land. |
| FTEN | Forest Tenure administration system. Publishes Crown tenure geometry only. |
| HBS | Harvest Billing System. Scale data and billing; holds a record for every mark. |
| Precision tier | HARP's statement of how tightly a harvest area is bounded. P1–P5. |

---

*Contains information licensed under the Open Government Licence — British Columbia.*

---

## Appendix B — the TraceMark target schema

Monthly output eventually goes into TraceMark as `sce_base` rows rather than
GeoJSON. Recorded here so the adapter can be written without rediscovering the
shape.

| Field | Type | Mode | |
|---|---|---|---|
| `sce_id` | STRING | REQUIRED | unique identifier for the supply chain entity |
| `sce_type` | STRING | REQUIRED | typically `Twinrise` for a cut block |
| `sce_source` | STRING | REQUIRED | source system identifier |
| `commodity` | STRING | REQUIRED | typically `Wood` |
| `name` | STRING | REQUIRED | display name for the entity |
| `geom` | GEOGRAPHY | NULLABLE | the cut block geometry |
| `display_geom` | GEOGRAPHY | NULLABLE | geometry for visualisation |

**Two things to note when the time comes.**

`display_geom` is a second geometry, separate from the declared one. HARP has
no equivalent concept, and a search area is arguably the display geometry for
a detection found inside it.

The batch key on the Domtar deployment is `TMSourceID - SiteID - SiloID -
DateIn (month)`. Ours is supplier plus month. Theirs carries a silo, which is
not useful here because this client segregates by species on delivery - but
the field exists if that changes.

Not built. See the roadmap.
