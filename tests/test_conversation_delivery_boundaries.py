"""Architectural regression checks for the incident's original failure domain."""
import ast
from pathlib import Path


def test_router_has_no_builder_delivery_owner_or_history_repair():
    root = Path(__file__).resolve().parents[1] / "src/adaos/services/router/service.py"
    source = root.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "builder_chat_notifications" not in (node.module or "")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in {"_mirror_builder_transcript", "_reconcile_builder_transcript",
                                    "_builder_transcript_reconcile_loop"}
    assert "builder-deliveries" not in source
    assert "record_builder_delivery" not in source


def test_builder_lifecycle_publishes_once_without_transport_claims():
    source = (Path(__file__).resolve().parents[1] / "src/adaos/services/builder/automation.py").read_text(encoding="utf-8")
    method = next(node for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.FunctionDef) and node.name == "_publish_lifecycle_notification")
    code = ast.get_source_segment(source, method)
    assert "conversation_publication.publish" in code
    assert '"ui.notify"' not in code
    assert "materialize_response" not in code
    assert 'receipt["status"] == "committed"' in code


def test_receipt_reconciliation_uses_hub_identity_not_root_administration(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from adaos.services.agent_context import get_ctx
    from adaos.services.conversation_delivery import query_receipt
    from adaos.services.root.client import RootHttpClient
    monkeypatch.setattr(get_ctx(), "config", SimpleNamespace(subnet_id="owned"))
    calls = []
    absent = {"schema": "adaos.telegram.receipt_status.v1", "ok": True,
              "found": False, "receipt": None, "status": "not_found"}
    monkeypatch.setattr(RootHttpClient, "from_hub_config", lambda *a, **kw:
        SimpleNamespace(request=lambda *a, **kw: calls.append((a, kw)) or absent))
    with pytest.raises(PermissionError, match="another hub"):
        query_receipt({"hub_id": "foreign", "operation_key": "secret"})
    assert calls == []
    assert query_receipt({"hub_id": "owned", "operation_key": "exact"}) == absent
    args, kwargs = calls[0]
    assert args == ("POST", "/v1/hub/communication/telegram/receipt/status")
    assert kwargs == {"json": {"operation_key": "exact"}, "timeout": 5}
