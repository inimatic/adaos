from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir
from adaos.services.skill_factory import SkillFactoryService


USER_HUB_SUBMISSION_SCHEMA = "adaos.skill_factory.user_hub_submission.v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


class UserHubSubmissionError(ValueError):
    pass


class UserHubResultService:
    def __init__(
        self,
        *,
        factory: SkillFactoryService,
        state_dir: Path | None = None,
    ) -> None:
        self.factory = factory
        self.state_dir = Path(state_dir or current_state_dir())

    @property
    def root(self) -> Path:
        path = self.state_dir / "skill_factory" / "user_hub_staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def fetch_validate_stage(
        self,
        *,
        task_id: str,
        source_url: str,
        source_digest: str,
        webspace_id: str,
        fetcher: Callable[[str], bytes | Mapping[str, Any]],
        pending_action_publisher: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        url = str(source_url or "").strip()
        if not url.startswith("https://"):
            raise UserHubSubmissionError("User Hub result fetch requires HTTPS")
        raw = fetcher(url)
        body = (
            bytes(raw)
            if isinstance(raw, (bytes, bytearray))
            else json.dumps(dict(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        observed_digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        if observed_digest != str(source_digest or "").strip().lower():
            raise UserHubSubmissionError("User Hub result digest mismatch")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UserHubSubmissionError(f"User Hub result is not UTF-8 JSON: {exc}") from exc
        if not isinstance(value, Mapping):
            raise UserHubSubmissionError("User Hub result must contain an object")
        result = dict(value.get("dev_result") or value)
        if str(result.get("task_id") or "") != str(task_id):
            raise UserHubSubmissionError("User Hub result belongs to another task")
        errors = sorted(
            Draft202012Validator(_schema("skill_factory.dev_result.v1.schema.json")).iter_errors(
                {"schema": "adaos.skill_factory.dev_result.v1", **result}
            ),
            key=lambda item: list(item.path),
        )
        if errors:
            raise UserHubSubmissionError(f"User Hub result ABI validation failed: {errors[0].message}")
        task = next(
            (item for item in self.factory.snapshot(include_tasks=True)["tasks"] if item["task_id"] == task_id),
            None,
        )
        if task is None:
            raise UserHubSubmissionError("User Hub result references an unknown task")
        normalized = self.factory._normalize_result(result, task)
        self.factory.validate_result_paths(task, normalized)
        submission_id = f"uhsub.{new_id()}"
        now = _now()
        submission = {
            "schema": USER_HUB_SUBMISSION_SCHEMA,
            "submission_id": submission_id,
            "task_id": task_id,
            "source_url": url,
            "source_digest": observed_digest,
            "status": "approval_pending",
            "result": normalized,
            "validation": {
                "status": "passed",
                "checks": ["https", "sha256", "utf8_json", "dev_result_abi", "task_identity", "sparse_paths"],
            },
            "pending_action_id": f"pa.{submission_id}",
            "decision": None,
            "created_at": now,
            "updated_at": now,
        }
        self._write(submission)
        publisher = pending_action_publisher
        if publisher is None:
            from adaos.services.pending_actions import publish_pending_action

            publisher = publish_pending_action
        publisher(
            webspace_id=webspace_id,
            action_id=submission["pending_action_id"],
            kind="skill_factory.user_hub_result.review",
            title="Review User Hub development result",
            summary=f"Validated result for task {task_id} is staged and has not been activated.",
            domain_ref={"type": "user_hub_submission", "id": submission_id},
            allowed_actions=[
                {"id": "approve", "label": "Accept result", "terminal": True},
                {"id": "refuse", "label": "Reject result", "terminal": True},
            ],
            response_topic="skill_factory.user_hub_result.response",
            metadata={
                "submission_id": submission_id,
                "task_id": task_id,
                "source_digest": observed_digest,
                "validation": submission["validation"],
            },
        )
        return submission

    def decide(
        self,
        submission_id: str,
        *,
        accepted: bool,
        approval_id: str,
        actor_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        submission = self.get(submission_id)
        if submission["status"] != "approval_pending":
            return submission
        if not approval_id or not actor_id:
            raise UserHubSubmissionError("User Hub decision requires approval identity")
        submission["decision"] = {
            "accepted": bool(accepted),
            "approval_id": approval_id,
            "actor_id": actor_id,
            "reason": str(reason or "").strip() or None,
            "decided_at": _now(),
        }
        if accepted:
            completion = self.factory.complete_task(submission["result"])
            submission["status"] = "accepted"
            submission["decision"]["ready_event"] = completion["ready_event"]
        else:
            submission["status"] = "rejected"
        submission["updated_at"] = _now()
        self._write(submission)
        return submission

    def get(self, submission_id: str) -> dict[str, Any]:
        path = self.root / f"{str(submission_id)}.json"
        if not path.is_file():
            raise KeyError(submission_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        self._validate(value)
        return value

    def _write(self, submission: Mapping[str, Any]) -> None:
        value = dict(submission)
        self._validate(value)
        atomic_write_json(self.root / f"{value['submission_id']}.json", value)

    @staticmethod
    def _validate(value: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(_schema("skill_factory.user_hub_submission.v1.schema.json")).iter_errors(value),
            key=lambda item: list(item.path),
        )
        if errors:
            raise UserHubSubmissionError(f"invalid User Hub submission: {errors[0].message}")


__all__ = ["USER_HUB_SUBMISSION_SCHEMA", "UserHubResultService", "UserHubSubmissionError"]
