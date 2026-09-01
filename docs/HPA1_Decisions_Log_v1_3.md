# HPA1 — decisions log

What was decided, when, by whom, and why. Kept so a decision does not have to be
reconstructed from a conversation later, and so a reversal is visible as a
reversal.

Newest first.

| | |
|---|---|
| Version | 1.3 |
| Date | 1 September 2026 |

Version increments whenever a decision is added or reversed.

---

## 1 September 2026 — The producer name comes from the register

**Michael, after a supplier code reached a customer.**

Two EUDR provenance files went out carrying `ProducerName: "WWW"`. WWW is a
code in the client's system, and it turned out to name sixteen timber marks
resolving to **six unrelated holders**: Cape Mudge Forestry, Tsawak-qin
Forestry, SSD Sawmill Sales Direct, Cisaa Forestry, the Regional District of
Alberni-Clayoquot, and Boralex. One of those was an airport runway clearing.

**So `ProducerName` is taken from the register, not from the client's code.**
The FTEN `CLIENT_NAME` was already being fetched and discarded; it is now
carried onto every feature at the point of resolution, in EUDR casing, with
`harp_producer_number` and `harp_producer_source` beside it.

**The client's alias is kept.** `harp_supplier` still carries WWW, so a name
that changed can be traced back to the code it came from.

**A bare code is never used as a name.** Where no register named a holder and
the client's own name for the supplier is just the code, the field is left
empty. Visibly empty beats plausibly wrong.

**One caution recorded with it.** A tenure holder is who held the tenure, not
necessarily who the client paid. Wood bought through a broker names the holder
in the register and somebody else on the invoice. `ProducerName` answers the
question the regulation asks and should not be read as the counterparty.

---

## 1 September 2026 — EUDR fields are added, then stripped at delivery

**Michael.** The four regulated fields are added to a month before validation
and the month is stripped to just those four only at the point of delivery.

**Why not strip earlier.** The obvious design - project to four fields, then
validate - breaks lot resolution outright, because a production lot is
resolved against `harp_supplier` and the projection has no such field. That was
built the wrong way round first and caught before it shipped.

**Why it works.** The validator inspects only the four named fields. Both the
blank check and the capitalisation check work from a known list, so `harp_`
fields ride through validation, cleaning and into the library untouched.

**Omit, never blank.** A missing field is Recommended; a field present but
blank is Required. So a feature with nothing known ships its area alone rather
than three empty strings.

---

## 1 September 2026 — Detection is part of a run

**Michael.** `harp run --month YYYY-MM` now goes from the client's drop to a
staged library month in one command.

**Why it changed.** Detection was a second command, and that produced runs
which looked finished and were not. The run log covered only the first four
stages, so a run that never reached detection was indistinguishable on disk
from one that had - which cost an afternoon of looking at the wrong file.

Everything now writes to one log, and the run reports where it stopped.
`detect`, `enrich` and `union` survive as recovery for when the service does
not answer, not as workflow.

---

## 31 August 2026 — A month is approved before it can be declared from

**Michael.** A completed month is validated, cleaned and revalidated, then
staged for a person to approve. Nothing is declared from a month that has not
been.

**Four states, not two.** Working, pending, quarantine, library. Quarantine
exists because early months will contain geometry the cleaner cannot fix, and a
month sitting visibly in quarantine with its findings beside it is more useful
than one that failed quietly.

**`read_month` refuses to read from pending.** A lot resolved against an
unapproved month would look identical to one resolved against an approved
month, and that difference matters.

The intent is a service that promotes clean months itself. Until the cleaning
parameters are settled for this data rather than for supplier submissions, a
person looks first.

---

## 31 August 2026 — No minimum species share in the walkback

**Michael.** A delivery is taken into a lot's walkback if it carries any
species the lot still needs, however small the share.

The case against: a load that is 95% cedar entering a lot that used no cedar,
on the strength of 2% fir, drags a cedar supplier's whole geometry into the
declaration.

