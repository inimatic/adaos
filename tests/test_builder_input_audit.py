import importlib.util
import hashlib
import json
from pathlib import Path


def load_audit():
    path = Path(__file__).resolve().parents[1] / "e2e/stand/inspect-builder-automation-context.py"
    spec = importlib.util.spec_from_file_location("builder_input_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inspect_admitted_input


def test_retained_audit_reports_gaps_without_exposing_credentials(tmp_path):
    packet = {"task_id": "task.test", "brief": "First\nFinal requirement", "root_mcp": {"token": "SECRET"},
              "context_packet": {"artifacts": {"prototype": {"acceptance": {
                  "acceptance_id": "accepted:1", "revision": "002", "webui_digest": "sha256:test"}}}}}
    handoff = {"acceptance_id": "accepted:1", "resources": [{"bundle": {"seed": []}}]}
    for name, value in (("packet.json", packet), ("prototype-resource-handoff.json", handoff)):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "task.md").write_bytes(b"# Task\r\nFirst\r\nFinal requirement\r\n")
    audit = load_audit()
    result = audit(tmp_path)
    assert result["checks"]["full_brief_in_task"]
    assert result["checks"]["handoff_acceptance_matches"]
    assert result["checks"]["production_seeds_empty"]
    assert not result["checks"]["create_initialization_contract"]
    assert not result["checks"]["verification_ownership_explicit"]
    assert "SECRET" not in json.dumps(result)
    assert result["manual_review_required"]
    (tmp_path / "task.md").write_text("First", encoding="utf-8")
    assert not audit(tmp_path)["checks"]["full_brief_in_task"]


def test_audit_checks_actual_attempt_not_only_authoring_task(tmp_path):
    brief = "Full requirement: \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430"
    packet = {"task_id": "task.input", "brief": brief, "iteration_instruction": "Only repair the test"}
    (tmp_path / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
    (tmp_path / "task.md").write_text(brief, encoding="utf-8")
    attempts = tmp_path / "model-attempts"
    attempts.mkdir()
    raw = (brief + "\nOnly repair the test").encode("utf-8")
    (attempts / "001.prompt.md").write_bytes(raw)
    (attempts / "001.prompt.json").write_text(json.dumps({
        "task_id": "task.input", "prompt_sha256": hashlib.sha256(raw).hexdigest(), "prompt_bytes": len(raw),
    }), encoding="utf-8")
    audit = load_audit()
    checks = audit(tmp_path)["model_attempts"][0]["checks"]
    assert checks["receipt_matches"] and checks["full_brief_present"] and checks["current_iteration_present"]
    (attempts / "001.prompt.md").write_text("Truncated or replaced prompt", encoding="utf-8")
    result = audit(tmp_path)
    assert result["checks"]["full_brief_in_task"]
    assert not any(result["model_attempts"][0]["checks"].values())
