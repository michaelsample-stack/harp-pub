# Reference data

Three files the pipeline reads on every run, shipped with the package rather
than pointed at by hand.

They are ours, not the client's: a few hundred rows of decisions made once and
reviewed since. Requiring somebody to select them is how a run quietly loses
its mill locations and produces a month of mill-buffer search areas without
saying why.

| | |
|---|---|
| `supplier_aliases.csv` | supplier to forest tenure client number, with who decided and on what basis |
| `supplier_locations.csv` | mill location and natural resource district per supplier |
| `supplier_areas.csv` | operating areas stated by hand, for suppliers no register can place |

## Keeping them current

`supplier_aliases.csv` is edited by hand, or by `harp register` when a new
supplier arrives. Every row records who accepted the match and why, because a
name match between a supplier and a tenure holder is a judgement.

`supplier_locations.csv` comes from `harp mills`, which queries BC's facility
register and matches supplier names against it. **Not regenerated on every
run** - the matching is fuzzy and costs live queries, and a stable answer is
worth more than a fresh one. Refresh it deliberately when the supplier list
changes.

`supplier_areas.csv` is written by `harp areas`. Empty until somebody states
an area, and each entry carries who stated it and what it rests on - only a
supplier's own words count as declared.

## Overriding them

A client with different reference data sets `sources.reference.path` in
config, and these are ignored. Nothing here is edited by a run.

## One caution

`supplier_aliases.csv` and `supplier_locations.csv` carry the client's
supplier names. That is fine in an NGIS repository and would not be fine in a
public one.