**Why it stands anyway.** That 2% is real fibre, and it plausibly came off the
same cut block the cedar did - a stand is rarely one species, and a load
reflects what was standing there. Declining it would refuse to declare ground
the wood may genuinely have come from.

A minimum share was considered and rejected: any threshold would be arbitrary,
and its error runs toward under-declaration.

**Species are still tracked separately.** Each has its own counter and target,
and the walk continues until the slowest is satisfied. What is not filtered by
species is which loads are eligible, not how deep the walk goes.

---

## 28 August 2026 — A search area is never declared

**Michael, on seeing the May output against the detections.**

The rule was that a tenure block overlapping a detection was confirmed and
kept. That is wrong: the block is where a company holds tenure, not where they
cut in the window, so keeping it declares ground that was never touched.

**What is kept is the detection.** The area only says whose it was and what
else is known about it. A detection inside a tenure block takes its mark and
holder; one inside a district takes the supplier and nothing more.

Both are search areas and both work the same way. The tiers record what the
parent could tell us: P2b for a tenure block, P3b for a district.

---

## 28 August 2026 — Titled parcels are search areas too

**Michael.** A parcel was being declared whole, at P1b.

**The measurement that settled it.** Across one month, parcels totalled
**303,434 ha against 71,274 ha of detected harvest** — a median of 41 ha with a
tail to 1,926. Declaring the parcel over-declares by about four times.

A parcel is the ownership boundary; the cut is somewhere inside it. So it is
submitted for detection like any other area — **and its timber mark travels
with it**, so a detection inside inherits a mark that came off the client's own
delivery record.

**That earns its own tier, P1c, rather than folding into P2.** A parcel's mark
is on the delivery: the client bought timber under it, and it was scaled from
that parcel. A tenure block's mark came from querying a company matched by
name, and nothing says the client bought any of it. Same shape of geometry,
different chain of evidence, and folding them would erase the distinction.

**Consequence.** 1,830 parcels moved out of the answer and into the search
set. What is declared for them is now whatever detection finds inside.

---

## 27 August 2026 — Detection is a service, not something we run

**Confirmed by testing against the live API.**

NGIS runs a weekly HLS-DIST job producing a maintained table of harvest
polygons. HARP submits an area and reads what comes back. No Earth Engine
account, no compute, no quota.

**What the return carries:** geometry, a date, an area, a feature type — and no
supplier and no mark. Attribution is recovered by spatial join against the
per-supplier areas that were submitted, which is the reason the union is kept
separate from the geometry it was built from.

**Three things learned the hard way**, each recorded so nobody rediscovers
them:

- **The filename is not to be trusted.** The service names everything
  `.geojson` and often sends CSV. Content is sniffed instead.
- **`sce_id` is a batch id**, one value across every row of a job. It looks
  like a per-feature identifier and is not one.
- **A missing `db-dtypes` package** on the service silently stripped the date
  column from an otherwise successful response. An absent field is not always
  an absent capability.

---

## 26 August 2026 — Union for submission, per-supplier for attribution

**Nathan, confirmed by Michael.** The detection service takes crude, large
bounding areas. Handing it a constellation of small polygons is not what it is
for.

So everything is dissolved into one polygon before submission, and the
per-supplier geometry is kept untouched. The union is a submission artefact and
is never declared.

**Where two suppliers' areas overlap, a detection inside both is attributed to
both.** The geometry repeats and the attribution does not, because a harvest
has to be declarable against whoever supplied the fibre. Accepted as ugly and
correct.

---

## 24 August 2026 — The catchment layer, and its six methods

**Michael, following the three live NGIS deployments.**

Read `tracemark-eo` properly before building — Domtar, Enviva and Billerud all
solve this, differently. The layer follows them rather than inventing a fourth
approach.

- **Domtar** — supplier-declared administrative areas joined to published
  boundaries. Several per supplier, exploded into separate records.
