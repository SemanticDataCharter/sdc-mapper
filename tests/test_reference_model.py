"""The reference-model parts beyond the data cluster (4.4.0): `reference_model: true` reads them, rules reach a Party's
details by the same identifiers as data-cluster components, `cluster`/`within` may name the reference-model element a
part stands in, and `element` names one that holds a value.

Fixture: SDCHealth's Laboratory Report (production, public) and two synthetic records of it
(samples/sdchealth-lab-report); labrep05 carries an Asked and Refused Date of Birth in the subject's details.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from lxml import etree

from refmapper import CSVMapper
from sdc_mapper import Engine, Gate, MappingError, VALID, VALID_WITH_EV, load_mapping, place, resolve
from sdc_mapper.mapping import RM_NAMES, ancestor_tokens
from sdcreader import load_package, read_model
from sdcreader.model import RM_ELEMENTS, RM_PARTS, RM_SLOTS
from sdcreader.record import read_record

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "sdchealth-lab-report"
CT = "zptekdommw56yiz9lqjy5dga"
PKG = load_package(SAMPLE, CT)
MODEL = read_model(PKG)
RECORD = (SAMPLE / "records" / "labrep00synthxxxxxxxxxxx.xml").read_bytes()
RECORD_EV = (SAMPLE / "records" / "labrep05synthxxxxxxxxxxx.xml").read_bytes()
SDC4_XSD = ROOT / "src" / "sdc_mapper" / "schemas" / "sdc4.xsd"
XSD = "{http://www.w3.org/2001/XMLSchema}"

PATIENT, AUTHOR = "uficdtk6vpsgvpeyr1sevj9j", "qau8mx32a2d0v54jrc2q7y9y"
PATIENT_DETAILS, PROFESSIONAL = "k2ph54lwfdtlrm7p5gw4s3tt", "geuvvqy9qol4t10s231a4ezo"
HUMAN_NAME, GIVEN_NAME, SURNAME = "g50zuf7amwobbgifx7o52i5l", "l9l169co9r23e6fftpvdz4e3", "jc1lfk3ovt6l8frhxvx8b669"
ATTESTER_PARTICIPATION, ATTESTER_FUNCTION = "e7jes8an7n8t58qyabk1nnm2", "z0gm7qs3bvdt2s1tie2gcrue"
DATE_OF_BIRTH = "js564tazefb6udqlh12rj04l"


def mapping(tmp_path, rules, **extra):
    doc = {"mapping": {"id": "t", "version": 1}, "model": {"ct_id": CT, "schema_sha256": PKG.sha256},
           "target": {"name": CSVMapper.target, "version": CSVMapper.target_version}, "rules": rules, **extra}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return load_mapping(p)


def resolved(xml: bytes = RECORD, rm: bool = True):
    g = Gate().check(xml, PKG, MODEL, reference_model=rm)
    assert g.outcome in (VALID, VALID_WITH_EV), g.errors
    return resolve(read_record(g.xml, MODEL, reference_model=rm), MODEL, PKG, g)


def names(steps):
    return "/".join(s for s, _ in steps)


RULES = [
    {"cluster": AUTHOR, "to": "/author"},
    {"cluster": "subject", "to": "/patient"},                                 # by the element the Party stands in
    {"component": GIVEN_NAME, "within": [AUTHOR], "to": "name/given", "transform": "copy"},
    {"component": GIVEN_NAME, "within": ["subject"], "to": "name/given", "transform": "copy"},
    {"component": SURNAME, "within": [AUTHOR, HUMAN_NAME], "to": "name/family", "transform": "copy"},
    {"component": DATE_OF_BIRTH, "within": ["subject"], "to": "birthDate", "transform": "copy"},
    {"cluster": ATTESTER_PARTICIPATION, "to": "/attester"},
    {"component": ATTESTER_FUNCTION, "to": "mode", "transform": "copy"},
    {"element": "start", "within": [ATTESTER_PARTICIPATION], "to": "time", "transform": "format", "format": "%Y-%m-%d"},
    {"element": "pending", "to": "/attestation/pending", "transform": "copy"},
]


# ------------------------------------------------------------------ the mapping file

def test_rules_naming_reference_model_elements_need_the_flag(tmp_path):
    with pytest.raises(MappingError, match="reference_model"):
        mapping(tmp_path, [{"element": "start", "to": "time", "transform": "copy"}])
    with pytest.raises(MappingError, match="reference_model"):
        mapping(tmp_path, [{"cluster": "subject", "to": "/patient"}])
    m = mapping(tmp_path, RULES, reference_model=True)
    assert m.reference_model and set(m.names_reference_model) == {"subject", "start", "pending"}


def test_a_mapping_without_the_flag_reads_as_before(tmp_path):
    m = mapping(tmp_path, [{"cluster": AUTHOR, "to": "/author"}])
    assert not m.reference_model and not m.names_reference_model


def test_the_schema_refuses_an_unknown_element_name(tmp_path):
    with pytest.raises(MappingError, match="does not validate"):
        mapping(tmp_path, [{"element": "stat", "to": "time", "transform": "copy"}], reference_model=True)
    with pytest.raises(MappingError, match="does not validate"):
        mapping(tmp_path, [{"element": "start", "component": ATTESTER_FUNCTION, "to": "t", "transform": "copy"}],
                reference_model=True)


def test_the_schema_names_are_sdcreaders():
    schema = json.loads((ROOT / "src" / "sdc_mapper" / "schemas" / "mapping.schema.json").read_text())
    assert set(schema["$defs"]["rm_part"]["enum"]) == RM_PARTS
    assert set(schema["$defs"]["rm_element"]["enum"]) == RM_ELEMENTS
    assert RM_NAMES == RM_PARTS | RM_ELEMENTS


def test_sdcreaders_slot_table_is_the_reference_models():
    """Every named element sdc4.xsd gives DMType, PartyType, ParticipationType, AttestationType and AuditType beyond
    the record attributes, the label and the data cluster is in sdcreader's table, with its type."""
    tree = etree.parse(str(SDC4_XSD)).getroot()
    types = {"DMType": "DM", "PartyType": "Party", "ParticipationType": "Participation", "AttestationType": "Attestation",
             "AuditType": "Audit"}
    kinds = {"sdc4:PartyType": "Party", "sdc4:ClusterType": "Cluster", "sdc4:AttestationType": "Attestation",
             "sdc4:XdStringType": "XdString", "sdc4:XdLinkType": "XdLink", "sdc4:XdFileType": "XdFile",
             "xsd:string": "string", "xsd:dateTime": "dateTime", "xsd:boolean": "boolean"}
    skip = {"label", "dm-label", "dm-language", "dm-encoding", "creation_timestamp", "instance_id", "instance_version",
            "source_instance_id", "source_version_id", "current-state"}
    for name, rm in types.items():
        ct = next(c for c in tree.iter(f"{XSD}complexType") if c.get("name") == name)
        declared = {e.get("name"): kinds[e.get("type")] for e in ct.iter(f"{XSD}element")
                    if e.get("name") and e.get("name") not in skip}
        assert declared == RM_SLOTS[rm], name


