# HARP — Harvest Area Resolution Pipeline

HARP aggregates likely harvest areas associated with timber log and wood chip purchases by Harmac Pacific.

Where possible, it resolves purchases using primary government and other authoritative records, including forest tenure, harvest, timber mark, and parcel data.

Where direct resolution is not possible, HARP uses known operating or supply areas and HLS-DIST change detection to identify likely harvest activity within the relevant area and time period.

The resulting geometries are attributed, consolidated, cleaned, and validated for EUDR geolocation requirements.

---

## Outputs

### Monthly

| File | Description |
|---|---|
| `harvest-YYYY-MM.geojson` | Consolidated harvest geometry for the month, including precision tier, traceability method, and supporting attributes |
| `resolution-*.csv` | One row per supply source showing how it was resolved |
| `run-*.txt` | Processing log for the run |

### Production lot

| File | Description |
|---|---|
| `lot-<id>.geojson` | Harvest areas that may have contributed fibre to the production lot |
| `lot-walkback-*.csv` | Delivery records included in the lot walkback and the period covered |

---

## Entry points

HARP can be run from the command line using:

    harp <command>

or:

    python -m harp <command>

### Main commands

| Command | Purpose |
|---|---|
| `harp run <drop> --month YYYY-MM` | Process a complete monthly client data package |
| `harp library` | Review staged and approved monthly datasets |
| `harp lot <lot list>` | Resolve production lots back to contributing deliveries and harvest areas |
| `harp areas` | Manage manually supplied operating and search areas |
| `harp mills` | Manage supplier mill locations and districts |

### Resuming a run

If a run reaches the detection stage but does not receive a result, it can resume without repeating the earlier resolution stages.

| Command | Purpose |
|---|---|
| `harp detect --month YYYY-MM` | Resume at change detection |
| `harp enrich <detections>` | Resume attribution using an existing detection result |
| `harp union` | Build the detection submission polygon without sending it |

### Other commands

`harp summary`, `harp runs`, `harp package`, `harp resolve`, `harp register`,
`harp ften`, `harp forget-parcels`

---

## Desktop interface

Launch with:

    python tools/harp_gui.py

The interface contains four tabs:

**The month**, **Library**, **Lots**, and **Setup**.

The Month tab runs the complete monthly pipeline and reports the status of each stage. If processing stops, the interface identifies where it stopped without requiring the user to inspect the run log.

---

## Investigation tools

The `tools/` directory contains utilities for investigation and troubleshooting outside the normal pipeline.

These include:

- supplier-to-tenure matching: `c2_probe.py`, `ften_candidates.py`, `aliases.py`
- Washington Forest Practices queries: `fpars_*.py`, `fpa_probe.py`
- private timber mark investigation: `ptm_*.py`
- direct detection API testing: `dist_api_test.py`

---

## Monthly workflow

A typical monthly run is:

    harp run ./data/inbox/2026-07 --month 2026-07

### 1. Sort

Incoming files are identified by their column structure rather than their filenames.

This is intentional. Source filenames have proven inconsistent and are not treated as reliable identifiers.

### 2. Resolve

Each supply source is passed through an ordered series of resolution methods. The first method that produces usable geometry is retained.

Examples include:

- a BC timber mark resolving to a cut block;
- a private timber mark resolving to the titled parcel from which it was scaled;
- a supplier resolving to forest tenure held by that company; or
- a Washington supplier resolving to registered Forest Practices applications.

### 3. Establish search areas

Sources that cannot be resolved directly are assigned the most specific reasonable geographic search area available.

Depending on the source, this may include:

- a known supplier operating area;
- a Natural Resource District;
- a county;
- a national forest; or
- another manually defined supply area.

Where no usable geographic boundary is available, no geometry is created and the source is recorded as unresolved.

### 4. Split

Geometry is separated into three groups:

1. resolved harvest areas;
2. tenure or registered harvest areas; and
3. broader search areas.

Resolved harvest areas require no further detection. The remaining areas are passed to the change-detection stage.

### 5. Detect

Tenure and search areas are combined into a submission geometry and sent to the NGIS change-detection service for the applicable time period.

The service uses HLS-DIST-derived harvest detection to identify likely recent clearing activity within the submitted areas.

### 6. Attribute

Detection results contain harvest geometry and dates but do not inherently identify the original supplier.

HARP spatially joins the returned detections against the supplier-specific input geometries to restore that attribution.

The original supplier geometries are therefore retained separately from the combined detection submission.

### 7. Validate and stage

The resulting harvest dataset is:

1. consolidated;
2. cleaned;
3. validated;
4. revalidated where necessary; and
5. staged for review.

An approved month can then be added to the HARP library.

---

## Precision tiers

Each feature carries a precision tier describing how closely the source has been resolved to an actual harvest location.

| Tier | Description | Traceability |
|---|---|---|
| P1a | Harvest block identified directly from a public forest record | direct |
| P1b | Titled parcel associated with a timber mark from the client's delivery record | direct |
| P1c | Harvest detected within that parcel | direct |
| P2a | Registered harvest or tenure area associated with a supplier | indirect |
| P2b | Harvest detected within that registered area | indirect |
| P3a | Broader search area such as an operating area, district, county, or national forest | inferred |
| P3b | Harvest detected within that broader area and attributed to the supplier | inferred |
| P4 | No usable geometry resolved | — |

P4 covers two situations that produce the same result for different reasons. A
source may be unresolved because no geographic basis could be established, in
which case it is a question for the client. Or it may be out of scope, such as
the mill's own yard piles or landfill, in which case no geometry is expected.
Both are reported, and only the first is outstanding work.

