# HARP — Harvest Area Resolution Pipeline

**Design document**

| | |
|---|---|
| Version | 0.7.0 |
| Status | End to end. Detection runs against the NGIS service; only validation and cleaning remain unwired. |
| Date | 28 August 2026 |
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
harp run <drop folder> --register <supplier register> --mills <locations>
harp detect --month 2026-07
```

`run` sorts, resolves, builds search areas and splits. `detect` unions, submits
for change detection, and joins the result back. The GUI does both from a
single button and lights each stage as it goes.

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
       ├──────────────► harvest-areas-*.geojson    P1a. Finished.
       │
       ├──────────────► tenure-blocks-*.geojson    P2a. To be searched.
       │
       └──────────────► search-areas-*.geojson     P1b, P3a. To be searched.
                              │
                              ▼
                        ┌──────────────┐
                        │ 5 UNION      │  one polygon to submit    local
                        └──────────────┘
                              │
                              ▼
                        ┌──────────────┐
                        │ 6 DETECT     │  NGIS service             remote
                        └──────────────┘
                              │  dated geometry, no attribution
                              ▼
                        ┌──────────────┐
                        │ 7 JOIN BACK  │  spatial join to what     local
                        │              │  was submitted
                        └──────────────┘
                              │
                              ▼
  ┌──────────────┐      harvest-YYYY-MM.geojson
  │ 8 VALIDATE   │  eudr_geojson → eudr_clean → revalidate
  │ 9 CLEAN      │  **specified, not yet wired**
  └──────────────┘
```

**Why three files rather than one.** They need different handling, not because
three stages produced them. The first is the answer; the second and third are
places to look.

**Why the union.** The service takes crude, large bounding areas. Handing it a
constellation of small polygons is not what it is for, so everything is
dissolved into one shape before submission. The union is a submission artefact
and is never declared — the per-supplier geometry it was built from is kept
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

## 3B. Library interfaces

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

## 5A. Sorting a package

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

## 6A. Monthly drops

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

## 6B. History, retention and the declaration window

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

*Updated 28 August 2026, from a full run over the May window.*

**Resolution.** 280 sources, 221 distinct identifiers. 140 cut blocks resolved
from timber marks, 1,830 titled parcels from private marks.

**Search areas submitted.** 9,636 features unioned into 41 parts — the tenure
blocks, the parcels, and 106 districts, counties and forests.

**What came back.** 6,552 detections in the window, 71,274 ha.

**After the join.**

| Tier | Features |
|---|---|
| P1a — cut block from a mark | 140 |
| P1c — detected within a parcel | *from 1,830 parcels* |
| P2b — detected within a tenure block | 52 |
| P3b — detected within a search area | 6,500 |

**The number that matters.** Search areas totalled 303,434 ha of parcel and
143,837 ha of tenure. What is declared is 71,274 ha of detected harvest. The
difference is the whole point of the round trip.

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

**Answered since v0.6.0**, and kept here so the reasoning is not lost:

- *Does a detected polygon carry a different tier from a registry cut block?*
  Yes, and three of them — P1c, P2b and P3b, by what the parent area could tell
  us about it.
- *Should an operator envelope carry a distinct tier?* Yes, P2a, and the
  detection inside it P2b.
- *Earth Engine, own account or shared service?* Shared. NGIS runs the weekly
  job and HARP submits to a service; nothing runs here.
- *Does a defensible P3 need the ownership-class intersection?* Moot. A search
  area is no longer declared, so how tightly it is drawn only affects how much
  ground detection has to cover.

**Still open:**

1. **How long is the declaration window?** 24 months is the default; the right
   answer is stump-to-digester residence time and only the client knows it.
2. **Which `eudr_clean` opt-ins should be on?** Hole filling, vertex collapsing
   and small-polygon-to-point conversion all change what is being asserted
   about a plot. Currently all off, and the stage is not yet wired.
3. **Should a Recommended finding ever block?** Currently never.
4. **Where does HARP end and TraceMark begin** — geometry only, or also the
   risk assessment against it?
5. **What is the re-resolution trigger** when a client's supplier list changes?
6. **A sub-four-hectare detection comes back as a point.** It carries an area
   but no boundary. Is a point admissible as a plot, or does it need a buffer
   of its stated area — and if so, centred where?
7. **How current is the detection table?** A Georgia control returned nothing
   after 2 June while the Pacific Northwest ran to mid-August. That looked like
   a stale regional copy rather than a lag, but it has not been confirmed, and
   a month cannot be declared against a table that stops before it.
8. **A mixed-tier collection** reports the full distribution and can be split
   on traceability. Is that the right contract for TraceMark, or does it want
   one number?

---

## 13. Roadmap

### Built

- Package sorting by column signature
- BC identifier → FTEN cutblock resolution (R1–R4)
- Private mark registry → PID → ParcelMap BC (R5b), via `bcparcel`
- HBS mark lookup, land basis classification, evidence retention
- HBS → FTEN client number chaining (R6, R7)
- R8 catchment — district ∩ private forest ownership, opt-in
- Assemble: merge, dedupe, tier reconciliation, `crs` removal
- Validate → clean → revalidate loop with registry protection
- Monthly drop comparison and carry-forward
- Per-kind cache doubling as the evidence store
- Source classification model (A–E), precision tiers (P1–P5)
- `sce_base` normalisation with tier → `eudr_sub_type` mapping
- Desktop wrapper over the library

### Next — in order

1. [ ] **`harp package` hands off to `resolve`.** One command, one folder.
       Needed before the first real monthly run.
2. [ ] **Append-only registry store**, with the 24-month window as a query
       filter and the raw extracts archived. See §6B.
3. [ ] **Detection framework.** Pass polygons in, get harvest polygons back.
       Parcels inline, catchments as submit/collect. The largest remaining
       piece and the one that makes the coarse routes safe.
4. [ ] **One live end-to-end run.** Every piece is tested; the whole chain has
       never run in a single pass with the private marks wired in.

### Then

- [ ] **Measure the R8 catchment.** The case for P3 is that district ∩ private
      forest is much smaller than the district. Unmeasured, the tier is not
      yet earned.
- [ ] **`sce_base` scope.** The column list is in
      `billerud_prod_20251029.py` — 32 fields, most of them risk assessment
      results. So the question is not the schema but whether HARP runs the risk
      assessment or hands off a thinner row. Also: the geometry column is
      `.geo`, plus `display_geom` and `deforestation_geom`. We currently write
      one called `geom`.
- [ ] Plan-number route, district-qualified
- [ ] Reserve land via NRCan CLSS
- [ ] `supplier_file.py` — supplier-declared and NGIS-drawn catchments
- [ ] Washington resolver — first non-BC jurisdiction
- [ ] Declarative ladder configuration

### Later

- [ ] Oregon, Alaska, California resolvers
- [ ] C2 supplier request workflow and re-classification loop
- [ ] `ngis-eo` detection paths — blocked on the library-or-service decision
- [ ] Non-forestry commodities

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
