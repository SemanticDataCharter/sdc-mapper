"""Chained records (4.5.0): a parent record and the records chained to it, mapped into one output.

The core is target-neutral, so most of this runs with a chain-mapping reference CSV mapper on the HalvorsenDemo Credit
Note (one model, the parent and its children all records of it). The last part checks sdc-mapper's Engine, CLI and MCP
against sdc-gdr2fhir's own `chain.map_files` on its five SDCHealth Laboratory Report chains, when sdc-gdr2fhir and its
samples are at hand.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import sdc_mapper.mcp_server as srv
import sdc_mapper.service as service
from refmapper import CSVMapper
from sdc_mapper import (ERROR, REFUSED, VALID, VALID_WITH_EV, ChainMapResult, Engine, MapResult, MappingError,
                        maps_chains)
from sdc_mapper.cli import main as cli
from test_core import CT, LINE_COUNT, RECORD, SAMPLE, mapping_file, source_ev

BROKEN = RECORD.replace(b"<dm-label>Credit Note</dm-label>", b"", 1)
WITH_EV = RECORD.replace(LINE_COUNT, b"<xdcount-value>abc</xdcount-value>", 1)


class ChainCSV(CSVMapper):
    """The reference CSV mapper, chaining: one CSV, the parent's rows then each child's, and each record's own
    outcomes."""
    name = "chaincsv"

    def map_chain(self, parent, children):
        results = [self.map(r, m) for r, m in [parent, *children]]
        body = b"".join(r.files["record.csv"] for r in results)
        files = {"chain.csv": body}
        return ChainMapResult(files=files, results=[MapResult(files, r.outcomes, r.cannot_hold) for r in results])


class Refuses(ChainCSV):
    def map_chain(self, parent, children):
        raise MappingError("chained record 2 does not belong to this parent")


class DropsOneOfTheSecond(ChainCSV):
    def map_chain(self, parent, children):
        out = super().map_chain(parent, children)
        out.results[2].outcomes.pop()
        return out


class TooFewResults(ChainCSV):
    def map_chain(self, parent, children):
        out = super().map_chain(parent, children)
        out.results.pop()
        return out


@pytest.fixture
def engine(tmp_path):
    return Engine(log_dir=tmp_path / "log", schema_dir=tmp_path / "schemas")


def chain(engine, children=(RECORD, RECORD), parent=RECORD, mapper=None, **kw):
    n = 1 + len(children)
    return engine.map_chain(parent, list(children), mapper or ChainCSV(), directories=[str(SAMPLE)] * n,
                            caller="tests", **kw)


def schema_validator():
    from jsonschema import Draft202012Validator
    return Draft202012Validator(json.loads(
        (Path(__file__).resolve().parents[1] / "src/sdc_mapper/schemas/log-entry.schema.json").read_text()))


# ---------------------------------------------------------------- the engine

def test_a_chain_maps_to_one_output_with_every_records_report_in_order(engine):
    r = chain(engine, children=(RECORD, WITH_EV))
    assert r.outcome == VALID_WITH_EV and r.mapped and set(r.files) == {"chain.csv"}
    assert [x.outcome for x in r.records] == [VALID, VALID, VALID_WITH_EV]
    assert [x.ct_id for x in r.records] == [CT] * 3
    for x in r.records:
        assert len(x.report.values) == sum(v for k, v in x.report.counts.items() if k != "exceptional") \
            + sum(x.report.counts["exceptional"].values())
    assert r.records[2].report.exceptional[0]["code"] == "INV"
    assert b"abc" not in r.files["chain.csv"]


def test_a_chain_without_exceptional_values_is_valid(engine):
    assert chain(engine).outcome == VALID


def test_a_target_that_maps_one_record_at_a_time_cannot_map_a_chain(engine):
    assert not maps_chains(CSVMapper()) and maps_chains(ChainCSV())
    r = chain(engine, mapper=CSVMapper())
    assert r.outcome == ERROR and not r.files and r.errors[0]["kind"] == "chain"
    assert "one record at a time" in r.errors[0]["reason"]


def test_any_refused_record_refuses_the_chain_and_says_which(engine):
    r = chain(engine, children=(RECORD, BROKEN, BROKEN))
    assert r.outcome == REFUSED and not r.files and not r.records
    assert {e["record"] for e in r.errors} == {2, 3} and r.errors[0]["kind"] == "structural"
    r = chain(engine, parent=BROKEN)
    assert r.outcome == REFUSED and {e["record"] for e in r.errors} == {0}


def test_a_childs_mapping_for_another_schema_is_an_error_naming_it(engine, tmp_path):
    good = mapping_file(tmp_path)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = mapping_file(bad_dir, model={"ct_id": CT, "schema_sha256": "0" * 64})
    r = chain(engine, parent_mapping=good, child_mappings=[good, bad])
    assert r.outcome == ERROR and r.errors[0] | {"reason": ""} == {"kind": "mapping", "record": 2, "reason": ""}


def test_an_unreachable_model_is_an_error_naming_the_record(engine):
    r = engine.map_chain(RECORD, [b"<x/>"], ChainCSV(), directories=[str(SAMPLE), None])
    assert r.outcome == ERROR and r.errors[0]["kind"] == "model" and r.errors[0]["record"] == 1


def test_a_target_that_refuses_the_chain_is_an_error_with_its_reason(engine):
    r = chain(engine, mapper=Refuses())
    assert r.outcome == ERROR and r.errors == [{"kind": "mapping", "reason": "chained record 2 does not belong to "
                                                                            "this parent"}]


def test_a_record_the_target_did_not_account_for_fails_the_chain(engine):
    r = chain(engine, mapper=DropsOneOfTheSecond())
    assert r.outcome == ERROR and r.errors[0]["kind"] == "incomplete mapping" and r.errors[0]["record"] == 2
    r = chain(engine, mapper=TooFewResults())
    assert r.outcome == ERROR and "2 results for a chain of 3" in r.errors[0]["reason"]


def test_records_and_their_side_lists_must_line_up(engine):
    with pytest.raises(ValueError):
        engine.map_chain(RECORD, [RECORD], ChainCSV(), directories=[str(SAMPLE)])


def test_a_chain_is_one_log_entry_with_every_records_anchors_and_no_values(engine, tmp_path):
    letters = RECORD.replace(LINE_COUNT, b"<xdcount-value>zzqx</xdcount-value>", 1)   # letters no hash can contain
    ok = chain(engine, children=(RECORD, letters), parent_mapping=mapping_file(tmp_path), child_mappings=[None, None])
    chain(engine, children=(BROKEN,))
    chain(engine, mapper=DropsOneOfTheSecond())
    chain(engine, mapper=CSVMapper())
    entries = engine.log.entries()
    assert [e["outcome"] for e in entries] == [VALID_WITH_EV, REFUSED, ERROR, ERROR]
    assert entries[0]["hash"] == ok.log_hash
    first = entries[0]
    assert first["mapping"]["id"] == "credit-note-to-csv" and len(first["chained"]) == 2
    assert [c["mapping"] for c in first["chained"]] == [None, None]
    assert first["chained"][1]["exceptional"][0]["code"] == "INV"
    assert first["outputs"].keys() == {"chain.csv"}
    assert entries[1]["errors"][0]["record"] == 1 and "reason" not in entries[1]["errors"][0]
    v = schema_validator()
    for e in entries:
        assert not list(v.iter_errors(e)), [x.message for x in v.iter_errors(e)]
    assert engine.log.verify()[0]
    text = "".join(p.read_text() for p in engine.log.dir.glob("mapper-*.jsonl"))
    assert "zzqx" not in text, "the log holds no values and no failure reasons"


def test_log_summary_counts_every_record_of_a_chain(engine):
    chain(engine, children=(RECORD, source_ev()))
    t = engine.log.summary(days=1)["targets"]["Reference CSV"]
    one = engine.log.entries()[0]["counts"]["lookup"]
    assert t["calls"] == 1 and t["values"]["lookup"] >= 2 * one and t["exceptional"]["carried"] == 1


def test_a_single_record_log_entry_is_as_before(engine):
    engine.map_record(RECORD, CSVMapper(), directory=str(SAMPLE))
    e = engine.log.entries()[0]
    assert "chained" not in e and set(e) == {"log_version", "time", "caller", "record_sha256", "mapper", "target",
                                            "mapping", "model", "record", "counts", "exceptional", "outputs",
                                            "outcome", "errors", "previous", "hash"}


# ---------------------------------------------------------------- the CLI and the MCP server, reference mapper

@pytest.fixture
def installed(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "installed_mappers", lambda: {"chaincsv": ChainCSV, "csv": CSVMapper})
    monkeypatch.setattr(srv, "_ENGINE", None)
    monkeypatch.setattr(srv, "_LOG_DIR", str(tmp_path / "mcp-log"))


def call(name, arguments):
    resp = srv._server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": name, "arguments": arguments}})
    content = resp["result"]["content"][0]["text"]
    return resp["result"], (json.loads(content) if not resp["result"].get("isError") else content)


def test_cli_map_chain_writes_the_output_and_the_reports_and_exits_as_map(installed, tmp_path, capsys):
    log = ["--log-dir", str(tmp_path / "log")]
    rec = str(SAMPLE / "record.xml")
    bad = tmp_path / "bad.xml"
    bad.write_bytes(BROKEN)
    out = tmp_path / "out"
    assert cli(log + ["map-chain", "chaincsv", rec, "--package", str(SAMPLE), "--child", "-", rec,
                      "--packages", str(SAMPLE.parent), "-o", str(out)]) == 0
    reports = json.loads((out / "report.json").read_text())
    assert [r["instance_id"] for r in reports] == [reports[0]["instance_id"]] * 2 and (out / "chain.csv").is_file()
    assert "chained record 1:" in capsys.readouterr().out
    assert cli(log + ["map-chain", "chaincsv", rec, "--package", str(SAMPLE), "--child", "-", str(bad),
                      "--packages", str(SAMPLE.parent)]) == 1
    assert "chained record 1, structural" in capsys.readouterr().out
    assert cli(log + ["map-chain", "csv", rec, "--package", str(SAMPLE), "--child", "-", rec,
                      "--packages", str(SAMPLE.parent)]) == 2
    assert cli(log + ["map-chain", "chaincsv"]) == 2


def test_cli_map_chain_reads_a_chain_manifest(installed, tmp_path):
    man = tmp_path / "set" / "chain.json"
    man.parent.mkdir()
    (man.parent / "child.xml").write_bytes(WITH_EV)
    man.write_text(json.dumps({"parent": str(SAMPLE / "record.xml"), "parent_ct_id": CT,
                               "children": [{"file": "child.xml", "ct_id": CT}]}))
    out = tmp_path / "out"
    assert cli(["--log-dir", str(tmp_path / "log"), "map-chain", "chaincsv", "--chain", str(man),
                "--packages", str(SAMPLE.parent), "-o", str(out)]) == 0
    assert [r["outcome"] for r in json.loads((out / "report.json").read_text())] == [VALID, VALID_WITH_EV]
    assert cli(["--log-dir", str(tmp_path / "log"), "map-chain", "chaincsv", "--chain", str(man),
                "--child", "-", str(SAMPLE / "record.xml")]) == 2


def test_mcp_map_chain_returns_the_output_and_each_records_report(installed, tmp_path):
    child = tmp_path / "child.xml"
    child.write_bytes(WITH_EV)
    result, out = call("map_chain", {"target": "chaincsv", "parent": str(SAMPLE / "record.xml"),
                                     "directory": str(SAMPLE),
                                     "children": [{"record": str(child), "directory": str(SAMPLE)}]})
    assert not result.get("isError")
    assert out["outcome"] == VALID_WITH_EV and set(out["files"]) == {"chain.csv"} and out["log_hash"]
    assert [r["outcome"] for r in out["records"]] == [VALID, VALID_WITH_EV]
    assert "survived only as text" in out["records"][1]["could_not_say"]
    _, out = call("map_chain", {"target": "chaincsv", "parent": str(SAMPLE / "record.xml"),
                                "packages": [str(SAMPLE.parent)], "children": [{"record": str(child)}],
                                "output_dir": str(tmp_path / "o")})
    assert (tmp_path / "o" / "report.json").is_file() and out["written"]


def test_mcp_map_chain_refusals_and_misuse(installed, tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_bytes(BROKEN)
    _, out = call("map_chain", {"target": "chaincsv", "parent": str(SAMPLE / "record.xml"),
                                "packages": [str(SAMPLE.parent)], "children": [{"record": str(bad)}]})
    assert out["outcome"] == REFUSED and out["errors"][0]["record"] == 1 and "files" not in out
    for args, words in [({"parent": "x.xml", "chain": "c.json"}, "not both"),
                        ({"parent": str(SAMPLE / "record.xml")}, "at least one"),
                        ({"parent": str(SAMPLE / "record.xml"),
                          "children": [{"record": "x"}] * (srv.MAX_CHILDREN + 1)}, "at most"),
                        ({"chain": str(tmp_path / "nope.json")}, "No such file")]:
        result, text = call("map_chain", {"target": "chaincsv", **args})
        assert result.get("isError") and words in text, (args, text)
    assert "map_chain" in {t["name"] for t in srv.TOOLS}


# ---------------------------------------------------------------- against sdc-gdr2fhir's own chain.map_files

GDR2FHIR = Path(os.environ.get("SDC_GDR2FHIR_REPO", Path(__file__).resolve().parents[2] / "sdc-gdr2fhir"))
CHAINS = GDR2FHIR / "samples" / "sdchealth-lab-chains"
REPORT_CT = "zptekdommw56yiz9lqjy5dga"


@pytest.fixture(scope="module")
def fhir():
    pytest.importorskip("sdc_gdr2fhir.chain")
    if not CHAINS.is_dir():
        pytest.skip(f"sdc-gdr2fhir's chain samples are not at {CHAINS}")
    import sdc_gdr2fhir
    return sdc_gdr2fhir


def _chains():
    return sorted(CHAINS.glob("chain-*")) if CHAINS.is_dir() else []


def _spec(fhir, d):
    """A chain as sdc-gdr2fhir's test takes it, and as a manifest naming each mapping and package."""
    man = json.loads((d / "chain.json").read_text(encoding="utf-8"))
    parent = (GDR2FHIR / man["parent"], fhir.mapping_path(REPORT_CT), GDR2FHIR / "samples" / "sdchealth-lab-report")
    children = [(d / c["file"], fhir.mapping_path(c["ct_id"]), GDR2FHIR / "samples" / f"sdchealth-{c['model']}")
                for c in man["children"]]
    return parent, children


