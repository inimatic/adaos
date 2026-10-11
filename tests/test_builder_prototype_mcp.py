import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from adaos.sdk.builder import prototype
from adaos.services.builder import prototype_mcp as mcp
from adaos.services.builder_context import digest


def messages():
    return [{"role": "system", "content": json.dumps({"stable_builder_context": {
        "semantic_invariants": prototype.semantic_generation_guidance(), "other_authority": "keep"}})},
        {"role": "user", "content": json.dumps({"builder_request": {"instruction": "История закупок",
            "prototype_brief": {"required_references": ["req:1"], "facts": ["currency RUB"]}}})}]


def test_published_snapshot_matches_core_and_is_an_independent_value():
    snapshot = mcp.library_snapshot()
    path = Path(__file__).parents[1] / "src/adaos/integrations/adaos-backend/backend/prototype-context-library.json"
    assert snapshot == json.loads(path.read_text(encoding="utf-8"))
    assert len(snapshot["fragments"]) == 31
    assert snapshot["library_digest"] == digest(prototype.semantic_generation_guidance())
    for fragment in snapshot["fragments"]:
        assert fragment["content_digest"] == digest(fragment["content"])
    snapshot["fragments"][0]["content"] = "mutated"
    assert mcp.library_snapshot()["fragments"][0]["content"] != "mutated"


@pytest.mark.parametrize("available", [True, False])
def test_indexed_delivery_preserves_task_authority_and_does_not_mutate(available):
    original = messages()
    before = copy.deepcopy(original)
    projected, receipt = mcp.indexed_messages(original, available=available)
    assert original == before
    assert projected[1] == original[1]
    stable = json.loads(projected[0]["content"])["stable_builder_context"]
    assert stable["other_authority"] == "keep"
    assert stable["semantic_invariants"] == {k: v for k, v in prototype.semantic_generation_guidance().items()
        if k in {"contract", "stage", "limits"}}
    assert receipt["input_sha256"] == digest(original)
    assert receipt["output_sha256"] == digest(projected)
    assert stable["prototype_retrieval"]["available"] is available
    assert bool(stable["prototype_retrieval"]["tools"]) is available
    assert len(stable["prototype_retrieval"]["dimensions"]) == 31


def test_unknown_platform_authority_is_not_silently_discarded():
    source = messages()
    body = json.loads(source[0]["content"])
    body["stable_builder_context"]["semantic_invariants"]["new_authority"] = "keep"
    source[0]["content"] = json.dumps(body)
    with pytest.raises(ValueError, match="unknown or modified"):
        mcp.indexed_messages(source, available=True)


def test_empty_or_wrong_contract_is_not_indexed():
    with pytest.raises(ValueError):
        mcp.indexed_messages([], available=True)
    with pytest.raises(ValueError):
        mcp.indexed_messages([{"content": []}], available=True)


def test_credential_redaction_is_recursive_and_non_mutating():
    value = {"tools": [{"authorization": "PRIVATE", "name": mcp.READ_TOOL}],
        "nested": {"api_key": "SECRET", "access_token": "SECRET", "password": "SECRET",
            "session_id": "LEASE", "Mcp-Session-Id": "LEASE", "mcp_session_id": "LEASE"}, "messages": ["keep"]}
    before = copy.deepcopy(value)
    redacted = prototype.redact_generation_credentials(value)
    assert value == before
    assert all(secret not in json.dumps(redacted) for secret in ("PRIVATE", "SECRET", "LEASE"))
    assert redacted["tools"][0]["name"] == mcp.READ_TOOL
    assert redacted["messages"] == ["keep"]


def test_disabled_switch_never_contacts_root(monkeypatch):
    monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "0")
    with patch.object(mcp, "issue_protocol", side_effect=AssertionError("network")):
        assert prototype.mcp_generation_options() == {}
        assert prototype.mcp_retrieval_index() is None


def test_enabled_switch_preserves_native_descriptor_and_auto_policy(monkeypatch):
    monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "1")
    descriptor = {"type": "mcp", "authorization": "PRIVATE"}
    with patch.object(mcp, "issue_protocol", return_value={"tools": [descriptor]}):
        assert prototype.mcp_generation_options() == {"tools": [descriptor], "tool_choice": "auto", "max_tool_calls": 40}
    monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "yes")
    with pytest.raises(ValueError):
        prototype.mcp_generation_options()


