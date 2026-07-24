from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from adaos.services.runtime_paths import current_state_dir


OBSERVE_CHECKS = (
    "ssh_connect",
    "service_active",
    "runtime_ping",
    "supervisor_status",
    "version_identity",
)
TERMINAL_ASSIGNMENT_STATES = frozenset({"passed", "failed", "inconclusive", "timed_out"})
TERMINAL_CAMPAIGN_STATES = frozenset({"passed", "failed", "inconclusive", "cancelled"})
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")
_HOST_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_POSIX_PATH_RE = re.compile(r"^/[a-zA-Z0-9_./-]+$")
_SLOT_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_MAX_EVENTS = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_id(value: str, field_name: str) -> str:
    value = str(value or "").strip()
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid_{field_name}")
    return value


def _validate_port(value: int, field_name: str) -> int:
    value = int(value)
    if value < 1 or value > 65535:
        raise ValueError(f"invalid_{field_name}")
    return value


def _string_tuple(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in values if str(value).strip())


def _assignment_id(campaign_id: str, node_id: str, attempt: int = 1) -> str:
    digest = hashlib.sha256(f"{campaign_id}:{node_id}:{attempt}".encode("utf-8")).hexdigest()[:24]
    return f"assignment-{digest}"


def _target_matches(target_build: str, observed: Iterable[Any]) -> bool:
    target = str(target_build or "").strip()
    candidates = {
        str(value).strip()
        for value in observed
        if value is not None and str(value).strip()
    }
    if target in candidates:
        return True
    if not _GIT_COMMIT_RE.fullmatch(target):
        return False
    target_lower = target.lower()
    return any(
        _GIT_COMMIT_RE.fullmatch(candidate) and candidate.lower().startswith(target_lower)
        for candidate in candidates
    )


@dataclass(slots=True)
class TestNode:
    node_id: str
    display_name: str
    host: str
    identity_file: str
    ssh_user: str = "root"
    ssh_port: int = 22
    runtime_port: int = 8778
    supervisor_port: int = 8776
    base_dir: str = "/root/.adaos"
    transport: str = "ssh"
    capabilities: tuple[str, ...] = ("adaos.runtime.observe",)
    allowed_profiles: tuple[str, ...] = ("observe",)
    enabled: bool = True
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.node_id = _validate_id(self.node_id, "node_id")
        self.display_name = str(self.display_name or self.node_id).strip()[:160]
        self.host = str(self.host or "").strip()
        if not _HOST_RE.fullmatch(self.host):
            raise ValueError("invalid_host")
        self.ssh_user = str(self.ssh_user or "").strip()
        if not _USER_RE.fullmatch(self.ssh_user):
            raise ValueError("invalid_ssh_user")
        self.identity_file = str(self.identity_file or "").strip()
        if not self.identity_file or "\n" in self.identity_file or "\r" in self.identity_file:
            raise ValueError("invalid_identity_file")
        self.ssh_port = _validate_port(self.ssh_port, "ssh_port")
        self.runtime_port = _validate_port(self.runtime_port, "runtime_port")
        self.supervisor_port = _validate_port(self.supervisor_port, "supervisor_port")
        self.base_dir = str(self.base_dir or "").rstrip("/")
        if not _POSIX_PATH_RE.fullmatch(self.base_dir) or ".." in Path(self.base_dir).parts:
            raise ValueError("invalid_base_dir")
        if self.transport != "ssh":
            raise ValueError("unsupported_transport")
        self.capabilities = _string_tuple(self.capabilities)
        self.allowed_profiles = _string_tuple(self.allowed_profiles)
        if self.allowed_profiles != ("observe",):
            raise ValueError("only_observe_profile_is_supported")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TestNode":
        return cls(**dict(value))

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if public:
            value["identity_file"] = "<configured>" if self.identity_file else "<missing>"
        return value


