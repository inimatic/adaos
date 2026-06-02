from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from .client import RootMcpClient, RootMcpClientConfig
from .tokens import DEFAULT_ACCESS_TOKEN_CAPABILITIES


DEFAULT_CODEX_TARGET_CAPABILITIES: list[str] = [
    *DEFAULT_ACCESS_TOKEN_CAPABILITIES,
    "hub.get_operational_surface",
    "hub.get_status",
    "hub.get_runtime_summary",
    "hub.get_activity_log",
    "hub.get_capability_usage_summary",
    "hub.get_logs",
    "hub.run_healthchecks",
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _normalize_unique(items: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in items or []:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return out


def _resolve_mcp_dir(base_dir: str | Path) -> Path:
    root = Path(base_dir)
    if root.name == "mcp" and root.parent.name == ".adaos":
        return root
    return root / ".adaos" / "mcp"


def _default_log_scope(value: Any) -> str:
    return _normalize_text(value) or "subnet_active"


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


@dataclass(slots=True)
class CodexBridgeProfile:
    root_url: str
    target_id: str | None = None
    subnet_id: str | None = None
    zone: str | None = None
    bootstrap_mode: str = "mcp_session_lease"
    session_id: str | None = None
    capability_profile: str | None = None
    access_token: str | None = None
    access_token_file: str | None = None
    server_name: str = "adaos-test-hub"
    audience: str = "codex-vscode"
    generated_at: str | None = None
    capabilities: list[str] = field(default_factory=lambda: list(DEFAULT_CODEX_TARGET_CAPABILITIES))

    def resolved_access_token(self) -> str:
        direct = _normalize_text(self.access_token)
        if direct:
            return direct
        env_name = _normalize_text(os.getenv("ADAOS_MCP_ACCESS_TOKEN_ENV"))
        if env_name:
            token = _normalize_text(os.getenv(env_name))
            if token:
                return token
        path = _normalize_text(self.access_token_file)
        if path:
            try:
                token = Path(path).read_text(encoding="utf-8").strip()
            except Exception as exc:  # pragma: no cover - file errors depend on host
                raise RuntimeError(f"failed to read Root MCP access token from {path}: {exc}") from exc
            if token:
                return token
        raise RuntimeError("Root MCP access token is missing; re-run 'adaos dev root mcp prepare-codex'")

    def client_config(self) -> RootMcpClientConfig:
        return RootMcpClientConfig(
            root_url=str(self.root_url),
            subnet_id=self.subnet_id,
            zone=self.zone,
            access_token=self.resolved_access_token(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "root_url": self.root_url,
            "target_id": self.target_id,
            "subnet_id": self.subnet_id,
            "zone": self.zone,
            "bootstrap_mode": self.bootstrap_mode,
            "session_id": self.session_id,
            "capability_profile": self.capability_profile,
            "access_token_file": self.access_token_file,
            "audience": self.audience,
            "generated_at": self.generated_at or _iso_now(),
            "capabilities": list(self.capabilities),
        }


def default_profile_paths(base_dir: str | Path, server_name: str) -> tuple[Path, Path]:
    token = str(server_name or "adaos-test-hub").strip() or "adaos-test-hub"
    safe_name = token.replace(":", "-").replace("/", "-").replace("\\", "-").replace(" ", "-")
    root = _resolve_mcp_dir(base_dir)
    return root / f"{safe_name}.profile.json", root / f"{safe_name}.token"


def load_codex_bridge_profile(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> CodexBridgeProfile:
    environment = dict(env or os.environ)
    profile_path = _normalize_text(path) or _normalize_text(environment.get("ADAOS_MCP_PROFILE"))
    payload: dict[str, Any] = {}
    if profile_path:
        resolved = Path(profile_path)
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"failed to read Codex MCP profile {resolved}: {exc}") from exc
    root_url = _normalize_text(payload.get("root_url")) or _normalize_text(environment.get("ADAOS_MCP_ROOT_URL"))
    if not root_url:
        raise RuntimeError("Root MCP bridge requires root_url; set ADAOS_MCP_PROFILE or ADAOS_MCP_ROOT_URL")
    return CodexBridgeProfile(
        root_url=root_url,
        target_id=_normalize_text(payload.get("target_id")) or _normalize_text(environment.get("ADAOS_MCP_TARGET_ID")),
        subnet_id=_normalize_text(payload.get("subnet_id")) or _normalize_text(environment.get("ADAOS_MCP_SUBNET_ID")),
        zone=_normalize_text(payload.get("zone")) or _normalize_text(environment.get("ADAOS_MCP_ZONE")),
        bootstrap_mode=_normalize_text(payload.get("bootstrap_mode")) or "mcp_session_lease",
        session_id=_normalize_text(payload.get("session_id")) or _normalize_text(environment.get("ADAOS_MCP_SESSION_ID")),
        capability_profile=_normalize_text(payload.get("capability_profile")) or _normalize_text(environment.get("ADAOS_MCP_CAPABILITY_PROFILE")),
        access_token=_normalize_text(payload.get("access_token")) or _normalize_text(environment.get("ADAOS_MCP_ACCESS_TOKEN")),
        access_token_file=_normalize_text(payload.get("access_token_file")) or _normalize_text(environment.get("ADAOS_MCP_ACCESS_TOKEN_FILE")),
        server_name=_normalize_text(payload.get("server_name")) or "adaos-test-hub",
        audience=_normalize_text(payload.get("audience")) or "codex-vscode",
        generated_at=_normalize_text(payload.get("generated_at")),
        capabilities=_normalize_unique(list(payload.get("capabilities") or [])) or list(DEFAULT_CODEX_TARGET_CAPABILITIES),
    )


def write_codex_bridge_profile(
    *,
    profile_path: str | Path,
    token_path: str | Path,
    profile: CodexBridgeProfile,
    access_token: str,
) -> tuple[Path, Path]:
    profile_file = Path(profile_path)
    token_file = Path(token_path)
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(str(access_token).strip() + "\n", encoding="utf-8")
    stored = CodexBridgeProfile(
        root_url=profile.root_url,
        target_id=profile.target_id,
        subnet_id=profile.subnet_id,
        zone=profile.zone,
        bootstrap_mode=profile.bootstrap_mode,
        session_id=profile.session_id,
        capability_profile=profile.capability_profile,
        access_token_file=str(token_file),
        server_name=profile.server_name,
        audience=profile.audience,
        generated_at=profile.generated_at or _iso_now(),
        capabilities=_normalize_unique(profile.capabilities) or list(DEFAULT_CODEX_TARGET_CAPABILITIES),
    )
    profile_file.write_text(_json_text(stored.to_dict()) + "\n", encoding="utf-8")
    return profile_file, token_file


def build_codex_stdio_command(
    *,
    server_name: str,
    python_executable: str,
    profile_path: str | Path,
) -> list[str]:
    return [
        "codex",
        "mcp",
        "add",
        str(server_name or "adaos-test-hub").strip() or "adaos-test-hub",
        "--env",
        f"ADAOS_MCP_PROFILE={Path(profile_path)}",
        "--",
        str(python_executable),
        "-m",
        "adaos",
        "dev",
        "root",
        "mcp",
        "serve",
    ]


def _tool_text(payload: Any, *, error: bool = False) -> dict[str, Any]:
    text = _json_text(payload)
    response = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
    }
    if error:
        response["isError"] = True
    return response


class CodexRootMcpBridge:
    def __init__(self, profile: CodexBridgeProfile):
        self.profile = profile

    def _client(self) -> RootMcpClient:
        return RootMcpClient(self.profile.client_config())

    def _effective_target_id(self, arguments: Mapping[str, Any] | None) -> str:
        target_id = _normalize_text((arguments or {}).get("target_id")) or self.profile.target_id
        if not target_id:
            raise ValueError("target_id is required for this bridge call")
        return target_id

    def instructions(self) -> str:
        target = self.profile.target_id or "the configured managed target"
        bootstrap = "MCP Session Lease" if self.profile.bootstrap_mode == "mcp_session_lease" else "bounded access token"
        return (
            "This MCP server is a local stdio bridge from Codex to AdaOS Root MCP. "
            f"It is currently bound to {target} using {bootstrap}. "
            "For descriptive AdaOS programming context, prefer get_architecture_catalog, get_sdk_metadata, "
            "get_template_catalog, NLU authoring context, named entity registry, and public registry summaries "
            "from AdaOSDevPlane/NLUAuthoringPlane. "
            "For operational context, prefer get_status, get_runtime_summary, and get_operational_surface "
            "before requesting logs or healthchecks."
        )

    def tool_definitions(self) -> list[dict[str, Any]]:
        target_optional = self.profile.target_id is not None
        target_properties = {"target_id": {"type": "string", "description": "Managed target id. Defaults to the configured test hub."}}
        target_required = [] if target_optional else ["target_id"]
        return [
            {
                "name": "foundation",
                "description": "Read the AdaOS Root MCP foundation snapshot used by this bridge.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_architecture_catalog",
                "description": "Read the AdaOS architecture catalog through the AdaOSDevPlane descriptive surface.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_sdk_metadata",
                "description": "Read AdaOS SDK export metadata through the AdaOSDevPlane descriptive surface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "string", "enum": ["mini", "std", "rich"], "default": "std"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_template_catalog",
                "description": "Read the root-curated skill and scenario template catalog through AdaOSDevPlane.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_public_skill_registry",
                "description": "Read the published workspace skill registry summary through AdaOSDevPlane.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_public_scenario_registry",
                "description": "Read the published workspace scenario registry summary through AdaOSDevPlane.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "get_named_entity_registry",
                "description": "Read the compact canonical named-entity registry through AdaOSDevPlane.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "kind": {"type": "string", "description": "Optional entity kind filter, such as device.browser or skill."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_nlu_authoring_context",
                "description": "Read NLUAuthoringPlane context with named entities, contextual action surface, runtime/process state, developer hints, locale hints, and read-only authoring boundaries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "kind": {"type": "string", "description": "Optional entity kind filter, such as device.browser or skill."},
                        "request_locale": {"type": "string", "description": "Optional active request locale, such as ru or en-US."},
                        "preferred_locales": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional ordered locale preferences for label selection.",
                        },
                        "include_live": {"type": "boolean", "default": True, "description": "Include live runtime state when available."},
                        "include_hints": {"type": "boolean", "default": True, "description": "Include developer-authored skill/scenario hints."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "check_nlu_phrase",
                "description": "Run a side-effect-free NLU Teacher phrase probe through NLUAuthoringPlane.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Phrase to check."},
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "use_rasa": {"type": "boolean", "default": True, "description": "Allow Rasa fallback during the probe."},
                        "emit_trace": {"type": "boolean", "default": False, "description": "Persist NLU trace stages while probing."},
                        "request_locale": {"type": "string", "description": "Optional active request locale, such as ru or en-US."},
                        "preferred_locales": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional ordered locale preferences for label selection.",
                        },
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_nlu_trace",
                "description": "Read NLU trace and Teacher evidence for a request or candidate.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "request_id": {"type": "string", "description": "Optional Teacher/NLU request id filter."},
                        "candidate_id": {"type": "string", "description": "Optional Teacher candidate id filter."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 80},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_nlu_dialog_context",
                "description": "Read correction-aware NLU Teacher dialog/thread context.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "request_id": {"type": "string", "description": "Optional Teacher/NLU request id filter."},
                        "candidate_id": {"type": "string", "description": "Optional Teacher candidate id filter."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_nlu_recent_failures",
                "description": "Read recent NLU misses and Teacher skip/teachable classification.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "lookup_desktop_registry",
                "description": "Read desktop lookup tables for apps, modals, scenarios, webspaces, skills, and nodes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "include_live": {"type": "boolean", "default": True, "description": "Include live YJS overlay when available."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "describe_skill_nlu",
                "description": "Read a skill's NLU intents, regex rules, event surface, and LLM policy.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "skill_id": {"type": "string", "description": "Skill id/folder name."},
                    },
                    "required": ["skill_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "describe_scenario_nlu",
                "description": "Read a scenario's NLU intents and regex rules.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "scenario_id": {"type": "string", "description": "Scenario id/folder name."},
                    },
                    "required": ["scenario_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "describe_sdk_surface",
                "description": "Read descriptive SDK/function-call boundaries for NLU Teacher planning; this does not allow direct SDK calls.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "level": {"type": "string", "enum": ["mini", "std", "rich"], "default": "std"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_nlu_templates",
                "description": "Read NLU examples, regex rules, and route templates with stable fingerprints.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "owner_type": {"type": "string", "enum": ["skill", "scenario", "system_action"]},
                        "owner_id": {"type": "string", "description": "Optional owner id/folder/action id filter."},
                        "include_system_actions": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_nlu_training_targets",
                "description": "Read skill, scenario, and system-action surfaces available for NLU Teacher placement decisions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "include_system_actions": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "preview_nlu_template_patch",
                "description": "Dry-run an NLU Teacher template/training patch and return validation gates without mutation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "operation": {"type": "string", "enum": ["add_regex_rule", "save_example"]},
                        "target": {
                            "type": "object",
                            "properties": {"type": {"type": "string"}, "id": {"type": "string"}},
                            "additionalProperties": True,
                        },
                        "intent": {"type": "string"},
                        "text": {"type": "string", "description": "Source utterance/example text."},
                        "pattern": {"type": "string", "description": "Python regex for add_regex_rule."},
                        "slots": {"type": "object", "additionalProperties": True},
                        "base_fingerprint": {"type": "string", "description": "Optional target fingerprint for stale-write protection."},
                    },
                    "required": ["operation", "target", "intent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "preview_desktop_action",
                "description": "Dry-run a desktop/system action and return would-dispatch payload without dispatching.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string", "description": "Optional managed target id. Defaults from the Root MCP bearer scope."},
                        "webspace_id": {"type": "string", "description": "Webspace id. Defaults to desktop."},
                        "action_id": {"type": "string", "description": "System action id, such as host.desktop.modal.open."},
                        "intent": {"type": "string", "description": "Optional NLU intent linked to a system action."},
                        "host_action": {"type": "string", "description": "Optional host event name, such as desktop.modal.open."},
                        "params": {"type": "object", "additionalProperties": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "add_device_alias",
                "description": "Add a governed alias for a browser/member device through NLUAuthoringPlane. Requires a write-capable Root MCP session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_ref": {"type": "string", "description": "Canonical device ref, such as browser:<id> or member:<node_id>."},
                        "alias": {"type": "string", "description": "Human phrase to register as an alias."},
                        "locale": {"type": "string", "description": "Optional alias locale, such as en or ru."},
                        "actor": {"type": "string", "description": "Optional actor metadata for audit/event payloads."},
                        "base_fingerprint": {"type": "string", "description": "Optional entity fingerprint from get_named_entity_registry for stale-write protection."},
                        "dry_run": {"type": "boolean", "default": False, "description": "When true, return the governed proposal without mutating state."},
                    },
                    "required": ["device_ref", "alias"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "remove_device_alias",
                "description": "Remove a governed alias for a browser/member device through NLUAuthoringPlane. Requires a write-capable Root MCP session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_ref": {"type": "string", "description": "Canonical device ref, such as browser:<id> or member:<node_id>."},
                        "alias": {"type": "string", "description": "Human phrase to remove from aliases."},
                        "locale": {"type": "string", "description": "Optional alias locale, such as en or ru."},
                        "actor": {"type": "string", "description": "Optional actor metadata for audit/event payloads."},
                        "base_fingerprint": {"type": "string", "description": "Optional entity fingerprint from get_named_entity_registry for stale-write protection."},
                        "dry_run": {"type": "boolean", "default": False, "description": "When true, return the governed proposal without mutating state."},
                    },
                    "required": ["device_ref", "alias"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "deprecate_device_alias",
                "description": "Mark a governed alias as deprecated for a browser/member device through NLUAuthoringPlane. Requires a write-capable Root MCP session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_ref": {"type": "string", "description": "Canonical device ref, such as browser:<id> or member:<node_id>."},
                        "alias": {"type": "string", "description": "Human phrase to deprecate as an alias."},
                        "locale": {"type": "string", "description": "Optional alias locale, such as en or ru."},
                        "actor": {"type": "string", "description": "Optional actor metadata for audit/event payloads."},
                        "base_fingerprint": {"type": "string", "description": "Optional entity fingerprint from get_named_entity_registry for stale-write protection."},
                        "dry_run": {"type": "boolean", "default": False, "description": "When true, return the governed proposal without mutating state."},
                    },
                    "required": ["device_ref", "alias"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_profileops_status",
                "description": "Read root-published profiler status and latest session summary for the managed target.",
                "inputSchema": {"type": "object", "properties": target_properties, "required": target_required, "additionalProperties": False},
            },
            {
                "name": "list_profileops_sessions",
                "description": "List root-published profiler sessions for the managed target.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **target_properties,
                        "state": {"type": "string"},
                        "suspected_only": {"type": "boolean", "default": False},
                    },
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_profileops_session",
                "description": "Read one root-published profiler session for the managed target.",
                "inputSchema": {
                    "type": "object",
                    "properties": {**target_properties, "session_id": {"type": "string"}},
                    "required": [*target_required, "session_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_profileops_incidents",
                "description": "List suspected profiler incidents for the managed target.",
                "inputSchema": {"type": "object", "properties": target_properties, "required": target_required, "additionalProperties": False},
            },
            {
                "name": "list_profileops_artifacts",
                "description": "List root-published profiler artifacts for a profiler session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {**target_properties, "session_id": {"type": "string"}},
                    "required": [*target_required, "session_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_profileops_artifact",
                "description": "Read one root-published profiler artifact for a profiler session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **target_properties,
                        "session_id": {"type": "string"},
                        "artifact_id": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                        "max_bytes": {"type": "integer", "minimum": 1, "default": 262144},
                    },
                    "required": [*target_required, "session_id", "artifact_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "start_profileops_session",
                "description": "Start a supervisor-owned profiler session through the bounded ProfileOps control surface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **target_properties,
                        "profile_mode": {"type": "string", "default": "sampled_profile"},
                        "reason": {"type": "string"},
                        "trigger_source": {"type": "string", "default": "root_mcp"},
                    },
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "stop_profileops_session",
                "description": "Stop a supervisor-owned profiler session through the bounded ProfileOps control surface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {**target_properties, "session_id": {"type": "string"}, "reason": {"type": "string"}},
                    "required": [*target_required, "session_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "retry_profileops_session",
                "description": "Retry a supervisor-owned profiler session through the bounded ProfileOps control surface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {**target_properties, "session_id": {"type": "string"}, "reason": {"type": "string"}},
                    "required": [*target_required, "session_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "publish_profileops_session",
                "description": "Publish a supervisor-owned profiler session to root through the bounded ProfileOps control surface.",
                "inputSchema": {
                    "type": "object",
                    "properties": {**target_properties, "session_id": {"type": "string"}, "reason": {"type": "string"}},
                    "required": [*target_required, "session_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_managed_targets",
                "description": "List managed targets visible to the current Root MCP token scope.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "environment": {"type": "string", "description": "Optional environment filter such as test."},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_managed_target",
                "description": "Describe the configured managed target or another accessible target.",
                "inputSchema": {
                    "type": "object",
                    "properties": target_properties,
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_operational_surface",
                "description": "Inspect the published infra_access_skill surface, token management, and WebUI hints.",
                "inputSchema": {
                    "type": "object",
                    "properties": target_properties,
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_status",
                "description": "Read the current hub status, route, root-control, and deployment state.",
                "inputSchema": {
                    "type": "object",
                    "properties": target_properties,
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_runtime_summary",
                "description": "Read the current runtime summary published for the managed hub.",
                "inputSchema": {
                    "type": "object",
                    "properties": target_properties,
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_activity_log",
                "description": "Read the audit-derived recent activity view for the target. For richer operational history, prefer get_subnet_timeline.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **target_properties,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                        "errors_only": {"type": "boolean", "default": False},
                    },
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_capability_usage_summary",
                "description": "Read aggregated capability-usage counters for the target.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **target_properties,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                    },
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_logs",
                "description": "Read bounded logs through infra_access_skill when the target exposes local_process execution.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **target_properties,
                        "tail": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200},
                    },
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "run_healthchecks",
                "description": "Run bounded healthchecks through infra_access_skill when the target exposes local_process execution.",
                "inputSchema": {
                    "type": "object",
                    "properties": target_properties,
                    "required": target_required,
                    "additionalProperties": False,
                },
            },
            {
                "name": "recent_audit",
                "description": "Read recent Root MCP audit events for this bridge scope.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                        "tool_id": {"type": "string"},
                        "trace_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_yjs_load_mark_history",
                "description": "Read queryable YJS load-mark history captured beside adaos.log.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 100},
                        "webspace_id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["owner", "root"]},
                        "bucket_id": {"type": "string"},
                        "display_contains": {"type": "string"},
                        "status": {"type": "string"},
                        "last_source": {"type": "string"},
                        "since_ts": {"type": "number"},
                        "until_ts": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_yjs_logs",
                "description": "Read bounded YJS log tails. Defaults to aggregated logs from active subnet nodes unless scope=root_local is requested, and returns explicit provenance and health for the selected log path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                        "lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                        "contains": {"type": "string"},
                        "file": {"type": "string"},
                        "scope": {"type": "string", "enum": ["root_local", "subnet_active"], "default": "subnet_active"},
                        "include_hub": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_skill_logs",
                "description": "Read bounded skill log tails, including service stdout/stderr, in-process runtime logs, and browser UI runtime diagnostics. Defaults to aggregated logs from active subnet nodes unless scope=root_local is requested, and returns explicit provenance and health for the selected log path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                        "lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                        "skill": {"type": "string"},
                        "contains": {"type": "string"},
                        "file": {"type": "string"},
                        "scope": {"type": "string", "enum": ["root_local", "subnet_active"], "default": "subnet_active"},
                        "include_hub": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_adaos_logs",
                "description": "Read bounded adaos.log tails. Defaults to aggregated logs from active subnet nodes unless scope=root_local is requested, and returns explicit provenance and health for the selected log path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                        "lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                        "contains": {"type": "string"},
                        "file": {"type": "string"},
                        "scope": {"type": "string", "enum": ["root_local", "subnet_active"], "default": "subnet_active"},
                        "include_hub": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_events_logs",
                "description": "Read bounded events.log tails. Defaults to aggregated logs from active subnet nodes unless scope=root_local is requested, and returns explicit provenance and health for the selected log path.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                        "lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                        "contains": {"type": "string"},
                        "file": {"type": "string"},
                        "scope": {"type": "string", "enum": ["root_local", "subnet_active"], "default": "subnet_active"},
                        "include_hub": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_subnet_info",
                "description": "Read the currently scoped root-known subnet information and visible sessions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_subnet_analysis_health",
                "description": "Assess which subnet analysis channels are currently trustworthy, including control-report freshness, session freshness, and optional subnet-active log probes.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string"},
                        "probe_logs": {"type": "boolean", "default": True},
                        "lines": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                        "include_hub": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_subnet_timeline",
                "description": "Read the typed subnet operational timeline derived from Root MCP audit and report-ingest events, with current control-report references attached.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 300, "default": 100},
                        "include_control_reports": {"type": "boolean", "default": True},
                        "include_profile_ops": {"type": "boolean", "default": True},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_subnet_diagnostics",
                "description": "Read typed route, backlog, ack, YJS, and memory-profile diagnostics for the current subnet from Root MCP projections.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_id": {"type": "string"},
                        "session_limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    },
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        client = self._client()
        tool = str(name or "").strip()
        if tool == "foundation":
            return _tool_text(client.foundation())
        if tool == "get_architecture_catalog":
            return _tool_text(client.get_adaos_dev_architecture_catalog())
        if tool == "get_sdk_metadata":
            return _tool_text(client.get_adaos_dev_sdk_metadata(level=str(args.get("level") or "std")))
        if tool == "get_template_catalog":
            return _tool_text(client.get_adaos_dev_template_catalog())
        if tool == "get_public_skill_registry":
            return _tool_text(client.get_adaos_dev_public_skill_registry())
        if tool == "get_public_scenario_registry":
            return _tool_text(client.get_adaos_dev_public_scenario_registry())
        if tool == "get_named_entity_registry":
            return _tool_text(
                client.get_adaos_dev_named_entity_registry(
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    kind=_normalize_text(args.get("kind")),
                )
            )
        if tool == "get_nlu_authoring_context":
            raw_locales = args.get("preferred_locales")
            preferred_locales = _normalize_unique(raw_locales if isinstance(raw_locales, list) else None)
            return _tool_text(
                client.get_nlu_authoring_context(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    kind=_normalize_text(args.get("kind")),
                    request_locale=_normalize_text(args.get("request_locale")),
                    preferred_locales=preferred_locales,
                    include_live=bool(args.get("include_live", True)),
                    include_hints=bool(args.get("include_hints", True)),
                )
            )
        if tool == "check_nlu_phrase":
            raw_locales = args.get("preferred_locales")
            preferred_locales = _normalize_unique(raw_locales if isinstance(raw_locales, list) else None)
            return _tool_text(
                client.check_nlu_authoring_phrase(
                    str(args.get("text") or ""),
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    use_rasa=bool(args.get("use_rasa", True)),
                    emit_trace=bool(args.get("emit_trace", False)),
                    request_locale=_normalize_text(args.get("request_locale")),
                    preferred_locales=preferred_locales,
                )
            )
        if tool == "get_nlu_trace":
            return _tool_text(
                client.get_nlu_authoring_trace(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    request_id=_normalize_text(args.get("request_id")),
                    candidate_id=_normalize_text(args.get("candidate_id")),
                    limit=int(args.get("limit") or 80),
                )
            )
        if tool == "get_nlu_dialog_context":
            return _tool_text(
                client.get_nlu_authoring_dialog_context(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    request_id=_normalize_text(args.get("request_id")),
                    candidate_id=_normalize_text(args.get("candidate_id")),
                    limit=int(args.get("limit") or 25),
                )
            )
        if tool == "get_nlu_recent_failures":
            return _tool_text(
                client.get_nlu_authoring_recent_failures(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    limit=int(args.get("limit") or 50),
                )
            )
        if tool == "lookup_desktop_registry":
            return _tool_text(
                client.get_desktop_registry_lookup(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    include_live=bool(args.get("include_live", True)),
                )
            )
        if tool == "describe_skill_nlu":
            return _tool_text(
                client.describe_skill_nlu(
                    str(args.get("skill_id") or ""),
                    target_id=_normalize_text(args.get("target_id")),
                )
            )
        if tool == "describe_scenario_nlu":
            return _tool_text(
                client.describe_scenario_nlu(
                    str(args.get("scenario_id") or ""),
                    target_id=_normalize_text(args.get("target_id")),
                )
            )
        if tool == "describe_sdk_surface":
            return _tool_text(
                client.describe_sdk_surface(
                    target_id=_normalize_text(args.get("target_id")),
                    level=str(args.get("level") or "std"),
                )
            )
        if tool == "list_nlu_templates":
            return _tool_text(
                client.list_nlu_authoring_templates(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    owner_type=_normalize_text(args.get("owner_type")),
                    owner_id=_normalize_text(args.get("owner_id")),
                    include_system_actions=bool(args.get("include_system_actions", True)),
                )
            )
        if tool == "list_nlu_training_targets":
            return _tool_text(
                client.list_nlu_authoring_training_targets(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    include_system_actions=bool(args.get("include_system_actions", True)),
                )
            )
        if tool == "preview_nlu_template_patch":
            target = args.get("target") if isinstance(args.get("target"), Mapping) else {}
            slots = args.get("slots") if isinstance(args.get("slots"), Mapping) else {}
            return _tool_text(
                client.preview_nlu_authoring_template_patch(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    operation=str(args.get("operation") or ""),
                    target=target,
                    intent=str(args.get("intent") or ""),
                    text=_normalize_text(args.get("text")),
                    pattern=_normalize_text(args.get("pattern")),
                    slots=slots,
                    base_fingerprint=_normalize_text(args.get("base_fingerprint")),
                )
            )
        if tool == "preview_desktop_action":
            params = args.get("params") if isinstance(args.get("params"), Mapping) else {}
            return _tool_text(
                client.preview_desktop_action(
                    target_id=_normalize_text(args.get("target_id")),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    action_id=_normalize_text(args.get("action_id")),
                    intent=_normalize_text(args.get("intent")),
                    host_action=_normalize_text(args.get("host_action")),
                    params=params,
                )
            )
        if tool == "add_device_alias":
            return _tool_text(
                client.add_nlu_authoring_device_alias(
                    device_ref=str(args.get("device_ref") or ""),
                    alias=str(args.get("alias") or ""),
                    locale=_normalize_text(args.get("locale")),
                    actor=_normalize_text(args.get("actor")),
                    base_fingerprint=_normalize_text(args.get("base_fingerprint")),
                    dry_run=bool(args.get("dry_run")),
                )
            )
        if tool == "remove_device_alias":
            return _tool_text(
                client.remove_nlu_authoring_device_alias(
                    device_ref=str(args.get("device_ref") or ""),
                    alias=str(args.get("alias") or ""),
                    locale=_normalize_text(args.get("locale")),
                    actor=_normalize_text(args.get("actor")),
                    base_fingerprint=_normalize_text(args.get("base_fingerprint")),
                    dry_run=bool(args.get("dry_run")),
                )
            )
        if tool == "deprecate_device_alias":
            return _tool_text(
                client.deprecate_nlu_authoring_device_alias(
                    device_ref=str(args.get("device_ref") or ""),
                    alias=str(args.get("alias") or ""),
                    locale=_normalize_text(args.get("locale")),
                    actor=_normalize_text(args.get("actor")),
                    base_fingerprint=_normalize_text(args.get("base_fingerprint")),
                    dry_run=bool(args.get("dry_run")),
                )
            )
        if tool == "get_profileops_status":
            return _tool_text(client.get_profileops_status(self._effective_target_id(args)))
        if tool == "list_profileops_sessions":
            return _tool_text(
                client.list_profileops_sessions(
                    self._effective_target_id(args),
                    state=_normalize_text(args.get("state")),
                    suspected_only=bool(args.get("suspected_only")),
                )
            )
        if tool == "get_profileops_session":
            return _tool_text(client.get_profileops_session(self._effective_target_id(args), str(args.get("session_id") or "")))
        if tool == "list_profileops_incidents":
            return _tool_text(client.list_profileops_incidents(self._effective_target_id(args)))
        if tool == "list_profileops_artifacts":
            return _tool_text(client.list_profileops_artifacts(self._effective_target_id(args), str(args.get("session_id") or "")))
        if tool == "get_profileops_artifact":
            return _tool_text(
                client.get_profileops_artifact(
                    self._effective_target_id(args),
                    str(args.get("session_id") or ""),
                    str(args.get("artifact_id") or ""),
                    offset=int(args.get("offset") or 0),
                    max_bytes=int(args.get("max_bytes") or 256 * 1024),
                )
            )
        if tool == "start_profileops_session":
            return _tool_text(
                client.start_profileops_session(
                    self._effective_target_id(args),
                    profile_mode=str(args.get("profile_mode") or "sampled_profile"),
                    reason=str(args.get("reason") or "root_mcp.memory.start"),
                    trigger_source=str(args.get("trigger_source") or "root_mcp"),
                )
            )
        if tool == "stop_profileops_session":
            return _tool_text(
                client.stop_profileops_session(
                    self._effective_target_id(args),
                    str(args.get("session_id") or ""),
                    reason=str(args.get("reason") or "root_mcp.memory.stop"),
                )
            )
        if tool == "retry_profileops_session":
            return _tool_text(
                client.retry_profileops_session(
                    self._effective_target_id(args),
                    str(args.get("session_id") or ""),
                    reason=str(args.get("reason") or "root_mcp.memory.retry"),
                )
            )
        if tool == "publish_profileops_session":
            return _tool_text(
                client.publish_profileops_session(
                    self._effective_target_id(args),
                    str(args.get("session_id") or ""),
                    reason=str(args.get("reason") or "root_mcp.memory.publish"),
                )
            )
        if tool == "list_managed_targets":
            return _tool_text(client.list_managed_targets(environment=_normalize_text(args.get("environment"))))
        if tool == "get_managed_target":
            return _tool_text(client.get_managed_target(self._effective_target_id(args)))
        if tool == "get_operational_surface":
            return _tool_text(client.get_operational_surface(self._effective_target_id(args)))
        if tool == "get_status":
            return _tool_text(client.get_target_status(self._effective_target_id(args)))
        if tool == "get_runtime_summary":
            return _tool_text(client.get_target_runtime_summary(self._effective_target_id(args)))
        if tool == "get_activity_log":
            return _tool_text(
                client.get_target_activity_log(
                    self._effective_target_id(args),
                    limit=int(args.get("limit") or 50),
                    errors_only=bool(args.get("errors_only")),
                )
            )
        if tool == "get_capability_usage_summary":
            return _tool_text(
                client.get_target_capability_usage_summary(
                    self._effective_target_id(args),
                    limit=int(args.get("limit") or 200),
                )
            )
        if tool == "get_logs":
            return _tool_text(
                client.get_target_logs(
                    self._effective_target_id(args),
                    tail=int(args.get("tail") or 200),
                )
            )
        if tool == "run_healthchecks":
            return _tool_text(client.run_target_healthchecks(self._effective_target_id(args)))
        if tool == "recent_audit":
            return _tool_text(
                client.recent_audit(
                    limit=int(args.get("limit") or 50),
                    tool_id=_normalize_text(args.get("tool_id")),
                    trace_id=_normalize_text(args.get("trace_id")),
                    target_id=self.profile.target_id,
                    subnet_id=self.profile.subnet_id,
                )
            )
        if tool == "get_yjs_load_mark_history":
            return _tool_text(
                client.get_yjs_load_mark_history(
                    limit=int(args.get("limit") or 100),
                    webspace_id=_normalize_text(args.get("webspace_id")),
                    kind=_normalize_text(args.get("kind")),
                    bucket_id=_normalize_text(args.get("bucket_id")),
                    display_contains=_normalize_text(args.get("display_contains")),
                    status=_normalize_text(args.get("status")),
                    last_source=_normalize_text(args.get("last_source")),
                    since_ts=float(args["since_ts"]) if args.get("since_ts") is not None else None,
                    until_ts=float(args["until_ts"]) if args.get("until_ts") is not None else None,
                )
            )
        if tool == "get_yjs_logs":
            return _tool_text(
                client.get_yjs_logs(
                    limit=int(args.get("limit") or 5),
                    lines=int(args.get("lines") or 200),
                    contains=_normalize_text(args.get("contains")),
                    file=_normalize_text(args.get("file")),
                    scope=_default_log_scope(args.get("scope")),
                    include_hub=bool(args["include_hub"]) if "include_hub" in args else None,
                )
            )
        if tool == "get_skill_logs":
            return _tool_text(
                client.get_skill_logs(
                    limit=int(args.get("limit") or 5),
                    lines=int(args.get("lines") or 200),
                    skill=_normalize_text(args.get("skill")),
                    contains=_normalize_text(args.get("contains")),
                    file=_normalize_text(args.get("file")),
                    scope=_default_log_scope(args.get("scope")),
                    include_hub=bool(args["include_hub"]) if "include_hub" in args else None,
                )
            )
        if tool == "get_adaos_logs":
            return _tool_text(
                client.get_adaos_logs(
                    limit=int(args.get("limit") or 5),
                    lines=int(args.get("lines") or 200),
                    contains=_normalize_text(args.get("contains")),
                    file=_normalize_text(args.get("file")),
                    scope=_default_log_scope(args.get("scope")),
                    include_hub=bool(args["include_hub"]) if "include_hub" in args else None,
                )
            )
        if tool == "get_events_logs":
            return _tool_text(
                client.get_events_logs(
                    limit=int(args.get("limit") or 5),
                    lines=int(args.get("lines") or 200),
                    contains=_normalize_text(args.get("contains")),
                    file=_normalize_text(args.get("file")),
                    scope=_default_log_scope(args.get("scope")),
                    include_hub=bool(args["include_hub"]) if "include_hub" in args else None,
                )
            )
        if tool == "get_subnet_info":
            return _tool_text(client.get_subnet_info(target_id=_normalize_text(args.get("target_id"))))
        if tool == "get_subnet_analysis_health":
            return _tool_text(
                client.get_subnet_analysis_health(
                    target_id=_normalize_text(args.get("target_id")),
                    probe_logs=bool(args["probe_logs"]) if "probe_logs" in args else True,
                    lines=int(args.get("lines") or 20),
                    include_hub=bool(args["include_hub"]) if "include_hub" in args else True,
                )
            )
        if tool == "get_subnet_timeline":
            return _tool_text(
                client.get_subnet_timeline(
                    target_id=_normalize_text(args.get("target_id")),
                    limit=int(args.get("limit") or 100),
                    include_control_reports=bool(args["include_control_reports"]) if "include_control_reports" in args else True,
                    include_profile_ops=bool(args["include_profile_ops"]) if "include_profile_ops" in args else True,
                )
            )
        if tool == "get_subnet_diagnostics":
            return _tool_text(
                client.get_subnet_diagnostics(
                    target_id=_normalize_text(args.get("target_id")),
                    session_limit=int(args.get("session_limit") or 5),
                )
            )
        raise KeyError(tool)

    def handle_request(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        method = str(request.get("method") or "").strip()
        request_id = request.get("id")
        params = request.get("params")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.profile.server_name, "version": "0.1.0"},
                    "instructions": self.instructions(),
                }
                return {"jsonrpc": "2.0", "id": request_id, "result": result}
            if method == "ping":
                return {"jsonrpc": "2.0", "id": request_id, "result": {}}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tool_definitions()}}
            if method == "tools/call":
                payload = dict(params or {})
                name = str(payload.get("name") or "").strip()
                arguments = payload.get("arguments")
                return {"jsonrpc": "2.0", "id": request_id, "result": self.call_tool(name, arguments)}
            if method.startswith("notifications/"):
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not supported: {method}"},
            }
        except Exception as exc:
            if method == "tools/call":
                error_payload = {
                    "ok": False,
                    "error": {
                        "message": str(exc),
                        "type": exc.__class__.__name__,
                    },
                }
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_text(error_payload, error=True)}
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }


def _read_framed_message(stream: BinaryIO) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        try:
            key, value = line.decode("utf-8").split(":", 1)
        except ValueError:
            continue
        headers[key.strip().lower()] = value.strip()
    try:
        length = int(headers.get("content-length") or "0")
    except ValueError:
        length = 0
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_framed_message(stream: BinaryIO, payload: Mapping[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\nContent-Type: application/json\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(body)
    stream.flush()


def serve_codex_stdio_bridge(profile: CodexBridgeProfile | None = None) -> None:
    bridge = CodexRootMcpBridge(profile or load_codex_bridge_profile())
    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer
    while True:
        message = _read_framed_message(input_stream)
        if message is None:
            return
        response = bridge.handle_request(message)
        if response is not None:
            _write_framed_message(output_stream, response)


__all__ = [
    "CodexBridgeProfile",
    "CodexRootMcpBridge",
    "DEFAULT_CODEX_TARGET_CAPABILITIES",
    "build_codex_stdio_command",
    "default_profile_paths",
    "load_codex_bridge_profile",
    "serve_codex_stdio_bridge",
    "write_codex_bridge_profile",
]
