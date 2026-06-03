from __future__ import annotations

import io
import importlib
import json
import sys
import types
from pathlib import Path

from typer.testing import CliRunner

if "y_py" not in sys.modules:
    try:
        importlib.import_module("y_py")
    except ModuleNotFoundError:
        sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    try:
        importlib.import_module("ypy_websocket")
    except ModuleNotFoundError:
        ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
        sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
        sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.apps.cli.commands import dev as dev_cmd
from adaos.services.root_mcp import codex_bridge as bridge_mod


class _FakeRootMcpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def foundation(self) -> dict:
        self.calls.append(("foundation", "", {}))
        return {"foundation": {"id": "root-mcp-foundation"}}

    def get_adaos_dev_architecture_catalog(self) -> dict:
        self.calls.append(("get_adaos_dev_architecture_catalog", "", {}))
        return {"descriptor": {"payload": {"available": True, "page_count": 3}}}

    def get_adaos_dev_sdk_metadata(self, *, level: str = "std") -> dict:
        self.calls.append(("get_adaos_dev_sdk_metadata", level, {}))
        return {"descriptor": {"payload": {"meta": {"generated_at": "2026-01-01T00:00:00+00:00"}, "level": level}}}

    def get_adaos_dev_template_catalog(self) -> dict:
        self.calls.append(("get_adaos_dev_template_catalog", "", {}))
        return {"descriptor": {"payload": {"skills": ["skill_default"], "scenarios": ["scenario_default"]}}}

    def get_adaos_dev_public_skill_registry(self) -> dict:
        self.calls.append(("get_adaos_dev_public_skill_registry", "", {}))
        return {"descriptor": {"payload": {"kind": "skills", "item_count": 2}}}

    def get_adaos_dev_public_scenario_registry(self) -> dict:
        self.calls.append(("get_adaos_dev_public_scenario_registry", "", {}))
        return {"descriptor": {"payload": {"kind": "scenarios", "item_count": 2}}}

    def get_adaos_dev_named_entity_registry(self, *, webspace_id: str | None = None, kind: str | None = None) -> dict:
        self.calls.append(("get_adaos_dev_named_entity_registry", webspace_id or "", {"kind": kind}))
        return {
            "descriptor": {
                "payload": {
                    "version": 1,
                    "webspace_id": webspace_id or "desktop",
                    "items": [{"canonical_ref": "device:browser:browser-1", "kind": kind or "device.browser"}],
                }
            }
        }

    def get_builder_context(
        self,
        *,
        webspace_id: str | None = None,
        level: str = "mini",
        request_locale: str | None = None,
        preferred_locales: list[str] | None = None,
        include_live: bool = True,
        include_hints: bool = True,
        include_payloads: bool = False,
    ) -> dict:
        self.calls.append(
            (
                "get_builder_context",
                webspace_id or "",
                {
                    "level": level,
                    "request_locale": request_locale,
                    "preferred_locales": preferred_locales or [],
                    "include_live": include_live,
                    "include_hints": include_hints,
                    "include_payloads": include_payloads,
                },
            )
        )
        return {"builder_context": {"context_id": "builder_context.v1", "webspace_id": webspace_id or "desktop"}}

    def get_nlu_authoring_context(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        kind: str | None = None,
        request_locale: str | None = None,
        preferred_locales: list[str] | None = None,
        include_live: bool = True,
        include_hints: bool = True,
    ) -> dict:
        self.calls.append(
            (
                "get_nlu_authoring_context",
                webspace_id or "",
                {
                    "target_id": target_id,
                    "kind": kind,
                    "request_locale": request_locale,
                    "preferred_locales": preferred_locales or [],
                    "include_live": include_live,
                    "include_hints": include_hints,
                },
            )
        )
        return {
            "context": {
                "plane_id": "nlu_authoring",
                "webspace_id": webspace_id or "desktop",
                "named_entities": {"items": [{"canonical_ref": "device:browser:browser-1", "kind": kind or "device.browser"}]},
            }
        }

    def check_nlu_authoring_phrase(
        self,
        text: str,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        use_rasa: bool = True,
        emit_trace: bool = False,
        request_locale: str | None = None,
        preferred_locales: list[str] | None = None,
    ) -> dict:
        self.calls.append(
            (
                "check_nlu_authoring_phrase",
                webspace_id or "",
                {
                    "text": text,
                    "target_id": target_id,
                    "use_rasa": use_rasa,
                    "emit_trace": emit_trace,
                    "request_locale": request_locale,
                    "preferred_locales": preferred_locales or [],
                },
            )
        )
        return {"check": {"ok": True, "intent": "desktop.open_weather", "text": text}}

    def get_nlu_authoring_trace(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        request_id: str | None = None,
        candidate_id: str | None = None,
        limit: int = 80,
    ) -> dict:
        self.calls.append(
            (
                "get_nlu_authoring_trace",
                webspace_id or "",
                {"target_id": target_id, "request_id": request_id, "candidate_id": candidate_id, "limit": limit},
            )
        )
        return {"ok": True, "trace": [{"request_id": request_id}], "candidates": []}

    def get_nlu_authoring_dialog_context(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        request_id: str | None = None,
        candidate_id: str | None = None,
        limit: int = 25,
    ) -> dict:
        self.calls.append(
            (
                "get_nlu_authoring_dialog_context",
                webspace_id or "",
                {"target_id": target_id, "request_id": request_id, "candidate_id": candidate_id, "limit": limit},
            )
        )
        return {"ok": True, "threads_by_request": [{"request_id": request_id}], "candidates": []}

    def get_nlu_authoring_recent_failures(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        limit: int = 50,
    ) -> dict:
        self.calls.append(("get_nlu_authoring_recent_failures", webspace_id or "", {"target_id": target_id, "limit": limit}))
        return {"ok": True, "failures": [{"reason": "no_match"}]}

    def get_desktop_registry_lookup(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        include_live: bool = True,
    ) -> dict:
        self.calls.append(("get_desktop_registry_lookup", webspace_id or "", {"target_id": target_id, "include_live": include_live}))
        return {"ok": True, "lookups": {"modal_id": [{"value": "nlu_teacher_modal"}]}}

    def describe_skill_nlu(self, skill_id: str, *, target_id: str | None = None) -> dict:
        self.calls.append(("describe_skill_nlu", skill_id, {"target_id": target_id}))
        return {"ok": True, "owner": {"type": "skill", "id": skill_id}, "nlu": {"intents": {}}}

    def describe_scenario_nlu(self, scenario_id: str, *, target_id: str | None = None) -> dict:
        self.calls.append(("describe_scenario_nlu", scenario_id, {"target_id": target_id}))
        return {"ok": True, "owner": {"type": "scenario", "id": scenario_id}, "nlu": {"intents": {}}}

    def describe_sdk_surface(self, *, target_id: str | None = None, level: str = "std") -> dict:
        self.calls.append(("describe_sdk_surface", level, {"target_id": target_id}))
        return {"ok": True, "surface_id": "adaos.sdk.describe_surface.v1"}

    def list_nlu_authoring_templates(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        include_system_actions: bool = True,
    ) -> dict:
        self.calls.append(
            (
                "list_nlu_authoring_templates",
                webspace_id or "",
                {
                    "target_id": target_id,
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "include_system_actions": include_system_actions,
                },
            )
        )
        return {"ok": True, "templates": [{"id": "tpl.test", "owner": {"type": owner_type, "id": owner_id}}]}

    def list_nlu_authoring_training_targets(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        include_system_actions: bool = True,
    ) -> dict:
        self.calls.append(
            (
                "list_nlu_authoring_training_targets",
                webspace_id or "",
                {"target_id": target_id, "include_system_actions": include_system_actions},
            )
        )
        return {"ok": True, "targets": [{"type": "skill", "id": "weather_skill"}]}

    def preview_nlu_authoring_template_patch(
        self,
        *,
        operation: str,
        target: dict,
        intent: str,
        target_id: str | None = None,
        webspace_id: str | None = None,
        text: str | None = None,
        pattern: str | None = None,
        slots: dict | None = None,
        base_fingerprint: str | None = None,
    ) -> dict:
        self.calls.append(
            (
                "preview_nlu_authoring_template_patch",
                webspace_id or "",
                {
                    "target_id": target_id,
                    "operation": operation,
                    "target": target,
                    "intent": intent,
                    "text": text,
                    "pattern": pattern,
                    "slots": slots or {},
                    "base_fingerprint": base_fingerprint,
                },
            )
        )
        return {"ok": True, "status": "ready", "normalized_patch": {"operation": operation}}

    def preview_desktop_action(
        self,
        *,
        target_id: str | None = None,
        webspace_id: str | None = None,
        action_id: str | None = None,
        intent: str | None = None,
        host_action: str | None = None,
        params: dict | None = None,
    ) -> dict:
        self.calls.append(
            (
                "preview_desktop_action",
                webspace_id or "",
                {"target_id": target_id, "action_id": action_id, "intent": intent, "host_action": host_action, "params": params or {}},
            )
        )
        return {"ok": True, "status": "ready", "would_dispatch": {"target": host_action or "desktop.modal.open"}}

    def add_nlu_authoring_device_alias(
        self,
        *,
        device_ref: str,
        alias: str,
        locale: str | None = None,
        actor: str | None = None,
        base_fingerprint: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        self.calls.append(
            (
                "add_nlu_authoring_device_alias",
                device_ref,
                {
                    "alias": alias,
                    "locale": locale,
                    "actor": actor,
                    "base_fingerprint": base_fingerprint,
                    "dry_run": dry_run,
                },
            )
        )
        return {"ok": True, "status": "proposed" if dry_run else "applied", "device_ref": device_ref}

    def remove_nlu_authoring_device_alias(
        self,
        *,
        device_ref: str,
        alias: str,
        locale: str | None = None,
        actor: str | None = None,
        base_fingerprint: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        self.calls.append(
            (
                "remove_nlu_authoring_device_alias",
                device_ref,
                {
                    "alias": alias,
                    "locale": locale,
                    "actor": actor,
                    "base_fingerprint": base_fingerprint,
                    "dry_run": dry_run,
                },
            )
        )
        return {"ok": True, "status": "proposed" if dry_run else "applied", "device_ref": device_ref}

    def deprecate_nlu_authoring_device_alias(
        self,
        *,
        device_ref: str,
        alias: str,
        locale: str | None = None,
        actor: str | None = None,
        base_fingerprint: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        self.calls.append(
            (
                "deprecate_nlu_authoring_device_alias",
                device_ref,
                {
                    "alias": alias,
                    "locale": locale,
                    "actor": actor,
                    "base_fingerprint": base_fingerprint,
                    "dry_run": dry_run,
                },
            )
        )
        return {"ok": True, "status": "proposed" if dry_run else "applied", "device_ref": device_ref}

    def get_profileops_status(self, target_id: str) -> dict:
        self.calls.append(("get_profileops_status", target_id, {}))
        return {"target_id": target_id, "report_count": 1, "latest_session": {"session_id": "mem-001"}}

    def list_profileops_sessions(self, target_id: str, *, state: str | None = None, suspected_only: bool = False) -> dict:
        self.calls.append(("list_profileops_sessions", target_id, {"state": state, "suspected_only": suspected_only}))
        return {"target_id": target_id, "sessions": [{"session_id": "mem-001"}]}

    def get_profileops_session(self, target_id: str, session_id: str) -> dict:
        self.calls.append(("get_profileops_session", target_id, {"session_id": session_id}))
        return {"target_id": target_id, "session": {"session_id": session_id}}

    def list_profileops_incidents(self, target_id: str) -> dict:
        self.calls.append(("list_profileops_incidents", target_id, {}))
        return {"target_id": target_id, "incidents": [{"session_id": "mem-001"}]}

    def list_profileops_artifacts(self, target_id: str, session_id: str) -> dict:
        self.calls.append(("list_profileops_artifacts", target_id, {"session_id": session_id}))
        return {"target_id": target_id, "artifacts": [{"artifact_id": "art-1"}]}

    def get_profileops_artifact(self, target_id: str, session_id: str, artifact_id: str, *, offset: int = 0, max_bytes: int = 256 * 1024) -> dict:
        self.calls.append(
            ("get_profileops_artifact", target_id, {"session_id": session_id, "artifact_id": artifact_id, "offset": offset, "max_bytes": max_bytes})
        )
        return {"target_id": target_id, "artifact": {"artifact_id": artifact_id}, "exists": True}

    def start_profileops_session(self, target_id: str, *, profile_mode: str = "sampled_profile", reason: str = "root_mcp.memory.start", trigger_source: str = "root_mcp") -> dict:
        self.calls.append(("start_profileops_session", target_id, {"profile_mode": profile_mode, "reason": reason, "trigger_source": trigger_source}))
        return {"target_id": target_id, "profile": {"control": {"session": {"session_id": "mem-101", "profile_mode": profile_mode}}}}

    def stop_profileops_session(self, target_id: str, session_id: str, *, reason: str = "root_mcp.memory.stop") -> dict:
        self.calls.append(("stop_profileops_session", target_id, {"session_id": session_id, "reason": reason}))
        return {"target_id": target_id, "profile": {"control": {"session": {"session_id": session_id, "session_state": "cancelled"}}}}

    def retry_profileops_session(self, target_id: str, session_id: str, *, reason: str = "root_mcp.memory.retry") -> dict:
        self.calls.append(("retry_profileops_session", target_id, {"session_id": session_id, "reason": reason}))
        return {"target_id": target_id, "profile": {"control": {"retry_of_session_id": session_id, "session": {"session_id": "mem-102"}}}}

    def publish_profileops_session(self, target_id: str, session_id: str, *, reason: str = "root_mcp.memory.publish") -> dict:
        self.calls.append(("publish_profileops_session", target_id, {"session_id": session_id, "reason": reason}))
        return {"target_id": target_id, "profile": {"control": {"session": {"session_id": session_id, "publish_state": "published"}}}}

    def list_managed_targets(self, *, environment: str | None = None) -> dict:
        self.calls.append(("list_managed_targets", environment or "", {}))
        return {"targets": [{"target_id": "hub:test-subnet"}]}

    def get_managed_target(self, target_id: str) -> dict:
        self.calls.append(("get_managed_target", target_id, {}))
        return {"target": {"target_id": target_id}}

    def get_operational_surface(self, target_id: str) -> dict:
        self.calls.append(("get_operational_surface", target_id, {}))
        return {"target_id": target_id, "operational_surface": {"published_by": "skill:infra_access_skill"}}

    def get_target_status(self, target_id: str) -> dict:
        self.calls.append(("get_target_status", target_id, {}))
        return {"target_id": target_id, "status": "ok"}

    def get_target_runtime_summary(self, target_id: str) -> dict:
        self.calls.append(("get_target_runtime_summary", target_id, {}))
        return {"target_id": target_id, "runtime": {"skills_active": 2}}

    def get_target_activity_log(self, target_id: str, *, limit: int = 50, errors_only: bool = False) -> dict:
        self.calls.append(("get_target_activity_log", target_id, {"limit": limit, "errors_only": errors_only}))
        return {"target_id": target_id, "activity": [{"status": "ok"}]}

    def get_target_capability_usage_summary(self, target_id: str, *, limit: int = 200) -> dict:
        self.calls.append(("get_target_capability_usage_summary", target_id, {"limit": limit}))
        return {"target_id": target_id, "usage": [{"tool_id": "hub.get_status", "count": 3}]}

    def get_target_logs(self, target_id: str, *, tail: int = 200) -> dict:
        self.calls.append(("get_target_logs", target_id, {"tail": tail}))
        return {"target_id": target_id, "logs": {"files": []}}

    def run_target_healthchecks(self, target_id: str) -> dict:
        self.calls.append(("run_target_healthchecks", target_id, {}))
        return {"target_id": target_id, "healthchecks": {"status": "ok"}}

    def recent_audit(
        self,
        *,
        limit: int = 50,
        tool_id: str | None = None,
        trace_id: str | None = None,
        target_id: str | None = None,
        subnet_id: str | None = None,
    ) -> dict:
        self.calls.append(
            (
                "recent_audit",
                target_id or "",
                {"limit": limit, "tool_id": tool_id, "trace_id": trace_id, "subnet_id": subnet_id},
            )
        )
        return {"events": [{"tool_id": tool_id or "hub.get_status"}]}

    def get_yjs_load_mark_history(
        self,
        *,
        limit: int = 100,
        webspace_id: str | None = None,
        kind: str | None = None,
        bucket_id: str | None = None,
        display_contains: str | None = None,
        status: str | None = None,
        last_source: str | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
    ) -> dict:
        self.calls.append(
            (
                "get_yjs_load_mark_history",
                webspace_id or "",
                {
                    "limit": limit,
                    "kind": kind,
                    "bucket_id": bucket_id,
                    "display_contains": display_contains,
                    "status": status,
                    "last_source": last_source,
                    "since_ts": since_ts,
                    "until_ts": until_ts,
                },
            )
        )
        return {"history": {"count": 1, "items": [{"bucket_id": bucket_id or "_by_owner/unknown"}]}}

    def get_yjs_logs(
        self,
        *,
        limit: int = 5,
        lines: int = 200,
        contains: str | None = None,
        file: str | None = None,
        scope: str | None = None,
        include_hub: bool | None = None,
    ) -> dict:
        self.calls.append(
            (
                "get_yjs_logs",
                "",
                {"limit": limit, "lines": lines, "contains": contains, "file": file, "scope": scope, "include_hub": include_hub},
            )
        )
        return {"logs": {"category": "yjs", "items": [{"rel": file or "yjs_load_mark.jsonl"}]}}

    def get_skill_logs(
        self,
        *,
        limit: int = 5,
        lines: int = 200,
        skill: str | None = None,
        contains: str | None = None,
        file: str | None = None,
        scope: str | None = None,
        include_hub: bool | None = None,
    ) -> dict:
        self.calls.append(
            (
                "get_skill_logs",
                skill or "",
                {"limit": limit, "lines": lines, "contains": contains, "file": file, "scope": scope, "include_hub": include_hub},
            )
        )
        return {"logs": {"category": "skills", "items": [{"rel": f"service.{skill or 'infra_access_skill'}.log"}]}}

    def get_adaos_logs(
        self,
        *,
        limit: int = 5,
        lines: int = 200,
        contains: str | None = None,
        file: str | None = None,
        scope: str | None = None,
        include_hub: bool | None = None,
    ) -> dict:
        self.calls.append(
            (
                "get_adaos_logs",
                "",
                {"limit": limit, "lines": lines, "contains": contains, "file": file, "scope": scope, "include_hub": include_hub},
            )
        )
        return {"logs": {"category": "adaos", "items": [{"rel": "adaos.log"}]}}

    def get_events_logs(
        self,
        *,
        limit: int = 5,
        lines: int = 200,
        contains: str | None = None,
        file: str | None = None,
        scope: str | None = None,
        include_hub: bool | None = None,
    ) -> dict:
        self.calls.append(
            (
                "get_events_logs",
                "",
                {"limit": limit, "lines": lines, "contains": contains, "file": file, "scope": scope, "include_hub": include_hub},
            )
        )
        return {"logs": {"category": "events", "items": [{"rel": file or "events.log"}]}}

    def get_subnet_info(self, *, target_id: str | None = None) -> dict:
        self.calls.append(("get_subnet_info", target_id or "", {}))
        return {"subnet": {"target_id": target_id or "hub:test-subnet", "subnet_id": "test-subnet"}}


def test_codex_bridge_profile_roundtrip(tmp_path: Path) -> None:
    profile_path, token_path = bridge_mod.default_profile_paths(tmp_path, "adaos-test-hub")
    profile = bridge_mod.CodexBridgeProfile(
        root_url="https://root.example.test",
        target_id="hub:test-subnet",
        subnet_id="test-subnet",
        zone="lab-a",
        server_name="adaos-test-hub",
    )
    stored_profile_path, stored_token_path = bridge_mod.write_codex_bridge_profile(
        profile_path=profile_path,
        token_path=token_path,
        profile=profile,
        access_token="secret-token",
    )

    loaded = bridge_mod.load_codex_bridge_profile(stored_profile_path)

    assert stored_profile_path.exists()
    assert stored_token_path.exists()
    assert loaded.root_url == "https://root.example.test"
    assert loaded.target_id == "hub:test-subnet"
    assert loaded.subnet_id == "test-subnet"
    assert loaded.zone == "lab-a"
    assert loaded.bootstrap_mode == "mcp_session_lease"
    assert loaded.resolved_access_token() == "secret-token"


def test_codex_bridge_handles_initialize_and_tool_calls(monkeypatch) -> None:
    profile = bridge_mod.CodexBridgeProfile(
        root_url="https://root.example.test",
        target_id="hub:test-subnet",
        subnet_id="test-subnet",
        access_token="access-123",
        server_name="adaos-test-hub",
    )
    bridge = bridge_mod.CodexRootMcpBridge(profile)
    fake_client = _FakeRootMcpClient()
    monkeypatch.setattr(bridge, "_client", lambda: fake_client)

    initialize = bridge.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools_list = bridge.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    status = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_status", "arguments": {}},
        }
    )
    architecture = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_architecture_catalog", "arguments": {}},
        }
    )
    builder_context = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 40,
            "method": "tools/call",
            "params": {
                "name": "get_builder_context",
                "arguments": {
                    "webspace_id": "desktop",
                    "level": "mini",
                    "request_locale": "ru",
                    "preferred_locales": ["en"],
                    "include_payloads": True,
                },
            },
        }
    )
    named_entities = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 41,
            "method": "tools/call",
            "params": {"name": "get_named_entity_registry", "arguments": {"webspace_id": "desktop", "kind": "device.browser"}},
        }
    )
    nlu_context = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {
                "name": "get_nlu_authoring_context",
                "arguments": {"webspace_id": "desktop", "kind": "device.browser", "request_locale": "ru", "preferred_locales": ["en"]},
            },
        }
    )
    nlu_check = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 421,
            "method": "tools/call",
            "params": {
                "name": "check_nlu_phrase",
                "arguments": {"text": "weather in Berlin", "webspace_id": "desktop", "use_rasa": False},
            },
        }
    )
    nlu_trace = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 422,
            "method": "tools/call",
            "params": {
                "name": "get_nlu_trace",
                "arguments": {"webspace_id": "desktop", "request_id": "req-1", "limit": 12},
            },
        }
    )
    nlu_dialog = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 423,
            "method": "tools/call",
            "params": {
                "name": "get_nlu_dialog_context",
                "arguments": {"webspace_id": "desktop", "candidate_id": "cand-1", "limit": 7},
            },
        }
    )
    nlu_failures = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 424,
            "method": "tools/call",
            "params": {"name": "get_nlu_recent_failures", "arguments": {"webspace_id": "desktop", "limit": 9}},
        }
    )
    desktop_lookup = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 425,
            "method": "tools/call",
            "params": {"name": "lookup_desktop_registry", "arguments": {"webspace_id": "desktop", "include_live": False}},
        }
    )
    skill_nlu = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 426,
            "method": "tools/call",
            "params": {"name": "describe_skill_nlu", "arguments": {"skill_id": "weather_skill"}},
        }
    )
    scenario_nlu = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 427,
            "method": "tools/call",
            "params": {"name": "describe_scenario_nlu", "arguments": {"scenario_id": "web_desktop"}},
        }
    )
    sdk_surface = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 428,
            "method": "tools/call",
            "params": {"name": "describe_sdk_surface", "arguments": {"level": "mini"}},
        }
    )
    nlu_templates = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 429,
            "method": "tools/call",
            "params": {
                "name": "list_nlu_templates",
                "arguments": {"webspace_id": "desktop", "owner_type": "skill", "owner_id": "weather_skill"},
            },
        }
    )
    nlu_targets = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 430,
            "method": "tools/call",
            "params": {"name": "list_nlu_training_targets", "arguments": {"webspace_id": "desktop", "include_system_actions": False}},
        }
    )
    preview_patch = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 431,
            "method": "tools/call",
            "params": {
                "name": "preview_nlu_template_patch",
                "arguments": {
                    "webspace_id": "desktop",
                    "operation": "add_regex_rule",
                    "target": {"type": "skill", "id": "weather_skill"},
                    "intent": "desktop.open_weather",
                    "text": "weather in Berlin",
                    "pattern": r"\bweather\b",
                },
            },
        }
    )
    preview_action = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 432,
            "method": "tools/call",
            "params": {
                "name": "preview_desktop_action",
                "arguments": {"webspace_id": "desktop", "action_id": "host.desktop.modal.open", "params": {"modal_id": "nlu_teacher_modal"}},
            },
        }
    )
    add_alias = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 43,
            "method": "tools/call",
            "params": {
                "name": "add_device_alias",
                "arguments": {
                    "device_ref": "browser:browser-1",
                    "alias": "office browser",
                    "locale": "en",
                    "base_fingerprint": "fp-1",
                    "dry_run": True,
                },
            },
        }
    )
    remove_alias = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 44,
            "method": "tools/call",
            "params": {
                "name": "remove_device_alias",
                "arguments": {
                    "device_ref": "browser:browser-1",
                    "alias": "office browser",
                    "locale": "en",
                    "base_fingerprint": "fp-2",
                    "dry_run": True,
                },
            },
        }
    )
    deprecate_alias = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 45,
            "method": "tools/call",
            "params": {
                "name": "deprecate_device_alias",
                "arguments": {
                    "device_ref": "browser:browser-1",
                    "alias": "old browser",
                    "locale": "en",
                    "base_fingerprint": "fp-3",
                    "dry_run": True,
                },
            },
        }
    )
    profileops = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_profileops_status", "arguments": {}},
        }
    )
    profileops_start = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "start_profileops_session", "arguments": {"profile_mode": "trace_profile"}},
        }
    )
    load_mark_history = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "get_yjs_load_mark_history",
                "arguments": {"webspace_id": "desktop", "kind": "owner", "bucket_id": "_by_owner/unknown", "limit": 25},
            },
        }
    )
    yjs_logs = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "get_yjs_logs",
                "arguments": {"limit": 3, "lines": 120, "contains": "load_mark", "scope": "subnet_active", "include_hub": False},
            },
        }
    )
    subnet_info = bridge.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "get_subnet_info", "arguments": {}},
        }
    )

    assert initialize is not None
    assert initialize["result"]["serverInfo"]["name"] == "adaos-test-hub"
    assert "hub:test-subnet" in initialize["result"]["instructions"]
    assert "AdaOSDevPlane" in initialize["result"]["instructions"]
    assert tools_list is not None
    tool_names = {item["name"] for item in tools_list["result"]["tools"]}
    assert "get_status" in tool_names
    assert "get_builder_context" in tool_names
    assert "get_architecture_catalog" in tool_names
    assert "get_named_entity_registry" in tool_names
    assert "get_nlu_authoring_context" in tool_names
    assert "check_nlu_phrase" in tool_names
    assert "get_nlu_trace" in tool_names
    assert "get_nlu_dialog_context" in tool_names
    assert "get_nlu_recent_failures" in tool_names
    assert "lookup_desktop_registry" in tool_names
    assert "describe_skill_nlu" in tool_names
    assert "describe_scenario_nlu" in tool_names
    assert "describe_sdk_surface" in tool_names
    assert "list_nlu_templates" in tool_names
    assert "list_nlu_training_targets" in tool_names
    assert "preview_nlu_template_patch" in tool_names
    assert "preview_desktop_action" in tool_names
    assert "add_device_alias" in tool_names
    assert "remove_device_alias" in tool_names
    assert "deprecate_device_alias" in tool_names
    assert "get_sdk_metadata" in tool_names
    assert "get_profileops_status" in tool_names
    assert "list_profileops_sessions" in tool_names
    assert "start_profileops_session" in tool_names
    assert "get_yjs_load_mark_history" in tool_names
    assert "get_yjs_logs" in tool_names
    assert "get_skill_logs" in tool_names
    assert "get_adaos_logs" in tool_names
    assert "get_events_logs" in tool_names
    assert "get_subnet_info" in tool_names
    assert status is not None
    assert status["result"]["structuredContent"]["target_id"] == "hub:test-subnet"
    assert architecture is not None
    assert architecture["result"]["structuredContent"]["descriptor"]["payload"]["page_count"] == 3
    assert builder_context is not None
    assert builder_context["result"]["structuredContent"]["builder_context"]["context_id"] == "builder_context.v1"
    assert named_entities is not None
    assert named_entities["result"]["structuredContent"]["descriptor"]["payload"]["items"][0]["canonical_ref"] == "device:browser:browser-1"
    assert nlu_context is not None
    assert nlu_context["result"]["structuredContent"]["context"]["plane_id"] == "nlu_authoring"
    assert nlu_check is not None
    assert nlu_check["result"]["structuredContent"]["check"]["intent"] == "desktop.open_weather"
    assert nlu_trace is not None
    assert nlu_trace["result"]["structuredContent"]["trace"][0]["request_id"] == "req-1"
    assert nlu_dialog is not None
    assert nlu_dialog["result"]["structuredContent"]["threads_by_request"][0]["request_id"] is None
    assert nlu_failures is not None
    assert nlu_failures["result"]["structuredContent"]["failures"][0]["reason"] == "no_match"
    assert desktop_lookup is not None
    assert desktop_lookup["result"]["structuredContent"]["lookups"]["modal_id"][0]["value"] == "nlu_teacher_modal"
    assert skill_nlu is not None
    assert skill_nlu["result"]["structuredContent"]["owner"]["id"] == "weather_skill"
    assert scenario_nlu is not None
    assert scenario_nlu["result"]["structuredContent"]["owner"]["id"] == "web_desktop"
    assert sdk_surface is not None
    assert sdk_surface["result"]["structuredContent"]["surface_id"] == "adaos.sdk.describe_surface.v1"
    assert nlu_templates is not None
    assert nlu_templates["result"]["structuredContent"]["templates"][0]["id"] == "tpl.test"
    assert nlu_targets is not None
    assert nlu_targets["result"]["structuredContent"]["targets"][0]["id"] == "weather_skill"
    assert preview_patch is not None
    assert preview_patch["result"]["structuredContent"]["status"] == "ready"
    assert preview_action is not None
    assert preview_action["result"]["structuredContent"]["would_dispatch"]["target"] == "desktop.modal.open"
    assert add_alias is not None
    assert add_alias["result"]["structuredContent"]["status"] == "proposed"
    assert remove_alias is not None
    assert remove_alias["result"]["structuredContent"]["status"] == "proposed"
    assert deprecate_alias is not None
    assert deprecate_alias["result"]["structuredContent"]["status"] == "proposed"
    assert profileops is not None
    assert profileops["result"]["structuredContent"]["latest_session"]["session_id"] == "mem-001"
    assert profileops_start is not None
    assert profileops_start["result"]["structuredContent"]["profile"]["control"]["session"]["profile_mode"] == "trace_profile"
    assert load_mark_history is not None
    assert load_mark_history["result"]["structuredContent"]["history"]["count"] == 1
    assert yjs_logs is not None
    assert yjs_logs["result"]["structuredContent"]["logs"]["category"] == "yjs"
    assert subnet_info is not None
    assert subnet_info["result"]["structuredContent"]["subnet"]["subnet_id"] == "test-subnet"
    assert ("get_target_status", "hub:test-subnet", {}) in fake_client.calls
    assert ("get_adaos_dev_architecture_catalog", "", {}) in fake_client.calls
    assert (
        "get_builder_context",
        "desktop",
        {
            "level": "mini",
            "request_locale": "ru",
            "preferred_locales": ["en"],
            "include_live": True,
            "include_hints": True,
            "include_payloads": True,
        },
    ) in fake_client.calls
    assert ("get_adaos_dev_named_entity_registry", "desktop", {"kind": "device.browser"}) in fake_client.calls
    assert (
        "get_nlu_authoring_context",
        "desktop",
        {
            "target_id": None,
            "kind": "device.browser",
            "request_locale": "ru",
            "preferred_locales": ["en"],
            "include_live": True,
            "include_hints": True,
        },
    ) in fake_client.calls
    assert (
        "check_nlu_authoring_phrase",
        "desktop",
        {
            "text": "weather in Berlin",
            "target_id": None,
            "use_rasa": False,
            "emit_trace": False,
            "request_locale": None,
            "preferred_locales": [],
        },
    ) in fake_client.calls
    assert (
        "get_nlu_authoring_trace",
        "desktop",
        {"target_id": None, "request_id": "req-1", "candidate_id": None, "limit": 12},
    ) in fake_client.calls
    assert (
        "get_nlu_authoring_dialog_context",
        "desktop",
        {"target_id": None, "request_id": None, "candidate_id": "cand-1", "limit": 7},
    ) in fake_client.calls
    assert ("get_nlu_authoring_recent_failures", "desktop", {"target_id": None, "limit": 9}) in fake_client.calls
    assert ("get_desktop_registry_lookup", "desktop", {"target_id": None, "include_live": False}) in fake_client.calls
    assert ("describe_skill_nlu", "weather_skill", {"target_id": None}) in fake_client.calls
    assert ("describe_scenario_nlu", "web_desktop", {"target_id": None}) in fake_client.calls
    assert ("describe_sdk_surface", "mini", {"target_id": None}) in fake_client.calls
    assert (
        "list_nlu_authoring_templates",
        "desktop",
        {"target_id": None, "owner_type": "skill", "owner_id": "weather_skill", "include_system_actions": True},
    ) in fake_client.calls
    assert (
        "list_nlu_authoring_training_targets",
        "desktop",
        {"target_id": None, "include_system_actions": False},
    ) in fake_client.calls
    assert (
        "preview_nlu_authoring_template_patch",
        "desktop",
        {
            "target_id": None,
            "operation": "add_regex_rule",
            "target": {"type": "skill", "id": "weather_skill"},
            "intent": "desktop.open_weather",
            "text": "weather in Berlin",
            "pattern": r"\bweather\b",
            "slots": {},
            "base_fingerprint": None,
        },
    ) in fake_client.calls
    assert (
        "preview_desktop_action",
        "desktop",
        {
            "target_id": None,
            "action_id": "host.desktop.modal.open",
            "intent": None,
            "host_action": None,
            "params": {"modal_id": "nlu_teacher_modal"},
        },
    ) in fake_client.calls
    assert (
        "add_nlu_authoring_device_alias",
        "browser:browser-1",
        {"alias": "office browser", "locale": "en", "actor": None, "base_fingerprint": "fp-1", "dry_run": True},
    ) in fake_client.calls
    assert (
        "remove_nlu_authoring_device_alias",
        "browser:browser-1",
        {"alias": "office browser", "locale": "en", "actor": None, "base_fingerprint": "fp-2", "dry_run": True},
    ) in fake_client.calls
    assert (
        "deprecate_nlu_authoring_device_alias",
        "browser:browser-1",
        {"alias": "old browser", "locale": "en", "actor": None, "base_fingerprint": "fp-3", "dry_run": True},
    ) in fake_client.calls
    assert ("get_profileops_status", "hub:test-subnet", {}) in fake_client.calls
    assert ("start_profileops_session", "hub:test-subnet", {"profile_mode": "trace_profile", "reason": "root_mcp.memory.start", "trigger_source": "root_mcp"}) in fake_client.calls
    assert ("get_yjs_load_mark_history", "desktop", {"limit": 25, "kind": "owner", "bucket_id": "_by_owner/unknown", "display_contains": None, "status": None, "last_source": None, "since_ts": None, "until_ts": None}) in fake_client.calls
    assert (
        "get_yjs_logs",
        "",
        {"limit": 3, "lines": 120, "contains": "load_mark", "file": None, "scope": "subnet_active", "include_hub": False},
    ) in fake_client.calls
    assert ("get_subnet_info", "", {}) in fake_client.calls


