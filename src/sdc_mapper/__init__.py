"""sdc-mapper: the shared core of the Semantic Data Charter's Governed Data Record mappers.

A record is checked by the validation gate (valid, valid with Exceptional Values, or refused), read and joined to its
model by sdcreader, resolved into one shape every mapper receives, mapped by a target mapper, and reported value by
value: how each value reached the target, or that the target cannot hold it, and for every Exceptional Value whether
the target could say it too. Every call is appended to a hash-chained log with no values in it.

Outbound only. Each target format is its own package (`sdc-gdr2<target>`) registered under the entry-point group
`sdc_mapper.targets`.
"""
from .contract import (CARRIED, DECLARED, DEGRADED, ENTRY_POINT_GROUP, EV_CARRIED, EV_CLASSES, LOOKUP, LOST,
                       NOT_REPRESENTABLE, PRESERVED, VALUE_CLASSES, EVRule, MapResult, Mapper, ValueOutcome,
                       installed_mappers)
from .engine import ERROR, CallResult, Engine
from .gate import REFUSED, VALID, VALID_WITH_EV, Gate, GateResult, Recovery
from .log import Log, entry_hash
from .mapping import Mapping, MappingError, Rule, load_mapping
from .codes import CONCEPT_LINKS, EXACT_MATCH, LOINC, NCIT, SNOMED_CT, SUBJECT, CodeRef, bound_code, code_of, linked_code, model_codes
from .placement import JOIN, LOOKUP_TRANSFORMS, ClusterPlacement, Extra, Placement, join_depth, place, text_of
from .report import CoverageReport, IncompleteMapping, build_report
from .resolve import Exceptional, ResolvedRecord, ResolvedValue, Units, resolve

__version__ = "4.3.0"
__all__ = [
    "Engine", "CallResult", "Gate", "GateResult", "Recovery", "Log", "entry_hash", "Mapping", "MappingError", "Rule",
    "load_mapping", "place", "Placement", "ClusterPlacement", "Extra", "JOIN", "join_depth", "LOOKUP_TRANSFORMS", "CodeRef", "code_of",
    "bound_code", "linked_code", "model_codes", "CONCEPT_LINKS", "EXACT_MATCH", "SUBJECT", "LOINC", "SNOMED_CT", "NCIT", "text_of", "CoverageReport", "IncompleteMapping", "build_report", "ResolvedRecord", "ResolvedValue",
    "Exceptional", "Units", "resolve", "Mapper", "MapResult", "ValueOutcome", "EVRule", "installed_mappers",
    "VALID", "VALID_WITH_EV", "REFUSED", "ERROR", "LOOKUP", "DECLARED", "CARRIED", "NOT_REPRESENTABLE", "VALUE_CLASSES",
    "PRESERVED", "DEGRADED", "EV_CARRIED", "LOST", "EV_CLASSES", "ENTRY_POINT_GROUP", "__version__",
]
