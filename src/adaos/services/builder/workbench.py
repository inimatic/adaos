from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from adaos.services.runtime_paths import current_state_dir
from adaos.services.webspace_id import coerce_webspace_id


BUILDER_WORKBENCH_SCENARIO_ID = "prompt_engineer_scenario"
BUILDER_RUNTIME_FALLBACK_SCENARIO_ID = "web_desktop"
BUILDER_DIALOG_CHANNEL_ID = "builder"
BUILDER_SKILL_ID = "builder_skill"
BUILDER_OWNER = f"skill:{BUILDER_SKILL_ID}"


def safe_source_webspace_id(value: Any) -> str:
    fallback = os.getenv("ADAOS_WEBSPACE_ID") or "desktop"
    token = coerce_webspace_id(value, fallback=fallback)
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(token or "").strip()).strip(".:-_")
    return token or fallback


def dev_webspace_id_for_source(source_webspace_id: Any) -> str:
    return f"{safe_source_webspace_id(source_webspace_id)}-dev"


def _now() -> float:
    return time.time()


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except FileNotFoundError:
        return dict(default or {})
    if not isinstance(data, dict):
        return dict(default or {})
    return data


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _info_to_dict(info: Any) -> dict[str, Any]:
    if info is None:
        return {}
    to_dict = getattr(info, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    out: dict[str, Any] = {}
    for key in ("id", "webspace_id", "title", "kind", "source_mode", "home_scenario"):
        value = getattr(info, key, None)
        if value is not None:
            out[key] = value
    return out


def _draft_runtime_scenario_id(state_dir: Path | None, draft_id: str | None) -> str | None:
    token = str(draft_id or "").strip()
    if not token:
        return None
    draft_path = Path(state_dir or current_state_dir()) / "builder" / "drafts" / token / "builder.draft.json"
    draft = _read_json(draft_path)
    artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
    if str(artifact.get("kind") or "").strip() != "scenario":
        return None
    scenario_id = str(artifact.get("id") or "").strip()
    return scenario_id or None


@dataclass(slots=True)
class BuilderWorkbenchService:
    state_dir: Path | None = None
    webspace_service: Any | None = None

    @classmethod
    def from_context(cls) -> "BuilderWorkbenchService":
        return cls(state_dir=current_state_dir())

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "builder" / "workbench"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def binding_path(self, source_webspace_id: str) -> Path:
        return self.root / "bindings" / f"{safe_source_webspace_id(source_webspace_id)}.json"

    def snapshot_path(self, source_webspace_id: str) -> Path:
        return self.root / "snapshots" / f"{safe_source_webspace_id(source_webspace_id)}.json"

    async def ensure_dev_webspace(
        self,
        source_webspace_id: str | None = None,
        *,
        active_draft_id: str | None = None,
        scenario_id: str | None = None,
        runtime_scenario_id: str | None = None,
    ) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        dev_id = dev_webspace_id_for_source(source_id)
        workbench_scenario = str(scenario_id or "").strip() or BUILDER_WORKBENCH_SCENARIO_ID
        runtime_scenario = (
            str(runtime_scenario_id or "").strip()
            or _draft_runtime_scenario_id(self.state_dir, active_draft_id)
            or BUILDER_RUNTIME_FALLBACK_SCENARIO_ID
        )
        created = False
        info_payload: dict[str, Any] = {}
        runtime_payload: dict[str, Any] = {}
        try:
            svc = self.webspace_service
            if svc is None:
                from adaos.services.scenario.webspace_runtime import WebspaceService

                svc = WebspaceService()
            existing = None
            for item in svc.list(mode="mixed"):
                if str(getattr(item, "id", "") or getattr(item, "webspace_id", "") or "").strip() == dev_id:
                    existing = item
                    break
            if existing is None:
                existing = await svc.create(
                    dev_id,
                    f"DEV: {source_id}",
                    scenario_id=runtime_scenario,
                    dev=True,
                )
                created = True
            else:
                kind = str(getattr(existing, "kind", "") or "").strip()
                if kind and kind != "dev":
                    raise ValueError(f"paired webspace {dev_id!r} exists but is not a dev webspace")
                home = str(getattr(existing, "home_scenario", "") or "").strip()
                if home != runtime_scenario:
                    updated = await svc.set_home_scenario(dev_id, runtime_scenario)
                    existing = updated or existing
            info_payload = _info_to_dict(existing)
            if runtime_scenario and self.webspace_service is None:
                try:
                    from adaos.services.scenario.webspace_runtime import reload_webspace_from_scenario

                    runtime_payload = await reload_webspace_from_scenario(
                        dev_id,
                        scenario_id=runtime_scenario,
                        action="reload",
                        event_payload={
                            "source": "builder.workbench",
                            "source_webspace_id": source_id,
                            "active_draft_id": active_draft_id,
                        },
                    )
                except Exception as exc:
                    runtime_payload = {
                        "ok": False,
                        "error": "dev_runtime_reload_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
            elif runtime_scenario:
                runtime_payload = {
                    "ok": True,
                    "skipped": "injected_webspace_service",
                    "webspace_id": dev_id,
                    "scenario_id": runtime_scenario,
                }
        except Exception as exc:
            info_payload = {"ok": False, "error": "dev_webspace_unavailable", "detail": f"{type(exc).__name__}: {exc}"}

        binding = self.set_active_draft(
            source_webspace_id=source_id,
            active_draft_id=active_draft_id,
            scenario_id=workbench_scenario,
            dev_webspace_id=dev_id,
            runtime_scenario_id=runtime_scenario,
            persist_projection=False,
        )
        binding["created"] = created
        binding["dev_webspace"] = info_payload
        binding["runtime"] = runtime_payload
        await self.publish_projection(source_id)
        return binding

    def get_workspace_binding(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        existing = _read_json(self.binding_path(source_id))
        if existing:
            return existing
        return {
            "source_webspace_id": source_id,
            "dev_webspace_id": dev_webspace_id_for_source(source_id),
            "scenario_id": BUILDER_WORKBENCH_SCENARIO_ID,
            "runtime_scenario_id": None,
            "purpose": "builder_prompt_ide",
            "active_draft_id": None,
            "dialog": self.dialog_widget_config(source_id),
            "created_at": None,
            "updated_at": None,
        }

    def set_active_draft(
        self,
        *,
        source_webspace_id: str | None = None,
        active_draft_id: str | None,
        scenario_id: str = BUILDER_WORKBENCH_SCENARIO_ID,
        dev_webspace_id: str | None = None,
        runtime_scenario_id: str | None = None,
        persist_projection: bool = True,
    ) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        now = _now()
        existing = _read_json(self.binding_path(source_id))
        binding = {
            "source_webspace_id": source_id,
            "dev_webspace_id": str(dev_webspace_id or existing.get("dev_webspace_id") or dev_webspace_id_for_source(source_id)).strip(),
            "scenario_id": str(scenario_id or existing.get("scenario_id") or BUILDER_WORKBENCH_SCENARIO_ID).strip(),
            "runtime_scenario_id": (
                str(runtime_scenario_id or "").strip()
                or _draft_runtime_scenario_id(self.state_dir, active_draft_id)
                or existing.get("runtime_scenario_id")
                or None
            ),
            "purpose": "builder_prompt_ide",
            "active_draft_id": str(active_draft_id or "").strip() or None,
            "dialog": self.dialog_widget_config(source_id),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        _write_json(self.binding_path(source_id), binding)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return binding

    def open_dev_webspace(self, source_webspace_id: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
        binding = self.get_workspace_binding(source_webspace_id)
        dev_id = str(binding.get("dev_webspace_id") or "").strip()
        base = str(base_url or "").strip().rstrip("/")
        url = f"{base}/?webspace={dev_id}" if base else f"/?webspace={dev_id}"
        return {"ok": True, "url": url, "webspace_id": dev_id, "binding": binding}

    def dialog_widget_config(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        return {
            "widget": "voice_chat",
            "mode": "embedded",
            "source_webspace_id": source_id,
            "dialog_channel_id": BUILDER_DIALOG_CHANNEL_ID,
            "conversation_id": f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}",
            "owner": BUILDER_OWNER,
            "default_tool": f"{BUILDER_SKILL_ID}.chat",
            "allow_voice": True,
            "allow_text": True,
        }

    def list_development_skills(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        drafts: list[dict[str, Any]] = []
        drafts_root = Path(self.state_dir or current_state_dir()) / "builder" / "drafts"
        if drafts_root.exists():
            for path in sorted(drafts_root.glob("*/builder.draft.json")):
                draft = _read_json(path)
                artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
                metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
                links = draft.get("links") if isinstance(draft.get("links"), dict) else {}
                conversation = links.get("conversation") if isinstance(links.get("conversation"), dict) else {}
                draft_webspace = str(metadata.get("webspace_id") or conversation.get("webspace_id") or source_id).strip()
                if draft_webspace and draft_webspace != source_id:
                    continue
                drafts.append(
                    {
                        "draft_id": draft.get("draft_id"),
                        "status": draft.get("status"),
                        "kind": artifact.get("kind"),
                        "id": artifact.get("id"),
                        "root": artifact.get("root"),
                        "source_idea": metadata.get("source_idea"),
                        "active": draft.get("draft_id") == binding.get("active_draft_id"),
                        "updated_at": draft.get("updated_at") or draft.get("created_at"),
                    }
                )
        return {"ok": True, "source_webspace_id": source_id, "active_draft_id": binding.get("active_draft_id"), "items": drafts}

    def delete_development_skill(self, draft_id: str, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        token = str(draft_id or "").strip()
        if not token:
            return {"ok": False, "error": "draft_id_required"}
        drafts_root = Path(self.state_dir or current_state_dir()) / "builder" / "drafts"
        draft_dir = (drafts_root / token).resolve()
        root = drafts_root.resolve()
        if root not in draft_dir.parents or not draft_dir.exists():
            return {"ok": False, "error": "draft_not_found", "draft_id": token}
        draft = _read_json(draft_dir / "builder.draft.json")
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        artifact_root = Path(str(artifact.get("root") or "")).expanduser()
        shutil.rmtree(draft_dir)
        removed_artifact = False
        if artifact_root and artifact_root.exists():
            try:
                shutil.rmtree(artifact_root.resolve())
                removed_artifact = True
            except Exception:
                removed_artifact = False
        binding = self.get_workspace_binding(source_id)
        if binding.get("active_draft_id") == token:
            self.set_active_draft(source_webspace_id=source_id, active_draft_id=None)
        self.publish_projection_sync(source_id)
        return {"ok": True, "draft_id": token, "removed_artifact": removed_artifact}

    def snapshot(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        snapshot = {
            "schema": "adaos.builder.workbench.v1",
            "source_webspace_id": source_id,
            "binding": binding,
            "dialog": self.dialog_widget_config(source_id),
            "development_skills": self.list_development_skills(source_id).get("items", []),
            "preview_state": dict(preview_state or {}),
            "updated_at": _now(),
        }
        _write_json(self.snapshot_path(source_id), snapshot)
        return snapshot

    async def publish_projection(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_id = safe_source_webspace_id(source_webspace_id)
        snapshot = self.snapshot(source_id, preview_state=preview_state)
        targets = [source_id, str(snapshot["binding"].get("dev_webspace_id") or "")]
        published: list[str] = []
        for target in [item for item in targets if item]:
            try:
                from adaos.services.yjs.doc import async_get_ydoc

                async with async_get_ydoc(target, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
                    data = ydoc.get_map("data")
                    with ydoc.begin_transaction() as txn:
                        data.set(txn, "builder", snapshot)
                published.append(target)
            except Exception:
                continue
        return {"ok": True, "snapshot": snapshot, "published_webspaces": published}

    def publish_projection_sync(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.publish_projection(source_webspace_id, preview_state=preview_state))
        snapshot = self.snapshot(source_webspace_id, preview_state=preview_state)
        return {"ok": True, "snapshot": snapshot, "published_webspaces": [], "deferred": True}
