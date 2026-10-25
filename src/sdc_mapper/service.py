"""The operations the CLI and the MCP server share, so both describe a run the same way."""
from __future__ import annotations

from pathlib import Path

from sdcreader.record import EXCEPTIONAL_VALUES

from .contract import installed_mappers
from .engine import CallResult, Engine
from .mapping import load_mapping


class ServiceError(Exception):
    pass


def mapper_by_name(name: str):
    found = installed_mappers()
    if name not in found:
        raise ServiceError(f"no mapper named {name!r} is installed; installed: {', '.join(sorted(found)) or 'none'}")
    return found[name]()


def targets() -> list[dict]:
    out = []
    for name, cls in sorted(installed_mappers().items()):
        m = cls()
        out.append({"name": name, "target": m.target, "target_version": m.target_version, "package": m.package,
                    "version": m.version,
                    "exceptional_values": {code: rule.cls for code, rule in sorted(m.ev_table.items())}})
    return out


def describe(result: CallResult, output_dir: str | Path | None = None, inline_limit: int = 200_000) -> dict:
    """A call's result as plain data: the outcome, the report, the errors, the log entry's hash, and the outputs
    either written to `output_dir` (and named) or, when small and text, inline."""
    out: dict = {"outcome": result.outcome, "errors": result.errors, "log_hash": result.log_hash}
    if result.report:
        out["could_not_say"] = result.report.could_not_say
        out["report"] = result.report.to_dict()
    if result.files:
        if output_dir:
            d = Path(output_dir)
            d.mkdir(parents=True, exist_ok=True)
            for name, data in result.files.items():
                (d / name).write_bytes(data)
            if result.report:
                (d / "coverage.json").write_text(result.report.to_json(), encoding="utf-8")
            out["written"] = sorted(str(d / n) for n in result.files)
        else:
            inline = {}
            for name, data in result.files.items():
                if len(data) > inline_limit:
                    raise ServiceError(f"{name} is {len(data)} bytes; give an output directory")
                try:
                    inline[name] = data.decode("utf-8")
                except UnicodeDecodeError as e:
                    raise ServiceError(f"{name} is not text; give an output directory") from e
            out["files"] = inline
    return out


def map_one(engine: Engine, target: str, record: str | Path | bytes, mapping: str | Path | None = None,
            ct_id: str | None = None, directory: str | None = None, caller: str | None = None) -> CallResult:
    mapper = mapper_by_name(target)
    xml = record if isinstance(record, bytes) else Path(record).read_bytes()
    return engine.map_record(xml, mapper, mapping=load_mapping(mapping) if mapping else None,
                             ct_id=ct_id, directory=directory, caller=caller)


def fidelity(engine: Engine, record: str | Path | bytes, mappings: dict[str, str] | None = None,
             ct_id: str | None = None, directory: str | None = None, caller: str | None = None) -> dict:
    """Run every installed mapper on one record: one row per Exceptional Value, one column per target, each cell the
    class and the code the target wrote. The single picture of what each format can and cannot say about this record."""
    mappings = mappings or {}
    xml = record if isinstance(record, bytes) else Path(record).read_bytes()
    columns, rows, outcomes = [], {}, {}
    for name, cls in sorted(installed_mappers().items()):
        mapper = cls()
        mapping = load_mapping(mappings[name]) if name in mappings else None
        r = engine.map_record(xml, mapper, mapping=mapping, ct_id=ct_id, directory=directory, caller=caller)
        col = f"{mapper.target} {mapper.target_version}"
        columns.append(col)
        outcomes[col] = r.outcome
        for e in (r.report.exceptional if r.report else []):
            row = rows.setdefault(e["position"], {"position": e["position"], "label": e["label"], "code": e["code"],
                                                  "name": EXCEPTIONAL_VALUES[e["code"]], "origin": e["origin"], "targets": {}})
            row["targets"][col] = {"class": e["class"], "target_code": e["target_code"], "merged_with": e["merged_with"]}
    return {"targets": columns, "outcomes": outcomes, "rows": [rows[k] for k in sorted(rows)]}


def fidelity_text(table: dict) -> str:
    cols = table["targets"]
    if not table["rows"]:
        return "This record carries no Exceptional Values; nothing to compare."
    head = ["Exceptional Value", *cols]
    lines = [" | ".join(head), " | ".join("---" for _ in head)]
    for r in table["rows"]:
        cells = []
        for c in cols:
            t = r["targets"].get(c)
            cells.append("(not mapped)" if not t else t["class"] + (f" ({t['target_code']})" if t["target_code"] else ""))
        lines.append(" | ".join([f"{r['label']}: {r['name']} ({r['code']})", *cells]))
    return "\n".join(lines)
