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
REVISION = "008"
EVIDENCE = ROOT / "e2e/artifacts/builder/site-studio-beta-20261005"
COLLABORATION_EVIDENCE = (
    ROOT / "e2e/artifacts/builder/site-studio-collaboration-prototype-20261005"
)
PREVIEW_EVIDENCE = ROOT / "e2e/artifacts/site-preview-transport"
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
    collaboration_report_path = COLLABORATION_EVIDENCE / "review.json"
    preview_report_path = PREVIEW_EVIDENCE / "report.json"
    wide_path = EVIDENCE / "wide-reorder-restored.png"
    compact_path = EVIDENCE / "compact-navigation-reorder.png"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    collaboration = json.loads(
        collaboration_report_path.read_text(encoding="utf-8")
    )
    preview_transport = json.loads(preview_report_path.read_text(encoding="utf-8"))
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
    collaboration_views = {
        (row.get("width"), row.get("view")): row for row in collaboration
    }
    expected_views = {
        (width, view)
        for width in (1440, 390)
        for view in ("Canvas", "Discussion", "Target description")
    }
    if set(collaboration_views) != expected_views or any(
        row.get("overflow") for row in collaboration_views.values()
    ):
        raise SystemExit("Site Studio collaboration surfaces did not pass")
    for width in (1440, 390):
        target_fields = collaboration_views[(width, "Target description")].get(
            "fields"
        ) or []
        discussion_fields = collaboration_views[(width, "Discussion")].get(
            "fields"
        ) or []
        if len(target_fields) != 6 or not all(
            str(field.get("value") or "").strip() for field in target_fields
        ):
            raise SystemExit("Target description fixture is incomplete")
        if len(discussion_fields) != 1:
            raise SystemExit("Discussion composer is unavailable")
    if len(preview_transport.get("checks") or []) != 2 or not all(
        row.get("last_good_retained")
        and (row.get("valid") or {}).get("type") == "adaos.site_preview.applied"
        and (row.get("tampered") or {}).get("type")
        == "adaos.site_preview.rejected"
        for row in preview_transport.get("checks") or []
    ):
        raise SystemExit("Static preview transport evidence did not pass")

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
    collaboration_ref = str(collaboration_report_path.resolve())
    preview_ref = str(preview_report_path.resolve())
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
                "evidence_refs": [evidence_ref, collaboration_ref, preview_ref],
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
            {
                "id": "authoring.tabs",
                "status": "passed",
                "evidence_refs": [collaboration_ref],
            },
            {
                "id": "discussion.composer",
                "status": "passed",
                "evidence_refs": [collaboration_ref],
            },
            {
                "id": "target_description.fixture",
                "status": "passed",
                "evidence_refs": [collaboration_ref],
            },
            {
                "id": "site_preview.digest_transport",
                "status": "passed",
                "evidence_refs": [preview_ref],
            },
        ],
        visual_checks=[
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 1000},
                "status": "passed",
                "evidence_ref": str(
                    (COLLABORATION_EVIDENCE / "1440-target_description.png").resolve()
                ),
            },
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": str(
                    (COLLABORATION_EVIDENCE / "390-target_description.png").resolve()
                ),
            },
        ],
        acceptance_id="acceptance:site-studio:prototype:008:agent-reviewed-20261005",
        actor="agent:codex-site-studio-beta-review",
        expected_generation=state.get("generation"),
    )
    receipt_path = EVIDENCE / "prototype-acceptance.json"
    _write_json(
        receipt_path,
        {
            "schema": "adaos.e2e.builder.prototype_acceptance_receipt.v1",
            "scope": (
                "Agent-reviewed Site Studio collaboration composition and persisted "
                "reorder beta verification under the owner's continuation directive"
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
