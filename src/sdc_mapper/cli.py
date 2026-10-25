"""`sdc-map`: the command line for the Governed Data Record mappers.

    sdc-map targets
    sdc-map map TARGET RECORD [--package DIR | --ct-id CT] [--mapping FILE] [-o DIR] [--log-dir DIR]
    sdc-map check-mapping FILE [--package DIR | --ct-id CT]
    sdc-map fidelity RECORD [--package DIR | --ct-id CT] [--mapping NAME=FILE ...]
    sdc-map log summary [--days N] | sdc-map log verify   [--log-dir DIR]

Exit status: 0 mapped, 1 refused, 2 error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from sdcreader import DEFAULT_HOST

from .engine import ERROR, Engine
from .gate import REFUSED
from .log import Log
from .mapping import MappingError, load_mapping
from .service import ServiceError, describe, fidelity, fidelity_text, map_one, targets


def _source(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--package", help="A model package directory (dm-<ct>.jsonld and dm-<ct>.xsd).")
    g.add_argument("--ct-id", help="A published model's identifier, fetched from --host.")


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
