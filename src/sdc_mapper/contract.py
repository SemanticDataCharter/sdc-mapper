"""The contract between the core and a target mapper.

A mapper receives a `ResolvedRecord` and an optional `Mapping`, and returns a `MapResult`: the output files and one
`ValueOutcome` for every value it was given. It never writes a rejected value as a value, and it accounts for every
value: the core checks that, and fails the call if any value is missing from the outcomes.

A mapper that can map chained records (a parent record and the records chained to it, written as one output) also
has `map_chain(parent, children) -> ChainMapResult`: `parent` and each child a (ResolvedRecord, Mapping) pair. It is
optional: a mapper without it maps one record at a time, and the core refuses a chain for it (`maps_chains`).

Mappers register under the entry-point group `sdc_mapper.targets`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Protocol, runtime_checkable

from .mapping import Mapping
from .resolve import ResolvedRecord

#: How a value that holds a value reached the target, in the order the classification tries them (section 11).
LOOKUP = "lookup"
DECLARED = "declared"
CARRIED = "carried"
NOT_REPRESENTABLE = "not_representable"
VALUE_CLASSES = (LOOKUP, DECLARED, CARRIED, NOT_REPRESENTABLE)

#: What the target did with an Exceptional Value (section 10).
PRESERVED = "preserved"
DEGRADED = "degraded"
EV_CARRIED = "carried"
LOST = "lost"
EV_CLASSES = (PRESERVED, DEGRADED, EV_CARRIED, LOST)

ENTRY_POINT_GROUP = "sdc_mapper.targets"


@dataclass
class ValueOutcome:
    """What happened to one value. `cls` is a value class, or an Exceptional Value class when the value carries one."""
    position: int
    cls: str
    location: str = ""            # where in the target it went: "MEASUREMENT.value_as_number", "OBX-5", ...
    target_code: str = ""         # for an Exceptional Value: the code the target wrote ("asked-declined"), if any
    merged_with: list[str] = field(default_factory=list)   # for degraded: the ISO 21090 codes the target code also covers
    note: str = ""


@dataclass
class EVRule:
    """One row of a mapper's Exceptional Value table: what the target writes for an ISO 21090 code."""
    cls: str                      # preserved, degraded, carried or lost
    target_code: str = ""
    merged_with: tuple[str, ...] = ()


@dataclass
class MapResult:
    files: dict[str, bytes]
    outcomes: list[ValueOutcome]
    cannot_hold: list[str] = field(default_factory=list)   # what the target structurally cannot hold for any record


@dataclass
class ChainMapResult:
    """What a mapper's `map_chain` returns: the files of the one output, and one MapResult per record, the parent's
    first and the children in the order given. Each MapResult accounts for its own record's values (the core builds
    each record's coverage report from it, as for one record); each usually holds the same files."""
    files: dict[str, bytes]
    results: list[MapResult]


@runtime_checkable
class Mapper(Protocol):
    name: str                     # "edifact", "omop", ...
    target: str                   # "UN/EDIFACT"
    target_version: str           # "D.24A"
    package: str                  # the distribution name, "sdc-gdr2edifact"
    version: str                  # the mapper's own version
    ev_table: dict[str, EVRule]   # one rule per ISO 21090 code

    def map(self, record: ResolvedRecord, mapping: Mapping | None) -> MapResult: ...


@runtime_checkable
class ChainMapper(Mapper, Protocol):
    """A mapper that also maps a parent record and its chained records into one output."""

    def map_chain(self, parent: tuple[ResolvedRecord, Mapping | None],
                  children: list[tuple[ResolvedRecord, Mapping | None]]) -> ChainMapResult: ...


def maps_chains(mapper) -> bool:
    """Whether a mapper implements the optional `map_chain`."""
    return callable(getattr(mapper, "map_chain", None))


def installed_mappers() -> dict[str, type]:
    """The mappers installed in this environment, by name."""
    found = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        cls = ep.load()
        found[getattr(cls, "name", ep.name)] = cls
    return found
