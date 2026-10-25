"""The validation gate: every record is valid, valid with Exceptional Values, or refused.

The gate uses sdcvalidator's public recovery API (`SDC4Validator.recover()`, 4.6.0), which keeps the Exceptional
Values a record already carries (a refusal, a masked field: what this line exists to report), inserts one beside each
value that fails its constraint and reports it as data with the constraint that failed, and returns every error it
cannot cover rather than skipping it. The gate refuses a record with any such error: nothing is projected that the
gate has not accounted for.

The model's schema includes the SDC4 reference model from https://semanticdatacharter.com/ns/sdc4/sdc4.xsd. The gate
maps that include to the copy shipped in this package, so validation never touches the network, and a schema is built
once per SHA-256 and kept for the life of the process.
"""
from __future__ import annotations

import hashlib
import io
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from sdcreader import LEAF_TYPES, Model, ModelPackage
from sdcvalidator import (SDC4StructuralValidationError, SDC4Validator, build_xsd11_schema, etree_tostring,
                          validate_sdc4_schema_compliance)
from xmlschema import XMLResource

SDC4_NS = "https://semanticdatacharter.com/ns/sdc4/"
SDC4_XSD = Path(__file__).with_name("schemas") / "sdc4.xsd"

VALID = "valid"
VALID_WITH_EV = "valid_with_ev"
REFUSED = "refused"


@dataclass
class Recovery:
    """One value the gate covered with an Exceptional Value."""
    position: int          # the value's position in the record, as sdcreader numbers it
    ct_id: str
    code: str              # the ISO 21090 code inserted
    constraint: str        # what failed, from the validator


@dataclass
class GateResult:
    outcome: str                                   # VALID, VALID_WITH_EV or REFUSED
    xml: bytes | None = None                       # the record to read: the input, or the input with recoveries
    recoveries: list[Recovery] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)   # for REFUSED: each error that could not be covered

    @property
    def refused(self) -> bool:
        return self.outcome == REFUSED


def _map_sdc4(url: str) -> str:
    return str(SDC4_XSD) if url.rstrip("/").endswith("/ns/sdc4/sdc4.xsd") else url


class Gate:
    """Builds and caches one validator per model schema, keyed by the schema's SHA-256."""

    def __init__(self, workdir: str | Path | None = None):
        self._dir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="sdc-mapper-schemas-"))
        self._dir.mkdir(parents=True, exist_ok=True)
        self._validators: dict[str, SDC4Validator] = {}
        self._lock = threading.Lock()

    def validator(self, pkg: ModelPackage) -> SDC4Validator:
        sha = pkg.sha256
        with self._lock:
            v = self._validators.get(sha)
            if v is None:
                path = self._dir / f"{sha}.xsd"
                if not path.exists():
                    path.write_bytes(pkg.xsd_bytes)
                ok, problems = validate_sdc4_schema_compliance(str(path))
                if not ok:
                    raise ValueError("the model's schema violates SDC4 compliance: " + "; ".join(problems))
                schema = build_xsd11_schema(str(path), validation="lax", uri_mapper=_map_sdc4, allow="local")
                v = SDC4Validator(schema, check_sdc4_compliance=False, validation="lax")
                self._validators[sha] = v
            return v

    def check(self, xml: bytes, pkg: ModelPackage, model: Model) -> GateResult:
        try:
            resource = XMLResource(io.BytesIO(xml))
        except Exception as e:  # noqa: BLE001  xmlschema raises its own and the parser's errors
            return GateResult(REFUSED, errors=[{"kind": "not well-formed", "reason": str(e)}])
        if resource.root.tag != f"{{{SDC4_NS}}}dm-{model.ct_id}":
            return GateResult(REFUSED, errors=[{"kind": "wrong model",
                                                "reason": f"the record's root is {resource.root.tag}, not sdc4:dm-{model.ct_id}"}])
        try:
            result = self.validator(pkg).recover(resource, remove_existing_ev=False)
        except SDC4StructuralValidationError as e:
            return GateResult(REFUSED, errors=[_summary(err, "structural") for err in e.errors])
        if not result.ok:
            return GateResult(REFUSED, errors=[_summary(err, "not recoverable") for err in result.unrecoverable])
        if not result.recoveries:
            return GateResult(VALID, xml=xml)

        root = result.tree.getroot()
        positions = _value_positions(root, model)
        recoveries = []
        for r in result.inserted:     # an Exceptional Value the record already carried is the source's, not the gate's
            comp = _component_element(root, _find(root, r.path), model)
            if comp is None:
                return GateResult(REFUSED, errors=[{"kind": "not recoverable", "path": r.path,
                                                    "reason": "the recovered value is not inside a value component"}])
            recoveries.append(Recovery(position=positions[id(comp)], ct_id=comp.tag.split("ms-", 1)[1], code=r.code,
                                       constraint=r.reason))
        recovered = etree_tostring(root, namespaces=resource.get_namespaces(root_only=False) or {"sdc4": SDC4_NS},
                                   xml_declaration=True, encoding="utf-8")
        return GateResult(VALID_WITH_EV, xml=recovered if isinstance(recovered, bytes) else recovered.encode("utf-8"),
                          recoveries=recoveries)


def _reason(e) -> str:
    return " ".join((e.reason or e.message or "").split())


def _summary(e, kind: str) -> dict:
    return {"kind": kind, "path": e.path or "", "reason": _reason(e)}


_STEP = re.compile(r"/((?:\{[^}]*\})?[^/{\[]+)(?:\[(\d+)\])?")


def _find(root, path: str):
    """The element a Clark-form error path names (`/{ns}dm-x/{ns}ms-y/xdcount-value`), or None. The namespace URI
    holds slashes, so the path is tokenized by step, not split."""
    steps = _STEP.findall(path)
    if not steps or steps[0][0] != root.tag:
        return None
    el = root
    for name, idx in steps[1:]:
        matches = [c for c in el if c.tag == name]
        i = int(idx) - 1 if idx else 0
        if i >= len(matches):
            return None
        el = matches[i]
    return el


def _component_element(root, el, model: Model):
    """The leaf component element (`sdc4:ms-<ct_id>` of a value type) that holds `el`, or None."""
    parents = {c: p for p in root.iter() for c in p}
    while el is not None:
        if el.tag.startswith(f"{{{SDC4_NS}}}ms-"):
            comp = model.components.get(el.tag.split("ms-", 1)[1])
            if comp is not None and comp.sdc_type in LEAF_TYPES:
                return el
        el = parents.get(el)
    return None


def _value_positions(root, model: Model) -> dict[int, int]:
    """Each leaf component element's position among the record's values, in document order: the numbering
    sdcreader's `read_record` gives the same record."""
    out, n = {}, 0

    def walk(el):
        nonlocal n
        for c in el:
            if not (isinstance(c.tag, str) and c.tag.startswith(f"{{{SDC4_NS}}}ms-")):
                continue
            comp = model.components.get(c.tag.split("ms-", 1)[1])
            if comp is not None and comp.sdc_type in LEAF_TYPES:
                out[id(c)] = n
                n += 1
            else:
                walk(c)

    walk(root)
    return out


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
