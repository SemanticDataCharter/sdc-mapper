"""Codes the model names (4.3.0): the IRI forms, `binding` with a code system, `link`, and the join order."""
from __future__ import annotations

import dataclasses

import pytest

from sdc_mapper import (JOIN, LOINC, LOOKUP_TRANSFORMS, NCIT, SNOMED_CT, CodeRef, MappingError, code_of, join_depth,
                        model_codes, place)
from test_placement import mapping, resolved

UBL_LIST = "http://docs.oasis-open.org/ubl/os-UBL-2.3/cl/gc/default/PaymentMeansCode-2.3.gc"


@pytest.mark.parametrize("iri,expected", [
    ("https://loinc.org/8480-6/", CodeRef(LOINC, "8480-6")),
    ("http://loinc.org/rdf/8480-6", CodeRef(LOINC, "8480-6")),
    ("https://loinc.org/LA32-8/", CodeRef(LOINC, "LA32-8")),
    ("http://snomed.info/id/38341003", CodeRef(SNOMED_CT, "38341003")),
    ("http://snomed.info/sct/38341003", CodeRef(SNOMED_CT, "38341003")),
    ("http://purl.obolibrary.org/obo/NCIT_C17459", CodeRef(NCIT, "C17459")),
    (f"{UBL_LIST}#2", CodeRef(UBL_LIST, "2")),
    ("http://terminology.hl7.org/CodeSystem/v3-NullFlavor#ASKU",
     CodeRef("http://terminology.hl7.org/CodeSystem/v3-NullFlavor", "ASKU")),
])
def test_the_iri_forms_the_libraries_use(iri, expected):
    assert code_of(iri) == expected


@pytest.mark.parametrize("iri", ["http://loinc.org", "https://www.gs1.org/standards/id-keys", "", None,
                                 "https://wwwn.cdc.gov/Nchs/Nhanes/2017-2018/DEMO_J.htm"])
def test_a_whole_system_or_a_page_names_no_code(iri):
    assert code_of(iri) is None


def _value_with(rs, **changes):
    v = next(v for v in rs.values if v.kind == "string" and v.value)
    return dataclasses.replace(v, **changes)


def test_link_writes_the_code_of_the_concept_the_component_links_to(tmp_path):
    rs = resolved()
    links = {"http://www.w3.org/2004/02/skos/core#exactMatch": ["https://loinc.org/8480-6/"],
             "http://purl.org/dc/terms/subject": ["http://snomed.info/id/38341003"]}
    v = _value_with(rs, links=links)
    rs.values[v.position] = v
    m = mapping(tmp_path, [{"component": v.ct_id, "to": "/x:Value", "transform": "copy",
                            "also": [{"to": "/x:Code", "transform": "link", "system": LOINC},
                                     {"to": "/x:Subject", "transform": "link", "system": SNOMED_CT}]}])
    p = next(p for p in place(rs, m)[0] if p.value.position == v.position)
    assert [(e.steps[-1][0], e.text) for e in p.extras] == [("x:Code", "8480-6"), ("x:Subject", "38341003")]
    assert model_codes(v) == {"8480-6", "38341003"}


def test_link_is_written_beside_an_exceptional_value_and_a_miss_fails(tmp_path):
    from sdc_mapper.resolve import Exceptional
    rs = resolved()
    v = _value_with(rs, links={"http://www.w3.org/2004/02/skos/core#exactMatch": ["https://loinc.org/8480-6/"]},
                    exceptional=Exceptional(code="ASKR", name="asked and refused", origin="source"))
    rs.values[v.position] = v
    rule = {"component": v.ct_id, "to": "/x:Code", "transform": "link"}
    p = next(p for p in place(rs, mapping(tmp_path, [rule]))[0] if p.value.position == v.position)
    assert p.text == "8480-6", "the concept is known even when the value is not"
    with pytest.raises(MappingError, match="links to no code in http://snomed"):
        place(rs, mapping(tmp_path, [{**rule, "system": SNOMED_CT}]))


