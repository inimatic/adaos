from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adaos.apps.cli.active_control import (
    resolve_control_base_url,
    resolve_control_token,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.core_update_policy import current_env_type
from adaos.services.log_redaction import redact_log_text, redact_log_value

BROWSER_FEEDBACK_SCHEMA = "adaos.builder.browser_feedback_receipt.v1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_digest(path: Path) -> str | None:
    try:
        return _digest_bytes(path.read_bytes())
    except OSError:
        return None


def _safe_token(value: Any, *, fallback: str = "candidate") -> str:
    token = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in str(value or "").strip()
    ).strip("._")
    return token[:120] or fallback


def _next_attempt(parent: Path) -> tuple[int, Path]:
    attempt = 1
    while (parent / f"attempt-{attempt:02d}").exists():
        attempt += 1
    return attempt, parent / f"attempt-{attempt:02d}"


def _git_revision(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else None


def _record_editor_workflows(source: Path) -> list[dict[str, str | None]]:
    """Describe bounded CRUD journeys for accepted record editors.

    A fresh installation is allowed to have an empty collection.  In that case
    a row action cannot be exercised until the browser journey creates a
    disposable record through the application's own create command.  Returning
    the related create/delete commands lets the trusted browser runner do that
    without seeding application storage or inventing an out-of-band write.
    """

    root = source if source.is_dir() else source.parent
    semantic_path = root / "semantic.webui.json"
    try:
        document = json.loads(semantic_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    commands = [
        item
        for item in document.get("commands") or []
        if isinstance(item, Mapping)
    ]
    editor_primaries = {
        (str(item.get("view_ref") or ""), str(item.get("kind") or ""))
        for item in commands
        if str((item.get("exposure") or {}).get("placement") or "")
        == "editor_primary"
    }
    views = [
        item
        for item in document.get("views") or []
        if isinstance(item, Mapping)
    ]
    view_resources = {
        str(item.get("id") or "").strip(): str(item.get("resource_ref") or "").strip()
        for item in views
        if str(item.get("id") or "").strip()
    }
    collection_views = {
        str(item.get("resource_ref") or "").strip(): str(item.get("id") or "").strip()
        for item in views
        if str(item.get("resource_ref") or "").strip()
        and str(item.get("role") or "").strip() == "collection"
    }
    result: list[dict[str, str | None]] = []
    for item in commands:
        command_id = str(item.get("id") or "").strip()
        exposure = item.get("exposure") or {}
        signature = (
            str(item.get("view_ref") or ""),
            str(item.get("kind") or ""),
        )
        if (
            command_id
            and "," not in command_id
            and ":" not in command_id
            and str(exposure.get("placement") or "") == "row_action"
            and signature in editor_primaries
        ):
            view_ref = str(item.get("view_ref") or "").strip()
            related = [
                candidate
                for candidate in commands
                if str(candidate.get("view_ref") or "").strip() == view_ref
            ]

            def related_command(placement: str, kind: str) -> str | None:
                return next(
                    (
                        str(candidate.get("id") or "").strip()
                        for candidate in related
                        if str((candidate.get("exposure") or {}).get("placement") or "").strip()
                        == placement
                        and str(candidate.get("kind") or "").strip() == kind
                        and str(candidate.get("id") or "").strip()
                    ),
                    None,
                )

            resource_ref = view_resources.get(view_ref, "")
            result.append(
                {
                    "open_command": command_id,
                    "create_command": related_command("collection_header", "create"),
                    "delete_command": related_command("editor_danger", "delete"),
                    "collection_view_ref": collection_views.get(resource_ref) or None,
                }
            )
    unique: dict[str, dict[str, str | None]] = {}
    for item in result:
        unique.setdefault(str(item["open_command"]), item)
    return list(unique.values())[:3]


def _record_editor_commands(source: Path) -> list[str]:
    """Return bounded, non-mutating row commands that only open record editors."""

    return [
        str(item["open_command"])
        for item in _record_editor_workflows(source)
    ]


@dataclass(slots=True)
class BuilderBrowserFeedbackService:
    """Observe one materialized DEV candidate before Forge checkpoints exist."""

    state_dir: Path
    repo_root: Path
    command_runner: Callable[..., Any] = subprocess.run
    client_url: str = "http://127.0.0.1:8100/"
    timeout_seconds: int = 180

    @property
    def root(self) -> Path:
        path = Path(self.state_dir) / "builder" / "browser_feedback"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def script_path(self) -> Path:
        return Path(self.repo_root) / "e2e" / "stand" / "browser" / "builder-candidate-feedback.mjs"

    def evaluate(
        self,
        *,
        scenario_id: str,
        webspace_id: str,
        subnet_id: str,
        task_id: str,
        source_path: Path,
        context_packet_digest: str | None = None,
        runtime_url: str | None = None,
        space_kind: str = "development",
    ) -> dict[str, Any]:
        env_type = current_env_type()
        if env_type != "dev":
            return {
                "schema": BROWSER_FEEDBACK_SCHEMA,
                "ok": True,
                "status": "skipped",
                "reason": "browser_feedback_is_dev_only",
                "env_type": env_type,
            }
        if str(os.getenv("ADAOS_BUILDER_BROWSER_FEEDBACK") or "1").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return {
                "schema": BROWSER_FEEDBACK_SCHEMA,
                "ok": True,
                "status": "skipped",
                "reason": "browser_feedback_disabled",
                "env_type": env_type,
            }

        scenario = _safe_token(scenario_id)
        webspace = str(webspace_id or "").strip()
        subnet = str(subnet_id or "").strip()
        normalized_space_kind = str(space_kind or "").strip().lower()
        task = _safe_token(task_id, fallback=f"scenario-{scenario}")
        if not webspace or not subnet:
            raise ValueError("browser feedback requires webspace_id and subnet_id")
        if normalized_space_kind not in {"development", "workspace"}:
            raise ValueError("browser feedback space_kind must be development or workspace")
        if not self.script_path.is_file():
            raise RuntimeError(f"browser feedback script is missing: {self.script_path}")

        source = Path(source_path)
        webui_path = source / "webui.json" if source.is_dir() else source
        if not webui_path.is_file():
            raise RuntimeError(f"candidate WebUI is missing: {webui_path}")
        source_digest = _file_digest(webui_path)
        record_editor_workflows = _record_editor_workflows(source)
        record_editor_commands = [
            str(item["open_command"]) for item in record_editor_workflows
        ]
        attempt, output = _next_attempt(self.root / task)
        output.mkdir(parents=True, exist_ok=False)

        control_url = str(runtime_url or resolve_control_base_url(prefer_local=True)).rstrip("/")
        token = resolve_control_token(base_url=control_url)
        env = {
            **{key: value for key, value in os.environ.items() if not key.startswith("ADAOS_E2E_")},
            "ENV_TYPE": "dev",
            "ADAOS_E2E_SCENARIO_ID": scenario,
            "ADAOS_E2E_WEBSPACE_ID": webspace,
            "ADAOS_E2E_SPACE_KIND": normalized_space_kind,
            "ADAOS_E2E_SUBNET_ID": subnet,
            "ADAOS_E2E_HUB_TOKEN": token,
            "ADAOS_E2E_HUB_URL": control_url,
            "ADAOS_E2E_CLIENT_URL": str(self.client_url),
            "ADAOS_E2E_OUTPUT": str(output),
            "ADAOS_E2E_SOURCE_DIGEST": str(source_digest or ""),
            "ADAOS_E2E_TIMEOUT_MS": str(max(30, int(self.timeout_seconds)) * 1000),
            "ADAOS_E2E_COMMAND_SEQUENCE": ",".join(record_editor_commands),
            "ADAOS_E2E_RECORD_EDITOR_COMMANDS": ",".join(record_editor_commands),
            "ADAOS_E2E_RECORD_EDITOR_WORKFLOWS": json.dumps(
                record_editor_workflows,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        log_path = output / "browser.log"
        timed_out = False
        try:
            completed = self.command_runner(
                ["node", str(self.script_path)],
                cwd=Path(self.repo_root),
                env=env,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                timeout=max(30, int(self.timeout_seconds)),
            )
            stdout = str(getattr(completed, "stdout", "") or "")
            stderr = str(getattr(completed, "stderr", "") or "")
            exit_code = int(getattr(completed, "returncode", 1))
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = (
                exc.stdout.decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else str(exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes)
                else str(exc.stderr or "")
            )
        log_path.write_text(redact_log_text(stdout + stderr), encoding="utf-8")

        report_path = output / "report.json"
        try:
            report = redact_log_value(json.loads(report_path.read_text(encoding="utf-8")))
            atomic_write_json(report_path, report)
        except (OSError, json.JSONDecodeError):
            report = {
                "schema": "adaos.builder.browser_feedback.v1",
                "passed": False,
                "samples": [],
                "failure": "browser_feedback_timeout" if timed_out else "browser_feedback_report_missing",
            }

        evidence: list[dict[str, Any]] = []
        for path in sorted(output.iterdir()):
            if not path.is_file():
                continue
            evidence.append(
                {
                    "name": path.name,
                    "path": path.resolve().as_posix(),
                    "sha256": _file_digest(path),
                    "bytes": path.stat().st_size,
                    "media_type": "image/png" if path.suffix.lower() == ".png" else "application/json"
                    if path.suffix.lower() == ".json"
                    else "text/plain",
                }
            )

        abi_path = Path(self.repo_root) / "src" / "adaos" / "abi" / "webui.v1.schema.json"
        catalog_path = (
            Path(self.repo_root) / "src" / "adaos" / "abi" / "ui.capability_catalog.v1.json"
        )
        client_path = Path(self.repo_root) / "src" / "adaos" / "integrations" / "adaos-client"
        samples = [
            item for item in report.get("samples") or [] if isinstance(item, Mapping)
        ]
        authoritative_data_settled = bool(samples) and all(
            item.get("authoritative_data_settled") is True for item in samples
        )
        if bool(report.get("passed")) and not authoritative_data_settled:
            report["passed"] = False
            report["failure"] = "authoritative_data_not_settled"
        passed = (
            bool(report.get("passed"))
            and authoritative_data_settled
            and exit_code == 0
        )
        receipt = {
            "schema": BROWSER_FEEDBACK_SCHEMA,
            "ok": passed,
            "status": "passed" if passed else "failed",
            "attempt": attempt,
            "task_id": str(task_id or "").strip() or None,
            "scenario_id": scenario,
            "webspace_id": webspace,
            "space_kind": normalized_space_kind,
            "runtime_url": control_url,
            "source": {
                "path": webui_path.resolve().as_posix(),
                "digest": source_digest,
                "context_packet_digest": str(context_packet_digest or "").strip() or None,
            },
            "runtime_contract": {
                "client_revision": _git_revision(client_path),
                "webui_abi_digest": _file_digest(abi_path),
                "capability_catalog_digest": _file_digest(catalog_path),
            },
            "report": report,
            "report_digest": _file_digest(report_path),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "evidence": evidence,
            "created_at": _now_iso(),
        }
        receipt_path = output / "receipt.json"
        atomic_write_json(receipt_path, receipt)
        receipt["receipt_path"] = receipt_path.resolve().as_posix()
        receipt["receipt_digest"] = _file_digest(receipt_path)
        return receipt


def browser_feedback_failures(receipt: Mapping[str, Any]) -> list[str]:
    report = receipt.get("report") if isinstance(receipt.get("report"), Mapping) else {}
    failures: list[str] = []
    for sample in report.get("samples") or []:
        if not isinstance(sample, Mapping):
            continue
        layout = str(sample.get("layout") or "viewport")
        for value in sample.get("hard_failures") or []:
            text = str(value or "").strip()
            if text:
                failures.append(f"{layout}: {text}")
    fallback = str(report.get("failure") or "").strip()
    if fallback:
        failures.append(fallback)
    return list(dict.fromkeys(failures))[:24]


__all__ = [
    "BROWSER_FEEDBACK_SCHEMA",
    "BuilderBrowserFeedbackService",
    "browser_feedback_failures",
]
