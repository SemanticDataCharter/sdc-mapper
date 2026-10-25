"""The CLI, the MCP server and the fidelity table, with two reference mappers installed: one that carries every
Exceptional Value as text and one that loses them all."""
from __future__ import annotations

import json

import pytest

import sdc_mapper.mcp_server as srv
import sdc_mapper.service as service
from refmapper import CSVMapper
from sdc_mapper import EV_CARRIED, LOST, EVRule
from sdc_mapper.cli import main as cli
from sdcreader.record import EXCEPTIONAL_VALUES
from test_core import CT, LINE_COUNT, RECORD, SAMPLE, source_ev


class LossyMapper(CSVMapper):
    name = "lossy"
    target = "Lossy CSV"
    ev_table = {code: EVRule(LOST) for code in EXCEPTIONAL_VALUES}


@pytest.fixture(autouse=True)
def installed(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "installed_mappers", lambda: {"csv": CSVMapper, "lossy": LossyMapper})
    monkeypatch.setattr(srv, "_ENGINE", None)
    monkeypatch.setattr(srv, "_LOG_DIR", str(tmp_path / "mcp-log"))


@pytest.fixture
def two_evs(tmp_path):
    """A record with one source Exceptional Value and one from recovery."""
    p = tmp_path / "record.xml"
    p.write_bytes(source_ev(RECORD.replace(LINE_COUNT, b"<xdcount-value>abc</xdcount-value>", 1)))
    return p


def call(name, arguments):
    resp = srv._server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": name, "arguments": arguments}})
    content = resp["result"]["content"][0]["text"]
    return resp["result"], (json.loads(content) if not resp["result"].get("isError") else content)


def test_fidelity_puts_each_exceptional_value_beside_every_target(two_evs, tmp_path):
    from sdc_mapper import Engine
    table = service.fidelity(Engine(log_dir=tmp_path / "log"), two_evs, directory=str(SAMPLE))
    assert table["targets"] == ["Reference CSV 1", "Lossy CSV 1"]
    assert [(r["code"], r["origin"]) for r in table["rows"]] == [("ASKR", "source"), ("INV", "recovery")]
    for r in table["rows"]:
        assert r["targets"]["Reference CSV 1"]["class"] == EV_CARRIED and r["targets"]["Lossy CSV 1"]["class"] == LOST
    text = service.fidelity_text(table)
    assert "Asked and Refused (ASKR) | carried (ASKR) | lost" in text


def test_mcp_lists_targets_with_their_exceptional_value_tables():
    _, out = call("list_targets", {})
    assert [t["name"] for t in out["targets"]] == ["csv", "lossy"]
    assert out["targets"][1]["exceptional_values"]["ASKR"] == LOST


def test_mcp_map_record_returns_output_report_and_the_sentence_to_pass_on(two_evs):
    _, out = call("map_record", {"target": "lossy", "record": str(two_evs), "directory": str(SAMPLE)})
    assert out["outcome"] == "valid_with_ev" and "record.csv" in out["files"]
    assert out["could_not_say"].startswith("Lossy CSV: 2 Exceptional Values became empty values")
    assert len(out["log_hash"]) == 64


def test_mcp_map_record_names_the_model_from_the_record_and_writes_outputs(two_evs, tmp_path, monkeypatch):
    from sdcreader import load_package
    monkeypatch.setattr("sdcreader.mcp.fetch_package", lambda ct_id, host=None, **k: load_package(SAMPLE, ct_id))
    _, out = call("map_record", {"target": "csv", "record": str(two_evs), "output_dir": str(tmp_path / "out")})
    assert sorted(p.split("/")[-1] for p in out["written"]) == ["record.csv"]
    assert (tmp_path / "out" / "coverage.json").exists()


def test_mcp_refusal_is_a_result_not_a_crash(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_bytes(RECORD.replace(b"<dm-label>Credit Note</dm-label>", b"", 1))
    result, out = call("map_record", {"target": "csv", "record": str(p), "directory": str(SAMPLE)})
    assert not result.get("isError") and out["outcome"] == "refused" and out["errors"][0]["kind"] == "structural"


def test_mcp_unknown_target_is_a_tool_error():
    result, text = call("map_record", {"target": "nope", "record": "x.xml"})
    assert result["isError"] and "no mapper named 'nope'" in text


def test_mcp_log_summary_and_verify(two_evs):
    call("map_record", {"target": "csv", "record": str(two_evs), "directory": str(SAMPLE)})
    _, s = call("log_summary", {"days": 1})
    assert s["targets"]["Reference CSV"]["exceptional"] == {"carried": 2}
    _, v = call("verify_log", {})
    assert v["ok"] and v["message"] == "1 entries, chain intact"


def test_mcp_check_mapping(tmp_path):
    import yaml
    from sdcreader import load_package
    sha = load_package(SAMPLE, CT).sha256
    (tmp_path / "m.yaml").write_text(yaml.safe_dump({"mapping": {"id": "m", "version": 1},
                                                      "model": {"ct_id": CT, "schema_sha256": sha},
                                                      "target": {"name": "Reference CSV", "version": "1"}}))
    _, out = call("check_mapping", {"mapping": str(tmp_path / "m.yaml"), "directory": str(SAMPLE)})
    assert out["ok"] and out["sha256"] and out["model"] == CT


def test_cli_exit_codes_and_outputs(two_evs, tmp_path, capsys):
    log = ["--log-dir", str(tmp_path / "log")]
    assert cli(log + ["map", "csv", str(two_evs), "--package", str(SAMPLE), "-o", str(tmp_path / "out")]) == 0
    assert "became" not in capsys.readouterr().out
    bad = tmp_path / "bad.xml"
    bad.write_bytes(RECORD.replace(b"<dm-label>Credit Note</dm-label>", b"", 1))
    assert cli(log + ["map", "csv", str(bad), "--package", str(SAMPLE)]) == 1
    assert cli(log + ["map", "nope", str(bad), "--package", str(SAMPLE)]) == 2
    assert cli(log + ["fidelity", str(two_evs), "--package", str(SAMPLE)]) == 0
    assert "carried (ASKR) | lost" in capsys.readouterr().out
    assert cli(log + ["log", "verify"]) == 0
