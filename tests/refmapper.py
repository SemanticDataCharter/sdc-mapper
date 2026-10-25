"""The reference mapper: a flat CSV of path, label, value, units, code and Exceptional Value. Test suite only, never
published as a target. It exercises the contract the way a real mapper must: it accounts for
every value, never writes a rejected value as a value, and follows its Exceptional Value table and the mapping file."""
from __future__ import annotations

import csv
import io

from sdc_mapper import (CARRIED, DECLARED, EV_CARRIED, LOOKUP, NOT_REPRESENTABLE, EVRule, MapResult, ValueOutcome)
from sdcreader.record import EXCEPTIONAL_VALUES


class CSVMapper:
    name = "csv"
    target = "Reference CSV"
    target_version = "1"
    package = "sdc-mapper-tests"
    version = "4.0.0"
    #: CSV has a column for the code, so every Exceptional Value is carried as text by default.
    ev_table = {code: EVRule(EV_CARRIED, target_code=code) for code in EXCEPTIONAL_VALUES}

    def map(self, record, mapping):
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["position", "path", "label", "value", "units", "code", "exceptional", "note"])
        outcomes = []
        evs = {**self.ev_table, **{k: EVRule(v["class"], v.get("target_code", ""), tuple(v.get("merged_with", [])))
                                   for k, v in (mapping.exceptional_values if mapping else {}).items()}}
        for v in record.values:
            path = "/".join(v.path_labels + [v.label])
            rule = mapping.rule_for(v.ct_id, v.sdc_type) if mapping else None
            if v.exceptional:
                r = evs[v.exceptional.code]
                note = ""
                if mapping and mapping.carry_rejected and v.rejected:
                    note = "REJECTED: " + "; ".join(f"{k}={val}" for k, val in v.rejected.items() if isinstance(val, str))
                if r.cls != "lost":
                    w.writerow([v.position, path, v.label, "", "", "", r.target_code, note])
                outcomes.append(ValueOutcome(v.position, r.cls, location="exceptional", target_code=r.target_code,
                                             merged_with=list(r.merged_with)))
                continue
            if rule and rule.transform == "omit":
                outcomes.append(ValueOutcome(v.position, NOT_REPRESENTABLE, note=rule.reason or ""))
                continue
            value = v.value if not isinstance(v.value, list) else " ".join(str(i) for i in v.value)
            if rule and rule.transform == "code":
                value = rule.table.get(str(v.value), "")
            elif rule and rule.transform == "constant":
                value = rule.value
            units = v.units.symbol if v.units else ""
            code = v.code.iri if v.code else ""
            w.writerow([v.position, path, v.label, "" if value is None else value, units, code, "", ""])
            cls = DECLARED if rule else LOOKUP
            outcomes.append(ValueOutcome(v.position, cls, location=rule.to if rule else "value"))
        return MapResult(files={"record.csv": out.getvalue().encode("utf-8")}, outcomes=outcomes,
                         cannot_hold=["validity windows", "the bound definitions"])
