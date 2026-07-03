from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


REALIZE_REQUEST_SCHEMA = "adaos.builder.realize_request.v1"
DEV_NODE_REGISTRATION_SCHEMA = "adaos.skill_factory.dev_node_registration.v1"
DEV_TASK_ASSIGNMENT_SCHEMA = "adaos.skill_factory.dev_task_assignment.v1"
DEV_RESULT_SCHEMA = "adaos.skill_factory.dev_result.v1"
DEV_READY_EVENT_SCHEMA = "adaos.skill_factory.dev_ready_event.v1"
DEV_TASK_FAILURE_SCHEMA = "adaos.skill_factory.dev_task_failure.v1"

STATE_SCHEMA = "adaos.skill_factory.state.v1"
TASK_BRANCH_PREFIX = "realize/"
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_TASK_TIMEOUT_SECONDS = 4 * 60 * 60

TASK_ACTIVE_STATES = {"assigned", "workspace_preparing", "in_progress", "tests_running", "commit_ready"}
TASK_TERMINAL_STATES = {"completed", "failed", "cancelled", "expired"}
DEV_NODE_DEFAULT_CAPABILITIES = ["codex", "git_sparse_checkout", "adaos_sdk", "mcp_client", "local_tests"]
REALIZATION_POLICY_SCHEMA = "adaos.skill_factory.realization_policy.v1"
TASK_CONTEXT_SCHEMA = "adaos.skill_factory.task_context.v1"
TASK_PROVENANCE_SCHEMA = "adaos.skill_factory.task_provenance.v1"

