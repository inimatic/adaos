"""One-time evidence migration; never sends or reconstructs historical messages.

Run from the repository with its configured Core environment. The backup and
unresolved operation list are private runtime recovery data, not prompt input.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

from adaos.apps.bootstrap import init_ctx
from adaos.services.agent_context import get_ctx
from adaos.services.settings import Settings
from adaos.services import conversation_publication


def migrate(*, apply: bool = False, query_root: bool = False) -> dict:
    ctx = get_ctx()
    with ctx.sql.connect() as con:
        rows = con.execute("SELECT payload_json FROM telegram_outbound_attempts").fetchall()
        high_water = con.execute("SELECT MAX(seq) FROM conversation_messages WHERE conversation_id=?",
                                ("conv.skill.builder_skill.default",)).fetchone()[0]
    attempts = [json.loads(row[0]) for row in rows]
    attempts = [item for item in attempts if item["operation_key"].startswith("builder-notify:")]
    pending = [item for item in attempts if item["status"] not in {"delivered", "deduplicated"}]
    report = {"schema": "adaos.conversation.legacy_delivery_migration.v1",
        "conversation_high_water": high_water, "historical_backfill": False,
        "source_states": dict(Counter(item["status"] for item in attempts)),
        "unresolved_count": len(pending), "reconciled_count": 0, "sends": 0,
        "recovery_ref": None, "applied": apply}
    if not apply:
        return report
    conversation_publication.ensure_schema()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recovery = Path(ctx.paths.base_dir()) / "recovery" / "conversation-output" / stamp
    recovery.mkdir(parents=True, exist_ok=False)
    source = Path(ctx.paths.base_dir()) / "state/router/builder-deliveries"
    if source.is_dir():
        shutil.copytree(source, recovery / "builder-deliveries")
    with ctx.sql.connect() as con, sqlite3.connect(recovery / "before.sqlite3") as backup:
        con.backup(backup)
    resolved = []
    if query_root:
        from adaos.services import telegram_delivery
        from adaos.services.conversation_delivery import query_receipt
        from adaos.services.root.client import RootHttpError
        for attempt in pending[:20]:  # A bounded operator batch, never a background scan.
            try:
                response = query_receipt(attempt)
                if response is None:
                    break
                if not response.get("receipt"):
                    continue
                receipt = telegram_delivery.validate_receipt(response["receipt"])
                if (receipt["operation_key"] != attempt["operation_key"]
                        or receipt["delivery_attempt_id"] != attempt["attempt_id"]
                        or not receipt["delivered"] or not receipt["external_message_ids"]):
                    continue
                telegram_delivery.complete_outbound(attempt["attempt_id"], delivered=True, receipt=receipt)
                resolved.append(attempt["attempt_id"])
            except RootHttpError as exc:
                if exc.status_code in {401, 403, 405, 500, 502, 503, 504}:
                    break
            except (ValueError, KeyError, PermissionError):
                continue
    unresolved = [item for item in pending if item["attempt_id"] not in resolved]
    report.update(reconciled_count=len(resolved), unresolved_count=len(unresolved), recovery_ref=str(recovery))
    (recovery / "unresolved.json").write_text(json.dumps(unresolved, ensure_ascii=False, indent=2), encoding="utf-8")
    (recovery / "migration.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with ctx.sql.connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS conversation_output_migrations (
            migration_id TEXT PRIMARY KEY, report_json TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
        con.execute("INSERT OR REPLACE INTO conversation_output_migrations VALUES(?,?,?)",
                    ("builder-json-receipts-v1", json.dumps(report), stamp))
        con.commit()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--query-root", action="store_true")
    args = parser.parse_args()
    init_ctx(Settings.from_sources())
    print(json.dumps(migrate(apply=args.apply, query_root=args.query_root), indent=2))
