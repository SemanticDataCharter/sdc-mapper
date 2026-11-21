"""`sdc-map`: the command line for the Governed Data Record mappers.

    sdc-map targets
    sdc-map map TARGET RECORD [--package DIR | --ct-id CT] [--mapping FILE] [-o DIR] [--log-dir DIR]
    sdc-map map-chain TARGET PARENT [--package DIR | --ct-id CT] [--mapping FILE] --child MAPPING RECORD ...
                      [--packages DIR ...] [-o DIR]
    sdc-map map-chain TARGET --chain chain.json [--packages DIR ...] [-o DIR]
    sdc-map check-mapping FILE [--package DIR | --ct-id CT]
    sdc-map fidelity RECORD [--package DIR | --ct-id CT] [--mapping NAME=FILE ...]
    sdc-map log summary [--days N] | sdc-map log verify   [--log-dir DIR]

`map-chain` maps a parent record and the records chained to it into one output, and writes the output and
report.json (each record's coverage report, the parent's first). A child's MAPPING may be `-` for the mapping the
target ships for its model. `--packages` names directories holding model packages (each directly, or one directory
down), found by the identifier a record names.

Exit status: 0 mapped, 1 refused, 2 error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sdcreader import DEFAULT_HOST

from .engine import ERROR, Engine
from .gate import REFUSED
from .log import Log
from .mapping import MappingError, load_mapping
from .service import (CHAIN_REPORT, ServiceError, describe, describe_chain, fidelity, fidelity_text, load_chain,
                      map_chain, map_one, targets)


def _source(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--package", help="A model package directory (dm-<ct>.jsonld and dm-<ct>.xsd).")
    g.add_argument("--ct-id", help="A published model's identifier, fetched from --host.")


def _who(e: dict) -> str:
    if "record" not in e:
        return ""
    return "parent, " if e["record"] == 0 else f"chained record {e['record']}, "


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sdc-map", description="Map Governed Data Records to other formats, every value reported.")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--log-dir", default=os.environ.get("SDC_MAPPER_LOG_DIR", "sdc-mapper-log"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("targets")
    m = sub.add_parser("map")
    m.add_argument("target")
    m.add_argument("record")
    _source(m)
    m.add_argument("--mapping")
    m.add_argument("-o", "--output-dir")
    mc = sub.add_parser("map-chain")
    mc.add_argument("target")
    mc.add_argument("parent", nargs="?", help="The parent record (or give --chain).")
    _source(mc)
    mc.add_argument("--mapping", help="The parent's mapping file (default: the one the target ships, if any).")
    mc.add_argument("--child", nargs=2, action="append", default=[], metavar=("MAPPING", "RECORD"),
                    help="A chained record and its mapping file (`-` for the target's own), in order.")
    mc.add_argument("--chain", help="A chain manifest (chain.json) in place of PARENT and --child.")
    mc.add_argument("--packages", action="append", default=[], metavar="DIR",
                    help="A directory of model packages, found by the identifier each record names.")
    mc.add_argument("-o", "--output-dir")
    c = sub.add_parser("check-mapping")
    c.add_argument("mapping")
    _source(c)
    f = sub.add_parser("fidelity")
    f.add_argument("record")
    _source(f)
    f.add_argument("--mapping", action="append", default=[], metavar="NAME=FILE")
    f.add_argument("--json", action="store_true")
    lg = sub.add_parser("log")
    lg.add_argument("action", choices=["summary", "verify"])
    lg.add_argument("--days", type=float, default=7)
    a = p.parse_args(argv)

    try:
        if a.cmd == "targets":
            print(json.dumps(targets(), indent=2))
            return 0
        if a.cmd == "log":
            log = Log(a.log_dir)
            if a.action == "verify":
                ok, msg = log.verify()
                print(msg)
                return 0 if ok else 2
            print(json.dumps(log.summary(days=a.days), indent=2))
            return 0
        engine = Engine(log_dir=a.log_dir, host=a.host)
        if a.cmd == "map":
            r = map_one(engine, a.target, a.record, mapping=a.mapping, ct_id=a.ct_id, directory=a.package, caller="cli")
            out = describe(r, output_dir=a.output_dir or "sdc-map-out")
            if r.report:
                print(r.report.summary())
                print("written:", ", ".join(out["written"]))
            else:
                print(f"{r.outcome}:", *(f"\n  {e.get('kind')}: {e.get('reason', '')}" for e in r.errors))
            return {REFUSED: 1, ERROR: 2}.get(r.outcome, 0)
        if a.cmd == "map-chain":
            if bool(a.chain) == bool(a.parent):
                raise ServiceError("give the parent record or --chain, not both")
            if a.chain and (a.child or a.mapping or a.package or a.ct_id):
                raise ServiceError("--chain names the records and their mappings; give no PARENT, --child, "
                                   "--mapping, --package or --ct-id with it")
            if a.chain:
                spec = load_chain(a.chain)
            else:
                spec = {"parent": a.parent, "parent_mapping": a.mapping, "parent_directory": a.package,
                        "parent_ct_id": a.ct_id,
                        "children": [{"record": rec, "mapping": None if mp == "-" else mp} for mp, rec in a.child]}
            r = map_chain(engine, a.target, spec["parent"], spec["children"], parent_mapping=spec["parent_mapping"],
                          parent_directory=spec["parent_directory"], parent_ct_id=spec["parent_ct_id"],
                          packages=a.packages, caller="cli")
            out = describe_chain(r, output_dir=a.output_dir or "sdc-map-out")
            if r.records:
                for i, rec in enumerate(r.records):
                    print(("parent: " if i == 0 else f"chained record {i}: ") + rec.report.summary())
                print("written:", ", ".join(out["written"] + [str(Path(a.output_dir or "sdc-map-out") / CHAIN_REPORT)]))
            else:
                print(f"{r.outcome}:", *(f"\n  {_who(e)}{e.get('kind')}: {e.get('reason', '')}" for e in r.errors))
            return {REFUSED: 1, ERROR: 2}.get(r.outcome, 0)
        if a.cmd == "check-mapping":
            mp = load_mapping(a.mapping)
            print(f"ok: {mp.id} {mp.version}, {len(mp.rules)} rules, sha256 {mp.sha256}")
            if a.package or a.ct_id:
                pkg, model = engine.packages.one({"directory": a.package} if a.package else {"ct_id": a.ct_id})
                if (mp.model_ct_id, mp.model_schema_sha256) != (model.ct_id, pkg.sha256):
                    print(f"not bound to this model's current schema ({model.ct_id} {pkg.sha256[:12]})")
                    return 2
            return 0
        if a.cmd == "fidelity":
            mappings = dict(x.split("=", 1) for x in a.mapping)
            table = fidelity(engine, a.record, mappings=mappings, ct_id=a.ct_id, directory=a.package, caller="cli")
            print(json.dumps(table, indent=2) if a.json else fidelity_text(table))
            return 0
    except (ServiceError, MappingError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