# ------------------------------------------------------------------ placement

def test_a_partys_details_land_by_the_party_that_holds_them(tmp_path):
    ps, clusters = place(resolved(), mapping(tmp_path, RULES, reference_model=True))
    given = {names(p.steps): p.text for p in ps if p.value.ct_id == GIVEN_NAME and p.rule}
    assert given == {"author/name/given": "Elena", "patient/name/given": "Ana"}
    family = [(names(p.steps), p.text) for p in ps if p.value.ct_id == SURNAME and p.rule]
    assert family == [("author/name/family", "Moreau")]
    # the Attester's and the Legal Authenticator's names share the components but no rule reaches them
    assert any(p.unmapped for p in ps if p.value.ct_id == GIVEN_NAME)


def test_a_participations_function_and_start(tmp_path):
    ps, _ = place(resolved(), mapping(tmp_path, RULES, reference_model=True))
    function = next(p for p in ps if p.value.ct_id == ATTESTER_FUNCTION)
    assert names(function.steps) == "attester/mode" and function.text == "attester"
    start = next(p for p in ps if p.value.role == "start" and p.value.path[0].ct_id == ATTESTER_PARTICIPATION)
    assert names(start.steps) == "attester/time" and start.text == "2026-10-01"
    others = [p for p in ps if p.value.role == "start" and p.value.path[0].ct_id != ATTESTER_PARTICIPATION]
    assert others and all(p.unmapped for p in others)
    pending = next(p for p in ps if p.value.role == "pending")
    assert names(pending.steps) == "attestation/pending" and pending.text == "false"


