"""Mapping files: YAML, validated against `schemas/mapping.schema.json`, bound to one model schema
version and one target version, and cited by their SHA-256."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from sdcreader.model import RM_ELEMENTS, RM_PARTS

SCHEMA_PATH = Path(__file__).with_name("schemas") / "mapping.schema.json"


class MappingError(Exception):
    pass


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@dataclass
class Rule:
    to: str
    transform: str = "group"
    component: str | None = None
    sdc_type: str | None = None
    cluster: str | None = None
    within: list[str] = field(default_factory=list)
    table: dict[str, str] = field(default_factory=dict)
    format: str | None = None
    value: str | None = None
    reason: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    units: dict | None = None
    also: list[dict] = field(default_factory=list)
    on_miss: str = "fail"
    prefix: str | None = None      # binding: the code system (the 4.1.0 name; `system` since 4.3.0)
    system: str | None = None      # binding, link: the code system the code must be in
    predicates: list[str] = field(default_factory=list)   # link: the links to read, in order
    order: int | None = None
    element: str | None = None     # a reference-model element (4.4.0): "start", "party-name", "committed", ...

    def applies_within(self, ancestors: list[str]) -> bool:
        """`within` names clusters that must enclose the target, outermost first; each must appear among the
        ancestors in that order (not necessarily adjacent). Since 4.4.0 an ancestor is a cluster's identifier or the
        reference-model element it stands in ("subject", "performer", "party-details"); see `ancestor_tokens`."""
        i = 0
        for ct in ancestors:
            if i < len(self.within) and ct == self.within[i]:
                i += 1
        return i == len(self.within)


@dataclass
class Mapping:
    id: str
    version: str
    sha256: str
    model_ct_id: str
    model_schema_sha256: str
    target_name: str
    target_version: str
    rules: list[Rule]
    exceptional_values: dict[str, dict] = field(default_factory=dict)
    carry_rejected: bool = False
    profile_of: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    reference_model: bool = False  # read the record's reference-model parts beyond the data cluster (4.4.0)

    def rule_for(self, ct_id: str, sdc_type: str, ancestors: list[str] | None = None, role: str = "") -> Rule | None:
        """The rule for a value: its component's own rule wins, then the rule for the reference-model element it
        stands in (`role`, 4.4.0), then its type's default; among several, the one with the longest `within` that
        applies. `ancestors` are the enclosing clusters' identifiers, root first (`ancestor_tokens`)."""
        ancestors = ancestors or []
        for pick in (lambda r: r.component == ct_id, lambda r: bool(role) and r.element == role,
                     lambda r: r.sdc_type == sdc_type):
            found = [r for r in self.rules if pick(r) and r.applies_within(ancestors)]
            if found:
                return max(found, key=lambda r: len(r.within))
        return None

    def cluster_rule(self, ct_id: str, ancestors: list[str], role: str = "") -> Rule | None:
        """The rule for a cluster instance: one naming its identifier wins over one naming the reference-model element
        it stands in (`role`: "subject", "performer", 4.4.0); among several, the longest `within` that applies."""
        for pick in (lambda r: r.cluster == ct_id, lambda r: bool(role) and r.cluster == role):
            found = [r for r in self.rules if pick(r) and r.applies_within(ancestors)]
            if found:
                return max(found, key=lambda r: len(r.within))
        return None

    @property
    def names_reference_model(self) -> list[str]:
        """The reference-model names the rules use (an `element`, or a `cluster` or `within` entry that is an element
        name rather than an identifier)."""
        names = []
        for r in self.rules:
            for n in [r.element, r.cluster, *r.within]:
                if n and n in RM_NAMES and n not in names:
                    names.append(n)
        return names

    @property
    def cluster_order(self) -> dict[str, int]:
        """The declared order of clusters, by identifier: for a writer to rank same-named elements made under
        sibling clusters (lines held in wrapper clusters) where the record's own order is not the target's."""
        return {r.cluster: r.order for r in self.rules if r.cluster and r.order is not None}

    def check(self, ct_id: str, schema_sha256: str, target_name: str, target_version: str) -> None:
        """Refuse a mapping for another model, another schema version or another target."""
        if self.model_ct_id != ct_id:
            raise MappingError(f"mapping {self.id} is for model {self.model_ct_id}, not {ct_id}")
        if self.model_schema_sha256 != schema_sha256:
            raise MappingError(f"mapping {self.id} is for schema {self.model_schema_sha256[:12]}, not {schema_sha256[:12]}: "
                               "a model's new schema version needs a new mapping version")
        if (self.target_name, self.target_version) != (target_name, target_version):
            raise MappingError(f"mapping {self.id} is for {self.target_name} {self.target_version}, not {target_name} {target_version}")


def load_mapping(source: str | Path | bytes) -> Mapping:
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        doc = yaml.safe_load(data)
    except yaml.YAMLError as e:
        raise MappingError(f"the mapping file is not YAML: {e}") from e
    if not isinstance(doc, dict):
        raise MappingError("the mapping file is not a YAML mapping")
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        raise MappingError("the mapping file does not validate: " + "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors))
    rules = [Rule(**r) for r in doc.get("rules", [])]
    m = Mapping(id=doc["mapping"]["id"], version=str(doc["mapping"]["version"]),
                sha256=hashlib.sha256(data).hexdigest(), model_ct_id=doc["model"]["ct_id"],
                model_schema_sha256=doc["model"]["schema_sha256"], target_name=doc["target"]["name"],
                target_version=doc["target"]["version"], rules=rules,
                exceptional_values=doc.get("exceptional_values", {}), carry_rejected=doc.get("carry_rejected", False),
                profile_of=doc.get("profile_of"), raw=doc, reference_model=doc.get("reference_model", False))
    if m.names_reference_model and not m.reference_model:
        # such a rule could never apply: the record's reference-model parts are read only when the mapping asks
        raise MappingError(f"mapping {m.id}: rules name reference-model elements ({', '.join(m.names_reference_model)}) "
                           "but the mapping does not set `reference_model: true`")
    return m


#: The reference-model element names a rule may use (sdcreader 4.5.0): parts that hold further elements, for `cluster`
#: and `within`, and elements that hold a value, for `element`.
RM_NAMES = RM_PARTS | RM_ELEMENTS


def ancestor_tokens(path, roles) -> list[list[str]]:
    """Per step of a value's path, the names a rule's `within` or `cluster` matches it by: the reference-model element
    the step stands in, if any, then its identifier. A step whose identifier is the element's own name (a part the
    schema types by no component) is named once."""
    roles = tuple(roles) or ("",) * len(path)
    return [[n for n in (role, c.ct_id) if n] if role != c.ct_id else [c.ct_id] for c, role in zip(path, roles)]
