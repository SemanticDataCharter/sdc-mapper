"""The coverage report: every value, every time, and what the target could not say."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field

from .contract import (EV_CLASSES, LOST, VALUE_CLASSES, MapResult, ValueOutcome)
from .resolve import ResolvedRecord


class IncompleteMapping(Exception):
    """A mapper returned no outcome, or more than one, for some value: the call fails rather than drop it."""


@dataclass
class CoverageReport:
    record: dict                  # instance_id, instance_version, gate outcome
    model: dict                   # ct_id, title, schema_sha256
    mapping: dict | None          # id, version, sha256
    mapper: dict                  # package, version, name
    target: dict                  # name, version
    counts: dict[str, int]        # per value class, and per Exceptional Value class under "exceptional"
    values: list[dict]            # one per value: position, ct_id, path, class, location, ...
    exceptional: list[dict]       # one per Exceptional Value: position, ct_id, code, origin, constraint, class, target_code
    could_not_say: str            # one plain sentence for an agent to pass on
    cannot_hold: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False, default=str)

    def summary(self) -> str:
        c = self.counts
        total = sum(c.get(k, 0) for k in VALUE_CLASSES) + sum(c.get("exceptional", {}).values())
        lines = [f"{self.target['name']} {self.target['version']} from {self.model['title']} "
                 f"({self.model['ct_id']}), record {self.record['instance_id']}: {total} values"]
        lines += [f"  {k.replace('_', ' ')}: {c.get(k, 0)}" for k in VALUE_CLASSES if c.get(k)]
        ev = c.get("exceptional", {})
        if any(ev.values()):
            lines.append("  Exceptional Values: " + ", ".join(f"{n} {k}" for k, n in ev.items() if n))
        lines.append(self.could_not_say)
        return "\n".join(lines)


def build_report(resolved: ResolvedRecord, result: MapResult, mapper, mapping) -> CoverageReport:
    by_pos: dict[int, list[ValueOutcome]] = {}
    for o in result.outcomes:
        by_pos.setdefault(o.position, []).append(o)
    missing = [v for v in resolved.values if len(by_pos.get(v.position, [])) != 1]
    extra = set(by_pos) - {v.position for v in resolved.values}
    if missing or extra:
        named = [f"{v.label} ({v.ct_id}, position {v.position})" for v in missing[:5]]
        raise IncompleteMapping(f"{mapper.package} accounted for {len(resolved.values) - len(missing)} of "
                                f"{len(resolved.values)} values" + (f"; missing or doubled: {', '.join(named)}" if named else "")
                                + (f"; unknown positions {sorted(extra)[:5]}" if extra else ""))

    values, exceptional = [], []
    counts: Counter = Counter()
    ev_counts: Counter = Counter()
    for v in resolved.values:
        o = by_pos[v.position][0]
        row = {"position": v.position, "ct_id": v.ct_id, "label": v.label, "path": v.path_labels, "class": o.cls,
               "location": o.location}
        if o.note:              # why: the mapping's reason, or what the target requires; never a value
            row["note"] = o.note
        if v.exceptional:
            if o.cls not in EV_CLASSES:
                raise IncompleteMapping(f"{mapper.package} gave {v.label} ({v.ct_id}) the value class {o.cls!r}, "
                                        f"but it carries the Exceptional Value {v.exceptional.code}")
            ev_counts[o.cls] += 1
            exceptional.append({"position": v.position, "ct_id": v.ct_id, "label": v.label, "code": v.exceptional.code,
                                "name": v.exceptional.name, "origin": v.exceptional.origin,
                                "constraint": v.exceptional.constraint, "class": o.cls, "target_code": o.target_code,
                                "merged_with": o.merged_with})
        else:
            if o.cls not in VALUE_CLASSES:
                raise IncompleteMapping(f"{mapper.package} gave {v.label} ({v.ct_id}) the class {o.cls!r}")
            counts[o.cls] += 1
        values.append(row)

    all_counts = {k: counts.get(k, 0) for k in VALUE_CLASSES}
    all_counts["exceptional"] = {k: ev_counts.get(k, 0) for k in EV_CLASSES}
    return CoverageReport(
        record={"instance_id": resolved.instance_id, "instance_version": resolved.attributes.get("instance_version", ""),
                "gate": resolved.outcome},
        model={"ct_id": resolved.ct_id, "title": resolved.title, "schema_sha256": resolved.schema_sha256},
        mapping={"id": mapping.id, "version": mapping.version, "sha256": mapping.sha256} if mapping else None,
        mapper={"package": mapper.package, "version": mapper.version, "name": mapper.name},
        target={"name": mapper.target, "version": mapper.target_version},
        counts=all_counts, values=values, exceptional=exceptional,
        could_not_say=_could_not_say(mapper.target, all_counts, exceptional),
        cannot_hold=list(result.cannot_hold),
    )


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _could_not_say(target: str, counts: dict, exceptional: list[dict]) -> str:
    """One sentence an agent can pass on as it is."""
    parts = []
    nr = counts.get("not_representable", 0)
    if nr:
        parts.append(f"{_plural(nr, 'value')} had no place in {target}")
    if exceptional:
        lost = [e for e in exceptional if e["class"] == LOST]
        kept = [e for e in exceptional if e["class"] != LOST]
        degraded = [e for e in exceptional if e["class"] == "degraded"]
        carried = [e for e in exceptional if e["class"] == "carried"]
        if lost:
            names = Counter(e["name"] for e in lost)
            parts.append(f"{_plural(len(lost), 'Exceptional Value')} became empty values ("
                         + ", ".join(f"{n} {name}" for name, n in names.most_common()) + ")")
        if degraded:
            parts.append(f"{_plural(len(degraded), 'Exceptional Value')} kept only a coarser code")
        if carried:
            parts.append(f"{_plural(len(carried), 'Exceptional Value')} survived only as text")
        if not lost and not degraded and not carried and kept:
            parts.append(f"every Exceptional Value kept its code")
    if not parts:
        return f"{target} holds everything this record says."
    return f"{target}: " + "; ".join(parts) + "."
