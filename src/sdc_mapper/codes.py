"""Codes the model names (4.3.0): the IRIs a model uses for a concept, read as a code system and a code.

A model names concepts in two places. An enumerated value is defined by a code (`rdfs:isDefinedBy`: a LOINC answer, a
SNOMED CT concept, an entry in a UBL code list). A component links to the concept it measures or asks
(`skos:exactMatch` to a LOINC code, `dcterms:subject` to a SNOMED CT concept). Wherever such an IRI names one concept
of a code system, the code is the model's: a target that writes it is looking it up, not choosing it.

`code_of()` reads the IRI forms the component libraries use. A code system is named by the IRI FHIR uses for it, the
one most targets recognize; each mapper turns that into its own name for the system (OMOP's `vocabulary_id`, HL7 v2's
coding system table) in its mapping file or its code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LOINC = "http://loinc.org"
SNOMED_CT = "http://snomed.info/sct"
NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"

EXACT_MATCH = "http://www.w3.org/2004/02/skos/core#exactMatch"
SUBJECT = "http://purl.org/dc/terms/subject"
#: The links `link` reads by default, in order: what the component is, then what it is about.
CONCEPT_LINKS = (EXACT_MATCH, SUBJECT)

_FORMS = [
    # LOINC codes, answers (LA), parts (LP) and groups (LG), with or without the /rdf/ path and a trailing slash
    (re.compile(r"^https?://loinc\.org/(?:rdf/)?((?:LA|LP|LG)?[0-9]+-[0-9])/?$"), LOINC),
    (re.compile(r"^https?://snomed\.info/(?:id|sct)/([0-9]+)/?$"), SNOMED_CT),
    (re.compile(r"^https?://purl\.obolibrary\.org/obo/NCIT_(C[0-9]+)$"), NCIT),
]
#: An IRI whose fragment is the code: a code list or code system and one entry in it (UBL's code lists, HL7's).
_FRAGMENT = re.compile(r"^(https?://[^#\s]+)#([^#\s]+)$")


@dataclass(frozen=True)
class CodeRef:
    system: str     # the code system, by the IRI FHIR uses for it, or the IRI before the fragment
    code: str


def code_of(iri: str | None, code_lists: bool = True) -> CodeRef | None:
    """The code an IRI names, or None (an IRI that names a whole code system, or a page about a variable).

    The code systems' own forms (LOINC, SNOMED CT, NCI Thesaurus) always read as codes. An IRI ending in `#code` reads
    as an entry in a code list only with `code_lists`: that is how a model binds an enumerated value to a list (UBL's,
    HL7's), but the same form also names a vocabulary term (`prov#Entity`) or an anchor on a page about a variable
    (`DEMO_J.htm#SEQN`), which are not codes. 4.3.1: concept links read without it.
    """
    if not iri:
        return None
    iri = iri.strip()
    for form, system in _FORMS:
        m = form.match(iri)
        if m:
            return CodeRef(system, m.group(1))
    if code_lists:
        m = _FRAGMENT.match(iri)
        if m:
            return CodeRef(m.group(1), m.group(2))
    return None


def _in(ref: CodeRef | None, system: str | None) -> bool:
    return ref is not None and (system is None or ref.system == system.rstrip("/") or ref.system.startswith(system))


def bound_code(value, system: str | None = None) -> CodeRef | None:
    """The code the model defines an enumerated value by, if it is one (in `system`, when given): a code system's
    concept, or an entry in a code list."""
    ref = code_of(value.code.defined_by) if getattr(value, "code", None) else None
    return ref if _in(ref, system) else None


def linked_code(value, system: str | None = None, predicates=CONCEPT_LINKS) -> CodeRef | None:
    """The first code the component links to (in `system`, when given), by the predicates in order: a code system's
    concept only, never a `#fragment` (a link names a concept; a fragment in a link is a term or a page anchor)."""
    links = getattr(value, "links", None) or {}
    for predicate in predicates:
        for iri in links.get(predicate, []):
            ref = code_of(iri, code_lists=False)
            if _in(ref, system):
                return ref
    return None


def model_codes(value) -> set[str]:
    """Every code the model names for a value: its enumeration value's definition and the component's concept links.
    A target code that is one of these came from the model."""
    refs = [bound_code(value)] + [code_of(i, code_lists=False)
                                  for p in CONCEPT_LINKS for i in (getattr(value, "links", None) or {}).get(p, [])]
    return {r.code for r in refs if r}