@pytest.fixture(scope="module")
def reference(fhir, tmp_path_factory):
    """Each chain's Bundle and per-record reports from sdc-gdr2fhir's chain.map_files."""
    from sdc_gdr2fhir.chain import map_files
    from sdc_mapper import load_mapping
    engine = Engine(schema_dir=tmp_path_factory.mktemp("schemas"))
    out = {}
    for d in _chains():
        parent, children = _spec(fhir, d)
        r = map_files(engine, (parent[0].read_bytes(), load_mapping(parent[1]), str(parent[2])),
                      [(x.read_bytes(), load_mapping(m), str(p)) for x, m, p in children])
        out[d.name] = (r.outcome, r.files, [{"instance_id": x.instance_id, "ct_id": x.ct_id, "outcome": x.outcome,
                                             "report": x.report.to_dict()} for x in r.records])
    return out


@pytest.fixture
def fhir_installed(fhir, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "installed_mappers", lambda: {"fhir": fhir.FHIRMapper})
    monkeypatch.setattr(srv, "_ENGINE", None)
    monkeypatch.setattr(srv, "_LOG_DIR", str(tmp_path / "mcp-log"))


def test_there_are_five_sample_chains(fhir):
    assert len(_chains()) == 5


def test_engine_map_chain_gives_what_chain_map_files_gives(fhir, reference, tmp_path):
    from sdc_mapper import load_mapping
    engine = Engine(log_dir=tmp_path / "log", schema_dir=tmp_path / "schemas")
    for d in _chains():
        parent, children = _spec(fhir, d)
        mappings = [load_mapping(m) for _, m, _ in [parent, *children]]
        r = engine.map_chain(parent[0].read_bytes(), [x.read_bytes() for x, _, _ in children],
                             fhir.FHIRMapper.for_mapping(mappings[0]), parent_mapping=mappings[0],
                             child_mappings=mappings[1:], directories=[str(p) for _, _, p in [parent, *children]])
        outcome, files, records = reference[d.name]
        assert r.outcome == outcome and r.files == files, d.name
        assert [x.to_dict() for x in r.records] == records, d.name
    v = schema_validator()
    assert all(not list(v.iter_errors(e)) for e in engine.log.entries()) and engine.log.verify()[0]