def test_build_codex_stdio_command_uses_profile_and_server_name(tmp_path: Path) -> None:
    profile_path = tmp_path / "adaos-test-hub.profile.json"
    command = bridge_mod.build_codex_stdio_command(
        server_name="adaos-test-hub",
        python_executable="D:\\git\\adaos\\.venv\\Scripts\\python.exe",
        profile_path=profile_path,
    )

    assert command[:4] == ["codex", "mcp", "add", "adaos-test-hub"]
    assert f"ADAOS_MCP_PROFILE={profile_path}" in command
    assert command[-6:] == ["-m", "adaos", "dev", "root", "mcp", "serve"]


def test_bridge_framing_roundtrip() -> None:
    buffer = io.BytesIO()
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    bridge_mod._write_framed_message(buffer, payload)
    buffer.seek(0)
    restored = bridge_mod._read_framed_message(buffer)

    assert restored == payload


def test_prepare_codex_writes_profile_and_prints_command(tmp_path: Path, monkeypatch) -> None:
    class _Cfg:
        subnet_id = "test-subnet"
        zone_id = "lab-a"
        root_settings = types.SimpleNamespace(base_url="https://root.example.test")

    class _FakeSetupClient:
        def get_managed_target(self, target_id: str) -> dict:
            return {"target": {"target_id": target_id, "subnet_id": "test-subnet", "zone": "lab-a"}}

        def issue_target_mcp_session(
            self,
            target_id: str,
            *,
            audience: str,
            ttl_seconds: int | None = None,
            capability_profile: str | None = None,
            capabilities: list[str] | None = None,
            note: str | None = None,
            request_id: str | None = None,
            trace_id: str | None = None,
            dry_run: bool = False,
        ) -> dict:
            return {
                "response": {
                    "result": {
                        "session_id": "sess-1",
                        "access_token": "mcp-session-secret",
                        "expires_at": "2026-04-08T12:00:00+00:00",
                        "capability_profile": capability_profile,
                        "capabilities": list(capabilities or []),
                    }
                }
            }

        def issue_target_access_token(
            self,
            target_id: str,
            *,
            audience: str,
            ttl_seconds: int | None = None,
            capabilities: list[str] | None = None,
            note: str | None = None,
            request_id: str | None = None,
            trace_id: str | None = None,
            dry_run: bool = False,
        ) -> dict:
            return {
                "response": {
                    "result": {
                        "token_id": "tok-1",
                        "access_token": "mcp-secret",
                        "expires_at": "2026-04-08T12:00:00+00:00",
                        "capabilities": list(capabilities or []),
                    }
                }
            }

    monkeypatch.setattr(
        dev_cmd,
        "_resolve_root_mcp_management_client",
        lambda **kwargs: (_FakeSetupClient(), _Cfg(), "owner_bearer"),
    )

    result = CliRunner().invoke(dev_cmd.mcp_app, ["prepare-codex", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["bootstrap_mode"] == "mcp-session"
    assert payload["target_id"] == "hub:test-subnet"
    assert payload["session_id"] == "sess-1"
    assert payload["capability_profile"] == "ProfileOpsRead"
    assert payload["subnet_id"] is None
    assert payload["zone"] is None
    assert Path(payload["profile_file"]).exists()
    assert Path(payload["token_file"]).exists()
    assert Path(payload["token_file"]).read_text(encoding="utf-8").strip() == "mcp-session-secret"
    stored_profile = json.loads(Path(payload["profile_file"]).read_text(encoding="utf-8"))
    assert stored_profile["root_url"] == "https://root.example.test"
    assert stored_profile["target_id"] == "hub:test-subnet"
    assert stored_profile["bootstrap_mode"] == "mcp_session_lease"
    assert stored_profile["session_id"] == "sess-1"
    assert stored_profile["capability_profile"] == "ProfileOpsRead"
    assert payload["codex_add_command"][:4] == ["codex", "mcp", "add", "adaos-test-hub"]