@dataclass(slots=True)
class TestSuite:
    suite_id: str
    version: str
    display_name: str
    checks: tuple[str, ...] = OBSERVE_CHECKS
    profile: str = "observe"
    required_capabilities: tuple[str, ...] = ("adaos.runtime.observe",)
    timeout_s: float = 90.0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.suite_id = _validate_id(self.suite_id, "suite_id")
        self.version = str(self.version or "").strip()[:64]
        if not self.version:
            raise ValueError("invalid_suite_version")
        self.display_name = str(self.display_name or self.suite_id).strip()[:160]
        self.checks = _string_tuple(self.checks)
        if not self.checks or len(self.checks) != len(set(self.checks)):
            raise ValueError("invalid_suite_checks")
        unsupported = sorted(set(self.checks) - set(OBSERVE_CHECKS))
        if unsupported:
            raise ValueError(f"unsupported_observe_checks:{','.join(unsupported)}")
        if self.profile != "observe":
            raise ValueError("only_observe_profile_is_supported")
        self.required_capabilities = _string_tuple(self.required_capabilities)
        self.timeout_s = float(self.timeout_s)
        if self.timeout_s < 5 or self.timeout_s > 300:
            raise ValueError("invalid_suite_timeout")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TestSuite":
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ValidationCampaign:
    campaign_id: str
    suite_id: str
    target_build: str
    node_ids: tuple[str, ...]
    quorum: int = 1
    state: str = "pending"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.campaign_id = _validate_id(self.campaign_id, "campaign_id")
        self.suite_id = _validate_id(self.suite_id, "suite_id")
        self.target_build = str(self.target_build or "").strip()
        if not self.target_build or len(self.target_build) > 128 or any(ch.isspace() for ch in self.target_build):
            raise ValueError("invalid_target_build")
        self.node_ids = _string_tuple(self.node_ids)
        if not self.node_ids or len(self.node_ids) != len(set(self.node_ids)):
            raise ValueError("invalid_campaign_nodes")
        for node_id in self.node_ids:
            _validate_id(node_id, "node_id")
        self.quorum = int(self.quorum)
        if self.quorum < 1 or self.quorum > len(self.node_ids):
            raise ValueError("invalid_campaign_quorum")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationCampaign":
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ValidationAssignment:
    assignment_id: str
    campaign_id: str
    node_id: str
    attempt: int = 1
    idempotency_key: str = ""
    state: str = "assigned"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.assignment_id = _validate_id(self.assignment_id, "assignment_id")
        self.campaign_id = _validate_id(self.campaign_id, "campaign_id")
        self.node_id = _validate_id(self.node_id, "node_id")
        self.attempt = int(self.attempt)
        if self.attempt < 1:
            raise ValueError("invalid_assignment_attempt")
        if not self.idempotency_key:
            self.idempotency_key = f"{self.campaign_id}:{self.node_id}:{self.attempt}"

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationAssignment":
        return cls(**dict(value))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CommandExecutor = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def _default_executor(argv: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )


