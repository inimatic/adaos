from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.runtime_paths import current_state_dir


STATE_SCHEMA = "adaos.component_updates.state.v1"
NOTICE_SCHEMA = "adaos.component_update.v1"
VIEW_SCHEMA = "adaos.component_update.viewer_state.v1"
ACTIVE_NOTICE_STATES = frozenset({"active"})
CURRENT_NOTICE_STATES = frozenset({"active", "accepted"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _component_key(component_type: str, component_id: str) -> str:
    return f"{_text(component_type).lower()}:{_text(component_id)}"


def _viewer_key(actor: str, webspace_id: str) -> str:
    raw = f"{_text(actor) or 'user:local'}|{_text(webspace_id) or 'desktop'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _notice_id(component_type: str, component_id: str, candidate_identity: str) -> str:
    raw = f"{_component_key(component_type, component_id)}|{_text(candidate_identity)}"
    return f"cupdate.{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:26]}"


@dataclass(slots=True)
class ComponentUpdateService:
    """Persist user-visible release notices independently from Builder sessions."""

    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "component_updates"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".state.lock"

    def record_aprobation(
        self,
        *,
        component_type: str,
        component_id: str,
        aprobation: Mapping[str, Any],
        webspace_id: str = "desktop",
        ticket_ids: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        kind = _text(component_type).lower()
        identifier = _text(component_id)
        if kind not in {"skill", "scenario"} or not identifier:
            return None
        trial = dict(aprobation.get("trial")) if isinstance(aprobation.get("trial"), Mapping) else {}
        changelog = (
            dict(aprobation.get("changelog"))
            if isinstance(aprobation.get("changelog"), Mapping)
            else {}
        )
        candidate_id = _text(trial.get("candidate_id"))
        candidate_digest = _text(trial.get("candidate_digest"))
        identity = candidate_id or candidate_digest
        if not identity:
            return None

        decision = _text(trial.get("decision")).lower()
        trial_status = _text(trial.get("status")).lower()
        if trial_status == "published":
            stage = "stable"
            notice_status = "accepted"
            review_state = "accepted"
        elif decision == "accept" or trial_status == "accepted":
            stage = "beta"
            notice_status = "active"
            review_state = "publishing"
        elif trial_status in {"rejected", "rolled_back", "rollback"}:
            stage = "alpha"
            notice_status = "rolled_back" if decision == "rollback" else "withdrawn"
            review_state = "rolled_back" if decision == "rollback" else "changes_requested"
        else:
            stage = _text(aprobation.get("audience")).lower() or "alpha"
            notice_status = "active"
            review_state = "pending"

        notice_id = _notice_id(kind, identifier, identity)
        component_key = _component_key(kind, identifier)
        now = _now()
        linked_ticket_ids = list(
            dict.fromkeys(
                [
                    *[_text(item) for item in ticket_ids],
                    *[_text(item) for item in changelog.get("ticket_ids") or []],
                ]
            )
        )
        linked_ticket_ids = [item for item in linked_ticket_ids if item]
        changes = [
            _text(item)
            for item in changelog.get("changes") or []
            if _text(item)
        ][:20]

        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            notices = state["notices"]
            previous = notices.get(notice_id)
            created_at = _text((previous or {}).get("created_at")) or _text(trial.get("started_at")) or now
            revision = int((previous or {}).get("revision") or 0) + 1
            title = _text(changelog.get("title")) or f"{identifier} {stage} update"
            for previous_stage in ("alpha", "beta", "stable"):
                suffix = f" {previous_stage} update"
                if title.lower().endswith(suffix) and previous_stage != stage:
                    title = f"{title[:-len(suffix)]} {stage} update"
                    break
            published_at = (previous or {}).get("published_at")
            if stage == "stable" and not published_at:
                published_at = now
            notice = {
                "schema": NOTICE_SCHEMA,
                "notice_id": notice_id,
                "component": {
                    "type": kind,
                    "id": identifier,
                    "key": component_key,
                },
                "version": _text(trial.get("version")) or None,
                "stage": stage,
                "status": notice_status,
                "review_state": review_state,
                "source_kind": _text(aprobation.get("source_kind")) or "devspace",
                "title": title,
                "summary": _text(changelog.get("summary")) or "An updated component is ready for review.",
                "changes": changes,
                "ticket_ids": linked_ticket_ids,
                "candidate": {
                    "id": candidate_id or None,
                    "digest": candidate_digest or None,
                    "release_digest": _text(trial.get("release_digest")) or None,
                    "workflow_generation": trial.get("workflow_generation"),
                },
                "transition": {
                    "state": review_state,
                    "requires_user_decision": (
                        stage == "alpha" and notice_status == "active"
                    ),
                    "workspace_committed": stage == "stable",
                    "workspace_version": (
                        _text(trial.get("version")) or None
                        if stage == "stable"
                        else None
                    ),
                    "release_digest": (
                        _text(trial.get("release_digest")) or None
                        if stage == "stable"
                        else None
                    ),
                },
                "webspace_id": _text(webspace_id) or "desktop",
                "created_at": created_at,
                "updated_at": now,
                "published_at": published_at,
                "revision": revision,
            }
            comparable = dict(notice)
            comparable.pop("updated_at", None)
            comparable.pop("revision", None)
            previous_comparable = dict(previous or {})
            previous_comparable.pop("updated_at", None)
            previous_comparable.pop("revision", None)
            if comparable == previous_comparable:
                return copy.deepcopy(dict(previous))

            if notice_status in {"active", "accepted"}:
                for other_id, raw_other in list(notices.items()):
                    if other_id == notice_id or not isinstance(raw_other, Mapping):
                        continue
                    other = dict(raw_other)
                    if (
                        _text((other.get("component") or {}).get("key")) == component_key
                        and _text(other.get("status")) == "active"
                    ):
                        other["status"] = "superseded"
                        other["superseded_by"] = notice_id
                        other["updated_at"] = now
                        other["revision"] = int(other.get("revision") or 0) + 1
                        notices[other_id] = other
            notices[notice_id] = notice
            self._write(state)
        return copy.deepcopy(notice)

    def reconcile_builder_sessions(self) -> int:
        root = Path(self.state_dir or current_state_dir()) / "builder" / "automation"
        if not root.is_dir():
            return 0
        reconciled = 0
        for path in root.glob("*.json"):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(session, Mapping):
                continue
            readiness = session.get("completion_readiness")
            if not isinstance(readiness, Mapping) or not isinstance(readiness.get("aprobation"), Mapping):
                continue
            links = session.get("links") if isinstance(session.get("links"), Mapping) else {}
            ticket_ids = [
                _text(links.get("development_ticket_id")),
                *[_text(item) for item in links.get("development_ticket_ids") or []],
            ]
            notice = self.record_aprobation(
                component_type=_text(session.get("object_type")),
                component_id=_text(session.get("object_id")),
                aprobation=dict(readiness["aprobation"]),
                webspace_id=_text(session.get("webspace_id")) or "desktop",
                ticket_ids=tuple(item for item in ticket_ids if item),
            )
            if notice:
                reconciled += 1
        return reconciled

    def list_notices(
        self,
        *,
        component_type: str | None = None,
        component_id: str | None = None,
        stage: str | None = None,
        status: str | None = "active",
        actor: str = "user:local",
        webspace_id: str = "desktop",
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        state = self._read()
        status_token = _text(status).lower()
        current_only = status_token == "current"
        result: list[dict[str, Any]] = []
        for raw in state["notices"].values():
            if not isinstance(raw, Mapping):
                continue
            notice = dict(raw)
            component = notice.get("component") if isinstance(notice.get("component"), Mapping) else {}
            if component_type and _text(component.get("type")) != _text(component_type).lower():
                continue
            if component_id and _text(component.get("id")) != _text(component_id):
                continue
            if stage and _text(notice.get("stage")) != _text(stage).lower():
                continue
            notice_status = _text(notice.get("status")).lower()
            if current_only and notice_status not in CURRENT_NOTICE_STATES:
                continue
            if status and not current_only and notice_status != status_token:
                continue
            projected = self._project_notice(
                notice,
                state=state,
                actor=actor,
                webspace_id=webspace_id,
            )
            if unread_only and not projected["unread"]:
                continue
            result.append(projected)
        result.sort(key=lambda item: (_text(item.get("updated_at")), _text(item.get("notice_id"))), reverse=True)
        if current_only:
            current: list[dict[str, Any]] = []
            seen_components: set[str] = set()
            for item in result:
                component = item.get("component") if isinstance(item.get("component"), Mapping) else {}
                component_key = _text(component.get("key")) or _component_key(
                    _text(component.get("type")),
                    _text(component.get("id")),
                )
                if component_key in seen_components:
                    continue
                seen_components.add(component_key)
                current.append(item)
            result = current
        return result

    def active_component_metadata(self, component_type: str, component_id: str) -> dict[str, Any] | None:
        notices = self.list_notices(
            component_type=component_type,
            component_id=component_id,
            status="active",
        )
        if not notices:
            return None
        notice = dict(notices[0])
        notice.pop("viewer_state", None)
        notice.pop("unread", None)
        notice.pop("auto_prompt", None)
        return notice

    def respond(
        self,
        notice_id: str,
        *,
        action: str,
        actor: str = "user:local",
        webspace_id: str = "desktop",
    ) -> dict[str, Any]:
        token = _text(notice_id)
        action_token = _text(action).lower()
        if action_token not in {"presented", "review_started", "dismiss_auto", "restore_auto"}:
            raise ValueError("component update action must be presented, review_started, dismiss_auto, or restore_auto")
        now = _now()
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            notice = state["notices"].get(token)
            if not isinstance(notice, Mapping):
                raise KeyError(token)
            viewer_key = _viewer_key(actor, webspace_id)
            view_states = state["viewer_states"]
            view = dict(view_states.get(viewer_key) or {})
            view.setdefault("schema", VIEW_SCHEMA)
            view.setdefault("actor", _text(actor) or "user:local")
            view.setdefault("webspace_id", _text(webspace_id) or "desktop")
            notices = dict(view.get("notices") or {})
            notice_view = dict(notices.get(token) or {})
            if action_token in {"presented", "dismiss_auto", "review_started"}:
                notice_view.setdefault("presented_at", now)
            if action_token == "review_started":
                notice_view.setdefault("viewed_at", now)
                notice_view.setdefault("review_started_at", now)
            notices[token] = notice_view
            view["notices"] = notices

            component = notice.get("component") if isinstance(notice.get("component"), Mapping) else {}
            preferences = dict(view.get("component_preferences") or {})
            component_key = _text(component.get("key"))
            preference = dict(preferences.get(component_key) or {})
            if action_token == "dismiss_auto":
                preference["suppress_auto_prompt"] = True
                preference["updated_at"] = now
            elif action_token == "restore_auto":
                preference["suppress_auto_prompt"] = False
                preference["updated_at"] = now
            if component_key:
                preferences[component_key] = preference
            view["component_preferences"] = preferences
            view["updated_at"] = now
            view_states[viewer_key] = view
            self._write(state)
            projected = self._project_notice(dict(notice), state=state, actor=actor, webspace_id=webspace_id)
        return projected

    def _project_notice(
        self,
        notice: dict[str, Any],
        *,
        state: Mapping[str, Any],
        actor: str,
        webspace_id: str,
    ) -> dict[str, Any]:
        viewer_key = _viewer_key(actor, webspace_id)
        raw_view = (state.get("viewer_states") or {}).get(viewer_key) or {}
        notice_view = dict((raw_view.get("notices") or {}).get(_text(notice.get("notice_id"))) or {})
        component = notice.get("component") if isinstance(notice.get("component"), Mapping) else {}
        preference = dict((raw_view.get("component_preferences") or {}).get(_text(component.get("key"))) or {})
        unread = not bool(_text(notice_view.get("viewed_at")))
        notice["viewer_state"] = notice_view
        notice["unread"] = unread
        notice["auto_prompt"] = unread and not bool(preference.get("suppress_auto_prompt"))
        return notice

    def _read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schema": STATE_SCHEMA,
                "notices": {},
                "viewer_states": {},
            }
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("component update state is corrupt")
        notices = value.get("notices")
        viewer_states = value.get("viewer_states")
        if not isinstance(notices, Mapping) or not isinstance(viewer_states, Mapping):
            raise ValueError("component update state is corrupt")
        return {
            "schema": STATE_SCHEMA,
            "notices": dict(notices),
            "viewer_states": dict(viewer_states),
        }

    def _write(self, state: Mapping[str, Any]) -> None:
        atomic_write_json(self.state_path, dict(state))


__all__ = [
    "ACTIVE_NOTICE_STATES",
    "CURRENT_NOTICE_STATES",
    "ComponentUpdateService",
    "NOTICE_SCHEMA",
    "STATE_SCHEMA",
    "VIEW_SCHEMA",
]
