# sdc-mapper

<!-- mcp-name: io.github.SemanticDataCharter/sdc-mapper -->

The shared core of the Semantic Data Charter's Governed Data Record mappers. It validates a record, reads the model
the record names, resolves every value against what the model says about it, hands the result to a target mapper,
and reports every value: how it reached the target, or that the target cannot hold it. Where the record says *why* a
value is missing or unusable (an Exceptional Value, from the ISO 21090 null flavors), the report also says whether the
target could say it too. Every call is appended to a hash-chained log that holds no values.

Each target format is its own package (`sdc-gdr2ubl`, `sdc-gdr2edifact`, `sdc-gdr2omop`, ...) registered under the
entry-point group `sdc_mapper.targets`. Outbound only. Apache 2.0.

**Status:** 4.4.0. Five target mappers are released: `sdc-gdr2ubl` (OASIS UBL 2.3), `sdc-gdr2edifact` (UN/EDIFACT
D.24A), `sdc-gdr2omop` (OMOP CDM 5.4), `sdc-gdr2fhir` (HL7 FHIR R4) and `sdc-gdr2hl7v2` (HL7 v2.5.1).

## Use

```
sdc-map targets
sdc-map map TARGET record.xml --package DIR [--mapping m.yaml] [-o out/]
sdc-map map-chain TARGET parent.xml [--mapping m.yaml] --child MAPPING child.xml ... [--packages DIR] [-o out/]
sdc-map map-chain TARGET --chain chain.json [--packages DIR] [-o out/]
sdc-map fidelity record.xml --package DIR          # every installed target, side by side, per Exceptional Value
sdc-map check-mapping m.yaml --package DIR
sdc-map log summary --days 7 | sdc-map log verify
sdc-mapper [--log-dir DIR] [--host URL]            # MCP stdio server (also sdc-mapper-mcp): the primary way agents use the mappers
```

A model is a package directory (`dm-<ct>.jsonld`, `dm-<ct>.xsd`) or a published model's `--ct-id`, fetched from the
SDCStudio host; with neither, the record's own root names it. Exit status: 0 mapped, 1 refused, 2 error.

## Chained records

A parent record and the records chained to it (SDCHealth library decision 12: a child carries the parent's Record
Identifier in its Parent Record Identifier, or is named by another record of the chain) map into one output, for a
target that maps chains: `Engine.map_chain`, `sdc-map map-chain`, and the MCP tool `map_chain`. Each record is checked
against its mapping, gated against its own model and reported as one record is; if any record is refused, the chain
is refused with every refused record's errors and its position (record 0 is the parent), and nothing is written. The
output is written with `report.json`: each record's coverage report, the parent's first. The chain is one log entry:
the parent's anchors, and each child's under `chained`.

A child's `MAPPING` may be `-` for the mapping the target ships for its model. `--packages` names directories of model
packages, found by the identifier each record names. A manifest (`chain.json`) names `parent` and `children` (each
with `file` and `ct_id`, and optionally `mapping` and `directory`); its relative paths are resolved against its own
directory, then its ancestors.

