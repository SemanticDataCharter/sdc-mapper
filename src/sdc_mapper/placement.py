"""Placements (4.1.0): where each value of a record goes in a target, by its mapping file.

A mapping file places a value with its component's (or its type's) rule, relative to the target element of the
nearest enclosing cluster that has a cluster rule, so one shared component (a Party Name) lands in different places
under different clusters (the supplier's party, the customer's). Every step of a target path is tagged with the
cluster instance that owns it, so each repeat of a cluster (each invoice line) becomes its own target element.

`place()` is format-neutral: it gives a mapper, for every value, the target steps, the attribute if the path ends in
one, the text to write (after the rule's transform), and the units' placement. Writing XML, segments or rows from the
placements is the mapper's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from .mapping import Mapping, MappingError, Rule
from .resolve import ResolvedRecord, ResolvedValue

#: A step of a target path: the element name, and the key of the instance that owns it. Steps with equal names and
#: keys are one element. A cluster rule's steps belong to the cluster instance; a value rule's steps to the enclosing
#: cluster instance and the value's occurrence, so a value and a rule beside it (an identifier and its scheme) share
#: their elements; a step written '+name' belongs to the value alone (one Dimension per measure).
Step = tuple[str, tuple]

#: The key of a step written '=name': the element of that name its parent already has (made by a nested cluster, a
#: line item's element for the line's own answer), or a new one when there is none. Which element that is depends on
#: everything placed under the parent, so the writer resolves it, after placing every other value; more than one
#: element of that name under the parent is the writer's to refuse.
JOIN = ("=",)


@dataclass
class Extra:
    """The value written to a further place by an `also` sub-rule."""
    steps: list[Step]
    attribute: str | None
    text: str | None


@dataclass
class Placement:
    value: ResolvedValue
    rule: Rule | None                  # the leaf rule; None when no rule applies
    steps: list[Step] = field(default_factory=list)
    attribute: str | None = None       # the value goes in this attribute of the last step's element
    text: str | None = None            # the value as the target writes it, after the transform
    attributes: dict[str, str] = field(default_factory=dict)   # constant attributes on the last step's element
    units_attribute: str | None = None
    units_text: str | None = None
    omitted_by: Rule | None = None     # the rule (the value's own, or an enclosing cluster's) that omits it
    extras: list[Extra] = field(default_factory=list)
    note: str = ""                     # why a placed value has no text (a code table that does not list it)

    @property
    def unmapped(self) -> bool:
        return self.rule is None and self.omitted_by is None

    @property
    def omitted(self) -> bool:
        return self.omitted_by is not None


@dataclass
class ClusterPlacement:
    """A mapped cluster instance: its target steps and the constant attributes on its element."""
    key: tuple
    steps: list[Step]
    attributes: dict[str, str]


def _split(path: str) -> tuple[bool, list[str], str | None]:
    absolute = path.startswith("/")
    parts = [p for p in path.strip("/").split("/") if p and p != "."]
    attr = None
    if parts and parts[-1].startswith("@"):
        attr = parts.pop()[1:]
    return absolute, parts, attr


def _steps(mapping: Mapping, base: list[Step], path: str, key: tuple, own: tuple) -> tuple[list[Step], str | None]:
    """A path's steps from the enclosing element's: '..' goes up one, '+name' is the value's own element, '=name'
    joins the element of that name the parent already has."""
    absolute, parts, attr = _split(path)
    steps = [] if absolute else list(base)
    for part in parts:
        if part == "..":
            if not steps:
                raise MappingError(f"mapping {mapping.id}: {path!r} goes above the document")
            steps.pop()
        elif part.startswith("+"):
            steps.append((part[1:], own))
        elif part.startswith("="):
            steps.append((part[1:], JOIN))
        else:
            steps.append((part, key))
    return steps, attr


def text_of(value: ResolvedValue) -> str | None:
    v = value.value
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, list):
        return " ".join("" if i is None else (format(i, "f") if isinstance(i, Decimal) else str(i)) for i in v)
    return str(v)


def _format(value: ResolvedValue, fmt: str) -> str | None:
    """`format` for the forms a target's syntax requires: a strftime pattern for a temporal value, a Python format
    specification for a number. Anything else is copied."""
    raw = text_of(value)
    if raw is None:
        return None
    if value.kind == "temporal":
        form = value.detail.get("form")
        parsers = {"date": date.fromisoformat, "time": time.fromisoformat, "datetime": datetime.fromisoformat}
        if form in parsers:
            return parsers[form](raw).strftime(fmt)
        return raw
    if value.kind in ("integer", "decimal"):
        return format(value.value, fmt)
    return raw


class _Miss(Exception):
    pass


def _code(table: dict, raw: str, on_miss: str, what: str, mapping: Mapping) -> str:
    if raw in table:
        return table[raw]
    if on_miss == "omit":
        raise _Miss(f"{raw!r} is not in the code table")
    raise MappingError(f"mapping {mapping.id}: {what} holds {raw!r}, which its code table does not list")


def _transform(rule, value: ResolvedValue, mapping: Mapping) -> str | None:
    """`rule` is a Rule or an `also` sub-rule (a dict)."""
    get = rule.get if isinstance(rule, dict) else lambda k, d=None: getattr(rule, k, d)
    transform = get("transform")
    if transform == "constant":
        return get("value")
    if value.exceptional:
        return None                                  # an Exceptional Value's value is never written
    if transform == "copy":
        return text_of(value)
    if transform == "format":
        return _format(value, get("format") or "")
    if transform == "binding":
        # the code the model binds the value to: the fragment of the IRI its enumeration value is defined by
        prefix = get("prefix") or ""
        bound = value.code.defined_by if value.code else None
        if bound and bound.startswith(prefix) and "#" in bound:
            return bound.rsplit("#", 1)[1]
        if (get("on_miss") or "fail") == "omit":
            raise _Miss(f"{value.label} is not bound under {prefix}")
        raise MappingError(f"mapping {mapping.id}: {value.label} ({value.ct_id}) holds {text_of(value)!r}, which the model "
                           f"does not bind to a code under {prefix}")
    if transform == "code":
        raw = text_of(value)
        if raw is None:
            return None
        return _code(get("table") or {}, raw, get("on_miss") or "fail", f"{value.label} ({value.ct_id})", mapping)
    return None


def place(record: ResolvedRecord, mapping: Mapping) -> tuple[list[Placement], list[ClusterPlacement]]:
    """Every value's placement, in document order, and every mapped cluster instance's."""
    clusters: dict[tuple, ClusterPlacement] = {}
    placements: list[Placement] = []
    for v in record.values:
        ids = [c.ct_id for c in v.path]
        # the enclosing clusters' rules, outermost first, each resolved in its own context
        base: list[Step] = []
        omitted_by = None
        for i, cid in enumerate(ids):
            rule = mapping.cluster_rule(cid, ids[:i])
            if rule is None:
                continue
            if rule.transform == "omit":
                omitted_by = rule
                break
            key = v.instance_key[: i + 1]
            base, attr = _steps(mapping, base, rule.to, key, key)
            if attr:
                raise MappingError(f"mapping {mapping.id}: cluster rule for {cid} ends in an attribute ({rule.to})")
            clusters.setdefault(key, ClusterPlacement(key=key, steps=list(base), attributes=dict(rule.attributes)))
        leaf = None if omitted_by else mapping.rule_for(v.ct_id, v.sdc_type, ids)
        p = Placement(value=v, rule=leaf, omitted_by=omitted_by)
        if leaf is not None and leaf.transform == "omit":
            p.omitted_by = leaf
        if leaf is not None and not p.omitted:
            key = v.instance_key + (("#", v.occurrence),)
            own = key + (("+", v.ct_id),)
            p.steps, p.attribute = _steps(mapping, base, leaf.to, key, own)
            p.attributes = dict(leaf.attributes)
            try:
                p.text = _transform(leaf, v, mapping)
            except _Miss as e:
                p.text, p.note = None, str(e)
            for sub in leaf.also:
                steps, attr = _steps(mapping, base, sub["to"], key, own)
                try:
                    text = _transform(sub, v, mapping)
                except _Miss:
                    text = None
                p.extras.append(Extra(steps, attr, text))
            if leaf.units and v.units and not v.exceptional and p.text is not None:
                p.units_attribute = leaf.units["to"][1:]
                symbol = v.units.symbol
                if leaf.units.get("transform") == "code":
                    try:
                        symbol = _code(leaf.units.get("table", {}), symbol, leaf.units.get("on_miss", "fail"),
                                       f"the units of {v.label} ({v.ct_id})", mapping)
                    except _Miss:
                        symbol = None
                p.units_text = symbol
        placements.append(p)
    return placements, list(clusters.values())