MANUAL_ONLY_CONSTRAINT_KEYS = {
    "new_permissions",
    "permissions",
    "service_process",
    "filesystem_writes",
    "network_io",
    "external_io",
    "device_control",
    "endpoint_control",
    "high_rate_streams",
    "credentials",
    "new_dependencies",
    "dependency_changes",
    "model_artifacts",
}
DISALLOWED_CONSTRAINT_KEYS = {
    "real_user_data",
    "read_real_user_data",
    "read_secrets",
    "secrets_required",
    "destructive_actions",
    "production_actions",
    "direct_runtime_mutation",
}
DEPENDENCY_FILE_NAMES = {
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

_LOCK = threading.RLock()
_SLUG_RE = re.compile(r"[^a-z0-9_.-]+")
_ABS_WIN_PATH_RE = re.compile(r"^[a-zA-Z]:")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _string_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in _list(value):
        token = _text(item)
        if token and token not in out:
            out.append(token)
    return out


def _slug(value: Any, *, fallback: str = "artifact") -> str:
    text = _text(value).lower().replace(" ", "_")
    text = _SLUG_RE.sub("_", text).strip("._-")
    return text or fallback


def _safe_branch_fragment(value: Any) -> str:
    return _slug(value, fallback="task").replace("_", "-")


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _stable_suffix(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:10]


def _normalize_repo_path(value: Any, *, directory: bool) -> str:
    token = _text(value).replace("\\", "/").strip().strip("/")
    if not token:
        raise ValueError("repository path is required")
    parts = [part for part in token.split("/") if part]
    if token.startswith("/") or _ABS_WIN_PATH_RE.match(token):
        raise ValueError(f"absolute repository paths are not allowed: {value}")
    if any(part in {"..", ".git"} for part in parts):
        raise ValueError(f"unsafe repository path is not allowed: {value}")
    normalized = "/".join(parts)
    if directory:
        normalized = normalized.rstrip("/") + "/"
    return normalized


def _normalize_sparse_paths(value: Any) -> list[str]:
    paths: list[str] = []
    for raw in _list(value):
        path = _normalize_repo_path(raw, directory=True)
        if path not in paths:
            paths.append(path)
    return paths


def _changed_path_allowed(changed_path: str, allowed_dir: str) -> bool:
    changed = _normalize_repo_path(changed_path, directory=False)
    allowed = _normalize_repo_path(allowed_dir, directory=True)
    return changed == allowed.rstrip("/") or changed.startswith(allowed)


def _task_internal_path(task_id: str) -> str:
    return _normalize_repo_path(f".adaos/tasks/{_safe_branch_fragment(task_id)}", directory=True)


def _target_from_payload(payload: Mapping[str, Any], draft: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw_target = _mapping(payload.get("target"))
    draft_artifact = _mapping((draft or {}).get("artifact")) if isinstance(draft, Mapping) else {}
    target_type = _text(raw_target.get("type") or payload.get("target_type") or draft_artifact.get("kind") or "skill").lower()
    target_id = _slug(raw_target.get("id") or payload.get("target_id") or draft_artifact.get("id"), fallback="artifact")
    if target_type not in {"skill", "scenario", "webui", "datasource", "connector", "descriptor_fix", "unknown"}:
        target_type = "unknown"
    return {**raw_target, "type": target_type, "id": target_id}


def _default_sparse_paths(target: Mapping[str, Any], draft: Mapping[str, Any] | None = None) -> list[str]:
    target_type = _text(target.get("type")).lower() or "unknown"
    target_id = _slug(target.get("id"), fallback="artifact")
    paths: list[str] = []
    if target_type == "skill":
        paths.append(f"skills/{target_id}/")
    elif target_type == "scenario":
        paths.append(f"scenarios/{target_id}/")
    elif target_type == "webui":
        paths.append(f"webui/{target_id}/")
    elif target_type == "datasource":
        paths.append(f"datasources/{target_id}/")
    elif target_type == "connector":
        paths.append(f"connectors/{target_id}/")
    elif target_type == "descriptor_fix":
        draft_artifact = _mapping((draft or {}).get("artifact")) if isinstance(draft, Mapping) else {}
        kind = _text(draft_artifact.get("kind")).lower()
        artifact_id = _slug(draft_artifact.get("id") or target_id, fallback=target_id)
        if kind == "skill":
            paths.append(f"skills/{artifact_id}/")
        elif kind == "scenario":
            paths.append(f"scenarios/{artifact_id}/")
    paths.append(f"docs/requirements/{target_id}/")
    return _normalize_sparse_paths(paths)


def _assignment_mcp_scope(raw_scope: Any) -> list[str]:
    mapping = {
        "capability_snapshot": "read_capability_snapshot",
        "requirement_spec": "read_requirements",
        "requirements": "read_requirements",
        "ui_draft": "read_ui_draft",
        "datasource_schema": "read_datasource_schema",
        "mock_runtime": "read_mock_data",
        "mock_data": "read_mock_data",
        "staging_validation": "run_staging_validation",
    }
    scope: list[str] = []
    for item in _string_list(raw_scope):
        token = item.strip()
        normalized = mapping.get(token, token)
        if normalized and normalized not in scope:
            scope.append(normalized)
    if not scope:
        scope = ["read_capability_snapshot", "read_requirements", "read_mock_data", "run_staging_validation"]
    return scope


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    token = _text(value).lower()
    return token in {"1", "true", "yes", "on", "required", "present", "enabled"}


def _constraint_enabled(constraints: Mapping[str, Any], key: str) -> bool:
    if key not in constraints:
        return False
    value = constraints.get(key)
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return _truthy(value)


def _classify_realization_policy(raw: Mapping[str, Any], target: Mapping[str, Any], constraints: Mapping[str, Any]) -> dict[str, Any]:
    explicit = _mapping(raw.get("realization_policy"))
    explicit_class = _text(explicit.get("classification")).lower()
    if explicit_class not in {"allowed", "manual_only", "disallowed"}:
        explicit_class = ""

    risk_classes = _string_list(explicit.get("risk_classes"))
    reasons = _string_list(explicit.get("reasons"))

    disallowed = [key for key in sorted(DISALLOWED_CONSTRAINT_KEYS) if _constraint_enabled(constraints, key)]
    manual = [key for key in sorted(MANUAL_ONLY_CONSTRAINT_KEYS) if _constraint_enabled(constraints, key)]
    if not bool(constraints.get("no_external_api", True)):
        manual.append("external_api_allowed")
    if not bool(constraints.get("no_secrets", True)):
        disallowed.append("secrets_allowed")

    target_type = _text(target.get("type")).lower()
    if target_type == "connector" and "external_io" not in manual:
        manual.append("external_io")
    if target_type == "datasource" and "data_schema_review" not in risk_classes:
        risk_classes.append("data_schema_review")

    if disallowed:
        classification = "disallowed"
        reasons.extend(f"disallowed:{key}" for key in disallowed if f"disallowed:{key}" not in reasons)
    elif manual:
        classification = "manual_only"
        reasons.extend(f"manual_only:{key}" for key in manual if f"manual_only:{key}" not in reasons)
    else:
        classification = explicit_class or "allowed"

    if explicit_class == "disallowed":
        classification = "disallowed"
    elif explicit_class == "manual_only" and classification == "allowed":
        classification = "manual_only"

    return {
        **explicit,
        "schema": REALIZATION_POLICY_SCHEMA,
        "classification": classification,
        "manual_approval_required": classification in {"manual_only", "disallowed"},
        "disallowed": classification == "disallowed",
        "risk_classes": risk_classes,
        "reasons": reasons,
    }


def _snapshot_context(raw: Mapping[str, Any], artifacts: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    explicit = _mapping(raw.get("snapshot_context"))
    raw_provenance = explicit.get("provenance")
    provenance = [dict(item) for item in _list(raw_provenance) if isinstance(item, Mapping)]
    if not provenance:
        for key, value in artifacts.items():
            token = _text(value)
            if token:
                provenance.append({"kind": str(key), "ref": token})

    mock_data = _mapping(explicit.get("mock_data"))
    fixture_ids = _string_list(mock_data.get("fixture_ids") or raw.get("mock_fixture_ids"))
    seed = _text(mock_data.get("seed") or raw.get("mock_seed"))
    if not seed:
        seed = _stable_suffix({"artifacts": artifacts, "source": raw.get("source"), "target": raw.get("target")})

    redaction = {
        **_mapping(explicit.get("redaction")),
        "level": _text(_mapping(explicit.get("redaction")).get("level")) or "redacted",
        "secrets_absent": True,
        "raw_user_data_absent": True,
    }
    privacy = {
        **_mapping(explicit.get("privacy")),
        "secrets_absent": True,
        "raw_user_data_absent": True,
    }
    freshness = {
        **_mapping(explicit.get("freshness")),
        "generated_at": _text(_mapping(explicit.get("freshness")).get("generated_at")) or now,
    }
    return {
        **explicit,
        "schema": TASK_CONTEXT_SCHEMA,
        "generated_at": _text(explicit.get("generated_at")) or now,
        "freshness": freshness,
        "redaction": redaction,
        "privacy": privacy,
        "provenance": provenance,
        "mock_data": {
            **mock_data,
            "deterministic": True,
            "fixture_ids": fixture_ids,
            "seed": seed,
        },
        "byte_budget": int(explicit.get("byte_budget") or 256_000),
    }


def _expected_evidence_paths(task_id: str) -> dict[str, str]:
    root = _task_internal_path(task_id)
    return {
        "result": f"{root}result.json",
        "test_report": f"{root}test_report.json",
        "changed_files": f"{root}changed_files.txt",
        "provenance": f"{root}provenance.json",
        "sanitized_logs": f"{root}sanitized_logs/",
    }


def _dependency_delta(raw: Mapping[str, Any], changed_paths: list[str]) -> dict[str, Any]:
    explicit = _mapping(raw.get("dependency_delta"))
    changes = _list(explicit.get("changes") or raw.get("dependency_changes"))
    files = _string_list(explicit.get("files"))
    for path in changed_paths:
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if name in DEPENDENCY_FILE_NAMES and path not in files:
            files.append(path)
    return {
        **explicit,
        "changed": bool(files or changes),
        "files": files,
        "changes": changes,
        "review_required": bool(files or changes),
    }


def _result_provenance(raw: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    explicit = _mapping(raw.get("provenance"))
    request = _mapping(task.get("realize_request"))
    context = _mapping(request.get("snapshot_context"))
    return {
        **explicit,
        "schema": TASK_PROVENANCE_SCHEMA,
        "task_id": _text(raw.get("task_id") or task.get("task_id")),
        "dev_node_id": _text(explicit.get("dev_node_id") or raw.get("node_id") or task.get("assigned_node_id")) or None,
        "runner_version": _text(explicit.get("runner_version")) or "unknown",
        "image_digest": _text(explicit.get("image_digest")) or None,
        "instruction_packet_hash": _text(explicit.get("instruction_packet_hash")) or None,
        "dependency_changes": _list(explicit.get("dependency_changes") or raw.get("dependency_changes")),
        "snapshot_refs": _list(explicit.get("snapshot_refs") or context.get("provenance")),
        "reported_at": _text(explicit.get("reported_at") or raw.get("reported_at")) or _now_iso(),
    }


@dataclass(slots=True)
class SkillFactoryService:
    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "skill_factory"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def forge_policy(self) -> dict[str, Any]:
        return {
            "backend": "adaos_registry_local_forge_compatible",
            "layout": "private_task_branches",
            "branch_creator": "dev_node",
            "task_branch_prefix": TASK_BRANCH_PREFIX,
            "result_branch_required": True,
            "sparse_checkout_required": True,
            "cleanup_policy": {
                "retain_completed_branches_days": 30,
                "retain_failed_branches_days": 14,
                "retain_cancelled_branches_days": 7,
                "cleanup_trigger": "manual_or_scheduled_root_job",
            },
        }

    def calculate_sparse_paths(self, target: Mapping[str, Any], draft: Mapping[str, Any] | None = None) -> list[str]:
        return _default_sparse_paths(target, draft=draft)

    def normalize_realize_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = _mapping(payload.get("realize_request")) or _mapping(payload)
        draft = _mapping(raw.get("draft"))
        target = _target_from_payload(raw, draft=draft)
        repo = _mapping(raw.get("repo"))
        links = _mapping(raw.get("links"))
        artifacts = _mapping(raw.get("artifacts"))
        constraints = _mapping(raw.get("constraints"))
        mcp = _mapping(raw.get("mcp"))

        draft_id = _text(draft.get("draft_id") or links.get("draft_id") or artifacts.get("draft_id"))
        if draft_id:
            links["draft_id"] = draft_id
            artifacts.setdefault("draft_id", draft_id)
        draft_links = _mapping(draft.get("links"))
        draft_metadata = _mapping(draft.get("metadata"))
        if draft_links:
            if _text(draft_links.get("preview_id")):
                links.setdefault("preview_id", _text(draft_links.get("preview_id")))
                artifacts.setdefault("preview_id", _text(draft_links.get("preview_id")))
            if isinstance(draft_links.get("conversation"), Mapping):
                links.setdefault("conversation", _mapping(draft_links.get("conversation")))
            if _text(draft_links.get("builder_task_id")):
                links.setdefault("builder_task_id", _text(draft_links.get("builder_task_id")))
        if _text(draft.get("task_id")):
            links.setdefault("builder_task_id", _text(draft.get("task_id")))

        request_id = _text(raw.get("request_id"))
        if not request_id:
            request_id = f"realize.{target['id']}.{_stable_suffix({'target': target, 'draft_id': draft_id, 'source': raw.get('source')})}"

        user_subnet_id = _text(raw.get("user_subnet_id") or raw.get("subnet_id")) or None
        base_branch = _text(repo.get("base_branch")) or f"dev/{user_subnet_id or 'local'}"
        sparse_paths = _normalize_sparse_paths(repo.get("sparse_paths")) if repo.get("sparse_paths") else self.calculate_sparse_paths(target, draft=draft)
        forge_project = _text(repo.get("forge_project") or raw.get("forge_project") or user_subnet_id or "local_devspace")

        conversation = _mapping(links.get("conversation"))
        source_conversation_id = _text(
            raw.get("source_conversation_id")
            or conversation.get("conversation_id")
            or conversation.get("thread_id")
            or conversation.get("channel_id")
        ) or None
        source_session_id = _text(raw.get("source_session_id") or _mapping(raw.get("source")).get("session_id")) or None

        default_constraints = {
            "no_external_api": True,
            "no_secrets": True,
            "must_add_tests": True,
            "must_update_manifest": target.get("type") in {"skill", "scenario", "descriptor_fix"},
        }
        default_constraints.update({key: value for key, value in constraints.items() if value is not None})

        requested_scope = _string_list(mcp.get("requested_scope")) or [
            "capability_snapshot",
            "requirement_spec",
            "mock_runtime",
            "staging_validation",
        ]
        acceptance = _mapping(raw.get("acceptance"))
        draft_quality = _mapping(draft.get("quality_gates"))
        if draft_quality and not acceptance.get("test_commands"):
            acceptance["test_commands"] = _string_list(draft_quality.get("tests"))
        if draft_metadata and not acceptance.get("criteria"):
            criteria = _string_list(draft_metadata.get("expected_tests"))
            if criteria:
                acceptance["criteria"] = criteria

        now = _now_iso()
        created_at = _text(raw.get("created_at")) or now
        realization_policy = _classify_realization_policy(raw, target, default_constraints)
        snapshot_context = _snapshot_context(raw, artifacts, now=now)
        return {
            **raw,
            "schema": REALIZE_REQUEST_SCHEMA,
            "request_id": request_id,
            "status": _text(raw.get("status")) or "ready_for_queue",
            "user_subnet_id": user_subnet_id,
            "source_session_id": source_session_id,
            "source_conversation_id": source_conversation_id,
            "target": target,
            "artifacts": artifacts,
            "repo": {
                **repo,
                "forge_backend": _text(repo.get("forge_backend")) or self.forge_policy()["backend"],
                "forge_project": forge_project,
                "repo_url": repo.get("repo_url"),
                "base_branch": base_branch,
                "sparse_paths": sparse_paths,
            },
            "realization_policy": realization_policy,
            "snapshot_context": snapshot_context,
            "constraints": default_constraints,
            "mcp": {**mcp, "requested_scope": requested_scope},
            "acceptance": acceptance,
            "links": links,
            "source": _mapping(raw.get("source")) or {
                "type": "builder",
                "text": _text(draft_metadata.get("source_idea") or raw.get("requested_behavior") or raw.get("prompt")),
            },
            "created_at": created_at,
            "updated_at": now,
        }

    def submit_realize_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = self.normalize_realize_request(payload)
        with _LOCK:
            state = self._read_state()
            for existing in state["tasks"].values():
                refs = _mapping(existing.get("source_refs"))
                if refs.get("request_id") == request["request_id"] and existing.get("status") not in TASK_TERMINAL_STATES:
                    return {"ok": True, "duplicate": True, "task": _json_clone(existing), "queue": self._queue_summary(state)}

            task_id = _text(payload.get("task_id")) or f"task.{new_id()}"
            branch = f"{TASK_BRANCH_PREFIX}{_safe_branch_fragment(task_id)}"
            repo = _mapping(request.get("repo"))
            forge_sparse_paths = self._task_sparse_paths(task_id, _normalize_sparse_paths(repo.get("sparse_paths")))
            evidence_paths = _expected_evidence_paths(task_id)
            now = _now_iso()
            task = {
                "schema": "adaos.skill_factory.dev_task.v1",
                "task_id": task_id,
                "request_id": request["request_id"],
                "status": "queued",
                "priority": int(payload.get("priority") or request.get("priority") or 0),
                "attempts": 0,
                "max_attempts": max(1, int(payload.get("max_attempts") or request.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)),
                "timeout_seconds": max(60, int(payload.get("timeout_seconds") or request.get("timeout_seconds") or DEFAULT_TASK_TIMEOUT_SECONDS)),
                "target": _mapping(request.get("target")),
                "realization_policy": _mapping(request.get("realization_policy")),
                "snapshot_context": _mapping(request.get("snapshot_context")),
                "constraints": _mapping(request.get("constraints")),
                "acceptance": _mapping(request.get("acceptance")),
                "mcp": _mapping(request.get("mcp")),
                "repo": repo,
                "forge": {
                    "backend": repo.get("forge_backend") or self.forge_policy()["backend"],
                    "repo_url": repo.get("repo_url"),
                    "forge_project": repo.get("forge_project"),
                    "base_branch": repo.get("base_branch"),
                    "branch": branch,
                    "branch_creator": "dev_node",
                    "sparse_paths": forge_sparse_paths,
                    "cleanup_policy": self.forge_policy()["cleanup_policy"],
                },
                "evidence": {
                    "schema": "adaos.skill_factory.task_evidence.v1",
                    "expected_paths": evidence_paths,
                    "provenance_required": True,
                    "dependency_delta_review": True,
                },
                "source_refs": {
                    "request_id": request["request_id"],
                    "builder_task_id": _mapping(request.get("links")).get("builder_task_id"),
                    "draft_id": _mapping(request.get("links")).get("draft_id"),
                    "preview_id": _mapping(request.get("links")).get("preview_id"),
                    "pending_action_id": _mapping(request.get("links")).get("pending_action_id"),
                    "source_conversation_id": request.get("source_conversation_id"),
                    "source_session_id": request.get("source_session_id"),
                },
                "realize_request": request,
                "assigned_node_id": None,
                "progress": [],
                "failure_history": [],
                "avoid_node_ids": [],
                "created_at": now,
                "updated_at": now,
            }
            state["tasks"][task_id] = task
            self._append_event(state, "skill_factory.task_queued", {"task_id": task_id, "request_id": request["request_id"]})
            self._write_state(state)
            return {"ok": True, "duplicate": False, "task": _json_clone(task), "queue": self._queue_summary(state)}

    def register_dev_node(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = _mapping(payload.get("registration")) or _mapping(payload)
        now = _now_iso()
        node_id = _text(raw.get("node_id")) or f"devnode.{new_id()}"
        with _LOCK:
            state = self._read_state()
            existing = _mapping(state["dev_nodes"].get(node_id))
            assigned_tasks = _string_list(existing.get("assigned_tasks"))
            node = {
                **existing,
                **raw,
                "schema": DEV_NODE_REGISTRATION_SCHEMA,
                "node_id": node_id,
                "node_type": _text(raw.get("node_type")) or "isolated_dev_node",
                "capabilities": _string_list(raw.get("capabilities")) or _string_list(existing.get("capabilities")) or list(DEV_NODE_DEFAULT_CAPABILITIES),
                "status": _text(raw.get("status") or existing.get("status")) or "registered_waiting",
                "trust_level": _text(raw.get("trust_level") or existing.get("trust_level")) or "isolated",
                "max_parallel_tasks": max(1, int(raw.get("max_parallel_tasks") or existing.get("max_parallel_tasks") or 1)),
                "assigned_tasks": assigned_tasks,
                "heartbeat_at": now,
                "registered_at": existing.get("registered_at") or now,
                "updated_at": now,
                "metadata": _mapping(raw.get("metadata")) or _mapping(existing.get("metadata")),
            }
            state["dev_nodes"][node_id] = node
            self._append_event(state, "skill_factory.dev_node_registered", {"node_id": node_id, "status": node["status"]})
            self._write_state(state)
            return {"ok": True, "registration": _json_clone(node), "queue": self._queue_summary(state)}

    def heartbeat(self, node_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        raw = _mapping(payload)
        node_token = _text(node_id or raw.get("node_id"))
        if not node_token:
            raise ValueError("node_id is required")
        with _LOCK:
            state = self._read_state()
            node = _mapping(state["dev_nodes"].get(node_token))
            if not node:
                raise KeyError(node_token)
            node["heartbeat_at"] = _now_iso()
            node["updated_at"] = node["heartbeat_at"]
            if _text(raw.get("status")):
                node["status"] = _text(raw.get("status"))
            if "current_task_id" in raw:
                node["current_task_id"] = _text(raw.get("current_task_id")) or None
            if isinstance(raw.get("load"), Mapping):
                node["load"] = _mapping(raw.get("load"))
            if raw.get("capabilities") is not None:
                node["capabilities"] = _string_list(raw.get("capabilities"))
            state["dev_nodes"][node_token] = node
            self._write_state(state)
            return {"ok": True, "node": _json_clone(node), "queue": self._queue_summary(state)}

    def poll_assignment(self, node_id: str) -> dict[str, Any]:
        node_token = _text(node_id)
        if not node_token:
            raise ValueError("node_id is required")
        with _LOCK:
            state = self._read_state()
            self._expire_overdue_tasks(state)
            node = _mapping(state["dev_nodes"].get(node_token))
            if not node:
                raise KeyError(node_token)

            if bool(node.get("credentials_revoked")) or _text(node.get("status")) == "credentials_revoked":
                node["status"] = "credentials_revoked"
                node["heartbeat_at"] = _now_iso()
                node["updated_at"] = node["heartbeat_at"]
                state["dev_nodes"][node_token] = node
                self._write_state(state)
                return {"ok": True, "assigned": False, "reason": "credentials_revoked", "queue": self._queue_summary(state)}

            if bool(node.get("quarantined")) or _text(node.get("status")) == "quarantined":
                node["status"] = "quarantined"
                node["heartbeat_at"] = _now_iso()
                node["updated_at"] = node["heartbeat_at"]
                state["dev_nodes"][node_token] = node
                self._write_state(state)
                return {"ok": True, "assigned": False, "reason": "node_quarantined", "queue": self._queue_summary(state)}

            active_for_node = [
                task
                for task in state["tasks"].values()
                if _text(task.get("assigned_node_id")) == node_token and _text(task.get("status")) in TASK_ACTIVE_STATES
            ]
            if active_for_node:
                task = sorted(active_for_node, key=lambda item: _text(item.get("assigned_at")))[0]
                assignment = self._assignment_payload(task, node)
                self._write_state(state)
                return {"ok": True, "assigned": True, "assignment": assignment, "task": _json_clone(task)}

            if bool(_mapping(state.get("queue")).get("paused")):
                node["status"] = "waiting"
                node["heartbeat_at"] = _now_iso()
                node["updated_at"] = node["heartbeat_at"]
                state["dev_nodes"][node_token] = node
                self._write_state(state)
                return {"ok": True, "assigned": False, "reason": "queue_paused", "queue": self._queue_summary(state)}

            if bool(node.get("draining")) or _text(node.get("status")) == "draining":
                node["status"] = "draining"
                node["heartbeat_at"] = _now_iso()
                node["updated_at"] = node["heartbeat_at"]
                state["dev_nodes"][node_token] = node
                self._write_state(state)
                return {"ok": True, "assigned": False, "reason": "node_draining", "queue": self._queue_summary(state)}

            queued = [
                task
                for task in state["tasks"].values()
                if _text(task.get("status")) == "queued"
                and not bool(task.get("cancellation_requested"))
                and node_token not in _string_list(_mapping(task).get("avoid_node_ids"))
            ]
            queued.sort(key=lambda item: (-int(item.get("priority") or 0), _text(item.get("created_at")), _text(item.get("task_id"))))
            if not queued:
                node["status"] = "waiting"
                node["heartbeat_at"] = _now_iso()
                node["updated_at"] = node["heartbeat_at"]
                state["dev_nodes"][node_token] = node
                self._write_state(state)
                return {"ok": True, "assigned": False, "reason": "queue_empty", "queue": self._queue_summary(state)}

            task = queued[0]
            now = _now_iso()
            task["status"] = "assigned"
            task["assigned_node_id"] = node_token
            task["assigned_at"] = now
            task["updated_at"] = now
            task["attempts"] = max(0, int(task.get("attempts") or 0)) + 1
            task["timeout_at"] = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=int(task.get("timeout_seconds") or DEFAULT_TASK_TIMEOUT_SECONDS))).isoformat()
            node["status"] = "assigned"
            node["current_task_id"] = task["task_id"]
            node["assigned_tasks"] = [task["task_id"]]
            node["heartbeat_at"] = now
            node["updated_at"] = now
            state["tasks"][task["task_id"]] = task
            state["dev_nodes"][node_token] = node
            self._append_event(state, "skill_factory.task_assigned", {"task_id": task["task_id"], "node_id": node_token})
            assignment = self._assignment_payload(task, node)
            self._write_state(state)
            return {"ok": True, "assigned": True, "assignment": assignment, "task": _json_clone(task)}

    def report_progress(self, task_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        task_token = _text(task_id or payload.get("task_id"))
        if not task_token:
            raise ValueError("task_id is required")
        with _LOCK:
            state = self._read_state()
            task = self._require_task(state, task_token)
            node_id = _text(payload.get("node_id"))
            self._validate_assigned_node(task, node_id)
            self._ensure_node_credentials_active(state, node_id)
            status = self._normalize_task_status(payload.get("status") or payload.get("stage") or task.get("status"))
            now = _now_iso()
            event_id = _text(payload.get("event_id") or payload.get("progress_id"))
            if event_id:
                for existing in _list(task.get("progress")):
                    if _text(_mapping(existing).get("event_id")) == event_id:
                        return {"ok": True, "duplicate": True, "task": _json_clone(task)}
            entry = {
                "event_id": event_id,
                "at": now,
                "node_id": node_id or task.get("assigned_node_id"),
                "status": status,
                "stage": _text(payload.get("stage")) or status,
                "message": _text(payload.get("message")),
                "details": _mapping(payload.get("details")),
            }
            if not entry["event_id"]:
                entry["event_id"] = f"progress.{task_token}.{_stable_suffix(entry)}"
            task["status"] = status
            task["updated_at"] = now
            task.setdefault("progress", []).append(entry)
            if node_id and node_id in state["dev_nodes"]:
                node = _mapping(state["dev_nodes"][node_id])
                node_status = {
                    "workspace_preparing": "preparing_workspace",
                    "in_progress": "developing",
                    "tests_running": "testing",
                    "commit_ready": "committing",
                }.get(status, node.get("status", "assigned"))
                node["status"] = node_status
                node["heartbeat_at"] = now
                node["updated_at"] = now
                state["dev_nodes"][node_id] = node
            state["tasks"][task_token] = task
            self._write_state(state)
            return {"ok": True, "task": _json_clone(task)}

    def complete_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = _mapping(payload.get("dev_result")) or _mapping(payload)
        task_id = _text(raw.get("task_id"))
        if not task_id:
            raise ValueError("task_id is required")
        with _LOCK:
            state = self._read_state()
            task = self._require_task(state, task_id)
            node_id = _text(raw.get("node_id"))
            self._validate_assigned_node(task, node_id)
            self._ensure_node_credentials_active(state, node_id)
            if not _mapping(raw.get("provenance")):
                raise ValueError("result provenance is required")
            result = self._normalize_result(raw, task)
            self.validate_result_paths(task, result)
            if _text(task.get("status")) == "completed":
                existing = _mapping(task.get("result"))
                if (
                    _text(existing.get("branch")) == result["branch"]
                    and _text(existing.get("commit_hash")) == result["commit_hash"]
                    and (_text(existing.get("node_id")) or node_id) == (node_id or _text(existing.get("node_id")))
                ):
                    ready_event = self._ready_event(task, existing)
                    return {"ok": True, "duplicate": True, "task": _json_clone(task), "ready_event": ready_event}
                raise ValueError(f"task '{task_id}' is already completed with a different result")
            if _text(task.get("status")) in {"failed", "cancelled", "expired"}:
                raise ValueError(f"task '{task_id}' is terminal: {task.get('status')}")
            now = _now_iso()
            task["status"] = "completed"
            task["result"] = result
            task["dependency_delta"] = _mapping(result.get("dependency_delta"))
            task["provenance"] = _mapping(result.get("provenance"))
            task["completed_at"] = now
            task["updated_at"] = now
            self._release_node_after_task(state, task, status="cleanup")
            ready_event = self._ready_event(task, result)
            state["tasks"][task_id] = task
            state.setdefault("ready_events", []).append(ready_event)
            self._append_event(state, "skill_factory.task_completed", {"task_id": task_id, "branch": result["branch"]})
            self._write_state(state)
            return {"ok": True, "task": _json_clone(task), "ready_event": ready_event}

    def fail_task(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw = _mapping(payload.get("failure")) or _mapping(payload)
        task_id = _text(raw.get("task_id"))
        if not task_id:
            raise ValueError("task_id is required")
        with _LOCK:
            state = self._read_state()
            task = self._require_task(state, task_id)
            node_id = _text(raw.get("node_id"))
            self._validate_assigned_node(task, node_id)
            self._ensure_node_credentials_active(state, node_id)
            failure = self._normalize_failure(raw)
            failures = _list(task.get("failure_history"))
            for existing in failures:
                if _text(_mapping(existing).get("failure_id")) == failure["failure_id"]:
                    return {"ok": True, "duplicate": True, "retry_queued": _text(task.get("status")) == "queued", "task": _json_clone(task), "failure": _json_clone(existing)}
            if _text(task.get("status")) == "completed":
                raise ValueError(f"task '{task_id}' is already completed")
            failures.append(failure)
            task["failure_history"] = failures
            retry_requested = bool(raw.get("retry_requested") or raw.get("retry"))
            can_retry = retry_requested and int(task.get("attempts") or 0) < int(task.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
            now = _now_iso()
            self._release_node_after_task(state, task, status="waiting" if can_retry else "failed")
            if can_retry:
                task["status"] = "queued"
                task["assigned_node_id"] = None
                task["assigned_at"] = None
                task["timeout_at"] = None
            else:
                task["status"] = "failed"
                task["failed_at"] = now
            task["updated_at"] = now
            state["tasks"][task_id] = task
            self._append_event(state, "skill_factory.task_failed", {"task_id": task_id, "retry": can_retry})
            self._write_state(state)
            return {"ok": True, "retry_queued": can_retry, "task": _json_clone(task), "failure": failure}

    def cancel_task(self, task_id: str, *, reason: str | None = None, actor: str | None = None) -> dict[str, Any]:
        task_token = _text(task_id)
        if not task_token:
            raise ValueError("task_id is required")
        with _LOCK:
            state = self._read_state()
            task = self._require_task(state, task_token)
            if _text(task.get("status")) == "cancelled":
                return {"ok": True, "duplicate": True, "task": _json_clone(task)}
            now = _now_iso()
            task["status"] = "cancelled"
            task["cancellation_requested"] = True
            task["cancelled_at"] = now
            task["updated_at"] = now
            task["cancellation"] = {"reason": _text(reason), "actor": _text(actor), "at": now}
            self._release_node_after_task(state, task, status="waiting")
            state["tasks"][task_token] = task
            self._append_event(state, "skill_factory.task_cancelled", {"task_id": task_token, "reason": reason})
            self._write_state(state)
            return {"ok": True, "task": _json_clone(task)}

    def set_queue_paused(self, *, paused: bool, reason: str | None = None, actor: str | None = None) -> dict[str, Any]:
        with _LOCK:
            state = self._read_state()
            queue = _mapping(state.get("queue"))
            queue["paused"] = bool(paused)
            queue["reason"] = _text(reason)
            queue["actor"] = _text(actor)
            queue["updated_at"] = _now_iso()
            state["queue"] = queue
            self._append_event(state, "skill_factory.queue_paused" if paused else "skill_factory.queue_resumed", {"reason": reason, "actor": actor})
            self._write_state(state)
            return {"ok": True, "queue": _json_clone(queue), "summary": self._queue_summary(state)}

    def drain_dev_node(self, node_id: str, *, reason: str | None = None, actor: str | None = None) -> dict[str, Any]:
        node_token = _text(node_id)
        if not node_token:
            raise ValueError("node_id is required")
        with _LOCK:
            state = self._read_state()
            node = _mapping(state["dev_nodes"].get(node_token))
            if not node:
                raise KeyError(node_token)
            node["draining"] = True
            node["status"] = "draining"
            node["drain"] = {"reason": _text(reason), "actor": _text(actor), "at": _now_iso()}
            node["updated_at"] = node["drain"]["at"]
            state["dev_nodes"][node_token] = node
            self._append_event(state, "skill_factory.dev_node_draining", {"node_id": node_token, "reason": reason, "actor": actor})
            self._write_state(state)
            return {"ok": True, "node": _json_clone(node), "queue": self._queue_summary(state)}

    def quarantine_dev_node(self, node_id: str, *, reason: str | None = None, actor: str | None = None) -> dict[str, Any]:
        node_token = _text(node_id)
        if not node_token:
            raise ValueError("node_id is required")
        with _LOCK:
            state = self._read_state()
            node = _mapping(state["dev_nodes"].get(node_token))
            if not node:
                raise KeyError(node_token)
            node["quarantined"] = True
            node["status"] = "quarantined"
            node["quarantine"] = {"reason": _text(reason), "actor": _text(actor), "at": _now_iso()}
            node["updated_at"] = node["quarantine"]["at"]
            state["dev_nodes"][node_token] = node
            self._append_event(state, "skill_factory.dev_node_quarantined", {"node_id": node_token, "reason": reason, "actor": actor})
            self._write_state(state)
            return {"ok": True, "node": _json_clone(node), "queue": self._queue_summary(state)}

    def revoke_dev_node_credentials(self, node_id: str, *, reason: str | None = None, actor: str | None = None) -> dict[str, Any]:
        node_token = _text(node_id)
        if not node_token:
            raise ValueError("node_id is required")
        with _LOCK:
            state = self._read_state()
            node = _mapping(state["dev_nodes"].get(node_token))
            if not node:
                raise KeyError(node_token)
            now = _now_iso()
            node["credentials_revoked"] = True
            node["quarantined"] = True
            node["status"] = "credentials_revoked"
            node["credential_revocation"] = {"reason": _text(reason), "actor": _text(actor), "at": now}
            node["updated_at"] = now
            state["dev_nodes"][node_token] = node
            self._append_event(state, "skill_factory.dev_node_credentials_revoked", {"node_id": node_token, "reason": reason, "actor": actor})
            self._write_state(state)
            return {"ok": True, "node": _json_clone(node), "queue": self._queue_summary(state)}

    def retry_task(self, task_id: str, *, reason: str | None = None, actor: str | None = None, avoid_previous_node: bool = True) -> dict[str, Any]:
        task_token = _text(task_id)
        if not task_token:
            raise ValueError("task_id is required")
        with _LOCK:
            state = self._read_state()
            task = self._require_task(state, task_token)
            status = _text(task.get("status"))
            if status not in {"failed", "expired", "cancelled"}:
                raise ValueError(f"task '{task_token}' cannot be retried from status '{status}'")
            previous_node = _text(task.get("assigned_node_id"))
            avoid = _string_list(task.get("avoid_node_ids"))
            if avoid_previous_node and previous_node and previous_node not in avoid:
                avoid.append(previous_node)
            now = _now_iso()
            task["status"] = "queued"
            task["retry_requested"] = True
            task["retry"] = {"reason": _text(reason), "actor": _text(actor), "at": now, "avoid_previous_node": bool(avoid_previous_node)}
            task["assigned_node_id"] = None
            task["assigned_at"] = None
            task["timeout_at"] = None
            task["avoid_node_ids"] = avoid
            task["updated_at"] = now
            state["tasks"][task_token] = task
            if previous_node and previous_node in state["dev_nodes"]:
                node = _mapping(state["dev_nodes"].get(previous_node))
                assigned = [item for item in _string_list(node.get("assigned_tasks")) if item != task_token]
                node["assigned_tasks"] = assigned
                node["current_task_id"] = assigned[0] if assigned else None
                if _text(node.get("status")) not in {"quarantined", "draining"}:
                    node["status"] = "waiting"
                node["updated_at"] = now
                state["dev_nodes"][previous_node] = node
            self._append_event(state, "skill_factory.task_retry_queued", {"task_id": task_token, "reason": reason, "actor": actor, "avoid_node_ids": avoid})
            self._write_state(state)
            return {"ok": True, "task": _json_clone(task), "queue": self._queue_summary(state)}

    def validate_result_paths(self, task: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        expected_branch = _text(_mapping(task.get("forge")).get("branch"))
        branch = _text(result.get("branch"))
        if not branch:
            raise ValueError("result branch is required")
        if branch != expected_branch:
            raise ValueError(f"result branch '{branch}' does not match expected task branch '{expected_branch}'")
        if not branch.startswith(TASK_BRANCH_PREFIX):
            raise ValueError(f"result branch must start with '{TASK_BRANCH_PREFIX}'")
        if not _text(result.get("commit_hash")):
            raise ValueError("result commit_hash is required")
        changed_paths = _string_list(result.get("changed_paths"))
        if not changed_paths:
            raise ValueError("result changed_paths must not be empty")
        allowed_dirs = _normalize_sparse_paths(_mapping(task.get("forge")).get("sparse_paths"))
        for changed_path in changed_paths:
            _normalize_repo_path(changed_path, directory=False)
            if not any(_changed_path_allowed(changed_path, allowed) for allowed in allowed_dirs):
                raise ValueError(f"result changed path is outside the task sparse checkout: {changed_path}")
        provenance = _mapping(result.get("provenance"))
        if not provenance:
            raise ValueError("result provenance is required")
        expected = _mapping(_mapping(task.get("evidence")).get("expected_paths")) or _expected_evidence_paths(_text(task.get("task_id")))
        provenance_path = _text(expected.get("provenance"))
        if provenance_path and provenance_path not in changed_paths:
            raise ValueError(f"result must include provenance evidence path: {provenance_path}")

    def snapshot(self, *, include_tasks: bool = True) -> dict[str, Any]:
        with _LOCK:
            state = self._read_state()
            self._expire_overdue_tasks(state)
            self._write_state(state)
            tasks = list(state["tasks"].values()) if include_tasks else []
            nodes = list(state["dev_nodes"].values())
            return {
                "ok": True,
                "schema": STATE_SCHEMA,
                "state_path": str(self.state_path),
                "forge": state.get("forge", self.forge_policy()),
                "queue": self._queue_summary(state),
                "dev_nodes": [_json_clone(item) for item in nodes],
                "tasks": [_json_clone(item) for item in tasks],
                "ready_events": _json_clone(state.get("ready_events", []))[-50:],
                "diagnostics": {
                    "node_count": len(nodes),
                    "task_count": len(state["tasks"]),
                    "ready_event_count": len(state.get("ready_events", [])),
                    "published_status": "root.skill_factory.state",
                },
            }

    def _task_sparse_paths(self, task_id: str, request_paths: list[str]) -> list[str]:
        paths = list(request_paths)
        internal = _task_internal_path(task_id)
        if internal not in paths:
            paths.append(internal)
        return paths

    def _initial_state(self) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA,
            "version": 1,
            "forge": self.forge_policy(),
            "queue": {"paused": False, "reason": "", "updated_at": None, "actor": ""},
            "dev_nodes": {},
            "tasks": {},
            "ready_events": [],
            "events": [],
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }

    def _read_state(self) -> dict[str, Any]:
        path = self.state_path
        if not path.exists():
            return self._initial_state()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        state = self._initial_state()
        for key in ("forge", "queue", "dev_nodes", "tasks", "ready_events", "events", "created_at"):
            if key in data:
                state[key] = data[key]
        state["updated_at"] = data.get("updated_at") or state["updated_at"]
        if not isinstance(state.get("queue"), dict):
            state["queue"] = {"paused": False, "reason": "", "updated_at": None, "actor": ""}
        if not isinstance(state.get("dev_nodes"), dict):
            state["dev_nodes"] = {}
        if not isinstance(state.get("tasks"), dict):
            state["tasks"] = {}
        if not isinstance(state.get("ready_events"), list):
            state["ready_events"] = []
        if not isinstance(state.get("events"), list):
            state["events"] = []
        return state

    def _write_state(self, state: Mapping[str, Any]) -> None:
        payload = _json_clone(state)
        payload["schema"] = STATE_SCHEMA
        payload["updated_at"] = _now_iso()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _append_event(self, state: dict[str, Any], event_type: str, payload: Mapping[str, Any]) -> None:
        events = _list(state.get("events"))
        events.append({"type": event_type, "at": _now_iso(), "payload": dict(payload)})
        state["events"] = events[-500:]

    def _queue_summary(self, state: Mapping[str, Any]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for task in _mapping(state.get("tasks")).values():
            status = _text(_mapping(task).get("status")) or "unknown"
            counts[status] = counts.get(status, 0) + 1
        node_counts: dict[str, int] = {}
        for node in _mapping(state.get("dev_nodes")).values():
            status = _text(_mapping(node).get("status")) or "unknown"
            node_counts[status] = node_counts.get(status, 0) + 1
        queue = _mapping(state.get("queue"))
        return {
            "task_states": counts,
            "node_states": node_counts,
            "paused": bool(queue.get("paused")),
            "pause_reason": _text(queue.get("reason")),
            "queued": counts.get("queued", 0),
            "assigned_or_active": sum(counts.get(status, 0) for status in TASK_ACTIVE_STATES),
            "terminal": sum(counts.get(status, 0) for status in TASK_TERMINAL_STATES),
        }

    def _require_task(self, state: Mapping[str, Any], task_id: str) -> dict[str, Any]:
        task = _mapping(_mapping(state.get("tasks")).get(task_id))
        if not task:
            raise KeyError(task_id)
        return task

    def _validate_assigned_node(self, task: Mapping[str, Any], node_id: str) -> None:
        assigned = _text(task.get("assigned_node_id"))
        if assigned and node_id and node_id != assigned:
            raise ValueError(f"task '{task.get('task_id')}' is assigned to node '{assigned}', not '{node_id}'")

    def _ensure_node_credentials_active(self, state: Mapping[str, Any], node_id: str) -> None:
        if not node_id:
            return
        node = _mapping(_mapping(state.get("dev_nodes")).get(node_id))
        if bool(node.get("credentials_revoked")) or _text(node.get("status")) == "credentials_revoked":
            raise ValueError(f"dev node '{node_id}' credentials are revoked")

    def _normalize_task_status(self, value: Any) -> str:
        token = _text(value).lower()
        aliases = {
            "preparing": "workspace_preparing",
            "preparing_workspace": "workspace_preparing",
            "developing": "in_progress",
            "testing": "tests_running",
            "committing": "commit_ready",
            "done": "completed",
        }
        token = aliases.get(token, token)
        allowed = {"queued", "assigned", "workspace_preparing", "in_progress", "tests_running", "commit_ready", *TASK_TERMINAL_STATES}
        if token not in allowed:
            token = "in_progress"
        return token

    def _normalize_result(self, raw: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
        changed_paths = [_normalize_repo_path(item, directory=False) for item in _string_list(raw.get("changed_paths"))]
        provenance = _result_provenance(raw, task)
        dependency_delta = _dependency_delta(raw, changed_paths)
        return {
            **dict(raw),
            "schema": DEV_RESULT_SCHEMA,
            "task_id": _text(raw.get("task_id")),
            "node_id": _text(raw.get("node_id")) or None,
            "status": "completed",
            "commit_hash": _text(raw.get("commit_hash")),
            "branch": _text(raw.get("branch")),
            "changed_paths": changed_paths,
            "tests": _mapping(raw.get("tests")),
            "validation": _mapping(raw.get("validation")),
            "provenance": provenance,
            "dependency_delta": dependency_delta,
            "notes": _string_list(raw.get("notes")),
            "open_questions": _string_list(raw.get("open_questions")),
            "reported_at": _text(raw.get("reported_at")) or _now_iso(),
        }

    def _normalize_failure(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        message = _text(raw.get("message") or raw.get("error") or raw.get("reason"))
        if not message:
            raise ValueError("failure message is required")
        failure_id = _text(raw.get("failure_id"))
        if not failure_id:
            failure_id = f"failure.{_text(raw.get('task_id'))}.{_stable_suffix({'node_id': raw.get('node_id'), 'stage': raw.get('stage'), 'message': message})}"
        return {
            **dict(raw),
            "schema": DEV_TASK_FAILURE_SCHEMA,
            "failure_id": failure_id,
            "task_id": _text(raw.get("task_id")),
            "node_id": _text(raw.get("node_id")) or None,
            "status": "failed",
            "failure_class": _text(raw.get("failure_class")) or "dev_node_failure",
            "message": message,
            "stage": _text(raw.get("stage")) or None,
            "retryable": bool(raw.get("retryable", True)),
            "retry_requested": bool(raw.get("retry_requested") or raw.get("retry")),
            "logs_ref": _text(raw.get("logs_ref")) or None,
            "details": _mapping(raw.get("details")),
            "reported_at": _text(raw.get("reported_at")) or _now_iso(),
        }

    def _assignment_payload(self, task: Mapping[str, Any], node: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _text(task.get("task_id"))
        forge = _mapping(task.get("forge"))
        mcp = _mapping(task.get("mcp"))
        return {
            "schema": DEV_TASK_ASSIGNMENT_SCHEMA,
            "task_id": task_id,
            "request_id": task.get("request_id"),
            "subnet_id": _mapping(task.get("realize_request")).get("user_subnet_id"),
            "target": _mapping(task.get("target")),
            "forge": {
                "repo_url": forge.get("repo_url"),
                "forge_project": forge.get("forge_project"),
                "base_branch": forge.get("base_branch"),
                "branch": forge.get("branch"),
                "branch_creator": forge.get("branch_creator") or "dev_node",
                "sparse_paths": _normalize_sparse_paths(forge.get("sparse_paths")),
            },
            "mcp": {
                "endpoint": _text(mcp.get("endpoint")) or f"/v1/root/mcp/task/{task_id}",
                "token_ref": _text(mcp.get("token_ref")) or f"task_mcp_token:{task_id}",
                "scope": _assignment_mcp_scope(mcp.get("requested_scope")),
            },
            "codex": {
                "instruction_file": f".adaos/tasks/{_safe_branch_fragment(task_id)}/task.md",
                "working_dir": "workspace/",
                "mode": "autonomous_bounded",
            },
            "policy": {
                "network": "restricted",
                "secrets_visible_to_llm": False,
                "require_tests": bool(_mapping(task.get("constraints")).get("must_add_tests", True)),
                "require_commit": True,
                "cleanup_after_completion": True,
                "allowed_result_paths": _normalize_sparse_paths(forge.get("sparse_paths")),
                "realization": _mapping(task.get("realization_policy")),
            },
            "snapshot_context": _mapping(task.get("snapshot_context")),
            "evidence": _mapping(task.get("evidence")) or {
                "schema": "adaos.skill_factory.task_evidence.v1",
                "expected_paths": _expected_evidence_paths(task_id),
                "provenance_required": True,
                "dependency_delta_review": True,
            },
            "acceptance": _mapping(task.get("acceptance")),
            "constraints": _mapping(task.get("constraints")),
            "source_refs": _mapping(task.get("source_refs")),
            "assigned_to": {"node_id": node.get("node_id"), "capabilities": _string_list(node.get("capabilities"))},
            "created_at": _now_iso(),
        }

    def _ready_event(self, task: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _text(task.get("task_id"))
        return {
            "schema": DEV_READY_EVENT_SCHEMA,
            "event_id": f"ready.{task_id}.{_stable_suffix(result)}",
            "task_id": task_id,
            "subnet_id": _mapping(task.get("realize_request")).get("user_subnet_id"),
            "target": _mapping(task.get("target")),
            "forge": {
                "repo_url": _mapping(task.get("forge")).get("repo_url"),
                "branch": result.get("branch"),
                "commit_hash": result.get("commit_hash"),
            },
            "result": {
                "status": result.get("status"),
                "tests": _mapping(result.get("tests")).get("status"),
                "changed_paths": _string_list(result.get("changed_paths")),
            },
            "next_action": ["pull_revision", "validate_locally", "show_to_user"],
            "created_at": _now_iso(),
        }

    def _release_node_after_task(self, state: dict[str, Any], task: Mapping[str, Any], *, status: str) -> None:
        node_id = _text(task.get("assigned_node_id"))
        if not node_id:
            return
        node = _mapping(state["dev_nodes"].get(node_id))
        if not node:
            return
        task_id = _text(task.get("task_id"))
        assigned = [item for item in _string_list(node.get("assigned_tasks")) if item != task_id]
        node["assigned_tasks"] = assigned
        node["current_task_id"] = assigned[0] if assigned else None
        node["status"] = status
        node["updated_at"] = _now_iso()
        state["dev_nodes"][node_id] = node

    def _expire_overdue_tasks(self, state: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        changed = False
        for task_id, task in list(state["tasks"].items()):
            if _text(task.get("status")) not in TASK_ACTIVE_STATES:
                continue
            raw_timeout = _text(task.get("timeout_at"))
            if not raw_timeout:
                continue
            try:
                timeout_at = datetime.fromisoformat(raw_timeout)
            except Exception:
                continue
            if timeout_at.tzinfo is None:
                timeout_at = timeout_at.replace(tzinfo=timezone.utc)
            if timeout_at <= now:
                task["status"] = "expired"
                task["expired_at"] = _now_iso()
                task["updated_at"] = task["expired_at"]
                self._release_node_after_task(state, task, status="failed")
                state["tasks"][task_id] = task
                self._append_event(state, "skill_factory.task_expired", {"task_id": task_id})
                changed = True
        if changed:
            state["updated_at"] = _now_iso()


def get_skill_factory_service() -> SkillFactoryService:
    return SkillFactoryService()


__all__ = [
    "DEV_NODE_REGISTRATION_SCHEMA",
    "DEV_READY_EVENT_SCHEMA",
    "DEV_RESULT_SCHEMA",
    "DEV_TASK_ASSIGNMENT_SCHEMA",
    "DEV_TASK_FAILURE_SCHEMA",
    "REALIZE_REQUEST_SCHEMA",
    "SkillFactoryService",
    "get_skill_factory_service",
]
