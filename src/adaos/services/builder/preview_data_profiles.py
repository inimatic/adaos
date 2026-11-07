from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from adaos.services.builder.data_modes import BuilderDataModeError, select_profile
from adaos.services.runtime_paths import current_state_dir


_LOCK = threading.RLock()
_STORE_SCHEMA = "adaos.builder.preview_data_profile_store.v1"
_SELECTION_SCHEMA = "adaos.builder.preview_data_profile_selection.v1"


class BuilderPreviewDataProfileError(ValueError):
    """Raised when a Webspace-scoped Preview profile cannot be selected."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_token(value: Any) -> str:
    from adaos.services.builder.workbench import safe_source_webspace_id

    return safe_source_webspace_id(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_selection(value: Mapping[str, Any]) -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "abi"
        / "builder.preview_data_profile_selection.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(dict(value)),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise BuilderPreviewDataProfileError(
            f"invalid Preview data profile selection at {location}: {errors[0].message}"
        )


def _mapping_readiness(profile: Mapping[str, Any]) -> tuple[bool, list[str]]:
    mode = str(profile.get("mode") or "mock").strip() or "mock"
    mappings = [
        dict(item)
        for item in profile.get("implementation_mappings") or []
        if isinstance(item, Mapping)
    ]
    capabilities = [str(item).strip() for item in profile.get("capabilities") or [] if str(item).strip()]
    if mode in {"mock", "fixture"}:
        return True, []
    missing = [
        str(item.get("logical_ref") or "").strip()
        for item in mappings
        if str(item.get("status") or "missing").strip() != "mapped"
        or not str(item.get("implementation_ref") or "").strip()
    ]
    if capabilities and not mappings:
        missing = capabilities
    return not missing, [item for item in missing if item]


def _profile_label(profile: Mapping[str, Any]) -> str:
    profile_id = str(profile.get("profile_id") or "default-mock").strip() or "default-mock"
    mode = str(profile.get("mode") or "mock").strip() or "mock"
    return f"{profile_id} ({mode.replace('_', ' ')})"


class BuilderPreviewDataProfileService:
    """Own the active data profile of each Project Preview in one Builder host.

    The Project workflow owns the profile catalog and its default.  This store
    only owns a user's active Preview choice, scoped by source Webspace and
    scenario execution identity.  Keeping these concerns separate lets two
    Builder hosts inspect the same accepted UI revision with different data.
    """

    def __init__(self, state_dir: Path | None = None, *, workflow_service: Any | None = None) -> None:
        self.state_dir = Path(state_dir or current_state_dir())
        self.workflow_service = workflow_service

    @classmethod
    def from_context(cls) -> "BuilderPreviewDataProfileService":
        return cls(current_state_dir())

    @property
    def root(self) -> Path:
        return self.state_dir / "builder" / "workbench" / "data-profile-selections"

    def path(self, source_webspace_id: str) -> Path:
        return self.root / f"{_safe_token(source_webspace_id)}.json"

    def _source(self, webspace_id: str | None) -> str:
        from adaos.services.builder.workbench import BuilderWorkbenchService

        service = BuilderWorkbenchService(state_dir=self.state_dir)
        return service.resolve_source_webspace_id(webspace_id)

    def _catalog(self, scenario_id: str) -> tuple[dict[str, Any], str]:
        from adaos.services.builder.workflow import BuilderWorkflowService

        scenario = str(scenario_id or "").strip()
        if not scenario:
            raise BuilderPreviewDataProfileError("scenario_id is required")
        workflow = self.workflow_service or BuilderWorkflowService.from_context()
        state = workflow.describe("scenario", scenario)
        binding = state.get("data_binding") if isinstance(state, Mapping) else {}
        phase = str(state.get("active_phase") or "prototype").strip() or "prototype"
        return dict(binding) if isinstance(binding, Mapping) else {}, phase

    def _store(self, source_webspace_id: str) -> dict[str, Any]:
        source = _safe_token(source_webspace_id)
        raw = _read_json(self.path(source))
        selections = raw.get("selections") if isinstance(raw.get("selections"), Mapping) else {}
        return {
            "schema": _STORE_SCHEMA,
            "source_webspace_id": source,
            "selections": {
                str(key): dict(value)
                for key, value in selections.items()
                if isinstance(value, Mapping)
            },
            "updated_at": raw.get("updated_at"),
        }

    def projection(self, scenario_id: str, *, webspace_id: str | None) -> dict[str, Any]:
        scenario = str(scenario_id or "").strip()
        source = self._source(webspace_id)
        binding, phase = self._catalog(scenario)
        profiles = [dict(item) for item in binding.get("profiles") or [] if isinstance(item, Mapping)]
        by_id = {str(item.get("profile_id") or "").strip(): item for item in profiles}
        project_ref = f"scenario:{scenario}"
        with _LOCK:
            record = copy.deepcopy(self._store(source)["selections"].get(project_ref) or {})
        requested_id = str(record.get("profile_id") or "").strip()
        default_id = str(binding.get("selected_profile_id") or "").strip()
        selected_id = requested_id if requested_id in by_id else default_id
        if selected_id not in by_id and profiles:
            selected_id = str(profiles[0].get("profile_id") or "").strip()
        selected = by_id.get(selected_id, {})
        ready, missing = _mapping_readiness(selected)
        status = str(record.get("status") or "ready").strip() or "ready"
        error = str(record.get("error") or "").strip() or None
        if requested_id and requested_id not in by_id:
            status = "stale"
            error = "selected_profile_removed"
        elif not ready:
            status = "blocked"
            error = "builder_data_binding_incomplete"
        options: list[dict[str, Any]] = []
        for profile in profiles:
            profile_id = str(profile.get("profile_id") or "").strip()
            mode = str(profile.get("mode") or "mock").strip() or "mock"
            option_ready, option_missing = _mapping_readiness(profile)
            forbidden = phase == "prototype" and mode == "live"
            disabled = forbidden or not option_ready
            reason = (
                "Live modifying data is forbidden in Prototype."
                if forbidden
                else "Map every declared capability before selecting this profile."
                if not option_ready
                else ""
            )
            options.append(
                {
                    "id": profile_id,
                    "value": profile_id,
                    "label": _profile_label(profile),
                    "subtitle": str(profile.get("source_ref") or "").strip(),
                    "mode": mode,
                    "status": "blocked" if disabled else "ready",
                    "disabled": disabled,
                    "reason": reason or None,
                    "requires_confirmation": mode in {"sandbox", "live_readonly"},
                    "missing_mappings": option_missing,
                }
            )
        return {
            "schema": "adaos.builder.preview_data_profile_projection.v1",
            "source_webspace_id": source,
            "project_ref": project_ref,
            "scenario_id": scenario,
            "profile_id": selected_id or "default-mock",
            "label": _profile_label(selected or {"profile_id": selected_id or "default-mock"}),
            "mode": str(selected.get("mode") or "mock").strip() or "mock",
            "generation": max(0, int(record.get("generation") or 0)),
            "project_binding_generation": max(0, int(binding.get("generation") or 0)),
            "status": status,
            "ready": ready and status == "ready",
            "error": error,
            "missing_mappings": missing,
            "operation_id": record.get("operation_id"),
            "updated_at": record.get("updated_at") or binding.get("updated_at"),
            "profile": copy.deepcopy(selected),
            "options": options,
        }

    def begin_selection(
        self,
        scenario_id: str,
        profile_id: str,
        *,
        webspace_id: str | None,
        expected_generation: int,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        scenario = str(scenario_id or "").strip()
        target_id = str(profile_id or "").strip()
        source = self._source(webspace_id)
        binding, phase = self._catalog(scenario)
        try:
            validated = select_profile(
                binding,
                target_id,
                phase=phase,
                confirmed=confirmed,
            )
        except BuilderDataModeError as exc:
            raise BuilderPreviewDataProfileError(str(exc)) from exc
        target = next(
            (
                dict(item)
                for item in validated.get("profiles") or []
                if isinstance(item, Mapping)
                and str(item.get("profile_id") or "").strip() == target_id
            ),
            {},
        )
        ready, missing = _mapping_readiness(target)
        if not ready:
            raise BuilderPreviewDataProfileError(
                "binding profile has incomplete implementation mappings: " + ", ".join(missing)
            )
        project_ref = f"scenario:{scenario}"
        operation_id = f"data-profile:{uuid.uuid4().hex}"
        now = _now()
        with _LOCK:
            store = self._store(source)
            previous = dict(store["selections"].get(project_ref) or {})
            generation = max(0, int(previous.get("generation") or 0))
            if generation != int(expected_generation):
                raise BuilderPreviewDataProfileError("stale Preview data profile generation")
            previous_profile_id = str(previous.get("profile_id") or binding.get("selected_profile_id") or "").strip()
            record = {
                "schema": _SELECTION_SCHEMA,
                "project_ref": project_ref,
                "profile_id": target_id,
                "previous_profile_id": previous_profile_id or None,
                "requested_profile_id": target_id,
                "generation": generation + 1,
                "project_binding_generation": max(0, int(binding.get("generation") or 0)),
                "status": "materializing",
                "operation_id": operation_id,
                "error": None,
                "updated_at": now,
            }
            _validate_selection(record)
            store["selections"][project_ref] = record
            store["updated_at"] = now
            _write_json(self.path(source), store)
        return self.projection(scenario, webspace_id=source)

    def finish_selection(
        self,
        scenario_id: str,
        *,
        webspace_id: str | None,
        operation_id: str,
        ok: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        scenario = str(scenario_id or "").strip()
        source = self._source(webspace_id)
        project_ref = f"scenario:{scenario}"
        with _LOCK:
            store = self._store(source)
            record = dict(store["selections"].get(project_ref) or {})
            if str(record.get("operation_id") or "") != str(operation_id or ""):
                return self.projection(scenario, webspace_id=source)
            now = _now()
            if not ok:
                record["profile_id"] = str(record.get("previous_profile_id") or "").strip()
            record.update(
                status="ready" if ok else "failed",
                error=None if ok else str(error or "preview_data_profile_materialization_failed"),
                previous_profile_id=None,
                updated_at=now,
            )
            _validate_selection(record)
            store["selections"][project_ref] = record
            store["updated_at"] = now
            _write_json(self.path(source), store)
        return self.projection(scenario, webspace_id=source)


__all__ = ["BuilderPreviewDataProfileError", "BuilderPreviewDataProfileService"]