def test_binding_takes_a_code_system_and_reads_every_form(tmp_path):
    rs = resolved()
    bound = next(v for v in rs.values if v.code and v.code.defined_by and "#" in v.code.defined_by)
    system = bound.code.defined_by.rsplit("#", 1)[0]
    rule = {"component": bound.ct_id, "to": "/x:Code", "transform": "binding", "system": system}
    p = next(p for p in place(rs, mapping(tmp_path, [rule]))[0] if p.value.position == bound.position)
    assert p.text == bound.code.defined_by.rsplit("#", 1)[1]
    loinc_answer = dataclasses.replace(bound, code=dataclasses.replace(bound.code, defined_by="https://loinc.org/LA33-6/"))
    rs.values[bound.position] = loinc_answer
    p = next(p for p in place(rs, mapping(tmp_path, [{**rule, "system": LOINC}]))[0] if p.value.position == bound.position)
    assert p.text == "LA33-6"


def test_join_depth_orders_deferred_writes():
    a, b, c = ("x:A", ()), ("x:B", JOIN), ("x:C", JOIN)
    assert join_depth([a]) == -1 and join_depth([a, b]) == 1 and join_depth([b, a, c]) == 2
    assert LOOKUP_TRANSFORMS == {"binding", "link"}


@pytest.mark.parametrize("iri", ["https://wwwn.cdc.gov/Nchs/Nhanes/2017-2018/DEMO_J.htm#SEQN",
                                 "http://www.w3.org/ns/prov#Entity", "http://www.w3.org/2000/01/rdf-schema#label"])
def test_a_term_or_a_page_anchor_is_no_concept_code(iri, tmp_path):
    """4.3.1: a `#fragment` in a concept link is a vocabulary term or an anchor on a page, not a code."""
    assert code_of(iri, code_lists=False) is None
    rs = resolved()
    v = _value_with(rs, links={"http://www.w3.org/2004/02/skos/core#exactMatch": [iri, "https://loinc.org/8480-6/"]})
    rs.values[v.position] = v
    assert model_codes(v) == {"8480-6"}, "the link to LOINC is a code; the fragment is not"
    p = next(p for p in place(rs, mapping(tmp_path, [{"component": v.ct_id, "to": "/x:Code", "transform": "link"}]))[0]
             if p.value.position == v.position)
    assert p.text == "8480-6"
    only = _value_with(rs, links={"http://www.w3.org/2004/02/skos/core#exactMatch": [iri]})
    rs.values[only.position] = only
    with pytest.raises(MappingError, match="links to no code"):
        place(rs, mapping(tmp_path, [{"component": only.ct_id, "to": "/x:Code", "transform": "link"}]))


def test_a_code_list_entry_still_binds_an_enumerated_value():
    assert code_of(f"{UBL_LIST}#2") == CodeRef(UBL_LIST, "2")
    assert code_of(f"{UBL_LIST}#2", code_lists=False) is None


def test_a_compound_concept_gives_no_linked_code():
    """4.3.2: several subjects are the parts of a compound concept; no part alone is the component."""
    from sdc_mapper import linked_code, NCIT
    rs = resolved()
    subject = "http://purl.org/dc/terms/subject"
    parts = ["http://purl.obolibrary.org/obo/NCIT_C25190", "http://purl.obolibrary.org/obo/NCIT_C25471", "http://purl.obolibrary.org/obo/NCIT_C25742"]
    compound = _value_with(rs, links={subject: parts})
    assert linked_code(compound) is None and linked_code(compound, NCIT) is None
    one = _value_with(rs, links={subject: ["http://snomed.info/id/73211009"]})
    assert linked_code(one).code == "73211009", "a single subject is the component's concept"
    exact = _value_with(rs, links={"http://www.w3.org/2004/02/skos/core#exactMatch": ["https://loinc.org/8480-6/"], subject: parts})
    assert linked_code(exact).code == "8480-6", "an exact match wins over a compound's parts"
    assert model_codes(compound) == {"C25190", "C25471", "C25742"}, "the parts are still codes the model names"