- **Enviva** — mill point, radius held per mill in config, used as a query
  filter against harvest polygons rather than as the declared area.
- **Billerud** — buffer sized by the volume a source produced, not by an
  assumed haul distance.

**Copied directly from Domtar:** a supplier answering *"potentially all
counties"* gets null geometry. An unbounded answer is recorded as no answer
rather than as a large polygon. Nine suppliers currently have no catchment on
that basis.

**A catchment is a search area.** Every feature carries
`harp_plot_claimable: false`. Detection is what turns it into a harvest.

---

## 24 August 2026 — Accuracy over coverage in name matching

**Michael, explicitly.** A supplier-to-tenure-holder match is either verified
or not used. No medium-confidence tier.

The cost is real: eight suppliers who almost certainly hold tenure now have
none recorded, because the match could not be verified from the names. That is
accepted. A wrong holder is thousands of blocks of another company's forest in
a declaration, which is wrong rather than merely broad.

**Unverifiable-but-plausible matches are reported, never used.** `Gorman Group`
against `GORMAN BROS. LUMBER` may well be one firm; the names do not prove it,
so a person decides.

---

## 24 August 2026 — The alias table decides, the matcher proposes

**Michael.** Company-name matches are recorded in a persistent table rather
than re-derived each run.

**Reason.** Part of the answer is not in the names — Teal-Jones Group owns Teal
Cedar Products and no string comparison discovers that. A matcher asked to rule
will reach the same uncertain conclusion every month; a person asked once will
not.

A decision made today holds. Tightening the matcher later cannot silently
change a historical answer, because the answer no longer comes from the
matcher.

**Shared across clients**, not client data.

---

## 19 August 2026 — Supplier catchments

**Decided by Nathan, following his review of the data package.**

Harvest areas for the chip supply base will be produced by detecting harvests
inside a catchment boundary, rather than by tracing individual deliveries.

- **Per supplier, not one shared boundary.** A single catchment covering every
  supplier would span coastal BC, Washington, Alaska, Oregon and California.
- **Spatial and temporal.** A catchment is a boundary *and* a harvest window.
  The same boundary is queried repeatedly with different windows.
- **One batch per supplier per month.** Each batch carries a catchment, a
  window, and the deliveries it covers.

**What this does not change.** 137 sources already have real geometry from the
public register and the private mark route. Catchments apply to the 111 chip
sources only. A catchment is a weaker answer than a resolved cut block and does
not replace one.

---

## 19 August 2026 — Aggregation may be a formula, not a feed

**Nathan.** Rather than waiting on pile and silo movement records, aggregation
can be derived: take a production date, walk back through the logistics table,
and select deliveries until they account for a set multiple of storage capacity.
Worked examples exist from the Drax engagement.

**Blocked on** storage capacity per pile, and confirmation of whether recipes
exist.

**Unresolved conflict.** The 200% approach assumes FIFO, as the EUDR FAQ does.
Harmac's piles are LIFO. This needs a joint decision and has not yet been put to
Nathan.

---

## 18 August 2026 — Digital Material Passports ingested, not ignored

**Michael.** The client's own filed declarations are downloaded, exploded, and
sorted into cutblocks and regional areas.

- Cutblocks join the master collection at **P3**
- Regional areas go to the detection pool at **P4**
- Every feature carries `harp_provenance: client_declaration` and **no**
  `harp_source_id`, because none exists
- A declared cutblock with **50% or more** of its area inside one we resolved
  ourselves is **dropped**. Ours carries a timber mark and a tenure holder;
  theirs carries neither

**Why they are not a ladder rung.** A passport has no identifier to key on, so
it runs after the per-source loop rather than inside it.

---

## 18 August 2026 — No preemptive filtering

**Michael, correcting an earlier design.** Every source runs the full resolver
ladder regardless of its class, including chip sources whose identifier names a
mill.

**Reason.** Every code that turned out to matter — the `0R1` suffixes, Mosaic's
apostrophe codes, RYK's five-digit numbers — was judged unpromising by eye and
found to be real when finally tested. The cost of an extra query is trivial; the
cost of a lost cut block is not.

