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

Tools: `list_targets`, `map_record`, `check_mapping`, `fidelity`, `log_summary`, `verify_log`. No tool takes a URL:
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
from .mapping import MappingError, load_mapping
from .service import ServiceError, describe, fidelity, fidelity_text, map_one, targets

SERVER_NAME = "sdc-mapper"
ISSUES = "https://github.com/SemanticDataCharter/sdc-mapper/issues"

_ENGINE: Engine | None = None
_HOST = DEFAULT_HOST
_LOG_DIR = os.environ.get("SDC_MAPPER_LOG_DIR", "sdc-mapper-log")


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
        "name": "check_mapping",
        "description": "Validate a mapping file against the mapping schema, and, given a model, check it is bound to that model's current schema.",
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


def _handle_check_mapping(args: dict[str, Any]) -> Any:
    try:
        m = load_mapping(_str(args, "mapping", True))
    except (MappingError, OSError) as e:
        return {"ok": False, "error": str(e)}
    out = {"ok": True, "id": m.id, "version": m.version, "sha256": m.sha256, "model": m.model_ct_id,
           "target": f"{m.target_name} {m.target_version}", "rules": len(m.rules)}
    source = {k: args[k] for k in ("ct_id", "directory") if args.get(k)}
    if source:
        pkg, model = _engine().packages.one(source)
        if (m.model_ct_id, m.model_schema_sha256) != (model.ct_id, pkg.sha256):
            out.update(ok=False, error=f"bound to {m.model_ct_id} schema {m.model_schema_sha256[:12]}; "
                                       f"the model is {model.ct_id} schema {pkg.sha256[:12]}")
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
    "list_targets": _handle_list_targets, "map_record": _handle_map_record, "check_mapping": _handle_check_mapping,
    "fidelity": _handle_fidelity, "log_summary": _handle_log_summary, "verify_log": _handle_verify_log,
}


def _server() -> Server:
    return Server(name=SERVER_NAME, version=__version__, tools=TOOLS, handlers=TOOL_HANDLERS, issues_url=ISSUES,
                  instructions=(
                      "Map Governed Data Records to other formats, one record per call. Every value is reported: how "
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
