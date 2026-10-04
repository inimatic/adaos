"""Accept the exact Site Studio Prototype after its retained beta review."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.sdk.builder import workflow


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = "site_studio_test_20261003_e2eadc45d49732_26215830"
REVISION = "006"
EVIDENCE = ROOT / "e2e/artifacts/builder/site-studio-beta-20261005"
BASELINE_ORDER = [
    "header",
    "hero",
    "solutions",
    "platform",
    "applications",
    "network",
    "university",
    "pricing",
    "download",
    "footer",
]


def main() -> None:
    load_dotenv(ROOT / ".env")
    report_path = EVIDENCE / "review.json"
    wide_path = EVIDENCE / "wide-reorder-restored.png"
    compact_path = EVIDENCE / "compact-navigation-reorder.png"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    required_checks = {
        "reorder-affordances",
        "button-reorder-persists",
        "keyboard-reorder-persists",
        "pointer-reorder-persists",
        "original-order-restored",
        "compact-reorder-affordances",
    }
    passed = {
        str(check.get("id"))
        for check in report.get("checks") or []
        if check.get("status") == "passed"
    }
    if (
        report.get("status") != "passed"
        or report.get("errors")
        or report.get("initial_order") != BASELINE_ORDER
        or report.get("final_order") != BASELINE_ORDER
        or not required_checks.issubset(passed)
    ):
        raise SystemExit("Site Studio beta behavior evidence did not pass")
    if not wide_path.is_file() or not compact_path.is_file():
        raise SystemExit("wide and compact Site Studio screenshots are required")

    init_ctx(Settings.from_sources())
    state = workflow.get_state("scenario", SCENARIO)
    prototype = state.get("prototype") or {}
    if prototype.get("head_revision") != REVISION:
        raise SystemExit(
            f"current Prototype is not revision {REVISION}: "
            f"{prototype.get('head_revision')!r}"
        )
    existing = prototype.get("acceptance") or {}
    if existing.get("revision") == REVISION:
        print(
            json.dumps(
                {"ok": True, "duplicate": True, "revision": REVISION},
                ensure_ascii=False,
            )
        )
        return

    evidence_ref = str(report_path.resolve())
    result = workflow.accept_prototype(
        "scenario",
        SCENARIO,
        reviewer={
            "id": "agent:codex-site-studio-beta-review",
            "kind": "agent",
            "delegated_by": "conversation:user:site-studio-beta-20261005",
        },
        behavior_checks=[
            {
                "id": "render.ready",
                "status": "passed",
                "evidence_refs": [evidence_ref],
            },
            {
                "id": "list.move",
                "status": "passed",
                "evidence_refs": [evidence_ref],
            },
            {
                "id": "list.move.alternative",
                "status": "passed",
                "evidence_refs": [evidence_ref],
            },
        ],
        visual_checks=[
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 1000},
                "status": "passed",
                "evidence_ref": str(wide_path.resolve()),
            },
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": str(compact_path.resolve()),
            },
        ],
        acceptance_id="acceptance:site-studio:prototype:006:user-reviewed-20261005",
        actor="codex.record_explicit_user_acceptance",
        expected_generation=state.get("generation"),
    )
    receipt_path = EVIDENCE / "prototype-acceptance.json"
    _write_json(
        receipt_path,
        {
            "schema": "adaos.e2e.builder.prototype_acceptance_receipt.v1",
            "scope": (
                "User-reviewed Site Studio composition and delegated persisted "
                "reorder beta verification"
            ),
            "known_beta_debt": ["The central preview canvas remains tight."],
            "result": result,
        },
    )
    print(
        json.dumps(
            {
                "ok": True,
                "revision": result["acceptance"]["revision"],
                "acceptance_digest": result["acceptance"]["digest"],
                "generation": result["workflow"]["generation"],
                "receipt": str(receipt_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