**Superseded** an earlier rule that classified all chip sources as C2 and
skipped them. That rule was inferred from a correlation and stated as a fact.

---

## 17 August 2026 — Retention and the declaration window

**Michael.** Two separate things that were being confused:

- **What we declare** — a rolling window, 24 months by default, applied as a
  query filter. Never everywhere a supplier has ever cut.
- **What we keep** — raw extracts archived as received, never deleted. EUDR
  requires five years of due diligence records, and a roll-off is irreversible
  in a way a filter is not.

**Open.** The right window length is stump-to-digester residence time and only
Harmac knows it. 24 months is a placeholder.

---

## 17 August 2026 — Files recognised by columns, not filenames

**Michael.** A monthly drop is sorted by inspecting each file's columns.

**Reason.** Filenames in this data have been proven wrong three separate ways: a
workbook named "June 2026" whose data sheet is "January 2026" and whose records
were processed in February; a "Calendar Year" label on files that are not
year-to-date; and a `ProcessedOn` field that varies per record rather than per
file.

Registry extracts **accumulate**; a job list **replaces**. A file matching no
signature is reported with the columns it had, never skipped.

---

## 13 August 2026 — Private marks resolve through parcels

**Michael.** BC scaled-timbermark extracts link a private mark to the parcels it
was scaled from, and ParcelMap BC publishes those parcels. That became rung R5b.

**40 of 41 unresolved private sources** are covered, including twelve Mosaic
short codes that nothing had previously explained.

**A parcel is a search area, not an answer.** It is the ownership boundary; a
200 ha parcel behind a 12 ha cut over-declares by sixteen times. Tier P3 until
detection runs inside it.

**`bcparcel` is a dependency, not vendored.** `eudr_geojson` is currently
vendored three times inside `tracemark-eo`, and that is how the Billerud
instance ended up running a stale copy with the profile-filtering bug.

---

## 13 August 2026 — Blanket authority is a structural gap

**Michael, for Harmac to decide.** Nineteen timber marks in the ministry
extracts are blanket authorities — one mark covering an entire class of land,
such as every provincial highway right-of-way or every road held by a
municipality.

These carry real scaled volume, but no plot-level answer exists at any price and
no dataset will produce one. Either the volume is excluded from what Harmac
declares, or the supplier's own harvest records are obtained.

**A compliance decision, not a data problem.** Raised early because it is far
cheaper to settle now than during an audit.

---

## 12 August 2026 — Precision tiers

**Michael.** Every resolved source carries a tier saying how tightly its
geometry is bounded, and a consumer filters on it.

| Tier | Geometry | Plot claim |
|---|---|---|
| P1 | cut block | yes |
| P2 | authority or licence | yes, coarser |
| P2 envelope | holder's tenure in a district | **no** |
| P3 | parcel or constrained catchment | with a stated basis |
| P4 | administrative area | **no** |
| P5 | unresolved | no |

**Reason.** A cut block and a district are both geometry. Treating them as
equivalent is what makes a due diligence statement indefensible.

**Later refinement, 18 Aug.** P2 was covering two different things — a licence
polygon, which is a genuine coarser plot, and a holder's whole tenure in a
district, which is an envelope. The second is now explicitly not claimable.

---

## 12 August 2026 — Registry geometry is never repaired

**Michael.** A polygon from a public register that fails validation goes to
review unmodified rather than through `eudr_clean`.

**Reason.** An FTEN polygon is a government boundary. Nudging a vertex to remove
a sliver means we are no longer asserting the province's polygon but our edit of
it, which weakens the provenance claim that made the geometry worth having.

Supplier-submitted geometry is cleaned; registry geometry is not.

---

## Reversals and corrections

Kept deliberately. A wrong turn that is recorded costs less than one that is
quietly fixed.

