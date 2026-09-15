"""Independent synthetic qualification of a completed owned TEST's actual SQL chain."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from dotenv import load_dotenv
import yaml

from adaos.e2e.builder import _write_json
from adaos.services.applications.data_lifecycle import declared_databases
from adaos.services.applications.sqlite_data_transition import initialize_sqlite_schema, SQLiteDataTransition


def schema(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", type=Path, required=True)
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    root = Path.cwd().resolve()
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    identifier = created["id"]
    output = args.output.resolve()
    admitted = json.loads(args.start.read_text(encoding="utf-8"))["result"]["session"]
    session = json.loads((root / f".adaos/state/builder/automation/scenario.{identifier}.json").read_text(encoding="utf-8"))
    if (os.getenv("ENV_TYPE") != "dev" or not identifier.startswith("workbench_test_")
            or created["result"]["project"]["created_by"] != "builder.user"
            or admitted["object_id"] != identifier or session["status"] != "completed"
            or session["current_task_id"] != admitted["current_task_id"]
            or output.exists() or not output.is_relative_to(root / "e2e/artifacts/builder")):
        parser.error("Exact completed owned TEST task and new confined output required")
    manifest = root / f".adaos/dev/sn_6acf0c01/skills/{identifier}_skill/skill.yaml"
    raw = manifest.read_bytes()
    chains = declared_databases(yaml.safe_load(raw.decode("utf-8")))
    report = {"scope": "Actual Core executor on synthetic schema fixtures, not installed-data or full behavioral acceptance",
              "scenario": identifier, "task": session["current_task_id"],
              "manifest_sha256": hashlib.sha256(raw).hexdigest(), "databases": [], "passed": False}
    try:
        assert chains, "A declared lifecycle is required for this migration qualification"
        (root / ".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="migration-proof-", dir=root / ".tmp") as temporary:
            for index, (name, chain) in enumerate(chains.items()):
                private = Path(temporary) / str(index)
                private.mkdir()
                sample = {"path": name, "checks": [], "passed": False}
                report["databases"].append(sample)
                fresh = private / "fresh.db"
                result = initialize_sqlite_schema(fresh, chain)
                assert result["applied_versions"] == [item.version for item in sorted(chain, key=lambda item: item.version)]
                sample["checks"].append("fresh-install-real-chain")
                before = fresh.read_bytes()
                assert initialize_sqlite_schema(fresh, chain)["applied_versions"] == []
                assert fresh.read_bytes() == before
                sample["checks"].append("exact-repeat-no-schema-or-ledger-mutation")
                expected_schema = schema(fresh)
                transition = SQLiteDataTransition(private)
                ordered = sorted(chain, key=lambda item: item.version)
                for mode in ("declared-prior-version", "synthetic-legacy-without-ledger"):
                    base = private / (mode + ".db")
                    initialize_sqlite_schema(base, ordered[:1])
                    if mode == "synthetic-legacy-without-ledger":
                        # This is a newly created disposable fixture, never an installed store.
                        with closing(sqlite3.connect(base)) as connection:
                            connection.execute("DROP TABLE adaos_schema_migrations")
                            connection.commit()
                    original = base.read_bytes()
                    snapshot_path = private / (mode + "-snapshot.db")
                    snapshot = transition.snapshot(base, snapshot_path, operation_key=mode)
                    migrated = private / (mode + "-migrated.db")
                    transition.migrate(snapshot_path, migrated, snapshot_digest=snapshot["digest"], migrations=chain, operation_key=mode)
                    assert base.read_bytes() == original
                    assert schema(migrated) == expected_schema
                    assert initialize_sqlite_schema(migrated, chain)["applied_versions"] == []
                    sample["checks"].append(mode + "-fenced-copy-and-runtime-reopen")
                sample["passed"] = True
        report["passed"] = all(sample["passed"] for sample in report["databases"])
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        _write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
