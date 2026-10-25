# Changelog

Versions carry the leading 4 of the SDC4 reference model: an addition is a minor version, a fix a patch, and the
major changes only when the reference model does.

## 4.1.1 (2026-10-25)

The MCP server is registered in the MCP Registry as `io.github.SemanticDataCharter/sdc-mapper`, launched as
`uvx sdc-mapper`: the bare `sdc-mapper` command now starts the server (`sdc-mapper-mcp` stays). No change to the
library.

## 4.1.0 (2026-10-25)

The first release on PyPI.

Mapping files that can carry a target's structure, for targets the model does not already bind (the business
documents library and UBL, first).

- **Cluster rules.** A rule can map a cluster to a target element (`cluster:`, transform `group`, or `omit` for
  everything under it). A value's rule is relative to the nearest enclosing mapped cluster, so one shared component (a
  Party Name) lands in different places under different clusters. `within:` names enclosing clusters that must hold
  for a rule to apply, for a cluster or component used in several places.
- **Paths and attributes.** A path starting with `/` is absolute; a final `@name` step places the value in an attribute
  of the element before it; `attributes:` writes constant attributes on a value's or cluster's element.
- **Steps.** `..` goes up from the enclosing mapped cluster's element (a contact's website on the party), `.` is no
  step (a wrapper cluster that adds no element), and a step written `+name` makes a new element for each value instead
  of sharing one by name (one UBL `Dimension` per measure). Steps of the same name in the same cluster instance are one
  element, so an identifier and its scheme meet on one `cbc:ID`.
- **`also:`** writes the same value to further places (a payment means code's name beside the code; a scheme's
  `schemeID` and agency beside its `schemeName`). **`on_miss: omit`** leaves a code-table miss unwritten instead of
  failing, for tables that cover only some values.
- **`binding`.** A transform that writes the code the model binds a value to: the fragment of the IRI its enumeration
  value is defined by, under a `prefix` (a payment means value bound to `…/PaymentMeansCode-2.3.gc#2` writes `2`). The
  model supplies the value; the mapping says only where it goes.
- **Units.** A `units:` sub-rule places a quantity's unit in an attribute, copied or through a code table (a currency
  symbol to `currencyID`, an SI symbol to UN/ECE Recommendation 20).
- **`place(record, mapping)`.** For every value: its target steps, each tagged with the cluster instance that owns it
  (sdcreader 4.4.0), so each repeat of a cluster becomes its own target element; the attribute; the text after the
  transform (`format` gives dates a strftime pattern and numbers a format specification); and the units' placement. A
  value with no rule is reported as unmapped; a code-table miss fails rather than guess (the engine ends the call as an `error`). Writing the target from the
  placements is each mapper's.

## 4.0.0 (not released separately; part of 4.1.0)

The core of the Governed Data Record mappers.

- **The validation gate.** Every record is valid, valid with Exceptional Values, or refused. It uses sdcvalidator's
  public `recover()` (4.6.0, built for this): Exceptional Values written at the source are kept, and one that already
  covers a failing value stays the source's; a value that fails its constraint gets one, with the constraint kept as
  data; a structural error, or an error no Exceptional Value covers, refuses the record with its reasons. The record is
  read as an `XMLResource`, so prefixed QNames in content (`xsi:type`) resolve. The SDC4 reference model schema ships
  in the package, so validation never touches the network; a schema is built once per SHA-256.
- **The resolved record.** Every value typed by its SDC4 type, with its units and the model's definition of the
  symbol, its enumerated code, its semantic links, its context, and its Exceptional Value with the origin the gate
  knows. A rejected value is kept for a person to see and never handed to a writer as a value. A value beside an
  Exceptional Value written at the source is not data: SDCStudio's generated schemas require the value element, so a
  record keeps one there.
- **Mapping files.** YAML validated against `schemas/mapping.schema.json`, bound to one model schema version and one
  target version, five transforms (`copy`, `code`, `format`, `constant`, `omit`), Exceptional Value overrides, an
  opt-in `carry_rejected`.
- **The coverage report.** Every value classed (lookup, declared, carried, not representable; Exceptional Values
  preserved, degraded, carried, lost); a mapper that drops a value, or passes an Exceptional Value off as a value,
  fails the call. One plain sentence of what the target could not say. Byte-stable.
- **The log.** One JSON Lines entry per call, whatever the outcome: anchored by the SHA-256 of the record, schema,
  mapping file and outputs; chained to the entry before; no values and no failure reasons; validated by
  `schemas/log-entry.schema.json`; `verify` walks the chain, `summary` reads it back.
- **Interfaces.** The `Mapper` contract and the `sdc_mapper.targets` entry-point group; `sdc-map` (map, fidelity,
  check-mapping, targets, log); an MCP server, `sdc-mapper-mcp` (`list_targets`, `map_record`, `check_mapping`,
  `fidelity`, `log_summary`, `verify_log`).