### P1

P1 represents geometry tied directly to information contained in the client's supply records.

P1a requires no further detection because the harvest block itself has already been identified.

P1b represents a parcel associated directly with a timber mark. The parcel is treated as a bounded search area rather than as the harvest itself.

P1c is the harvest detected within that parcel.

### P2

P2 represents supplier-level geometry derived from external records.

For example, a company may be matched to forest tenure or a registered harvest application. This establishes an area associated with that supplier but does not by itself demonstrate that a particular Harmac delivery originated there.

### P3

P3 is used where more precise resolution is not possible.

A broader known operating or supply area is used as the search boundary, and detected harvest within that boundary becomes the resulting harvest geometry.

### Traceability

Traceability describes how geometry was associated with the supply source.

It does not by itself determine whether a feature satisfies a regulatory
due-diligence requirement.

The two are recorded separately because they can disagree. The tier describes
the geometry; traceability describes the route taken to it. A harvest block
retrieved from a public forest register carries a P1a geometry, but where it
was reached through a tenure holder rather than through an identifier on the
delivery, its traceability is recorded as indirect.

---

## Production lot walkback

Production lots are processed using:

    harp lot ./data/inbox/2026-07

Harmac production lots are made from wood chips accumulated from multiple deliveries and mixed in storage before entering production.

There is therefore no direct record identifying exactly which individual deliveries contributed to a particular lot.

HARP addresses this using a historical walkback.

For each production lot, the system:

1. reads the lot weight and species composition;
2. converts the production quantity to an estimated required mass of wood chips;
3. applies the configured safety margin; and
4. walks backward through the delivery record until the required quantity of each species has been accounted for.

All suppliers represented within that delivery window are included in the resulting lot dataset.

The current default margin is 2×.

This accounts for uncertainty introduced by chip storage and reclaim, including material that may remain in storage for an extended period before entering production.

Conversion factors are configured under:

    sources.lots

HARP also reports the relationship between incoming chip mass and pulp production as a basic QA check.

For a kraft mill, the expected relationship should generally be near 2:1. Results substantially outside approximately 1.5:1 to 3:1 indicate that the underlying conversion factors or assumptions should be reviewed.

---

## Dependencies

HARP uses existing NGIS services and supporting libraries where those functions already exist.

| Dependency | Purpose |
|---|---|
| **TraceMark** | The compliance platform HARP's output is loaded into. HARP produces the geolocation half; risk assessment and due-diligence reporting happen there |
| **TraceMark EO** | HLS-DIST-derived harvest change detection |
| **eudr_geojson** | Validation against EUDR geometry requirements |
| **eudr_clean** | Cleaning and repair of geometry that fails validation |
| **bcparcel** | Resolution of private BC timber marks to associated titled parcels |

Public data sources used by HARP include:

- BC Forest Tenure
- BC Harvest Billing
- ParcelMap BC
- BC Natural Resource Districts
- Washington DNR Forest Practices
- US Census county boundaries
- USDA Forest Service boundaries

---

## Installation

Install HARP and its supporting local packages in editable mode:

    pip install -e .
    pip install -e ../bcparcel
    pip install -e ../eudr_geojson
    pip install -e ../eudr_clean

Editable installs are used because the packages are under active development.

The EUDR libraries are imported only when required during processing.

`shapely` and `pyproj` are required for geodesic area calculations and geometric deduplication.

If a required dependency is unavailable, HARP reports the missing functionality rather than silently substituting another method.

Dependency status can also be checked from the **Setup** tab of the desktop interface.

---

## Repository layout

    harp/
      run.py            monthly pipeline
      router.py         source resolution
      catchments.py     operating and search areas
      detect.py         detection submission and attribution
      detection_api.py  detection service interface
      library.py        monthly library and approval workflow
      lots.py           production lot walkback
      sources/          source and register integrations
      configs/          client and environment configuration

    tools/              desktop interface and investigation utilities
    docs/               design and decision documentation
    data/               regenerated working data

The HARP geometry library is stored outside the repository.

Its location is configured under:

    sources.library.path

---

## Processing rules

### Do not create unsupported geometry

HARP only creates geometry where a reasonable geographic basis exists.

If no appropriate geometry can be established from the available information, the source is recorded as unresolved rather than assigned an arbitrary location.

### Do not declare a search area

A search area is a place to look, not an answer. Where a source resolves only
to a parcel, a tenure holding or an operating area, that geometry is submitted
for detection and is never itself declared.

What is declared is the harvest detection found inside it, carrying whatever
the search area could establish about it — a timber mark, a tenure holder, or
the supplier alone.

A supplier whose search area contained no detection in the period therefore
contributes no declared geometry for that month. This is the intended result:
nothing places a harvest there within the window being reported.

### Require approval

A monthly dataset is not promoted to the HARP library until it has been reviewed and approved.

Completed runs enter:

    pending

Runs containing unresolved validation or processing issues enter:

    quarantine

These require review before they can proceed.

---

## Documentation

`docs/HARP_Design_v0_7_0.md`

Detailed description of the pipeline, resolution methods, precision tiers, and processing workflow.

`docs/HPA1_Decisions_Log_v1_2.md`

Record of significant design decisions, including the date, rationale, and any later reversals.

`VERSION.md`

Package change history.

Document version numbers are maintained independently from the HARP package version.

---

## Licence

Proprietary. NGIS internal use only.

Contains information licensed under the Open Government Licence — British Columbia.

County boundaries are sourced from the US Census Bureau.

Forest boundaries are sourced from the USDA Forest Service.

Forest Practices data is sourced from the Washington State Department of Natural Resources.