class SshObserveRunner:
    def __init__(self, *, executor: CommandExecutor | None = None) -> None:
        self._executor = executor or _default_executor

    @staticmethod
    def _ssh_argv(node: TestNode, command: str) -> list[str]:
        return [
            "ssh",
            "-i",
            node.identity_file,
            "-p",
            str(node.ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            f"{node.ssh_user}@{node.host}",
            command,
        ]

    def _execute(self, node: TestNode, command: str, timeout_s: float) -> subprocess.CompletedProcess[str]:
        return self._executor(self._ssh_argv(node, command), timeout_s)

    @staticmethod
    def _check(check_id: str, started: float, status: str, detail: str, evidence: Any = None) -> dict[str, Any]:
        return {
            "check_id": check_id,
            "status": status,
            "detail": detail[:500],
            "evidence": evidence,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }

    @staticmethod
    def _transport_error(check_id: str, started: float, result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        stderr = str(result.stderr or "").strip()[-1000:]
        return SshObserveRunner._check(
            check_id,
            started,
            "error",
            f"ssh_transport_error:{result.returncode}",
            {"stderr": stderr},
        )

    def run(self, node: TestNode, suite: TestSuite, target_build: str) -> dict[str, Any]:
        if not node.enabled:
            return {"state": "inconclusive", "reason": "node_disabled", "checks": []}
        if suite.profile not in node.allowed_profiles:
            return {"state": "inconclusive", "reason": "profile_not_allowed", "checks": []}
        missing = sorted(set(suite.required_capabilities) - set(node.capabilities))
        if missing:
            return {
                "state": "inconclusive",
                "reason": "missing_capabilities",
                "missing_capabilities": missing,
                "checks": [],
            }
        if not Path(node.identity_file).expanduser().is_file():
            return {"state": "inconclusive", "reason": "identity_file_missing", "checks": []}

        checks: list[dict[str, Any]] = []
        timeout_s = max(15.0, min(30.0, suite.timeout_s / max(1, len(suite.checks))))
        commands = {
            "ssh_connect": "true",
            "service_active": "systemctl is-active adaos.service",
            "runtime_ping": f"curl -fsS --max-time 3 http://127.0.0.1:{node.runtime_port}/api/ping",
            "supervisor_status": (
                f"curl -fsS --max-time 3 http://127.0.0.1:{node.supervisor_port}"
                "/api/supervisor/public/update-status"
            ),
            "version_identity": (
                f"slot=$(cat {node.base_dir}/state/core_slots/active) && "
                f"printf '%s\\n' \"$slot\" && cat {node.base_dir}/state/core_slots/slots/\"$slot\"/manifest.json"
            ),
        }

        for check_id in suite.checks:
            started = time.monotonic()
            try:
                result = self._execute(node, commands[check_id], timeout_s)
            except subprocess.TimeoutExpired:
                checks.append(self._check(check_id, started, "error", "ssh_timeout"))
                if check_id == "ssh_connect":
                    return {"state": "inconclusive", "reason": "ssh_connect_timed_out", "checks": checks}
                return {"state": "timed_out", "reason": f"{check_id}_timed_out", "checks": checks}
            except (OSError, ValueError) as exc:
                checks.append(self._check(check_id, started, "error", f"runner_unavailable:{type(exc).__name__}"))
                return {"state": "inconclusive", "reason": "runner_unavailable", "checks": checks}

            if result.returncode == 255:
                checks.append(self._transport_error(check_id, started, result))
                return {"state": "inconclusive", "reason": "ssh_transport_error", "checks": checks}

            check = self._evaluate(check_id, started, result, target_build)
            checks.append(check)
            if check["status"] != "passed":
                return {"state": "failed", "reason": f"{check_id}_failed", "checks": checks}
        return {"state": "passed", "reason": "all_observe_checks_passed", "checks": checks}

    def _evaluate(
        self,
        check_id: str,
        started: float,
        result: subprocess.CompletedProcess[str],
        target_build: str,
    ) -> dict[str, Any]:
        stdout = str(result.stdout or "").strip()
        stderr = str(result.stderr or "").strip()[-1000:]
        if result.returncode != 0:
            return self._check(
                check_id,
                started,
                "failed",
                f"command_exit:{result.returncode}",
                {"stdout": stdout[-1000:], "stderr": stderr},
            )
        if check_id == "ssh_connect":
            return self._check(check_id, started, "passed", "ssh_connected")
        if check_id == "service_active":
            ok = stdout == "active"
            return self._check(check_id, started, "passed" if ok else "failed", stdout or "empty_status", stdout)
        if check_id in {"runtime_ping", "supervisor_status"}:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                return self._check(check_id, started, "failed", "invalid_json", stdout[-1000:])
            if check_id == "runtime_ping":
                ok = payload.get("ok") is True and payload.get("service") == "adaos-runtime"
                evidence = {
                    "ok": payload.get("ok"),
                    "service": payload.get("service"),
                    "runtime": payload.get("runtime"),
                }
                detail = "runtime_ready" if ok else "runtime_ping_not_ready"
            else:
                runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
                ok = (
                    payload.get("ok") is True
                    and runtime.get("runtime_state") == "ready"
                    and runtime.get("listener_running") is True
                    and runtime.get("runtime_api_ready") is True
                )
                evidence = {
                    "ok": payload.get("ok"),
                    "active_slot": runtime.get("active_slot"),
                    "runtime_state": runtime.get("runtime_state"),
                    "listener_running": runtime.get("listener_running"),
                    "runtime_api_ready": runtime.get("runtime_api_ready"),
                }
                detail = "supervisor_runtime_ready" if ok else "supervisor_runtime_not_ready"
            return self._check(check_id, started, "passed" if ok else "failed", detail, evidence)

        lines = stdout.splitlines()
        slot = lines[0].strip() if lines else ""
        if not _SLOT_RE.fullmatch(slot):
            return self._check(check_id, started, "failed", "invalid_active_slot", slot)
        try:
            manifest = json.loads("\n".join(lines[1:]))
        except json.JSONDecodeError:
            return self._check(check_id, started, "failed", "invalid_slot_manifest")
        observed = {
            key: manifest.get(key)
            for key in (
                "target_version",
                "requested_target_version",
                "resolved_target_version",
                "build_version",
                "base_version",
                "git_commit",
            )
        }
        ok = _target_matches(target_build, observed.values())
        evidence = {"active_slot": slot, "expected_target": target_build, **observed}
        detail = "target_build_observed" if ok else "target_build_mismatch"
        return self._check(check_id, started, "passed" if ok else "failed", detail, evidence)


class ReleaseValidationService:
    def __init__(
        self,
        *,
        state_path: Path | str | None = None,
        runner: SshObserveRunner | None = None,
    ) -> None:
        configured = str(os.getenv("ADAOS_RELEASE_VALIDATION_STATE_PATH") or "").strip()
        self.state_path = Path(state_path or configured or (current_state_dir() / "release_validation" / "state.json"))
        self.runner = runner or SshObserveRunner()
        self._lock = threading.RLock()
        self._state = self._load()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "nodes": {},
            "suites": {},
            "campaigns": {},
            "assignments": {},
            "events": [],
            "updated_at": _now(),
        }

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_state()
        except (OSError, json.JSONDecodeError):
            return self._empty_state()
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return self._empty_state()
        state = self._empty_state()
        for key in ("nodes", "suites", "campaigns", "assignments"):
            if isinstance(raw.get(key), dict):
                state[key] = dict(raw[key])
        if isinstance(raw.get("events"), list):
            state["events"] = list(raw["events"])[-_MAX_EVENTS:]
        state["updated_at"] = str(raw.get("updated_at") or _now())
        return state

    def _save_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = _now()
        text = json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True)
        temporary = self.state_path.with_name(f"{self.state_path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        temporary.write_text(text, encoding="utf-8")
        deadline = time.monotonic() + 1.0
        try:
            while True:
                try:
                    temporary.replace(self.state_path)
                    return
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()

    def _event_locked(self, event_type: str, **payload: Any) -> None:
        self._state["events"].append(
            {"event_id": uuid.uuid4().hex, "type": event_type, "at": _now(), "payload": payload}
        )
        self._state["events"] = self._state["events"][-_MAX_EVENTS:]

    def register_node(self, node: TestNode | Mapping[str, Any]) -> dict[str, Any]:
        model = node if isinstance(node, TestNode) else TestNode.from_dict(node)
        with self._lock:
            existing = self._state["nodes"].get(model.node_id)
            if isinstance(existing, dict):
                model.created_at = str(existing.get("created_at") or model.created_at)
                model.updated_at = _now()
            self._state["nodes"][model.node_id] = model.to_dict()
            self._event_locked("test_node.registered", node_id=model.node_id, profile="observe")
            self._save_locked()
            return model.to_dict(public=True)

    def register_suite(self, suite: TestSuite | Mapping[str, Any]) -> dict[str, Any]:
        model = suite if isinstance(suite, TestSuite) else TestSuite.from_dict(suite)
        with self._lock:
            existing = self._state["suites"].get(model.suite_id)
            if isinstance(existing, dict):
                model.created_at = str(existing.get("created_at") or model.created_at)
                model.updated_at = _now()
            self._state["suites"][model.suite_id] = model.to_dict()
            self._event_locked("test_suite.registered", suite_id=model.suite_id, version=model.version)
            self._save_locked()
            return model.to_dict()

    def create_campaign(self, campaign: ValidationCampaign | Mapping[str, Any]) -> dict[str, Any]:
        model = campaign if isinstance(campaign, ValidationCampaign) else ValidationCampaign.from_dict(campaign)
        with self._lock:
            if model.campaign_id in self._state["campaigns"]:
                raise ValueError("campaign_already_exists")
            if model.suite_id not in self._state["suites"]:
                raise KeyError("suite_not_found")
            missing = [node_id for node_id in model.node_ids if node_id not in self._state["nodes"]]
            if missing:
                raise KeyError(f"nodes_not_found:{','.join(missing)}")
            self._state["campaigns"][model.campaign_id] = model.to_dict()
            for node_id in model.node_ids:
                assignment = ValidationAssignment(
                    assignment_id=_assignment_id(model.campaign_id, node_id),
                    campaign_id=model.campaign_id,
                    node_id=node_id,
                )
                self._state["assignments"][assignment.assignment_id] = assignment.to_dict()
            self._event_locked(
                "validation_campaign.created",
                campaign_id=model.campaign_id,
                suite_id=model.suite_id,
                target_build=model.target_build,
                node_ids=list(model.node_ids),
            )
            self._save_locked()
            return self.campaign(model.campaign_id)

    def run_campaign(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = _validate_id(campaign_id, "campaign_id")
        with self._lock:
            raw_campaign = self._state["campaigns"].get(campaign_id)
            if not isinstance(raw_campaign, dict):
                raise KeyError("campaign_not_found")
            campaign = ValidationCampaign.from_dict(raw_campaign)
            if campaign.state in TERMINAL_CAMPAIGN_STATES:
                return self.campaign(campaign_id)
            if campaign.state != "pending":
                raise ValueError("campaign_already_running")
            campaign.state = "running"
            campaign.started_at = _now()
            self._state["campaigns"][campaign_id] = campaign.to_dict()
            self._event_locked("validation_campaign.started", campaign_id=campaign_id)
            self._save_locked()

        with self._lock:
            suite = TestSuite.from_dict(self._state["suites"][campaign.suite_id])
        assignment_ids = [_assignment_id(campaign_id, node_id) for node_id in campaign.node_ids]
        for assignment_id in assignment_ids:
            with self._lock:
                assignment = ValidationAssignment.from_dict(self._state["assignments"][assignment_id])
                assignment.state = "running"
                assignment.started_at = _now()
                self._state["assignments"][assignment_id] = assignment.to_dict()
                self._event_locked(
                    "validation_assignment.started",
                    campaign_id=campaign_id,
                    assignment_id=assignment_id,
                    node_id=assignment.node_id,
                )
                self._save_locked()

            with self._lock:
                node = TestNode.from_dict(self._state["nodes"][assignment.node_id])
            outcome = self.runner.run(node, suite, campaign.target_build)

            with self._lock:
                assignment = ValidationAssignment.from_dict(self._state["assignments"][assignment_id])
                assignment.state = "uploading"
                assignment.checks = list(outcome.get("checks") or [])
                self._state["assignments"][assignment_id] = assignment.to_dict()
                self._event_locked(
                    "validation_assignment.evidence_uploaded",
                    campaign_id=campaign_id,
                    assignment_id=assignment_id,
                    checks_total=len(assignment.checks),
                )
                self._save_locked()

                terminal_state = str(outcome.get("state") or "inconclusive")
                if terminal_state not in TERMINAL_ASSIGNMENT_STATES:
                    terminal_state = "inconclusive"
                assignment.state = terminal_state
                assignment.finished_at = _now()
                assignment.result = {
                    "reason": str(outcome.get("reason") or "runner_did_not_classify"),
                    "checks_total": len(assignment.checks),
                    "checks_passed": sum(1 for item in assignment.checks if item.get("status") == "passed"),
                }
                self._state["assignments"][assignment_id] = assignment.to_dict()
                self._event_locked(
                    "validation_assignment.finished",
                    campaign_id=campaign_id,
                    assignment_id=assignment_id,
                    node_id=assignment.node_id,
                    state=terminal_state,
                    reason=assignment.result["reason"],
                )
                self._save_locked()

        with self._lock:
            assignments = [
                ValidationAssignment.from_dict(self._state["assignments"][value]) for value in assignment_ids
            ]
            counts = {
                state: sum(1 for item in assignments if item.state == state)
                for state in ("passed", "failed", "inconclusive", "timed_out")
            }
            if counts["failed"] or counts["timed_out"]:
                final_state = "failed"
            elif counts["passed"] >= campaign.quorum:
                final_state = "passed"
            else:
                final_state = "inconclusive"
            campaign = ValidationCampaign.from_dict(self._state["campaigns"][campaign_id])
            campaign.state = final_state
            campaign.finished_at = _now()
            campaign.result = {
                "quorum": campaign.quorum,
                "assignments_total": len(assignments),
                **counts,
            }
            self._state["campaigns"][campaign_id] = campaign.to_dict()
            self._event_locked(
                "validation_campaign.finished",
                campaign_id=campaign_id,
                state=final_state,
                result=campaign.result,
            )
            self._save_locked()
            return self.campaign(campaign_id)

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = _validate_id(campaign_id, "campaign_id")
        with self._lock:
            raw = self._state["campaigns"].get(campaign_id)
            if not isinstance(raw, dict):
                raise KeyError("campaign_not_found")
            campaign = dict(raw)
            assignments = [
                dict(value)
                for value in self._state["assignments"].values()
                if isinstance(value, dict) and value.get("campaign_id") == campaign_id
            ]
            assignments.sort(key=lambda item: (str(item.get("node_id")), int(item.get("attempt") or 0)))
            campaign["assignments"] = assignments
            return campaign

    def assignment(self, assignment_id: str) -> dict[str, Any]:
        assignment_id = _validate_id(assignment_id, "assignment_id")
        with self._lock:
            raw = self._state["assignments"].get(assignment_id)
            if not isinstance(raw, dict):
                raise KeyError("assignment_not_found")
            return dict(raw)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            nodes = [TestNode.from_dict(value).to_dict(public=True) for value in self._state["nodes"].values()]
            suites = [dict(value) for value in self._state["suites"].values()]
            campaigns = [dict(value) for value in self._state["campaigns"].values()]
            assignments = [dict(value) for value in self._state["assignments"].values()]
            nodes.sort(key=lambda item: str(item.get("node_id")))
            suites.sort(key=lambda item: str(item.get("suite_id")))
            campaigns.sort(key=lambda item: str(item.get("created_at")), reverse=True)
            assignments.sort(key=lambda item: str(item.get("created_at")), reverse=True)
            terminal_counts = {
                state: sum(1 for item in campaigns if item.get("state") == state)
                for state in ("passed", "failed", "inconclusive")
            }
            return {
                "schema_version": 1,
                "mode": "observe-only",
                "summary": {
                    "nodes_total": len(nodes),
                    "nodes_enabled": sum(1 for item in nodes if item.get("enabled")),
                    "suites_total": len(suites),
                    "campaigns_total": len(campaigns),
                    "assignments_running": sum(
                        1 for item in assignments if item.get("state") in {"assigned", "running", "uploading"}
                    ),
                    **{f"campaigns_{key}": value for key, value in terminal_counts.items()},
                },
                "nodes": nodes,
                "suites": suites,
                "campaigns": campaigns,
                "assignments": assignments,
                "events": list(reversed(self._state["events"][-100:])),
                "updated_at": self._state["updated_at"],
            }


_SERVICE: ReleaseValidationService | None = None
_SERVICE_LOCK = threading.Lock()


def get_release_validation_service() -> ReleaseValidationService:
    global _SERVICE
    with _SERVICE_LOCK:
        configured = str(os.getenv("ADAOS_RELEASE_VALIDATION_STATE_PATH") or "").strip()
        if _SERVICE is None or (configured and str(_SERVICE.state_path) != configured):
            _SERVICE = ReleaseValidationService()
        return _SERVICE


def reset_release_validation_service() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None