def test_cli_map_chain_gives_the_same_bundles(fhir_installed, fhir, reference, tmp_path):
    for d in _chains():
        parent, children = _spec(fhir, d)
        out = tmp_path / d.name
        args = ["--log-dir", str(tmp_path / "log"), "map-chain", "fhir", str(parent[0]), "--mapping", str(parent[1]),
                "--package", str(parent[2]), "--packages", str(GDR2FHIR / "samples"), "-o", str(out)]
        for x, m, _ in children:
            args += ["--child", str(m), str(x)]
        assert cli(args) == 0, d.name
        outcome, files, records = reference[d.name]
        assert (out / "Bundle.json").read_bytes() == files["Bundle.json"], d.name
        assert json.loads((out / "report.json").read_text()) == json.loads(json.dumps(records, default=str)), d.name


def test_mcp_map_chain_gives_the_same_bundles(fhir_installed, fhir, reference, tmp_path):
    for d in _chains():
        parent, children = _spec(fhir, d)
        man = tmp_path / d.name / "chain.json"          # the sample's manifest, with each mapping named
        man.parent.mkdir()
        sample = json.loads((d / "chain.json").read_text(encoding="utf-8"))
        man.write_text(json.dumps({"parent": str(parent[0]), "parent_mapping": str(parent[1]),
                                   "parent_ct_id": sample["parent_ct_id"],
                                   "children": [{"file": str(x), "mapping": str(m), "ct_id": c["ct_id"]}
                                                for (x, m, _), c in zip(children, sample["children"])]}))
        result, out = call("map_chain", {"target": "fhir", "chain": str(man), "packages": [str(GDR2FHIR / "samples")]})
        assert not result.get("isError"), out
        outcome, files, records = reference[d.name]
        assert out["outcome"] == outcome and out["files"]["Bundle.json"].encode("utf-8") == files["Bundle.json"]
        assert [r["report"] for r in out["records"]] == json.loads(json.dumps([r["report"] for r in records],
                                                                              default=str)), d.name