Also listed on [mcprush](https://mcprush.com/semanticdatacharter/sdc-mapper-mcp).

## The three outcomes

- **valid:** mapped.
- **valid with Exceptional Values:** a value that failed its constraint is covered by an Exceptional Value (Invalid,
  with the constraint kept), and the rest of the record is mapped. Exceptional Values written at the source (a
  refusal, a masked field) are kept as written.
- **refused:** a broken structure, the wrong model, or an error no Exceptional Value covers. Nothing is written; the
  reasons are returned and logged.

## Mapping files

YAML, validated against `schemas/mapping.schema.json`, bound to one model schema version and one target version.

```yaml
mapping: {id: credit-note-to-ubl, version: 1}
model: {ct_id: jkuyr38hydqaiwlq5vhp2rjr, schema_sha256: "…"}
target: {name: OASIS UBL, version: "2.3 CreditNote"}
rules:
  - cluster: <Accounting Supplier Party>        # a cluster to a target element
    to: cac:AccountingSupplierParty
  - cluster: <Party>                            # relative to the enclosing mapped cluster
    to: cac:Party
  - component: <Party Identification Value>
    to: cac:PartyIdentification/cbc:ID
    transform: copy
  - component: <Party Identification Scheme>
    to: cac:PartyIdentification/cbc:ID/@schemeName   # an attribute of the same element
    transform: copy
  - component: <Line Extension Amount>
    to: cbc:LineExtensionAmount
    transform: copy
    units: {to: "@currencyID", transform: copy}
  - cluster: <Party Contact>
    to: "-"
    transform: omit
    reason: the target profile carries no contact
```

Transforms: `copy`, `code` (a table), `format`, `constant`, `binding` (the code the model defines an enumerated value
by), `link` (the code of the concept the component links to), `omit`; clusters `group` or `omit`. `binding` and `link`
take a `system` (`http://loinc.org`, `http://snomed.info/sct`, a code list's IRI); their text is the model's, so a mapper
reports it as a lookup (`LOOKUP_TRANSFORMS`). Steps: `..` up, `.` none, `+name` an element of the value's own, `=name`
the element of that name the parent already has. `within:` restricts a rule to a context. A value no rule reaches is
reported, never dropped. `place(record, mapping)` turns a record and its mapping into placements a mapper writes from.

**The reference-model parts (4.4.0).** `reference_model: true` reads a record's subject, providers, Participations,
protocol, workflow, acs, Audits and attestation as well as its data cluster. A Party, its details cluster and their
components are named by identifier, as anywhere; `cluster` and `within` may also name the reference-model element a part
stands in, and `element` a reference-model element that holds a value:

```yaml
reference_model: true
rules:
  - cluster: <Author>                       # the record's provider Party
    to: /entry#author/resource
  - component: <Given Name (Person)>
    within: [<Author>]                      # the Author's details, not the Patient's
    to: name/given
    transform: copy
  - cluster: subject                        # whatever Party the record's subject is
    to: /entry#patient/resource
  - element: start                          # a Participation's start (xsd:dateTime, read as a temporal value)
    within: [<Attester Participation>]
    to: time
    transform: copy
```

## Codes the model names

`sdc_mapper.codes` reads the IRIs the component libraries use for a concept as a code system and a code: LOINC codes
and answers, SNOMED CT, NCI Thesaurus, and any code list whose IRI ends in `#code` (UBL's, HL7's). A code system is
named by the IRI FHIR uses for it; a mapper turns that into its own name for the system (OMOP's `vocabulary_id`, HL7
v2's coding system). `bound_code(value)` is the code an enumerated value is defined by, `linked_code(value)` the code
of the concept its component links to (`skos:exactMatch`, then `dcterms:subject`), and `model_codes(value)` every code
the model names for it: a target code that is one of these came from the model.

## Writing to a target

`place()` gives each value its target steps. Steps written `=name` join an element made by another value, so a writer
holds those writes back until every other value is written, then writes them shallowest first: sort them by
`join_depth(steps)`. A row-oriented target (a set of tables, as OMOP's) can treat each step as a table or a column,
`+TABLE` as one new row per value, and keep the record's own row (its person, its document) under a name of its own;
`sdc-gdr2omop` does so with a `RECORD` table.

## Writing a mapper

A mapper has `name`, `target`, `target_version`, `package`, `version`, an Exceptional Value table (`ev_table`: an
`EVRule` per ISO 21090 code: preserved, degraded, carried or lost) and `map(record, mapping) -> MapResult`. It returns
one `ValueOutcome` for every value it was given, never writes a rejected value as a value, and registers itself:

```toml
[project.entry-points."sdc_mapper.targets"]
ubl = "sdc_gdr2ubl:UBLMapper"
```

Two methods are optional:
- `map_chain(parent, children) -> ChainMapResult`, for a target that writes a parent record and its chained records as
  one output. `parent` and each child are (resolved record, mapping) pairs. It returns the files and one `MapResult`
  per record, the parent's first, each accounting for its own record's values. A target without it maps one record at
  a time, and a chain for it is an error.
- `mapping_for(ct_id) -> path | None`, the mapping file the target ships for a model. `map-chain` and `map_chain` use
  it for a record given without a mapping.

## Tests

`python -m pytest tests -q` (offline). The fixture is a HalvorsenDemo Credit Note record and its model's package;
the reference mapper in `tests/refmapper.py` is a flat CSV used only to exercise the contract. When sdc-gdr2fhir is
installed and its repository is beside this one (or at `$SDC_GDR2FHIR_REPO`), `tests/test_chain.py` also checks that
`map_chain` gives the same Bundles and reports as sdc-gdr2fhir's own `chain.map_files` on its five sample chains.

## License

Apache 2.0. See NOTICE for the shipped SDC4 reference model schema and the fixture.
