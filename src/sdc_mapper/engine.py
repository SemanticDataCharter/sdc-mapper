"""One call, end to end: find the model, check the mapping, gate, read, resolve, map, report, log.

Every call ends in exactly one of four outcomes, and every one is logged:
- `valid` or `valid_with_ev`: mapped; the files and the report are returned;
- `refused`: the gate refused the record; the errors are returned, no output is written;
- `error`: the call could not be completed (a mapping for another model, a mapper that did not account for every
  value); the reason is returned.

`map_chain` (4.5.0) is the same call for a parent record and the records chained to it (SDCHealth library decision
12), mapped into one output by a target that implements the optional `map_chain`. Each record is checked, gated, read
and reported as one record is; the chain has one outcome and one log entry.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from sdcreader import DEFAULT_HOST
from sdcreader.mcp import PackageCache
from sdcreader.record import read_record

from .contract import maps_chains
from .gate import REFUSED, VALID, VALID_WITH_EV, Gate
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


@dataclass
class RecordReport:
    """One record of a chain: its instance id, its model, the gate's outcome for it, and its coverage report."""
    instance_id: str
    ct_id: str
    outcome: str                                  # the gate's: valid or valid_with_ev
    report: CoverageReport

    def to_dict(self) -> dict:
        return {"instance_id": self.instance_id, "ct_id": self.ct_id, "outcome": self.outcome,
                "report": self.report.to_dict()}


@dataclass
class ChainCallResult:
    """A chain mapped: as CallResult, with one RecordReport per record (the parent's first, the children in the order
    given) in place of the one report. An error names the record it concerns by `record`: 0 is the parent, 1 the
    first child."""
    outcome: str                                  # valid, valid_with_ev, refused or error
    files: dict[str, bytes] = field(default_factory=dict)
    records: list[RecordReport] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    log_hash: str | None = None

    @property
    def mapped(self) -> bool:
        return bool(self.records)


def _anchors(xml: bytes, mapping: Mapping | None) -> dict:
    return {"record_sha256": _sha(xml),
            "mapping": {"id": mapping.id, "version": mapping.version, "sha256": mapping.sha256} if mapping else None}


