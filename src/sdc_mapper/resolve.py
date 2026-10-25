"""The resolved record: every value joined to everything the record and its model say about it.

`resolve()` takes what sdcreader read and the gate's result, and gives every mapper one shape: the value typed by its
SDC4 type, its units with the definition the model gives the symbol, the enumerated code the model defines for it, its
semantic links, its context, and, where one applies, its Exceptional Value with the constraint that failed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sdcreader import Code, Component, Model, ModelPackage, Record
from sdcreader.record import EXCEPTIONAL_VALUES

from .gate import GateResult


@dataclass
class Exceptional:
    code: str                    # ISO 21090: "ASKR"
    name: str                    # "Asked and Refused"
    origin: str                  # "source" or "recovery"
    constraint: str | None = None   # for recovery: what failed


@dataclass
class Units:
    symbol: str                  # as the record holds it: "USD"
    label: str                   # the Units component's label in the record: "Currency"
    ct_id: str | None = None     # the Units component the model names
    code: Code | None = None     # the model's definition of the symbol (IRI, definition, defined-by), when it gives one


@dataclass
class ResolvedValue:
    position: int
    component: Component
    path: list[Component]
    occurrence: int
    kind: str                    # string, token, boolean, integer, decimal, temporal, ordinal, link, file, interval, list
    value: Any                   # typed; None when an Exceptional Value applies or the type keeps no single value
    detail: dict                 # what else the type carries: a temporal value's form, a string's language, an
                                 # ordinal's symbol, a link's relation, a list's items, a file's fields
    units: Units | None
    code: Code | None            # for an enumerated value: the model's definition of it
    links: dict[str, list[str]]
    context: dict[str, str]
    exceptional: Exceptional | None = None
    rejected: dict = field(default_factory=dict)   # recovery only: the value as the record held it; never a value
    instances: tuple[int, ...] = ()                # parallel to `path`: each cluster's instance (sdcreader 4.4.0)

    @property
    def instance_key(self) -> tuple[tuple[str, int], ...]:
        return tuple(zip((c.ct_id for c in self.path), self.instances))

    @property
    def ct_id(self) -> str:
        return self.component.ct_id

    @property
    def sdc_type(self) -> str:
        return self.component.sdc_type

    @property
    def label(self) -> str:
        return self.component.label

    @property
    def path_labels(self) -> list[str]:
        return [c.label for c in self.path]


@dataclass
class ResolvedRecord:
    ct_id: str
    title: str
    schema_sha256: str
    schema_url: str              # pinned to the SHA-256
    dublin_core: dict[str, Any]
    attributes: dict[str, str]
    outcome: str                 # the gate's: valid or valid_with_ev
    values: list[ResolvedValue]

    @property
    def instance_id(self) -> str:
        return self.attributes.get("instance_id", "")

    @property
    def exceptional(self) -> list[ResolvedValue]:
        return [v for v in self.values if v.exceptional]


DC_FIELDS = ("title", "creator", "subject", "description", "publisher", "contributor", "date", "type", "format",
             "identifier", "source", "language", "relation", "coverage", "rights")


def resolve(record: Record, model: Model, pkg: ModelPackage, gate: GateResult) -> ResolvedRecord:
    """The gate knows which Exceptional Values it inserted, so it, not the record's shape, decides the origin. A
    published model often requires the value element even where the reference model leaves it optional, so an
    Exceptional Value written at the source can sit beside a value; that value is not data and is not offered."""
    constraints = {r.position: r.constraint for r in gate.recoveries}
    values = [_resolve(v, model, constraints) for v in record.values]
    return ResolvedRecord(
        ct_id=model.ct_id, title=model.title, schema_sha256=pkg.sha256, schema_url=pkg.schema_url_pinned,
        dublin_core={k: v for k in DC_FIELDS if (v := (model.contributors if k == "contributor" else model.dc(k)))},
        attributes=dict(record.attributes), outcome=gate.outcome, values=values,
    )


def _resolve(v, model: Model, constraints: dict[int, str]) -> ResolvedValue:
    comp = v.component
    el = v.elements
    exceptional = None
    if v.exceptional:
        recovered = v.position in constraints
        exceptional = Exceptional(code=v.exceptional.code, name=EXCEPTIONAL_VALUES[v.exceptional.code],
                                  origin="recovery" if recovered else "source",
                                  constraint=constraints.get(v.position) if recovered else None)
    kind, value, detail = _typed(comp.sdc_type, el)
    if exceptional:
        value = None
    units = _units(comp, el, model)
    code = None
    if value is not None and kind in ("string", "token"):
        code = next((c for c in model.codes.get(comp.ct_id, []) if c.value == value), None)
    elif kind == "ordinal" and value is not None:
        code = next((c for c in model.codes.get(comp.ct_id, []) if c.value == detail.get("symbol")), None)
    return ResolvedValue(position=v.position, component=comp, path=list(v.path), occurrence=v.occurrence, kind=kind,
                         value=value, detail=detail, units=units, code=code, links=dict(comp.links),
                         context=dict(v.context), exceptional=exceptional,
                         rejected=dict(v.elements) if exceptional and exceptional.origin == "recovery" else {},
                         instances=tuple(getattr(v, "instances", ())) or tuple(0 for _ in v.path))


def _text(x) -> str | None:
    if x is None:
        return None
    if isinstance(x, dict):
        return x.get("xdstring-value")
    return x


def _decimal(s: str | None) -> Decimal | None:
    try:
        return Decimal(s) if s not in (None, "") else None
    except InvalidOperation:
        return None


def _integer(s: str | None) -> int | None:
    try:
        return int(s) if s not in (None, "") else None
    except ValueError:
        return None


def _typed(sdc_type: str, el: dict) -> tuple[str, Any, dict]:
    if sdc_type in ("XdString", "XdToken"):
        prefix = sdc_type.lower()
        lang = el.get(f"{prefix}-language")
        return ("string" if sdc_type == "XdString" else "token"), el.get(f"{prefix}-value"), ({"language": lang} if lang else {})
    if sdc_type == "XdBoolean":
        if "true-value" in el:
            return "boolean", True, {"text": el["true-value"]}
        if "false-value" in el:
            return "boolean", False, {"text": el["false-value"]}
        return "boolean", None, {}
    if sdc_type == "XdCount":
        return "integer", _integer(el.get("xdcount-value")), _quantified(el)
    if sdc_type in ("XdQuantity", "XdFloat", "XdDouble"):
        return "decimal", _decimal(el.get(f"{sdc_type.lower()}-value")), _quantified(el)
    if sdc_type == "XdTemporal":
        for k, val in el.items():
            if k.startswith("xdtemporal-"):
                return "temporal", val, {"form": k[len("xdtemporal-"):]}
        return "temporal", None, {}
    if sdc_type == "XdOrdinal":
        return "ordinal", _decimal(el.get("ordinal")), {"symbol": el.get("symbol")}
    if sdc_type == "XdLink":
        return "link", el.get("link"), {k: el[k] for k in ("relation", "relation-uri") if k in el}
    if sdc_type == "XdFile":
        return "file", el.get("uri"), {k: val for k, val in el.items() if k != "uri"}
    if sdc_type == "XdInterval":
        return "interval", None, dict(el)
    if sdc_type in ("XdStringList", "XdTokenList", "XdDecimalList", "XdIntegerList"):
        raw = el.get(f"{sdc_type.lower()}-value")
        items = raw.split() if raw else []
        if sdc_type == "XdDecimalList":
            items = [_decimal(i) for i in items]
        elif sdc_type == "XdIntegerList":
            items = [_integer(i) for i in items]
        return "list", items if raw is not None else None, {}
    return "other", None, dict(el)


def _quantified(el: dict) -> dict:
    return {k: el[k] for k in ("magnitude-status", "accuracy_margin", "precision_digits") if k in el}


def _units(comp: Component, el: dict, model: Model) -> Units | None:
    raw = next((val for k, val in el.items() if k.endswith("-units")), None)
    if raw is None:
        return None
    symbol = _text(raw) or ""
    label = raw.get("label", "") if isinstance(raw, dict) else ""
    ref = (comp.constraints or {}).get("units")
    ct = ref["@id"].split("mc-", 1)[1] if isinstance(ref, dict) and "mc-" in str(ref.get("@id", "")) else None
    code = next((c for c in model.codes.get(ct, []) if c.value == symbol), None) if ct else None
    return Units(symbol=symbol, label=label, ct_id=ct, code=code)
