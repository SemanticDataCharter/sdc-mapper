"""The log: one JSON Lines entry per call, whatever the outcome, anchored and chained.

Each entry carries the SHA-256 of the input record, the model schema, the mapping file and each output, so it can be
checked against the artifacts it names, and the SHA-256 of the previous entry, so a deleted or edited entry shows.
No values: identifiers, classes, codes and hashes only. A failure reason is not logged either, because a validator's
reason can quote the rejected value.

Files are `mapper-YYYY-MM-DD.jsonl` (UTC) in the log directory; the chain runs across files. Writes are serialized
within a process by a lock and across processes by an exclusive file lock on `mapper.lock`.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

GENESIS = "0" * 64
LOG_VERSION = 1


def entry_hash(entry: dict) -> str:
    """The SHA-256 of an entry's canonical JSON, without its own `hash` field."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class Log:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("mapper-*.jsonl"))

    def _last_hash(self) -> str:
        for f in reversed(self._files()):
            lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lines:
                return json.loads(lines[-1])["hash"]
        return GENESIS

    def append(self, entry: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self._lock, open(self.dir / "mapper.lock", "a") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                full = {"log_version": LOG_VERSION, "time": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), **entry,
                        "previous": self._last_hash()}
                full["hash"] = entry_hash(full)
                path = self.dir / f"mapper-{now.strftime('%Y-%m-%d')}.jsonl"
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(full, sort_keys=True, ensure_ascii=False) + "\n")
                return full
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

    def entries(self, since: datetime | None = None) -> list[dict]:
        out = []
        for f in self._files():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    if since is None or e["time"] >= since.strftime("%Y-%m-%dT%H:%M:%S.%fZ"):
                        out.append(e)
        return out

    def verify(self) -> tuple[bool, str]:
        """Walk the chain from the first entry: every hash recomputes and every entry names its predecessor."""
        prev = GENESIS
        n = 0
        for e in self.entries():
            n += 1
            if e.get("previous") != prev:
                return False, f"entry {n} ({e.get('time')}) does not follow the entry before it"
            if entry_hash(e) != e.get("hash"):
                return False, f"entry {n} ({e.get('time')}) has been altered"
            prev = e["hash"]
        return True, f"{n} entries, chain intact"

    def summary(self, days: float = 7, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        rows = self.entries(since=now - timedelta(days=days))
        by_target: dict[str, dict] = {}
        for e in rows:
            t = by_target.setdefault(e.get("target", {}).get("name", "?"), {
                "calls": 0, "outcomes": Counter(), "values": Counter(), "exceptional": Counter(), "models": set(),
                "mappings": set()})
            t["calls"] += 1
            t["outcomes"][e.get("outcome", "?")] += 1
            for k, n in (e.get("counts") or {}).items():
                if k == "exceptional":
                    t["exceptional"].update(n)
                else:
                    t["values"][k] += n
            if e.get("model", {}).get("ct_id"):
                t["models"].add(e["model"]["ct_id"])
            if (e.get("mapping") or {}).get("sha256"):
                t["mappings"].add(e["mapping"]["sha256"])
        return {"days": days, "calls": len(rows),
                "targets": {k: {"calls": v["calls"], "outcomes": dict(v["outcomes"]), "values": {k: n for k, n in v["values"].items() if n},
                                "exceptional": {k: n for k, n in v["exceptional"].items() if n},
                                "models": sorted(v["models"]),
                                "mappings": sorted(v["mappings"])} for k, v in by_target.items()}}