def test_public_sdk_exports_the_mcp_facade():
    assert {"mcp_generation_options", "mcp_retrieval_index", "redact_generation_credentials"}.issubset(prototype.__all__)


@pytest.mark.parametrize("available", [True, False])
def test_new_full_and_indexed_arms_have_identical_retained_authority(available):
    from adaos.e2e.prototype_context_ablation import compile_experiment_messages
    options = {"text": {"format": {"schema": {"const": "same schema"}}}}
    full, full_receipt = compile_experiment_messages(messages(), strategy="full", generation_options=options,
        context_mode="indexed", mcp_available=available)
    reduced, reduced_receipt = compile_experiment_messages(messages(), strategy="adaptive", generation_options=options,
        context_mode="indexed", mcp_available=available)
    assert full_receipt["core_sha256"] == reduced_receipt["core_sha256"]
    assert full[1] == reduced[1]
    assert full_receipt["estimated_tokens"] > reduced_receipt["estimated_tokens"]
    assert full_receipt["omitted"] == []
    assert reduced_receipt["strategy"] == "adaptive-indexed"


def test_native_trace_counts_provider_calls_not_discovery_or_host_checks():
    from adaos.e2e.prototype_mcp_eval import mcp_trace
    item = {"type": "mcp_call", "name": mcp.OPENAI_TOOLS[1], "arguments": json.dumps({"fragment_id": "view_roles"}),
        "output": json.dumps({"ok": True, "result": {"fragment": {"id": "view_roles"}, "library_digest": "digest"}}),
        "status": "completed", "error": None}
    trace = mcp_trace({"job_id": "job", "response": {"output": [{"type": "mcp_list_tools"}, item, item]}})
    assert trace["call_count"] == 2 and trace["discovery_count"] == 1
    assert trace["repeated_exact_calls"] == 1 and trace["errors"] == 0
    assert trace["calls"][0]["fragment_id"] == "view_roles"


def test_installed_builder_tolerates_old_core_only_with_mcp_disabled(monkeypatch):
    import ast
    import inspect
    import os
    import re
    from types import SimpleNamespace
    from typing import Any, Mapping

    root = Path(__file__).parents[1]
    handlers = [root / ".adaos/workspace/skills/builder_skill/handlers/main.py",
        root / ".adaos/dev/sn_6acf0c01/skills/builder_skill/handlers/main.py"]
    if not all(handler.exists() for handler in handlers):
        pytest.skip("Local rolling-version regression requires installed Builder copies")
    fragments = []
    for handler in handlers:
        tree = ast.parse(handler.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
            "_prototype_mcp_sdk_call", "_redact_generation_credentials", "_development_profile_kwargs"}]
        assert len(functions) == 3
        fragments.append([ast.dump(node) for node in functions])
        namespace = {"sdk_builder_prototype": SimpleNamespace(), "Any": Any, "Mapping": Mapping,
            "os": os, "re": re, "inspect": inspect, "_builder_llm_job_timeout_s": lambda: 960}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(handler), "exec"), namespace)
        monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "0")
        assert namespace["_prototype_mcp_sdk_call"]("mcp_retrieval_index") is None
        def supported(*, profile_scope=None, provider_timeout_s=None, **kwargs):
            pass
        assert namespace["_development_profile_kwargs"](supported) == {"profile_scope": "development", "provider_timeout_s": 900}
        for invalid in ("1", "typo"):
            monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", invalid)
            with pytest.raises(ValueError):
                namespace["_development_profile_kwargs"](supported)
        secret = {"tools": [{"authorization": "PRIVATE"}], "nested": {"access_token": "SECRET", "session_id": "LEASE"}}
        assert "PRIVATE" not in json.dumps(namespace["_redact_generation_credentials"](secret))
        assert "SECRET" not in json.dumps(namespace["_redact_generation_credentials"](secret))
        assert "LEASE" not in json.dumps(namespace["_redact_generation_credentials"](secret))
        assert secret["tools"][0]["authorization"] == "PRIVATE"
        options = {"tools": [{"type": "mcp", "authorization": "PRIVATE"}], "tool_choice": "auto", "max_tool_calls": 40}
        namespace["sdk_builder_prototype"] = SimpleNamespace(mcp_generation_options=lambda: copy.deepcopy(options))
        monkeypatch.setenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "1")
        assert namespace["_development_profile_kwargs"](supported)["tools"] == options["tools"]
        def unsupported(*, profile_scope=None):
            pass
        with pytest.raises(ValueError, match="cannot deliver"):
            namespace["_development_profile_kwargs"](unsupported)
    assert fragments[0] == fragments[1]


