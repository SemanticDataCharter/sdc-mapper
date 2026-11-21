#
# Copyright (c) 2026, Axius SDC, Inc.
# Licensed under the Apache License, Version 2.0.
#
"""
MCP stdio server for `sdc-mapper`: the primary way the mappers are used.

An agent sends one record at a time as records arise. `map_record` returns the output, the coverage report, and one
plain sentence of what the target could not say, so the agent can act at once: choose another target, tell its
person, or hold the record. Every call is appended to the hash-chained log, whatever the outcome; `log_summary`
reads it back and `verify_log` walks the chain.

Tools: `list_targets`, `map_record`, `map_chain`, `check_mapping`, `fidelity`, `log_summary`, `verify_log`. No tool takes a URL:
records, packages and mapping files are paths on disk, a model is fetched only by its identifier from the host fixed
when the server starts, and the log directory is fixed at start too.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sdcreader import DEFAULT_HOST
from sdcreader.mcp import Server, ToolError, source_properties

from . import __version__
from .engine import Engine
from .mapping import RM_NAMES, MappingError, load_mapping
from .service import (ServiceError, describe, describe_chain, fidelity, fidelity_text, load_chain, map_chain, map_one,
                      targets)

SERVER_NAME = "sdc-mapper"
ISSUES = "https://github.com/SemanticDataCharter/sdc-mapper/issues"

_ENGINE: Engine | None = None
_HOST = DEFAULT_HOST
_LOG_DIR = os.environ.get("SDC_MAPPER_LOG_DIR", "sdc-mapper-log")
#: The most records a chain may chain to its parent in one call, and the most bytes returned inline in all.
MAX_CHILDREN = 100
INLINE_TOTAL = 2_000_000


def _engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine(log_dir=_LOG_DIR, host=_HOST)
    return _ENGINE


def _str(args: dict, name: str, required: bool = False) -> str | None:
    v = args.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v:
        raise ToolError(f"`{name}` must be a non-empty string.")
    return v


RECORD = {"type": "string", "description": "Path to the Governed Data Record's XML file on disk."}
MAPPING = {"type": "string", "description": "Path to a mapping file (YAML) for this model and target, if the model alone does not settle the mapping."}

TOOLS = [
    {
        "name": "list_targets",
        "description": "The target mappers installed: each target format and version, its package, and how it writes each ISO 21090 Exceptional Value (preserved, degraded, carried or lost).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "map_record",
        "description": (
            "Map one Governed Data Record to one target format. The record is validated first: valid, valid with "
            "Exceptional Values (one bad value is marked, not fatal), or refused with the reasons. Returns the output "
            "(inline, or written to output_dir), the coverage report (how every value reached the target, and what "
            "happened to every Exceptional Value), one sentence of what the target could not say, and the hash of the "
            "log entry. The model is the one the record names unless ct_id or directory is given."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "A mapper name, as list_targets gives it."},
                "record": RECORD, "mapping": MAPPING, **source_properties(),
                "output_dir": {"type": "string", "description": "Write the outputs and coverage.json here instead of returning them inline."},
            },
            "required": ["target", "record"],
            "additionalProperties": False,
        },
    },
    {
        "name": "map_chain",
        "description": (
            "Since 4.5.0. Map a parent record and the records chained to it (SDCHealth library decision 12: each child "
            "carries the parent's Record Identifier, or is named by another record of the chain) into one output, for a "
            "target that maps chains (sdc-gdr2fhir: one FHIR document Bundle). Every record is validated against its own "
            "model first; if any is refused, the chain is refused with each refused record's errors and its position "
            "(record 0 is the parent, 1 the first child), and nothing is written. Returns the outcome, the output "
            "(inline, or written to output_dir with report.json), each record's coverage report and sentence, the parent's "
            "first, and the hash of the one log entry. A record without a mapping takes the one the target ships for its "
            "model, if any. Give parent and children, or chain (a chain.json manifest)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "A mapper name, as list_targets gives it."},
                "parent": {"type": "string", "description": "Path to the parent record's XML file on disk."},
                "mapping": {"type": "string", "description": "Path to the parent's mapping file (YAML)."},
                **source_properties(),
                "children": {"type": "array", "maxItems": MAX_CHILDREN, "description": "The chained records, in order.",
                             "items": {"type": "object", "required": ["record"], "additionalProperties": False,
                                       "properties": {"record": RECORD,
                                                      "mapping": {"type": "string", "description": "Path to this record's mapping file (YAML)."},
                                                      **source_properties()}}},
                "chain": {"type": "string", "description": "Path to a chain manifest (chain.json: parent, children with file, ct_id, and optionally mapping and directory) in place of parent and children."},
                "packages": {"type": "array", "items": {"type": "string"},
                             "description": "Directories of model packages on disk, searched by the identifier each record names (the directory itself or one level down)."},
                "output_dir": {"type": "string", "description": "Write the output and report.json here instead of returning them inline."},
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_mapping",
        "description": ("Validate a mapping file against the mapping schema, and, given a model, check it is bound to that model's "
                        "current schema. Since 4.4.0 also reports whether the mapping reads the record's reference-model parts "
                        "beyond the data cluster (reference_model) and the reference-model element names its rules use, and, "
                        "given a model, which identifiers its rules name that the model does not have."),
        "inputSchema": {"type": "object", "properties": {"mapping": MAPPING, **source_properties()},
                        "required": ["mapping"], "additionalProperties": False},
    },
    {
        "name": "fidelity",
        "description": (
            "Run every installed mapper on one record and compare what each target did with each Exceptional Value: "
            "one row per Exceptional Value, one column per target. Shows which formats can say why a value is missing "
            "or unusable, and which can only leave it empty."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"record": RECORD, **source_properties(),
                           "mappings": {"type": "object", "additionalProperties": {"type": "string"},
                                        "description": "Mapping file paths by mapper name."}},
            "required": ["record"],
            "additionalProperties": False,
        },
    },
    {
        "name": "log_summary",
        "description": "Read the log back over a window: calls, outcomes, value classes and Exceptional Value classes per target, and the models and mapping files in use.",
        "inputSchema": {"type": "object", "properties": {"days": {"type": "number", "minimum": 0, "default": 7}},
                        "additionalProperties": False},
    },
    {
        "name": "verify_log",
        "description": "Walk the log's hash chain from the first entry and report whether any entry was altered or removed.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _handle_list_targets(args: dict[str, Any]) -> Any:
    return {"targets": targets()}


def _handle_map_record(args: dict[str, Any]) -> Any:
    try:
        result = map_one(_engine(), _str(args, "target", True), _str(args, "record", True), mapping=_str(args, "mapping"),
                         ct_id=_str(args, "ct_id"), directory=_str(args, "directory"), caller="mcp")
        return describe(result, output_dir=_str(args, "output_dir"))
    except (ServiceError, MappingError, OSError) as e:
        raise ToolError(str(e)) from e


def _handle_map_chain(args: dict[str, Any]) -> Any:
    target = _str(args, "target", True)
    chain, parent = _str(args, "chain"), _str(args, "parent")
    if bool(chain) == bool(parent):
        raise ToolError("Give `parent` (with `children`) or `chain`, not both.")
    packages = args.get("packages") or []
    if not isinstance(packages, list) or not all(isinstance(d, str) and d for d in packages):
        raise ToolError("`packages` must be a list of directory paths.")
    try:
        if chain:
            if any(args.get(k) for k in ("children", "mapping", "ct_id", "directory")):
                raise ToolError("`chain` names the records and their mappings; give no parent, children, mapping, "
                                "ct_id or directory with it.")
            spec = load_chain(chain)
        else:
            children = args.get("children") or []
            if not isinstance(children, list) or not all(isinstance(c, dict) for c in children):
                raise ToolError("`children` must be a list of objects, each with `record`.")
            spec = {"parent": parent, "parent_mapping": _str(args, "mapping"), "parent_directory": _str(args, "directory"),
                    "parent_ct_id": _str(args, "ct_id"),
                    "children": [{"record": _str(c, "record", True), "mapping": _str(c, "mapping"),
                                  "directory": _str(c, "directory"), "ct_id": _str(c, "ct_id")} for c in children]}
        if not spec["children"]:
            raise ToolError("A chain needs at least one chained record; use map_record for one record.")
        if len(spec["children"]) > MAX_CHILDREN:
            raise ToolError(f"A chain may have at most {MAX_CHILDREN} chained records per call.")
        result = map_chain(_engine(), target, spec["parent"], spec["children"], parent_mapping=spec["parent_mapping"],
                           parent_directory=spec["parent_directory"], parent_ct_id=spec["parent_ct_id"],
                           packages=packages, caller="mcp")
        output_dir = _str(args, "output_dir")
        if not output_dir and sum(len(d) for d in result.files.values()) > INLINE_TOTAL:
            raise ServiceError(f"the outputs are over {INLINE_TOTAL} bytes in all; give an output directory")
        return describe_chain(result, output_dir=output_dir)
    except (ServiceError, MappingError, OSError) as e:
        raise ToolError(str(e)) from e


def _handle_check_mapping(args: dict[str, Any]) -> Any:
    try:
        m = load_mapping(_str(args, "mapping", True))
    except (MappingError, OSError) as e:
        return {"ok": False, "error": str(e)}
    out = {"ok": True, "id": m.id, "version": m.version, "sha256": m.sha256, "model": m.model_ct_id,
           "target": f"{m.target_name} {m.target_version}", "rules": len(m.rules),
           "reference_model": m.reference_model, "reference_model_names": m.names_reference_model}
    source = {k: args[k] for k in ("ct_id", "directory") if args.get(k)}
    if source:
        pkg, model = _engine().packages.one(source)
        if (m.model_ct_id, m.model_schema_sha256) != (model.ct_id, pkg.sha256):
            out.update(ok=False, error=f"bound to {m.model_ct_id} schema {m.model_schema_sha256[:12]}; "
                                       f"the model is {model.ct_id} schema {pkg.sha256[:12]}")
        else:
            named = {n for r in m.rules for n in (r.component, r.cluster, *r.within) if n and n not in RM_NAMES}
            out["unknown_identifiers"] = sorted(named - set(model.components))
    return out


def _handle_fidelity(args: dict[str, Any]) -> Any:
    try:
        table = fidelity(_engine(), _str(args, "record", True), mappings=args.get("mappings") or {},
                         ct_id=_str(args, "ct_id"), directory=_str(args, "directory"), caller="mcp")
    except (ServiceError, MappingError, OSError) as e:
        raise ToolError(str(e)) from e
    return {**table, "text": fidelity_text(table)}


def _handle_log_summary(args: dict[str, Any]) -> Any:
    days = args.get("days", 7)
    if not isinstance(days, (int, float)) or days < 0:
        raise ToolError("`days` must be a non-negative number.")
    return _engine().log.summary(days=days)


def _handle_verify_log(args: dict[str, Any]) -> Any:
    ok, message = _engine().log.verify()
    return {"ok": ok, "message": message, "directory": str(Path(_engine().log.dir).resolve())}


TOOL_HANDLERS = {
    "list_targets": _handle_list_targets, "map_record": _handle_map_record, "map_chain": _handle_map_chain,
    "check_mapping": _handle_check_mapping,
    "fidelity": _handle_fidelity, "log_summary": _handle_log_summary, "verify_log": _handle_verify_log,
}


def _server() -> Server:
    return Server(name=SERVER_NAME, version=__version__, tools=TOOLS, handlers=TOOL_HANDLERS, issues_url=ISSUES,
                  instructions=(
                      "Map Governed Data Records to other formats, one record per call (map_record), or a parent record and the "
                      "records chained to it in one output (map_chain). Every value is reported: how "
                      "it reached the target, or that the target cannot hold it; and for every Exceptional Value "
                      "(why a value is missing or unusable), whether the target could say it too. Pass the "
                      "`could_not_say` sentence on to the person when it reports a loss. Records, packages and mapping "
                      "files are paths on disk; no tool takes a URL."))


def main(argv: list[str] | None = None) -> None:
    import argparse

    global _HOST, _LOG_DIR
    p = argparse.ArgumentParser(prog="sdc-mapper-mcp", description="MCP stdio server for the Governed Data Record mappers.")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"SDCStudio host for fetching a model by ct_id. Default {DEFAULT_HOST}.")
    p.add_argument("--log-dir", default=_LOG_DIR, help="Where the log is kept. Default $SDC_MAPPER_LOG_DIR or ./sdc-mapper-log.")
    a = p.parse_args(argv)
    _HOST, _LOG_DIR = a.host.rstrip("/"), a.log_dir
    _server().run_stdio()


if __name__ == "__main__":
    main()