| Date | What was wrong | Correction |
|---|---|---|
| 1 Sep | `ProducerName` set from the client's supplier code | WWW covered six unrelated holders and reached a customer deliverable. The name comes from the register now, and a bare code is never used. |
| 1 Sep | The EUDR projection built to run before validation | It would have put a four-field collection into the library, and a lot is resolved against `harp_supplier`. Caught before shipping: the fields are added before validation, the stripping happens at delivery. |
| 31 Aug | Tenure blocks classified by a flag rather than by their tier | `harp_is_envelope` is set for R7 only. R6 also produces P2a, so 2,694 of its blocks were filed as harvest areas and would have been declared without detection. The tier decides now. |
| 31 Aug | Tenure blocks classified by a flag rather than by their tier | `harp_is_envelope` is set for R7 only. R6 also resolves by client number and also produces P2a, so 2,694 of its blocks were classified as cut blocks, landed in the harvest file, and would have been declared directly instead of searched. The tier decides now, and a guard reports anything in the harvest file that is not P1a. |
| 28 Aug | A tenure block confirmed by a detection was kept, and the detection discarded | Backwards. The block is where a company holds tenure, not where they cut. The detection is the harvest; the block only says whose it was. |
| 28 Aug | A titled parcel treated as an answer | 303,434 ha of parcel against 71,274 ha of detected harvest. The parcel is the ownership boundary, not the cut. |
| 27 Aug | An empty detection result read as a coverage gap | It was a stale regional copy of the table, and separately a missing package on the service. Two different causes producing the same empty file. |
| 26 Aug | `plot_claimable` asserted a regulatory position | Replaced by `harp_traceability` — direct, indirect, inferred. Whether a tier satisfies a test is a judgement for whoever declares. |
| 24 Aug | A 3,000-block cap per operator, silently truncating | Under-declaration by accident, and invisible — the output read "2,459 blocks" whether or not that was all of them. Cap removed; if one is set, the true total is fetched so the shortfall shows. |
| 24 Aug | Operator tenure filtered by the mill's district | Richmond Plywood holds 270 blocks and none in its mill's district, so the filter returned zero. The district is a flag, not a filter. |
| 24 Aug | `FTEN client tenure` read as geometry already held | The same string means "held" for some suppliers and "outstanding" for others. Reading it as held skipped 21 suppliers and removed 24,806 BDT of catchment. Only genuinely-held systems skip now. |
| 24 Aug | Alta Forest Products treated as a BC supplier | `STATEID` says BC; they mill at Port Angeles and Tacoma. Where the client's record and the mill town disagree, the town is better evidence. |
| 24 Aug | Coos Bay taken as Roseburg's mill | It is a chip export terminal. The chips are made at Coquille, Dillard and Riddle — two counties, not one. Confirmed by search. |
| 24 Aug | Green Diamond placed in one county | They manage ~428,000 acres across Del Norte, Humboldt and Trinity. One county covered about a third. Confirmed by search. |
| 24 Aug | `DISTURBANCE_START_DATE` used to narrow tenure | Cut 3,339 blocks to 15. The field is sparsely populated; filtering on it under-declares. Detection does the narrowing instead. |
| 20 Aug | Only one file in `tracemark-eo` read before advising | Reported that no mill-buffer precedent existed. Enviva does exactly that, in production. Read the repository, not a file. |
| 18 Aug | All chip sources classified C2 and skipped | Product type is a hint about the identifier, not a fact about supply tier. Nothing is filtered in advance. |
| 13 Aug | "Mosaic holds no Crown tenure" | A name-matching failure. The marks sit under TimberWest and Island Timberlands. Never match a tenure holder by the client's name for them. |
| 13 Aug | A transient service failure silently demoted a P1 to a P2 | A miss and an outage are not the same thing. Queries retry, then raise, and the ladder stops rather than falling to a weaker rung. |
| 12 Aug | Shape-based routing skipped the field holding `61/243` | Identify ranks candidate readings; it never eliminates one. |
