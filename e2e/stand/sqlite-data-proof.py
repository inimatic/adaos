"""Capture/compare a private SQLite baseline without exporting record contents."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read(path: Path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


def compare(baseline: Path, target: Path) -> dict:
    tables = []
    with read(baseline) as source, read(target) as current:
        source.execute("BEGIN")
        current.execute("BEGIN")
        names = source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        for (name,) in names:
            columns = [row[1] for row in source.execute(f"PRAGMA table_info({identifier(name)})")]
            projection = ",".join(map(identifier, columns))
            query = f"SELECT {projection} FROM {identifier(name)}"
            expected = Counter(source.execute(query).fetchall())
            observed = Counter(current.execute(query).fetchall())
            missing = sum((expected - observed).values())
            tables.append({"table": name, "baseline_records": sum(expected.values()),
                           "current_records": sum(observed.values()), "missing_or_changed": missing})
    return {"ok": all(item["missing_or_changed"] == 0 for item in tables), "tables": tables}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["capture", "verify"])
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    args = parser.parse_args()
    baseline = args.baseline.resolve()
    private = (Path(__file__).resolve().parents[2] / ".adaos/recovery").resolve()
    if not baseline.is_relative_to(private):
        parser.error("Baseline must stay in the private .adaos/recovery directory")
    if args.mode == "capture":
        baseline.parent.mkdir(parents=True, exist_ok=True)
        with baseline.open("xb"):
            pass
        with read(args.database) as source, closing(sqlite3.connect(baseline)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Baseline integrity check failed")
    result = compare(baseline, args.database)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