def test_native_trace_does_not_mistake_a_completed_tool_error_for_success():
    from adaos.e2e.prototype_mcp_eval import mcp_trace
    item = {"type": "mcp_call", "name": mcp.OPENAI_TOOLS[1], "arguments": "{}",
        "output": json.dumps({"ok": False, "error": "prototype_guidance_digest_mismatch"}),
        "status": "completed", "error": None}
    trace = mcp_trace({"response": {"output": [item]}})
    assert trace["errors"] == 1 and trace["calls"][0]["tool_result_error"] is True


@pytest.mark.parametrize("fault", [None, "extra_capability", "wrong_discovery", "stale_library", "untrusted_url",
    "wrong_authorization", "wrong_approval", "unexpected_port"])
def test_protocol_admission_is_scoped_pinned_and_cached(monkeypatch, fault):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from adaos.sdk.llm import llm_client
    from adaos.services.root import client
    requests = []
    library_digest = mcp.library_snapshot()["library_digest"]
    descriptor = {"type": "mcp", "server_label": mcp.SERVER_LABEL, "allowed_tools": list(mcp.OPENAI_TOOLS),
        "server_url": "https://mcp.api.inimatic.com/v1/root/mcp", "authorization": "PRIVATE", "require_approval": "never"}
    if fault == "untrusted_url":
        descriptor["server_url"] = "http://private.internal/v1/root/mcp"
    elif fault == "unexpected_port":
        descriptor["server_url"] = "https://mcp.api.inimatic.com:8443/v1/root/mcp"
    elif fault == "wrong_authorization":
        descriptor["authorization"] = "UNSCOPED"
    elif fault == "wrong_approval":
        descriptor["require_approval"] = "always"

    class Http:
        base_url = "https://api.inimatic.com"
        verify = True
        cert = None

        def request(self, method, path, **kwargs):
            requests.append((method, path, kwargs))
            if path.endswith("openai-tool"):
                return {"openai": {"responses_tool": copy.deepcopy(descriptor)}}
            if path == "/v1/root/mcp":
                names = [] if fault == "wrong_discovery" else mcp.OPENAI_TOOLS
                return {"result": {"tools": [{"name": name} for name in names]}}
            args = kwargs["json"]
            if args["tool_id"] == "hub.issue_mcp_session":
                assert args["arguments"]["capability_profile"] == "BuilderPrototypeRead"
                assert args["arguments"]["capabilities"] == [mcp.CAPABILITY]
                assert "openai" in args["arguments"]["audience"]
                caps = [mcp.CAPABILITY, "*"] if fault == "extra_capability" else [mcp.CAPABILITY]
                value = {"session_id": "session", "access_token": "PRIVATE", "capabilities": caps,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
            else:
                assert args["tool_id"] == mcp.SEARCH_TOOL
                value = {"library_digest": "stale" if fault == "stale_library" else library_digest,
                    "items": [{"id": "view_roles"}]}
            return {"response": {"result": value}}

    monkeypatch.setattr(llm_client, "_root_http_client", lambda: (Http(), SimpleNamespace(subnet_id="subnet")))
    monkeypatch.setattr(llm_client, "_current_ctx", lambda: None)
    monkeypatch.setattr(llm_client, "_identity_headers", lambda ctx, cfg: {"X-AdaOS-Subnet-Id": "subnet"})

    def factory(**kwargs):
        assert kwargs["cert"] is None, "Provider admission must not inherit certificate authority"
        assert kwargs["default_headers"]["Authorization"] == "Bearer PRIVATE"
        return Http()

    monkeypatch.setattr(client, "RootHttpClient", factory)
    monkeypatch.setattr(mcp, "_LEASES", {})
    if fault:
        with pytest.raises(RuntimeError):
            mcp.issue_protocol()
        assert not mcp._LEASES
    else:
        lease = mcp.issue_protocol()
        assert lease["library_digest"] == library_digest
        lease["tools"][0]["authorization"] = "mutated"
        assert mcp.issue_protocol()["tools"][0]["authorization"] == "PRIVATE"
        assert sum(kwargs.get("json", {}).get("tool_id") == "hub.issue_mcp_session" for _, _, kwargs in requests) == 1
