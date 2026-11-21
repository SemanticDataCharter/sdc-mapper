"""The operations the CLI and the MCP server share, so both describe a run the same way."""
from __future__ import annotations

import json
from pathlib import Path

from sdcreader.record import EXCEPTIONAL_VALUES

from .contract import installed_mappers
from .engine import _ROOT, CallResult, ChainCallResult, Engine
from .mapping import Mapping, load_mapping


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
            out["written"] = _write(Path(output_dir), result.files)
            if result.report:
                (Path(output_dir) / "coverage.json").write_text(result.report.to_json(), encoding="utf-8")
        else:
            out["files"] = _inline(result.files, inline_limit)
    return out


def _write(d: Path, files: dict[str, bytes]) -> list[str]:
    d.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    return sorted(str(d / n) for n in files)


def _inline(files: dict[str, bytes], inline_limit: int) -> dict[str, str]:
    inline = {}
    for name, data in files.items():
        if len(data) > inline_limit:
            raise ServiceError(f"{name} is {len(data)} bytes; give an output directory")
        try:
            inline[name] = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ServiceError(f"{name} is not text; give an output directory") from e
    return inline


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


# ---------------------------------------------------------------- chained records (4.5.0)

#: The file a chain's per-record reports are written to, beside the output.
CHAIN_REPORT = "report.json"


def record_ct_id(xml: bytes) -> str | None:
    """The model identifier a record names in its root element (`sdc4:dm-<ct_id>`), or None."""
    m = _ROOT.search(xml[:4000])
    return m.group(1).decode() if m else None


def shipped_mapping(mapper, ct_id: str | None) -> Mapping | None:
    """The mapping a target ships for a model, if it ships one: a mapper may offer `mapping_for(ct_id)`, returning a
    mapping file's path or None. A target without it, or without a mapping for the model, gives None."""
    find = getattr(mapper, "mapping_for", None)
    if not ct_id or not callable(find):
        return None
    path = find(ct_id)
    return load_mapping(path) if path else None


def find_package(ct_id: str | None, roots) -> str | None:
    """The model package directory for `ct_id` among `roots`: a root that holds `dm-<ct_id>.xsd`, or a directory just
    under a root that does. None when there is none, so the model is fetched by its identifier."""
    if not ct_id:
        return None
    for root in roots or ():
        root = Path(root)
        if (root / f"dm-{ct_id}.xsd").is_file():
            return str(root)
        for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else ():
            if (d / f"dm-{ct_id}.xsd").is_file():
                return str(d)
    return None


def _near(base: Path, name: str) -> Path:
    """A path a chain manifest names: absolute, or relative to the manifest's directory or, failing that, to the
    nearest of its ancestors where it exists (a sample set's manifest may name the parent from its repository root)."""
    p = Path(name)
    if p.is_absolute():
        return p
    for d in (base, *base.parents):
        if (d / p).exists():
            return d / p
    return base / p


def load_chain(manifest: str | Path) -> dict:
    """A chain manifest (`chain.json`) as the arguments `map_chain` takes. The manifest names `parent` (a record
    file) and `children` (each with `file`), and may give `parent_ct_id`, `parent_mapping`, `parent_directory`, and
    for each child `ct_id`, `mapping` and `directory`. Paths are resolved as `_near` says."""
    path = Path(manifest)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ServiceError(f"{path}: not JSON ({e})") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("parent"), str) or not isinstance(doc.get("children"), list):
        raise ServiceError(f"{path}: a chain manifest names `parent` (a file) and `children` (a list)")
    base = path.resolve().parent
    opt = lambda v: str(_near(base, v)) if isinstance(v, str) and v else None  # noqa: E731
    children = []
    for i, c in enumerate(doc["children"], 1):
        if not isinstance(c, dict) or not isinstance(c.get("file"), str):
            raise ServiceError(f"{path}: child {i} names no `file`")
        children.append({"record": str(_near(base, c["file"])), "mapping": opt(c.get("mapping")),
                         "directory": opt(c.get("directory")), "ct_id": c.get("ct_id") or None})
    return {"parent": str(_near(base, doc["parent"])), "parent_mapping": opt(doc.get("parent_mapping")),
            "parent_directory": opt(doc.get("parent_directory")), "parent_ct_id": doc.get("parent_ct_id") or None,
            "children": children}


def map_chain(engine: Engine, target: str, parent: str | Path | bytes, children: list[dict],
              parent_mapping: str | Path | None = None, parent_directory: str | None = None,
              parent_ct_id: str | None = None, packages=(), caller: str | None = None) -> ChainCallResult:
    """A parent record and the records chained to it, mapped by `target` into one output. Each of `children` is a
    dict with `record` (a path or bytes) and, optionally, `mapping`, `directory` and `ct_id`. A record without a
    mapping takes the one the target ships for its model, if any; a record without a directory takes its model's
    package from `packages` (directories searched by `find_package`), or else fetches it by its identifier."""
    mapper = mapper_by_name(target)
    entries = [{"record": parent, "mapping": parent_mapping, "directory": parent_directory, "ct_id": parent_ct_id},
               *children]
    xmls, mappings, directories, ct_ids = [], [], [], []
    for e in entries:
        rec = e.get("record")
        if rec is None:
            raise ServiceError("every record of a chain needs `record`")
        xml = rec if isinstance(rec, bytes) else Path(rec).read_bytes()
        ct = e.get("ct_id") or record_ct_id(xml)
        mp = load_mapping(e["mapping"]) if e.get("mapping") else shipped_mapping(mapper, ct)
        xmls.append(xml)
        mappings.append(mp)
        directory = e.get("directory") or find_package(ct, packages)
        directories.append(directory)
        ct_ids.append(None if directory else e.get("ct_id"))   # a package on disk, else the model by its identifier
    return engine.map_chain(xmls[0], xmls[1:], mapper, parent_mapping=mappings[0], child_mappings=mappings[1:],
                            directories=directories, ct_ids=ct_ids, caller=caller)


def chain_report_json(result: ChainCallResult) -> str:
    """The per-record reports of a chain, in order, as the JSON written to report.json."""
    return json.dumps([r.to_dict() for r in result.records], indent=2, sort_keys=True, ensure_ascii=False, default=str)


def describe_chain(result: ChainCallResult, output_dir: str | Path | None = None, inline_limit: int = 200_000) -> dict:
    """A chain's result as plain data: the outcome, the errors (each naming its record: 0 the parent, 1 the first
    child), the log entry's hash, each record's report and sentence, and the outputs written to `output_dir` (with
    report.json, the per-record reports in order) or returned inline."""
    out: dict = {"outcome": result.outcome, "errors": result.errors, "log_hash": result.log_hash}
    if result.records:
        out["records"] = [{"instance_id": r.instance_id, "ct_id": r.ct_id, "outcome": r.outcome,
                           "could_not_say": r.report.could_not_say, "report": r.report.to_dict()}
                          for r in result.records]
    if result.files:
        if output_dir:
            out["written"] = _write(Path(output_dir), result.files)
            (Path(output_dir) / CHAIN_REPORT).write_text(chain_report_json(result), encoding="utf-8")
        else:
            out["files"] = _inline(result.files, inline_limit)
    return out
