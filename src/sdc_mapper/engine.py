"""One call, end to end: find the model, check the mapping, gate, read, resolve, map, report, log.

Every call ends in exactly one of four outcomes, and every one is logged:
- `valid` or `valid_with_ev`: mapped; the files and the report are returned;
- `refused`: the gate refused the record; the errors are returned, no output is written;
- `error`: the call could not be completed (a mapping for another model, a mapper that did not account for every
  value); the reason is returned.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from sdcreader import DEFAULT_HOST
from sdcreader.mcp import PackageCache
from sdcreader.record import read_record

from .gate import REFUSED, Gate
from .log import Log
from .mapping import Mapping, MappingError
from .report import CoverageReport, IncompleteMapping, build_report
from .resolve import resolve

ERROR = "error"
_ROOT = re.compile(rb"<[A-Za-z0-9_]+:dm-([a-z0-9]{24,32})[\s>]")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class CallResult:
    outcome: str                                  # valid, valid_with_ev, refused or error
    files: dict[str, bytes] = field(default_factory=dict)
    report: CoverageReport | None = None
    errors: list[dict] = field(default_factory=list)
    log_hash: str | None = None

    @property
    def mapped(self) -> bool:
        return self.report is not None


class Engine:
    def __init__(self, log_dir: str | Path | None = None, host: str = DEFAULT_HOST, schema_dir: str | Path | None = None):
        self.gate = Gate(schema_dir)
        self.log = Log(log_dir) if log_dir else None
        self.packages = PackageCache(host=host, storage={})

    def model_for(self, xml: bytes, ct_id: str | None = None, directory: str | None = None):
        source = {k: v for k, v in (("ct_id", ct_id), ("directory", directory)) if v}
        if not source:
            m = _ROOT.search(xml[:4000])
            if not m:
                raise MappingError("the record does not name a model (no sdc4:dm-<ct_id> root); give ct_id or directory")
            source = {"ct_id": m.group(1).decode()}
        return self.packages.one(source)

    def map_record(self, xml: bytes, mapper, mapping: Mapping | None = None, ct_id: str | None = None,
                   directory: str | None = None, caller: str | None = None) -> CallResult:
        base = {"caller": caller or "", "record_sha256": _sha(xml),
                "mapper": {"package": mapper.package, "version": mapper.version, "name": mapper.name},
                "target": {"name": mapper.target, "version": mapper.target_version},
                "mapping": {"id": mapping.id, "version": mapping.version, "sha256": mapping.sha256} if mapping else None}
        try:
            pkg, model = self.model_for(xml, ct_id, directory)
        except Exception as e:  # noqa: BLE001  an unreachable model is an error result, logged, never a crash
            return self._finish(CallResult(ERROR, errors=[{"kind": "model", "reason": str(e)}]), base)
        base["model"] = {"ct_id": model.ct_id, "schema_sha256": pkg.sha256}
        try:
            if mapping:
                mapping.check(model.ct_id, pkg.sha256, mapper.target, mapper.target_version)
        except MappingError as e:
            return self._finish(CallResult(ERROR, errors=[{"kind": "mapping", "reason": str(e)}]), base)

        gate = self.gate.check(xml, pkg, model)
        if gate.outcome == REFUSED:
            return self._finish(CallResult(REFUSED, errors=gate.errors), base)

        try:
            resolved = resolve(read_record(gate.xml, model), model, pkg, gate)
            result = mapper.map(resolved, mapping)
            report = build_report(resolved, result, mapper, mapping)
        except IncompleteMapping as e:
            return self._finish(CallResult(ERROR, errors=[{"kind": "incomplete mapping", "reason": str(e)}]), base)
        except MappingError as e:                 # a code-table miss, a path the target refuses: never a guess
            return self._finish(CallResult(ERROR, errors=[{"kind": "mapping", "reason": str(e)}]), base)
        except Exception as e:  # noqa: BLE001  a mapper's failure is reported and logged, not raised into the stream
            return self._finish(CallResult(ERROR, errors=[{"kind": type(e).__name__, "reason": str(e)}]), base)

        base["record"] = {"instance_id": resolved.instance_id,
                          "instance_version": resolved.attributes.get("instance_version", "")}
        base["counts"] = report.counts
        base["exceptional"] = [{"position": e["position"], "ct_id": e["ct_id"], "code": e["code"], "origin": e["origin"],
                                "class": e["class"]} for e in report.exceptional]
        base["outputs"] = {name: _sha(data) for name, data in sorted(result.files.items())}
        return self._finish(CallResult(gate.outcome, files=result.files, report=report), base)

    def _finish(self, result: CallResult, base: dict) -> CallResult:
        if self.log is not None:
            entry = {**base, "outcome": result.outcome,
                     # kinds and paths only: a failure reason can quote the value that failed
                     "errors": [{"kind": e.get("kind", ""), "path": e.get("path", "")} for e in result.errors]}
            result.log_hash = self.log.append(entry)["hash"]
        return result
