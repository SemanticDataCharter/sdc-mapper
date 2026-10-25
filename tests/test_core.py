"""The core end to end with the reference CSV mapper, on a HalvorsenDemo Credit Note record."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from refmapper import CSVMapper
from sdc_mapper import (DECLARED, ERROR, LOOKUP, LOST, NOT_REPRESENTABLE, REFUSED, VALID, VALID_WITH_EV, Engine,
                        MappingError, MapResult, ValueOutcome, load_mapping)
from sdcreader import load_package

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "halvorsen-credit-note"
CT = "jkuyr38hydqaiwlq5vhp2rjr"
RECORD = (SAMPLE / "record.xml").read_bytes()
SHA = load_package(SAMPLE, CT).sha256
LINE_COUNT = b"<xdcount-value>1</xdcount-value>"


@pytest.fixture
def engine(tmp_path):
    return Engine(log_dir=tmp_path / "log", schema_dir=tmp_path / "schemas")


def run(engine, xml=RECORD, mapping=None, mapper=None):
    return engine.map_record(xml, mapper or CSVMapper(), mapping=mapping, directory=str(SAMPLE), caller="tests")


def mapping_file(tmp_path, **extra):
    doc = {"mapping": {"id": "credit-note-to-csv", "version": 1}, "model": {"ct_id": CT, "schema_sha256": SHA},
           "target": {"name": "Reference CSV", "version": "1"}, **extra}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return load_mapping(p)


def source_ev(xml=RECORD, code=b"ASKR", name=b"Asked and Refused", keep_value=True):
    """An Exceptional Value written at the source on the first string value. The model requires the value element
    (SDCStudio's generated schemas do, though the reference model leaves it optional), so by default it stays beside
    the Exceptional Value, as a record made with this model must keep it."""
    start = xml.index(b"<xdstring-value>")
    end = xml.index(b"</xdstring-value>", start) + len(b"</xdstring-value>")
    ev = b"<sdc4:" + code + b"><ev-name>" + name + b"</ev-name></sdc4:" + code + b">"
    return xml[:start] + ev + (xml[start:end] if keep_value else b"") + xml[end:]


# ---------------------------------------------------------------- the gate's three outcomes

def test_a_valid_record_maps_and_every_value_is_reported(engine):
    r = run(engine)
    assert r.outcome == VALID and r.mapped
    rows = r.files["record.csv"].decode().splitlines()
    assert len(rows) - 1 == len(r.report.values) == r.report.counts[LOOKUP]
    assert r.report.exceptional == [] and "holds everything" in r.report.could_not_say


def test_one_bad_value_is_an_exceptional_value_not_a_refusal(engine):
    r = run(engine, RECORD.replace(LINE_COUNT, b"<xdcount-value>abc</xdcount-value>", 1))
    assert r.outcome == VALID_WITH_EV and r.mapped
    (e,) = r.report.exceptional
    assert (e["label"], e["code"], e["origin"]) == ("Line Count", "INV", "recovery")
    assert "abc" in e["constraint"]
    assert b"abc" not in r.files["record.csv"], "a rejected value is never written as a value"


def test_a_structurally_broken_record_is_refused_with_its_reasons(engine):
    r = run(engine, RECORD.replace(b"<dm-label>Credit Note</dm-label>", b"", 1))
    assert r.outcome == REFUSED and not r.files and r.report is None
    assert r.errors and r.errors[0]["kind"] == "structural" and "dm-label" in r.errors[0]["reason"]


def test_a_record_of_another_model_is_refused(engine):
    r = run(engine, RECORD.replace(f"dm-{CT}".encode(), b"dm-" + b"a" * 24))
    assert r.outcome == REFUSED and r.errors[0]["kind"] == "wrong model"


def test_not_xml_is_refused(engine):
    assert run(engine, b"<sdc4:dm-" + CT.encode()).outcome == REFUSED


# ---------------------------------------------------------------- Exceptional Values

def test_an_exceptional_value_written_at_the_source_is_kept_and_reported(engine):
    r = run(engine, source_ev())
    assert r.outcome == VALID
    (e,) = r.report.exceptional
    assert (e["code"], e["origin"], e["class"]) == ("ASKR", "source", "carried")
    assert b",ASKR," in r.files["record.csv"]
    assert b"urn:kestrel:translator" not in r.files["record.csv"].split(b"ASKR")[0].splitlines()[-1], \
        "the value beside a source Exceptional Value is not written"
    assert "survived only as text" in r.report.could_not_say


def test_where_the_model_requires_the_value_a_bare_exceptional_value_is_refused_not_guessed(engine):
    r = run(engine, source_ev(keep_value=False))
    assert r.outcome == REFUSED and "xdstring-value" in r.errors[0]["reason"]


def test_a_target_that_loses_it_says_so(engine, tmp_path):
    m = mapping_file(tmp_path, exceptional_values={"ASKR": {"class": "lost"}})
    r = run(engine, source_ev(), mapping=m)
    assert r.report.counts["exceptional"][LOST] == 1
    assert "became empty values (1 Asked and Refused)" in r.report.could_not_say


def test_a_rejected_value_is_written_only_when_the_mapping_asks_and_only_marked(engine, tmp_path):
    bad = RECORD.replace(LINE_COUNT, b"<xdcount-value>abc</xdcount-value>", 1)
    m = mapping_file(tmp_path, carry_rejected=True)
    csv = run(engine, bad, mapping=m).files["record.csv"].decode()
    assert "REJECTED: xdcount-value=abc" in csv


# ---------------------------------------------------------------- mapping files

def test_rules_place_values_and_the_report_counts_how(engine, tmp_path):
    from sdcreader import read_model
    model = read_model(load_package(SAMPLE, CT))
    token = next(c for c in model.components.values() if c.sdc_type == "XdToken")
    temporal = next(c for c in model.components.values() if c.sdc_type == "XdTemporal")
    m = mapping_file(tmp_path, rules=[
        {"component": token.ct_id, "to": "action", "transform": "code", "table": {"C": "create"}},
        {"component": temporal.ct_id, "to": "-", "transform": "omit", "reason": "the target has no timestamp column"},
    ])
    r = run(engine, mapping=m)
    by_ct = {v["ct_id"]: v["class"] for v in r.report.values}
    assert by_ct[token.ct_id] == DECLARED and by_ct[temporal.ct_id] == NOT_REPRESENTABLE
    assert "had no place in Reference CSV" in r.report.could_not_say


def test_a_mapping_for_another_schema_version_is_an_error_and_logged(engine, tmp_path):
    doc = {"mapping": {"id": "x", "version": 1}, "model": {"ct_id": CT, "schema_sha256": "0" * 64},
           "target": {"name": "Reference CSV", "version": "1"}}
    (tmp_path / "m.yaml").write_text(yaml.safe_dump(doc))
    r = run(engine, mapping=load_mapping(tmp_path / "m.yaml"))
    assert r.outcome == ERROR and r.errors[0]["kind"] == "mapping" and "new mapping version" in r.errors[0]["reason"]
    assert engine.log.entries()[-1]["outcome"] == ERROR


@pytest.mark.parametrize("bad, words", [
    ({"rules": [{"component": "a" * 24, "to": "x", "transform": "code"}]}, "table"),
    ({"rules": [{"component": "a" * 24, "to": "x", "transform": "eval"}]}, "eval"),
    ({"exceptional_values": {"XYZ": {"class": "lost"}}}, "XYZ"),
    ({"model": {"ct_id": CT}}, "schema_sha256"),
])
def test_a_mapping_file_that_does_not_validate_is_refused_before_any_record(tmp_path, bad, words):
    doc = {"mapping": {"id": "x", "version": 1}, "model": {"ct_id": CT, "schema_sha256": SHA},
           "target": {"name": "Reference CSV", "version": "1"}, **bad}
    (tmp_path / "m.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(MappingError, match=words):
        load_mapping(tmp_path / "m.yaml")


# ---------------------------------------------------------------- nothing processed goes unrecorded

class DropsOne(CSVMapper):
    def map(self, record, mapping):
        res = super().map(record, mapping)
        return MapResult(res.files, res.outcomes[1:])


class ValueClassForAnEV(CSVMapper):
    def map(self, record, mapping):
        res = super().map(record, mapping)
        return MapResult(res.files, [ValueOutcome(o.position, LOOKUP) for o in res.outcomes])


def test_a_mapper_that_drops_a_value_fails_the_call(engine):
    r = run(engine, mapper=DropsOne())
    assert r.outcome == ERROR and r.errors[0]["kind"] == "incomplete mapping" and not r.files
    assert engine.log.entries()[-1]["errors"] == [{"kind": "incomplete mapping", "path": ""}]


def test_a_mapper_that_passes_off_an_exceptional_value_as_a_value_fails_the_call(engine):
    r = run(engine, source_ev(), mapper=ValueClassForAnEV())
    assert r.outcome == ERROR and "Exceptional Value ASKR" in r.errors[0]["reason"]


def test_outputs_and_reports_are_byte_stable(engine, tmp_path):
    a, b = run(engine), run(Engine(log_dir=tmp_path / "other"))
    assert a.files == b.files and a.report.to_json() == b.report.to_json()


# ---------------------------------------------------------------- the log

def test_every_call_is_logged_with_anchors_and_no_values(engine):
    bad = RECORD.replace(LINE_COUNT, b"<xdcount-value>zzqx</xdcount-value>", 1)   # letters no hash can contain
    results = [run(engine), run(engine, bad), run(engine, RECORD.replace(b"<dm-label>Credit Note</dm-label>", b"", 1))]
    entries = engine.log.entries()
    assert [e["outcome"] for e in entries] == [VALID, VALID_WITH_EV, REFUSED]
    assert [r.log_hash for r in results] == [e["hash"] for e in entries]
    ok = entries[0]
    assert ok["model"] == {"ct_id": CT, "schema_sha256": SHA} and len(ok["record_sha256"]) == 64
    assert set(ok["outputs"]) == {"record.csv"}
    assert entries[1]["exceptional"][0] | {"position": 0} == {"position": 0, "ct_id": entries[1]["exceptional"][0]["ct_id"],
                                                              "code": "INV", "origin": "recovery", "class": "carried"}
    text = "".join(p.read_text() for p in engine.log.dir.glob("mapper-*.jsonl"))
    assert "zzqx" not in text and "Duluth" not in text, "the log holds no values and no failure reasons"


def test_the_chain_verifies_and_shows_an_edit_or_a_deletion(engine):
    for _ in range(3):
        run(engine)
    assert engine.log.verify()[0]
    f = next(engine.log.dir.glob("mapper-*.jsonl"))
    lines = f.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["outcome"] = REFUSED
    f.write_text("\n".join([lines[0], json.dumps(edited, sort_keys=True), lines[2]]) + "\n")
    assert engine.log.verify() == (False, f"entry 2 ({edited['time']}) has been altered")
    f.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not engine.log.verify()[0]


def test_log_summary_counts_by_target(engine):
    run(engine)
    run(engine, source_ev())
    s = engine.log.summary(days=1)
    t = s["targets"]["Reference CSV"]
    assert s["calls"] == 2 and t["outcomes"] == {VALID: 2} and t["exceptional"]["carried"] == 1 and t["models"] == [CT]


def test_every_log_entry_validates_against_the_shipped_schema(engine, tmp_path):
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/sdc_mapper/schemas/log-entry.schema.json").read_text())
    m = mapping_file(tmp_path)
    run(engine)
    run(engine, RECORD.replace(LINE_COUNT, b"<xdcount-value>abc</xdcount-value>", 1), mapping=m)
    run(engine, RECORD.replace(b"<dm-label>Credit Note</dm-label>", b"", 1))
    run(engine, mapper=DropsOne())
    v = Draft202012Validator(schema)
    for e in engine.log.entries():
        assert not list(v.iter_errors(e)), (e["outcome"], [x.message for x in v.iter_errors(e)])


def test_a_source_exceptional_value_covering_a_bad_value_stays_the_sources(engine):
    """The record carries ASKR on a value that also fails its constraint: recovery covers it with the record's own
    Exceptional Value, inserts nothing, and the origin stays `source`."""
    xml = source_ev()
    start = xml.index(b"<xdstring-value>") + len(b"<xdstring-value>")
    end = xml.index(b"</xdstring-value>", start)
    bad = xml[:start] + b"two words" + xml[end:]          # the component's pattern is \S+
    r = run(engine, bad)
    assert r.outcome == VALID_WITH_EV
    (e,) = r.report.exceptional
    assert (e["code"], e["origin"], e["constraint"]) == ("ASKR", "source", None)
    assert b"<sdc4:INV>" not in bad


def test_the_gate_uses_only_sdcvalidators_public_api():
    import sdc_mapper.gate as gate
    src = Path(gate.__file__).read_text()
    for internal in ("error_mapper", "instance_modifier", "_schema_errors", "schema.iter_errors", "_find_element"):
        assert internal not in src, internal


def test_the_report_keeps_the_mappers_note(engine, tmp_path):
    from sdc_mapper import load_mapping
    import yaml
    from sdcreader import load_package
    pkg = load_package(SAMPLE, CT)
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"mapping": {"id": "n", "version": 1}, "model": {"ct_id": CT, "schema_sha256": pkg.sha256},
                                 "target": {"name": "Reference CSV", "version": "1"},
                                 "rules": [{"sdc_type": "XdString", "to": "-", "transform": "omit", "reason": "not here"}]}))
    r = engine.map_record(RECORD, CSVMapper(), load_mapping(p), directory=str(SAMPLE))
    assert any(v.get("note") == "not here" for v in r.report.values)
