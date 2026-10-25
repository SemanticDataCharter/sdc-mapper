"""Placements (4.1.0): cluster rules, context, relative and absolute paths, attributes, units, transforms, repeats."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sdc_mapper import Gate, MappingError, load_mapping, place, resolve
from sdcreader import load_package, read_model
from sdcreader.record import read_record

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "halvorsen-credit-note"
CT = "jkuyr38hydqaiwlq5vhp2rjr"
PKG = load_package(SAMPLE, CT)
MODEL = read_model(PKG)
RECORD = (SAMPLE / "record.xml").read_bytes()


def ct(label: str) -> str:
    found = [c.ct_id for c in MODEL.components.values() if c.label == label]
    assert len(found) == 1, (label, found)
    return found[0]


def resolved(xml: bytes = RECORD):
    g = Gate().check(xml, PKG, MODEL)
    return resolve(read_record(g.xml, MODEL), MODEL, PKG, g)


def mapping(tmp_path, rules, **extra):
    doc = {"mapping": {"id": "t", "version": 1}, "model": {"ct_id": CT, "schema_sha256": PKG.sha256},
           "target": {"name": "UBL", "version": "2.3"}, "rules": rules, **extra}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return load_mapping(p)


PARTY_RULES = [
    {"cluster": ct("Accounting Customer Party"), "to": "cac:AccountingCustomerParty"},
    {"cluster": ct("Accounting Supplier Party"), "to": "cac:AccountingSupplierParty"},
    {"cluster": ct("Party"), "to": "cac:Party"},
    {"component": ct("Organization Name"), "to": "cac:PartyName/cbc:Name", "transform": "copy"},
    {"component": ct("Party Identification Value"), "to": "cac:PartyIdentification/cbc:ID", "transform": "copy"},
    {"component": ct("Party Identification Scheme"), "to": "cac:PartyIdentification/cbc:ID/@schemeName", "transform": "copy"},
]


def by_label(placements, label):
    return [p for p in placements if p.value.label == label]


def names(steps):
    return "/".join(s for s, _ in steps)


def test_one_shared_component_lands_by_its_enclosing_cluster(tmp_path):
    ps, _ = place(resolved(), mapping(tmp_path, PARTY_RULES))
    where = {names(p.steps) for p in by_label(ps, "Organization Name") if p.rule}
    assert "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name" in where
    assert "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name" in where


def test_an_attribute_step_goes_on_the_element_before_it(tmp_path):
    ps, _ = place(resolved(), mapping(tmp_path, PARTY_RULES))
    scheme = next(p for p in by_label(ps, "Party Identification Scheme") if p.steps)
    value = next(p for p in by_label(ps, "Party Identification Value") if p.steps[:2] == scheme.steps[:2])
    assert scheme.attribute == "schemeName" and scheme.steps == value.steps[: len(scheme.steps)]


def test_values_under_unmapped_wrappers_are_unmapped_not_dropped(tmp_path):
    ps, _ = place(resolved(), mapping(tmp_path, PARTY_RULES))
    assert by_label(ps, "Email Address") and all(p.unmapped for p in by_label(ps, "Email Address"))
    assert len(ps) == len(resolved().values), "every value has a placement"


def test_within_picks_the_rule_for_a_cluster_by_context(tmp_path):
    rules = PARTY_RULES + [
        {"cluster": ct("Party"), "within": [ct("Accounting Supplier Party")], "to": "cac:Party/cac:Supplier"},
    ]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    where = {names(p.steps) for p in by_label(ps, "Organization Name") if p.rule}
    assert "cac:AccountingSupplierParty/cac:Party/cac:Supplier/cac:PartyName/cbc:Name" in where
    assert "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name" in where


def test_an_absolute_path_ignores_the_enclosing_cluster(tmp_path):
    rules = PARTY_RULES + [{"component": ct("Supplier Assigned Account ID"), "to": "/cbc:AccountingCost", "transform": "copy"}]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    assert {names(p.steps) for p in by_label(ps, "Supplier Assigned Account ID")} == {"cbc:AccountingCost"}


def test_an_omitted_cluster_omits_everything_under_it(tmp_path):
    rules = PARTY_RULES + [{"cluster": ct("Party Contact"), "to": "-", "transform": "omit", "reason": "no contact in UBL here"}]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    contact = by_label(ps, "Contact Name") + by_label(ps, "Email Address")
    assert contact and all(p.omitted and p.omitted_by.reason == "no contact in UBL here" for p in contact)


def test_units_go_to_an_attribute_through_a_code_table(tmp_path):
    quantity = next(v for v in resolved().values if v.units and v.kind == "decimal")
    rules = [{"component": quantity.ct_id, "to": "/cbc:Amount", "transform": "copy",
              "units": {"to": "@unitCode", "transform": "code", "table": {quantity.units.symbol: "XYZ"}}}]
    p = next(p for p in place(resolved(), mapping(tmp_path, rules))[0] if p.value.ct_id == quantity.ct_id)
    assert (p.units_attribute, p.units_text) == ("unitCode", "XYZ") and p.text


def test_a_code_table_miss_fails_rather_than_guess(tmp_path):
    token = next(v for v in resolved().values if v.kind == "token" and v.value)
    rules = [{"component": token.ct_id, "to": "/cbc:Code", "transform": "code", "table": {"nothing": "x"}}]
    with pytest.raises(MappingError, match="code table"):
        place(resolved(), mapping(tmp_path, rules))


def test_a_date_is_formatted_for_the_target(tmp_path):
    t = next(v for v in resolved().values if v.kind == "temporal" and v.detail.get("form") == "datetime")
    rules = [{"component": t.ct_id, "to": "/cbc:IssueDate", "transform": "format", "format": "%Y-%m-%d"}]
    p = next(p for p in place(resolved(), mapping(tmp_path, rules))[0] if p.value.ct_id == t.ct_id)
    assert p.text == t.value[:10]


def test_repeats_of_a_cluster_become_separate_target_elements(tmp_path):
    """A second instance of the Accounting Supplier Party (this model allows one, so the second is made in the resolved
    record): its values carry instance 1 at that cluster, and land in a second set of target elements."""
    import dataclasses
    rs = resolved()
    asp = ct("Accounting Supplier Party")
    copies = []
    for v in rs.values:
        ids = [c.ct_id for c in v.path]
        if asp in ids:
            i = ids.index(asp)
            copies.append(dataclasses.replace(v, position=len(rs.values) + len(copies),
                                              instances=v.instances[:i] + (1,) + v.instances[i + 1:]))
    rs.values.extend(copies)
    ps, clusters = place(rs, mapping(tmp_path, PARTY_RULES))
    keys = {p.steps[0][1] for p in by_label(ps, "Organization Name") if p.steps and p.steps[0][0] == "cac:AccountingSupplierParty"}
    assert len(keys) == 2
    assert sum(1 for c in clusters if c.steps[-1][0] == "cac:AccountingSupplierParty") == 2


def test_an_exceptional_value_is_placed_but_never_written(tmp_path):
    xml = RECORD
    start = xml.index(b"<xdstring-value>")
    end = xml.index(b"</xdstring-value>", start) + len(b"</xdstring-value>")
    xml = xml[:start] + b"<sdc4:ASKR><ev-name>Asked and Refused</ev-name></sdc4:ASKR>" + xml[start:end] + xml[end:]
    rs = resolved(xml)
    v = rs.exceptional[0]
    p = next(p for p in place(rs, mapping(tmp_path, [{"component": v.ct_id, "to": "/cbc:X", "transform": "copy"}]))[0]
             if p.value.position == v.position)
    assert p.steps and p.text is None


def test_dot_dot_goes_up_from_the_enclosing_element(tmp_path):
    rules = PARTY_RULES + [{"cluster": ct("Party Contact"), "to": "cac:Contact"},
                           {"component": ct("Email Address"), "to": "../cbc:WebsiteURI", "transform": "copy"}]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    where = {names(p.steps) for p in by_label(ps, "Email Address")}
    assert "cac:AccountingCustomerParty/cac:Party/cbc:WebsiteURI" in where


def test_a_plus_step_is_the_values_own_element(tmp_path):
    rules = PARTY_RULES + [
        {"component": ct("Contact Name"), "to": "+cac:Dimension/cbc:Measure", "transform": "copy"},
        {"component": ct("Contact Department"), "to": "+cac:Dimension/cbc:Measure", "transform": "copy"},
        {"component": ct("Phone Number"), "to": "cac:Shared/cbc:A", "transform": "copy"},
        {"component": ct("Email Address"), "to": "cac:Shared/cbc:B", "transform": "copy"},
    ]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    first = lambda label: next(p for p in by_label(ps, label) if p.steps)
    assert first("Contact Name").steps[-2] != first("Contact Department").steps[-2]
    assert first("Phone Number").steps[-2] == first("Email Address").steps[-2]


def test_also_writes_the_value_to_further_places_and_on_miss_omits(tmp_path):
    rules = PARTY_RULES[:3] + [
        {"component": ct("Party Identification Value"), "to": "cac:PartyIdentification/cbc:ID", "transform": "copy"},
        {"component": ct("Party Identification Scheme"), "to": "cac:PartyIdentification/cbc:ID/@schemeName",
         "transform": "copy", "also": [
             {"to": "cac:PartyIdentification/cbc:ID/@schemeID", "transform": "code", "table": {"nothing": "x"}, "on_miss": "omit"},
             {"to": "cac:PartyIdentification/cbc:ID/@schemeAgencyID", "transform": "constant", "value": "9"}]},
    ]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    p = next(p for p in by_label(ps, "Party Identification Scheme") if p.steps)
    assert [(e.attribute, e.text) for e in p.extras] == [("schemeID", None), ("schemeAgencyID", "9")]
    assert all(e.steps == p.steps for e in p.extras)


def test_binding_writes_the_code_the_model_binds_the_value_to(tmp_path):
    v = next(v for v in resolved().values if v.code and v.code.defined_by and "#" in v.code.defined_by)
    prefix = v.code.defined_by.rsplit("#", 1)[0]
    rules = [{"component": v.ct_id, "to": "/cbc:Code", "transform": "binding", "prefix": prefix}]
    p = next(p for p in place(resolved(), mapping(tmp_path, rules))[0] if p.value.position == v.position)
    assert p.text == v.code.defined_by.rsplit("#", 1)[1]
    with pytest.raises(MappingError, match="does not bind"):
        place(resolved(), mapping(tmp_path, [{**rules[0], "prefix": "urn:nothing"}]))


def test_an_equals_step_is_marked_for_the_writer_to_join(tmp_path):
    from sdc_mapper import JOIN
    rules = PARTY_RULES + [{"component": ct("Endpoint ID"), "to": "=cac:PartyName/cbc:Name2", "transform": "copy"}]
    ps, _ = place(resolved(), mapping(tmp_path, rules))
    p = next(p for p in by_label(ps, "Endpoint ID") if p.steps)
    assert p.steps[-2] == ("cac:PartyName", JOIN)
