"""Read-only live Hub receipt qualification; no send, replay or administrator token."""
from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from adaos.apps.bootstrap import init_ctx
from adaos.services.agent_context import get_ctx
from adaos.services import conversation_delivery
from adaos.services.root.client import RootHttpClient
from adaos.services.settings import Settings


def qualify(*, legacy_limit: int = 0) -> dict:
    if not 0 <= legacy_limit <= 100:
        raise ValueError("legacy_limit must be between 0 and 100")
    ctx = get_ctx()
    client = RootHttpClient.from_hub_config(ctx.config, fallback_base_url=ctx.settings.api_base)
    if client is None:
        raise RuntimeError("existing Hub mTLS identity unavailable")
    operation = "qualification:receipt:" + uuid.uuid4().hex
    report = {
        "schema": "adaos.conversation.receipt_qualification.v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": client.base_url,
        "method": "POST",
        "path": "/v1/hub/communication/telegram/receipt/status",
        "root_administrator_token_used": False,
        "messages_sent": 0,
        "historical_replay": False,
        "receipts_applied": 0,
        "ok": True,
    }
    with client.session(timeout=10) as session:
        def call(body):
            return session.client.post(report["path"], json=body)
        response = call({"operation_key": operation})
        assert response.status_code == 200, f"receipt API returned {response.status_code}"
        body = response.json()
        expected = {"schema": "adaos.telegram.receipt_status.v1", "ok": True,
                    "found": False, "receipt": None, "status": "not_found"}
        assert body == expected, "receipt miss did not match the provider contract"
        report["authenticated_miss"] = {"http_status": response.status_code, "body": body}
        # Scope is certificate-derived; no alternate destination can be supplied.
        denied = call({"operation_key": operation, "hub_id": "qualification:foreign"})
        assert denied.status_code == 403, "foreign Hub scope was admitted"
        invalid = call({"operation_key": ""})
        assert invalid.status_code == 400, "empty operation key was admitted"
        report.update(hub_scope_mismatch=denied.status_code, invalid_operation=invalid.status_code)
    if legacy_limit:
        with ctx.sql.connect() as con:
            total = con.execute("SELECT COUNT(*) FROM telegram_outbound_attempts "
                "WHERE operation_key LIKE 'builder-notify:%' AND status NOT IN ('delivered','deduplicated')").fetchone()[0]
            rows = con.execute("SELECT payload_json FROM telegram_outbound_attempts "
                "WHERE operation_key LIKE 'builder-notify:%' AND status NOT IN ('delivered','deduplicated') "
                "ORDER BY claimed_at LIMIT ?", (legacy_limit,)).fetchall()
        counts = {}
        proven = 0
        for row in rows:
            result = conversation_delivery.query_receipt(json.loads(row[0]))
            status = str((result or {}).get("status") or "unavailable")
            counts[status] = counts.get(status, 0) + 1
            proof = (result or {}).get("receipt") or {}
            proven += int(proof.get("delivered") is True and bool(proof.get("external_message_ids")))
            if not result or not result.get("ok"):
                report["ok"] = False
                break  # An outage is not a reason to hammer Root with historical reads.
            time.sleep(0.25)
        report["legacy_audit"] = {"unproven_local": total, "checked": sum(counts.values()),
                                  "complete": sum(counts.values()) == min(total, legacy_limit),
                                  "root_results": counts, "exact_delivery_proofs": proven}
    report["delivery_runtime"] = conversation_delivery.runtime_snapshot()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-limit", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    init_ctx(Settings.from_sources())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    result = qualify(legacy_limit=args.legacy_limit)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["ok"] else 1)
