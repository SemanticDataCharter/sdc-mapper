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
    prefix: str | None = None

    def applies_within(self, ancestors: list[str]) -> bool:
        """`within` names clusters that must enclose the target, outermost first; each must appear among the
        ancestors in that order (not necessarily adjacent)."""
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

    def rule_for(self, ct_id: str, sdc_type: str, ancestors: list[str] | None = None) -> Rule | None:
        """The rule for a value: its component's own rule wins over its type's default, and among several, the one
        with the longest `within` that applies. `ancestors` are the enclosing clusters' identifiers, root first."""
        ancestors = ancestors or []
        for pick in (lambda r: r.component == ct_id, lambda r: r.sdc_type == sdc_type):
            found = [r for r in self.rules if pick(r) and r.applies_within(ancestors)]
            if found:
                return max(found, key=lambda r: len(r.within))
        return None

    def cluster_rule(self, ct_id: str, ancestors: list[str]) -> Rule | None:
        found = [r for r in self.rules if r.cluster == ct_id and r.applies_within(ancestors)]
        return max(found, key=lambda r: len(r.within)) if found else None

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
    return Mapping(id=doc["mapping"]["id"], version=str(doc["mapping"]["version"]),
                   sha256=hashlib.sha256(data).hexdigest(), model_ct_id=doc["model"]["ct_id"],
                   model_schema_sha256=doc["model"]["schema_sha256"], target_name=doc["target"]["name"],
                   target_version=doc["target"]["version"], rules=rules,
                   exceptional_values=doc.get("exceptional_values", {}), carry_rejected=doc.get("carry_rejected", False),
                   profile_of=doc.get("profile_of"), raw=doc)