def _counted(anchor: dict, resolved, report: CoverageReport) -> None:
    anchor["record"] = {"instance_id": resolved.instance_id,
                        "instance_version": resolved.attributes.get("instance_version", "")}
    anchor["counts"] = report.counts
    anchor["exceptional"] = [{"position": e["position"], "ct_id": e["ct_id"], "code": e["code"],
                              "origin": e["origin"], "class": e["class"]} for e in report.exceptional]


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

        rm = bool(mapping and mapping.reference_model)   # the reference-model parts, read only when the mapping asks
        gate = self.gate.check(xml, pkg, model, reference_model=rm)
        if gate.outcome == REFUSED:
            return self._finish(CallResult(REFUSED, errors=gate.errors), base)

        try:
            resolved = resolve(read_record(gate.xml, model, reference_model=rm), model, pkg, gate)
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

    def map_chain(self, parent_xml: bytes, children_xml: list[bytes], mapper, parent_mapping: Mapping | None = None,
                  child_mappings: list[Mapping | None] | None = None, directories: list[str | None] | None = None,
                  ct_ids: list[str | None] | None = None, caller: str | None = None) -> ChainCallResult:
        """A parent record and the records chained to it, mapped into one output by `mapper.map_chain`.

        `child_mappings`, `directories` and `ct_ids` run beside the records: `child_mappings` beside `children_xml`,
        `directories` and `ct_ids` beside the parent and then the children (a record's model is the one it names
        unless its directory or ct_id is given). Nothing is mapped unless every record finds its model, matches its
        mapping and passes the gate; the outcome is `valid_with_ev` if any record needed the gate's recovery."""
        xmls = [parent_xml, *children_xml]
        n = len(xmls)
        mappings = [parent_mapping, *(child_mappings if child_mappings is not None else [None] * len(children_xml))]
        directories = list(directories) if directories is not None else [None] * n
        ct_ids = list(ct_ids) if ct_ids is not None else [None] * n
        if not (len(mappings) == len(directories) == len(ct_ids) == n):
            raise ValueError("child_mappings, directories and ct_ids must run beside the records")

        anchors = [_anchors(x, mp) for x, mp in zip(xmls, mappings)]
        base = {"caller": caller or "",
                "mapper": {"package": mapper.package, "version": mapper.version, "name": mapper.name},
                "target": {"name": mapper.target, "version": mapper.target_version}}

        def finish(result: ChainCallResult) -> ChainCallResult:
            return self._finish(result, {**base, **anchors[0], "chained": anchors[1:]})

        def error(i: int | None, kind: str, reason: str) -> ChainCallResult:
            e = {"kind": kind, "reason": reason}
            if i is not None:
                e["record"] = i
            return finish(ChainCallResult(ERROR, errors=[e]))

        if not maps_chains(mapper):
            return error(None, "chain", f"{mapper.package} ({mapper.target}) maps one record at a time; it does "
                                        "not map chained records")

        found = []
        for i, (xml, mp) in enumerate(zip(xmls, mappings)):
            try:
                pkg, model = self.model_for(xml, ct_ids[i], directories[i])
            except Exception as e:  # noqa: BLE001  an unreachable model is an error result, logged, never a crash
                return error(i, "model", str(e))
            anchors[i]["model"] = {"ct_id": model.ct_id, "schema_sha256": pkg.sha256}
            try:
                if mp:
                    mp.check(model.ct_id, pkg.sha256, mapper.target, mapper.target_version)
            except MappingError as e:
                return error(i, "mapping", str(e))
            found.append((pkg, model))

        gates, refused = [], []
        for i, ((pkg, model), mp) in enumerate(zip(found, mappings)):
            gate = self.gate.check(xmls[i], pkg, model, reference_model=bool(mp and mp.reference_model))
            gates.append(gate)
            if gate.outcome == REFUSED:
                refused += [{**e, "record": i} for e in gate.errors] or [{"kind": "refused", "reason": "", "record": i}]
        if refused:                                   # every record's refusal, so all can be fixed at once
            return finish(ChainCallResult(REFUSED, errors=refused))

        at = 0
        try:
            resolved = []
            for at, ((pkg, model), mp, gate) in enumerate(zip(found, mappings, gates)):
                rm = bool(mp and mp.reference_model)
                resolved.append(resolve(read_record(gate.xml, model, reference_model=rm), model, pkg, gate))
            at = None
            mapped = mapper.map_chain((resolved[0], mappings[0]), list(zip(resolved[1:], mappings[1:])))
            results = list(mapped.results)
            if len(results) != n:
                return error(None, "incomplete mapping", f"{mapper.package} returned {len(results)} results for a "
                                                         f"chain of {n} records")
            reports = []
            for at, (r, result, mp) in enumerate(zip(resolved, results, mappings)):
                reports.append(build_report(r, result, mapper, mp))
        except IncompleteMapping as e:
            return error(at, "incomplete mapping", str(e))
        except MappingError as e:                 # a record that does not belong, a code-table miss: never a guess
            return error(at, "mapping", str(e))
        except Exception as e:  # noqa: BLE001  a mapper's failure is reported and logged, not raised into the stream
            return error(at, type(e).__name__, str(e))

        for anchor, r, report in zip(anchors, resolved, reports):
            _counted(anchor, r, report)
        base["outputs"] = {name: _sha(data) for name, data in sorted(mapped.files.items())}
        records = [RecordReport(r.instance_id, r.ct_id, r.outcome, report) for r, report in zip(resolved, reports)]
        outcome = VALID_WITH_EV if any(g.outcome == VALID_WITH_EV for g in gates) else VALID
        return finish(ChainCallResult(outcome, files=mapped.files, records=records))

    def _finish(self, result, base: dict):
        if self.log is not None:
            entry = {**base, "outcome": result.outcome,
                     # kinds and paths only: a failure reason can quote the value that failed
                     "errors": [{"kind": e.get("kind", ""), "path": e.get("path", ""),
                                 **({"record": e["record"]} if "record" in e else {})} for e in result.errors]}
            result.log_hash = self.log.append(entry)["hash"]
        return result