def test_an_exceptional_value_in_a_partys_details_is_placed_and_never_written(tmp_path):
    ps, _ = place(resolved(RECORD_EV), mapping(tmp_path, RULES, reference_model=True))
    dob = next(p for p in ps if p.value.ct_id == DATE_OF_BIRTH and p.value.roles[0] == "subject")
    assert dob.value.exceptional.code == "ASKR" and dob.value.exceptional.origin == "source"
    assert names(dob.steps) == "patient/birthDate" and dob.text is None


def test_the_ancestor_tokens_name_a_step_by_its_element_and_its_identifier():
    v = next(v for v in resolved().values if v.ct_id == GIVEN_NAME and v.roles[:1] == ("subject",))
    assert ancestor_tokens(v.path, v.roles) == [["subject", PATIENT], ["party-details", PATIENT_DETAILS], [HUMAN_NAME]]
    plain = next(v for v in resolved(rm=False).values)
    assert all(len(t) == 1 for t in ancestor_tokens(plain.path, plain.roles))


def test_a_component_rule_wins_over_an_element_rule(tmp_path):
    m = mapping(tmp_path, [{"element": "function", "to": "f", "transform": "copy"},
                           {"component": ATTESTER_FUNCTION, "to": "g", "transform": "copy"}], reference_model=True)
    ps, _ = place(resolved(), m)
    assert {names(p.steps) for p in ps if p.value.role == "function"} == {"f", "g"}
    assert names(next(p for p in ps if p.value.ct_id == ATTESTER_FUNCTION).steps) == "g"


# ------------------------------------------------------------------ end to end

def test_the_engine_reads_the_parts_only_when_the_mapping_asks(tmp_path):
    engine = Engine(schema_dir=tmp_path / "schemas")
    plain = engine.map_record(RECORD, CSVMapper(), mapping(tmp_path, [{"cluster": AUTHOR, "to": "/author"}]),
                              directory=str(SAMPLE))
    assert plain.outcome == VALID, plain.errors
    asked = engine.map_record(RECORD, CSVMapper(), mapping(tmp_path, RULES, reference_model=True), directory=str(SAMPLE))
    assert asked.outcome == VALID, asked.errors
    assert len(plain.report.values) == 22 and len(asked.report.values) == 126
    rows = {(tuple(r["path"]), r["label"]) for r in asked.report.values}
    assert (("Author", "Health Professional", "Human Name"), "Given Name (Person)") in rows
    assert (("Attester Participation",), "start") in rows


def test_a_recovery_inside_a_participations_function_lands_on_its_value(tmp_path):
    """The Attester's function is fixed to 'attester' by its pattern: the gate recovers a wrong one with an Exceptional
    Value, and numbers it as the reader does when the mapping reads the parts. Without them, as in 4.3, a recovery
    outside the data cluster is not inside a value the mapping can see, and the record is refused."""
    bad = RECORD.replace(b"<xdstring-value>attester</xdstring-value>", b"<xdstring-value>attestor</xdstring-value>")
    assert bad != RECORD
    g = Gate().check(bad, PKG, MODEL, reference_model=True)
    assert g.outcome == VALID_WITH_EV, g.errors
    (rec,) = g.recoveries
    r = resolve(read_record(g.xml, MODEL, reference_model=True), MODEL, PKG, g)
    v = r.values[rec.position]
    assert rec.ct_id == ATTESTER_FUNCTION and v.ct_id == ATTESTER_FUNCTION and v.role == "function"
    assert v.exceptional.origin == "recovery" and v.exceptional.constraint and v.value is None
    assert Gate().check(bad, PKG, MODEL).outcome == "refused"


def test_check_mapping_reports_the_parts_a_mapping_reads(tmp_path):
    from sdc_mapper import mcp_server as srv
    mapping(tmp_path, RULES + [{"component": "zzzzzzzzzzzzzzzzzzzzzzzz", "to": "x", "transform": "copy"}], reference_model=True)
    out = srv._handle_check_mapping({"mapping": str(tmp_path / "m.yaml"), "directory": str(SAMPLE)})
    assert out["ok"] and out["reference_model"] is True
    assert set(out["reference_model_names"]) == {"subject", "start", "pending"}
    assert out["unknown_identifiers"] == ["zzzzzzzzzzzzzzzzzzzzzzzz"]
