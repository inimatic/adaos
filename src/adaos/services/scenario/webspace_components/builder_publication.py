from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class BuilderPublicationOperations:
    """Runtime-owned boundaries used by Builder publication orchestration."""

    canonical_materialization_identity: Callable[..., dict[str, Any]]
    clone_json_like: Callable[[Any], Any]
    describe_webspace_operational_state: Callable[[str], Awaitable[Any]]
    elapsed_ms: Callable[[float], float]
    logger: Any
    payload_command_trace: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    preflight_validated_scenario: Callable[..., tuple[str | None, str, dict[str, Any]]]
    rebuild_webspace_from_sources: Callable[..., Awaitable[dict[str, Any]]]
    reload_webspace_from_scenario: Callable[..., Awaitable[dict[str, Any]]]
    resolve_rebuild_scenario_target: Callable[..., Awaitable[tuple[Any, str | None, str]]]
    scenario_runtime_type: type[Any]
    scenarios_loader: Any
    skill_sources_fingerprint_for_materialization: Callable[[str], str]
    workspace_index: Any


class WebspaceBuilderPublicationService:
    """Own Builder preview materialization and publication consumer reloads."""

    @staticmethod
    def empty_canvas_widget() -> dict[str, Any]:
        return {
            "id": "builder-empty-canvas",
            "type": "ui.form",
            "area": "main",
            "inputs": {
                "fields": [
                    {
                        "id": "builder-empty-canvas-message",
                        "type": "staticContent",
                        "title": "Empty prototype canvas",
                        "content": "Describe the interface in Builder to create the first prototype revision.",
                    }
                ]
            },
        }

    def ensure_empty_canvas_widget(self, page: dict[str, Any], scenario_id: str) -> None:
        meta = page.get("meta") if isinstance(page.get("meta"), Mapping) else {}
        builder_meta = meta.get("builder") if isinstance(meta.get("builder"), Mapping) else {}
        widgets = page.get("widgets") if isinstance(page.get("widgets"), list) else []
        if not bool(builder_meta.get("empty_canvas")) or widgets:
            return
        page["id"] = scenario_id
        page["widgets"] = [self.empty_canvas_widget()]
        builder_meta["placeholder_injected"] = True
        meta["builder"] = builder_meta
        page["meta"] = meta

    def publication_package_content(
        self,
        scenario_id: str,
        *,
        revision: str | None,
        operations: BuilderPublicationOperations,
    ) -> dict[str, Any] | None:
        """Read an installed immutable Publication even when its slot is not active."""

        from io import BytesIO
        from zipfile import BadZipFile, ZipFile

        from adaos.domain.artifact_release import ProjectRelease
        from adaos.services.artifact_pipeline.channels import ChannelError, SubscriptionStore
        from adaos.services.artifact_pipeline.packages import (
            ContentAddressedPackageStore,
            PackageVerificationError,
        )
        from adaos.services.runtime_paths import current_state_dir

        scenario_root = operations.scenarios_loader.scenario_root_for_space(
            scenario_id, "workspace"
        )
        workspace_root = scenario_root.parent.parent
        try:
            subscription = SubscriptionStore(
                workspace_root / ".adaos" / "subscriptions.json"
            ).load().get(scenario_id)
        except ChannelError as exc:
            raise ValueError("Builder publication subscription metadata is invalid") from exc
        if subscription is None or not subscription.installed_digest:
            return None
        expected_release = f"{scenario_id}@{str(revision or '').strip()}"
        if revision and subscription.installed_release != expected_release:
            return None

        release_path = (
            workspace_root
            / ".adaos"
            / "releases"
            / f"{subscription.installed_digest.split(':', 1)[-1]}.json"
        )
        if not release_path.is_file():
            return None
        try:
            release = ProjectRelease.from_mapping(
                json.loads(release_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Builder publication release metadata is invalid") from exc
        release_digest = release.release_digest or release.computed_digest()
        if (
            release.project_id != scenario_id
            or release_digest != subscription.installed_digest
            or (revision and release.version != str(revision).strip())
        ):
            raise ValueError(
                "Builder publication release identity does not match its subscription"
            )
        component = next(
            (
                item
                for item in release.components
                if item.kind == "scenario" and item.artifact_id == scenario_id
            ),
            None,
        )
        if component is None:
            raise ValueError("Builder publication release has no scenario component")

        store = ContentAddressedPackageStore(
            Path(current_state_dir()) / "artifact_pipeline" / "packages"
        )
        try:
            archive, verified = store.read_verified(component.digest)
            if verified.ref != component:
                raise ValueError("published package identity differs from its release")
            with ZipFile(BytesIO(archive), "r") as bundle:
                payload = json.loads(bundle.read("webui.json").decode("utf-8-sig"))
        except (OSError, KeyError, ValueError, BadZipFile, PackageVerificationError) as exc:
            raise ValueError("Builder publication package is unavailable or invalid") from exc
        return dict(payload) if isinstance(payload, Mapping) else None

    def trial_workspace_for_preview(
        self,
        scenario_id: str,
        *,
        revision: str | None,
        operations: BuilderPublicationOperations,
    ) -> tuple[dict[str, Any], Path]:
        from adaos.services.artifact_pipeline.trial_activation import (
            TrialActivationStore,
            legacy_runtime_trial_workspace,
            trial_workspace_root,
        )
        from adaos.services.runtime_paths import current_state_dir

        activations = TrialActivationStore(
            current_state_dir() / "artifact_pipeline" / "trial-activations"
        )
        activation = activations.find_for_target(
            scenario_id=scenario_id,
            revision=str(revision or "").strip() or None,
        )
        if activation is None:
            raise ValueError(
                f"Builder Trial activation is unavailable: "
                f"{scenario_id}@{str(revision or '').strip() or 'current'}"
            )
        runtime_binding = (
            activation.get("runtime_binding")
            if isinstance(activation.get("runtime_binding"), Mapping)
            else {}
        )
        candidate_ref = (
            activation.get("candidate_ref")
            if isinstance(activation.get("candidate_ref"), Mapping)
            else {}
        )
        candidate_id = str(candidate_ref.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("Builder Trial activation has no Candidate identity")
        installed_scenario = operations.scenarios_loader.scenario_root_for_space(
            scenario_id, "workspace"
        )
        workspace_root = installed_scenario.parent.parent
        canonical_root = trial_workspace_root(workspace_root, candidate_id).resolve()
        legacy_root = legacy_runtime_trial_workspace(
            workspace_root, candidate_id
        ).resolve()
        bound_path = str(runtime_binding.get("path") or "").strip()
        bound_root = Path(bound_path).resolve() if bound_path else canonical_root
        if bound_root not in {canonical_root, legacy_root}:
            raise ValueError("Builder Trial activation points outside its governed root")
        canonical_contract = bool(
            bound_root == canonical_root
            and str(runtime_binding.get("kind") or "").strip()
            == "isolated_trial_workspace"
            and str(runtime_binding.get("authority") or "").strip()
            == "immutable_candidate"
        )
        legacy_contract = bool(
            bound_root == legacy_root
            and str(runtime_binding.get("kind") or "").strip()
            in {"", "derived_workspace_runtime"}
        )
        if not canonical_contract and not legacy_contract:
            raise ValueError("Builder Trial activation has no trusted source authority")
        selected_root = canonical_root if canonical_root.is_dir() else bound_root
        if not selected_root.is_dir():
            raise ValueError("Builder Trial Workspace is unavailable")
        return dict(activation), selected_root

    def preview_content_override(
        self,
        scenario_id: str,
        *,
        stage: str,
        revision: str | None,
        label: str | None,
        operations: BuilderPublicationOperations,
    ) -> tuple[dict[str, Any] | None, str | None]:
        stage_token = str(stage or "").strip().lower()
        if stage_token not in {"prototype", "automation", "trial", "publication"}:
            return None, None
        source_space = {
            "prototype": "dev",
            "automation": "dev",
            "trial": "trial",
            "publication": "workspace",
        }[stage_token]
        content: Mapping[str, Any] | None = None
        revision_token = str(revision or "").strip()
        if stage_token == "prototype" and revision_token:
            if not revision_token.isdigit():
                raise ValueError(f"Builder prototype revision is unavailable: {revision}")
            root = operations.scenarios_loader.scenario_root_for_space(scenario_id, "dev")
            revision_path = root / "ui_revisions" / f"{revision_token}.json"
            try:
                revision_payload = json.loads(revision_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Builder prototype revision is unavailable: {revision}"
                ) from exc
            content = (
                revision_payload.get("after_webui")
                if isinstance(revision_payload, Mapping)
                else None
            )
        elif stage_token == "automation":
            from adaos.services.runtime_paths import current_state_dir

            snapshot_path = (
                current_state_dir()
                / "builder"
                / "workflow_snapshots"
                / "scenario"
                / scenario_id
                / "automation"
                / "webui.json"
            )
            try:
                snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                snapshot_payload = None
            content = snapshot_payload if isinstance(snapshot_payload, Mapping) else None
        elif stage_token == "trial":
            _, selected_root = self.trial_workspace_for_preview(
                scenario_id,
                revision=revision_token or None,
                operations=operations,
            )
            scenario_root = selected_root / "scenarios" / scenario_id
            manifest: Mapping[str, Any] = {}
            manifest_path = scenario_root / "scenario.yaml"
            if manifest_path.is_file():
                try:
                    loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig")) or {}
                    manifest = loaded if isinstance(loaded, Mapping) else {}
                except Exception:
                    manifest = {}
            ui = manifest.get("ui") if isinstance(manifest.get("ui"), Mapping) else {}
            descriptor_name = str(ui.get("manifest") or "").strip()
            candidates = [
                scenario_root / descriptor_name if descriptor_name else None,
                scenario_root / "webui.json",
                scenario_root / "scenario.json",
            ]
            for candidate_path in candidates:
                if candidate_path is None or not candidate_path.is_file():
                    continue
                try:
                    loaded = json.loads(candidate_path.read_text(encoding="utf-8-sig"))
                except Exception:
                    continue
                if isinstance(loaded, Mapping):
                    content = loaded
                    break
        if stage_token == "trial" and not isinstance(content, Mapping):
            raise ValueError(
                f"Builder Trial package content is unavailable: {scenario_id}"
            )
        if not isinstance(content, Mapping):
            content = operations.scenarios_loader.read_content(
                scenario_id, space=source_space
            )
        if stage_token == "publication" and (
            not isinstance(content, Mapping) or not content
        ):
            content = self.publication_package_content(
                scenario_id,
                revision=revision_token or None,
                operations=operations,
            )
        if not isinstance(content, Mapping) or not content:
            raise ValueError(
                f"Builder {stage_token} preview source is unavailable: {scenario_id}"
            )

        override = operations.clone_json_like(content)
        ui = override.get("ui") if isinstance(override.get("ui"), Mapping) else {}
        application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
        desktop = (
            application.get("desktop")
            if isinstance(application.get("desktop"), Mapping)
            else {}
        )
        page = (
            desktop.get("pageSchema")
            if isinstance(desktop.get("pageSchema"), Mapping)
            else {}
        )
        if stage_token == "prototype" and not page:
            try:
                manifest = operations.scenarios_loader.read_manifest(
                    scenario_id, space=source_space
                )
            except Exception:
                manifest = {}
            title = str(
                manifest.get("title") or manifest.get("name") or scenario_id
            ).strip() or scenario_id
            page = {
                "id": scenario_id,
                "title": title,
                "layout": {
                    "type": "single",
                    "pattern": "stack",
                    "areas": [{"id": "main", "role": "main"}],
                },
                "widgets": [self.empty_canvas_widget()],
                "meta": {
                    "builder": {
                        "empty_canvas": True,
                        "compatibility_fallback": True,
                    }
                },
            }
            desktop["pageSchema"] = page
            application["desktop"] = desktop
            ui["application"] = application
            override["ui"] = ui
        if stage_token == "prototype" and page:
            self.ensure_empty_canvas_widget(page, scenario_id)
        if page:
            existing_title = str(page.get("title") or scenario_id).strip() or scenario_id
            prefix = {
                "prototype": f"proto:{str(revision or 'current').strip() or 'current'}",
                "automation": "active:",
                "trial": f"trial:{str(revision or 'current').strip() or 'current'}",
                "publication": f"public:{str(revision or 'current').strip() or 'current'}",
            }[stage_token]
            page["title"] = str(label or f"{prefix} {existing_title}").strip()
            metadata = (
                dict(page.get("_adaos"))
                if isinstance(page.get("_adaos"), Mapping)
                else {}
            )
            release_stage = {
                "prototype": "ALPHA",
                "automation": "ALPHA",
                "trial": "BETA",
                "publication": "STABLE",
            }[stage_token]
            metadata["releaseStage"] = release_stage
            metadata["releaseStageSource"] = "builder_materialization"
            metadata["materialization"] = {
                "stage": stage_token,
                "revision": revision_token or None,
                "sourceSpace": source_space,
            }
            page["_adaos"] = metadata
        return dict(override), source_space

    async def apply_revision_materialization(
        self,
        webspace_id: str,
        *,
        scenario_id: str,
        revision: str | None = None,
        preview_stage: str | None = None,
        preview_label: str | None = None,
        source_fingerprint: str | None = None,
        user_id: str | None = None,
        roles: Any = None,
        policy_fingerprint: str | None = None,
        event_payload: dict[str, Any] | None = None,
        operations: BuilderPublicationOperations,
    ) -> dict[str, Any]:
        webspace_id = str(webspace_id or "").strip()
        if not webspace_id:
            raise ValueError("webspace_id is required")
        requested_scenario = str(scenario_id or "").strip()
        if not requested_scenario:
            raise ValueError("scenario_id is required")
        preview_stage_token = str(preview_stage or "").strip().lower()

        source_webspace_id = str(
            (event_payload or {}).get("source_webspace_id") or ""
        ).strip()
        if source_webspace_id:
            try:
                from adaos.services.builder.workbench import BuilderWorkbenchService

                binding = BuilderWorkbenchService.from_context().get_workspace_binding(
                    source_webspace_id
                )
            except Exception:
                binding = {}
                operations.logger.debug(
                    "builder materialization target guard unavailable source_webspace=%s dev_webspace=%s scenario=%s",
                    source_webspace_id,
                    webspace_id,
                    requested_scenario,
                    exc_info=True,
                )
            desired_dev_webspace = str(binding.get("dev_webspace_id") or "").strip()
            desired_scenario = str(binding.get("runtime_scenario_id") or "").strip()
            if (
                desired_dev_webspace == webspace_id
                and desired_scenario
                and desired_scenario != requested_scenario
            ):
                operations.logger.info(
                    "builder materialization superseded source_webspace=%s dev_webspace=%s requested_scenario=%s desired_scenario=%s revision=%s",
                    source_webspace_id,
                    webspace_id,
                    requested_scenario,
                    desired_scenario,
                    str(revision or "").strip() or "-",
                )
                return {
                    "ok": True,
                    "accepted": False,
                    "skipped": "superseded_builder_target",
                    "action": "builder_revision_apply",
                    "source_webspace_id": source_webspace_id,
                    "webspace_id": webspace_id,
                    "scenario_id": requested_scenario,
                    "desired_scenario_id": desired_scenario,
                    "revision": str(revision or "").strip() or None,
                }

        state, resolved_scenario_id, scenario_resolution = (
            await operations.resolve_rebuild_scenario_target(
                webspace_id,
                requested_scenario,
                prefer_manifest_home_before_current=False,
            )
        )
        if preview_stage_token == "trial" and resolved_scenario_id:
            trial_activation, trial_workspace = self.trial_workspace_for_preview(
                resolved_scenario_id,
                revision=revision,
                operations=operations,
            )
            trial_scenario = trial_workspace / "scenarios" / resolved_scenario_id
            if not trial_scenario.is_dir():
                raise ValueError(
                    f"Builder Trial scenario is unavailable: {resolved_scenario_id}"
                )
            candidate_ref = (
                trial_activation.get("candidate_ref")
                if isinstance(trial_activation.get("candidate_ref"), Mapping)
                else {}
            )
            release_ref = (
                trial_activation.get("release_ref")
                if isinstance(trial_activation.get("release_ref"), Mapping)
                else {}
            )
            candidate_id = str(candidate_ref.get("candidate_id") or "").strip()
            release_digest = str(
                candidate_ref.get("release_digest")
                or release_ref.get("digest")
                or candidate_ref.get("package_digest")
                or candidate_id
            ).strip()
            source_fingerprint = f"trial:{release_digest}"
            scenario_resolution = "builder_trial_candidate"
            preflight = {
                "ok": True,
                "scenario_id": resolved_scenario_id,
                "resolution": scenario_resolution,
                "source": "immutable_trial_workspace",
                "candidate_id": candidate_id,
                "path": str(trial_scenario),
            }
        else:
            resolved_scenario_id, scenario_resolution, preflight = (
                operations.preflight_validated_scenario(
                    resolved_scenario_id,
                    source_mode=state.source_mode,
                    resolution=scenario_resolution or "builder_revision",
                )
            )
        if not resolved_scenario_id:
            return {
                "ok": False,
                "accepted": False,
                "action": "builder_revision_apply",
                "webspace_id": webspace_id,
                "scenario_id": None,
                "scenario_resolution": scenario_resolution,
                "kind": state.kind,
                "source_mode": state.source_mode,
                "validation": preflight,
                "error": "scenario_not_found",
            }

        identity_update = {
            "attempted": False,
            "changed": False,
            "webspace_id": webspace_id,
            "home_scenario_before": state.effective_home_scenario,
            "home_scenario": state.effective_home_scenario,
        }
        if state.is_dev and str(state.effective_home_scenario or "").strip() != resolved_scenario_id:
            stage_started = time.perf_counter()
            try:
                row = operations.workspace_index.set_workspace_manifest(
                    webspace_id, home_scenario=resolved_scenario_id
                )
                identity_update.update(
                    {
                        "attempted": True,
                        "changed": True,
                        "home_scenario": row.effective_home_scenario,
                        "timing_ms": operations.elapsed_ms(stage_started),
                    }
                )
            except Exception as exc:
                identity_update.update(
                    {
                        "attempted": True,
                        "changed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "timing_ms": operations.elapsed_ms(stage_started),
                    }
                )
                operations.logger.warning(
                    "failed to persist builder dev webspace identity webspace=%s scenario=%s",
                    webspace_id,
                    resolved_scenario_id,
                    exc_info=True,
                )

        identity = operations.canonical_materialization_identity(
            webspace_id=webspace_id,
            scenario_id=resolved_scenario_id,
            revision=revision,
            source_fingerprint=source_fingerprint,
            user_id=user_id,
            roles=roles,
            policy_fingerprint=policy_fingerprint,
        )
        content_override, skill_source_mode = self.preview_content_override(
            resolved_scenario_id,
            stage=str(preview_stage or ""),
            revision=revision,
            label=preview_label,
            operations=operations,
        )
        skill_decls_snapshot = None
        skill_decls_fingerprint = None
        if str(preview_stage or "").strip().lower() == "trial":
            _, trial_workspace = self.trial_workspace_for_preview(
                resolved_scenario_id,
                revision=revision,
                operations=operations,
            )
            trial_runtime = operations.scenario_runtime_type()
            skill_decls_snapshot = trial_runtime._collect_skill_decls_from_root(
                trial_workspace / "skills"
            )
            skill_decls_fingerprint = str(
                getattr(trial_runtime, "_last_skill_decls_fingerprint", "") or ""
            ).strip()
            skill_source_mode = None
        request_id = f"builder-revision-{identity['key_hash']}-{int(time.time() * 1000)}"
        trace = operations.payload_command_trace(event_payload or {})
        operations.logger.info(
            "applying builder revision materialization webspace=%s scenario=%s revision=%s user=%s roles_hash=%s cmd=%s trace=%s key_hash=%s",
            webspace_id,
            resolved_scenario_id,
            identity.get("revision") or "-",
            identity.get("user_id") or "-",
            identity.get("roles_hash") or "-",
            trace.get("cmd_id") or "-",
            trace.get("trace_id") or "-",
            identity.get("key_hash") or "-",
        )

        result = await operations.rebuild_webspace_from_sources(
            webspace_id,
            action="builder_revision_apply",
            scenario_id=resolved_scenario_id,
            scenario_resolution=scenario_resolution or "builder_revision",
            source_of_truth="builder_revision",
            reseed_from_scenario=False,
            event_payload=event_payload,
            request_id=request_id,
            switch_mode="materialization_pointer_compat",
            materialization_identity=identity,
            scenario_content_override=content_override,
            skill_source_mode=skill_source_mode,
            skill_decls_snapshot=skill_decls_snapshot,
            skill_decls_fingerprint=skill_decls_fingerprint,
        )
        result.update(
            {
                "kind": state.kind,
                "source_mode": state.source_mode,
                "home_scenario": identity_update.get("home_scenario")
                or state.effective_home_scenario,
                "current_scenario_before": state.current_scenario,
                "validation": preflight,
                "materialization_identity": identity,
                "webspace_identity_update": identity_update,
            }
        )
        return result

    async def reload_preview_webspaces_for_project(
        self,
        object_type: str,
        object_id: str,
        *,
        reason: str | None,
        operations: BuilderPublicationOperations,
    ) -> dict[str, Any]:
        object_type = str(object_type or "").strip().lower()
        object_id = str(object_id or "").strip()
        if object_type not in {"scenario", "skill"} or not object_id:
            return {"ok": False, "accepted": False, "error": "project_identity_required"}

        def _discover_targets() -> list[tuple[str, str]]:
            try:
                from adaos.services.builder.workbench import BuilderWorkbenchService

                workbench = BuilderWorkbenchService.from_context()
                # Preserve legacy binding migration before reading the
                # authoritative relationship registry.
                workbench.list_workspace_bindings()
                relations = workbench.relationships.list()
            except Exception:
                relations = []

            targets: list[tuple[str, str]] = []
            seen_targets: set[str] = set()
            for relation in relations:
                webspace_id = str(relation.target_webspace_id or "").strip()
                if not webspace_id or webspace_id in seen_targets:
                    continue
                row = operations.workspace_index.get_workspace(webspace_id)
                if row is None:
                    operations.logger.info(
                        "ignoring stale preview relation target=%s project=%s:%s",
                        webspace_id,
                        object_type,
                        object_id,
                    )
                    continue
                home_scenario = str(
                    getattr(row, "effective_home_scenario", "")
                    or relation.metadata.get("scenario_id")
                    or ""
                ).strip()
                if not home_scenario:
                    continue
                if object_type == "scenario":
                    if home_scenario == object_id:
                        targets.append((webspace_id, home_scenario))
                        seen_targets.add(webspace_id)
                    continue
                try:
                    source_mode = str(
                        getattr(row, "effective_source_mode", "dev") or "dev"
                    )
                    manifest = operations.scenarios_loader.read_manifest(
                        home_scenario, space=source_mode
                    )
                    depends = {
                        str(item).strip()
                        for item in (manifest.get("depends") or [])
                        if str(item).strip()
                    }
                    if object_id in depends:
                        targets.append((webspace_id, home_scenario))
                        seen_targets.add(webspace_id)
                except Exception:
                    operations.logger.debug(
                        "failed to resolve scenario depends for preview webspace=%s home=%s",
                        webspace_id,
                        home_scenario,
                        exc_info=True,
                    )
            return targets

        targets = await asyncio.to_thread(_discover_targets)

        reloaded: list[str] = []
        failed: list[str] = []
        for webspace_id, scenario_id in targets:
            try:
                await operations.reload_webspace_from_scenario(
                    webspace_id, scenario_id=scenario_id, action="reload"
                )
                reloaded.append(webspace_id)
            except Exception:
                failed.append(webspace_id)
                operations.logger.warning(
                    "failed to reload preview webspace=%s for %s:%s reason=%s",
                    webspace_id,
                    object_type,
                    object_id,
                    reason,
                    exc_info=True,
                )
        return {
            "ok": not failed,
            "accepted": bool(targets),
            "object_type": object_type,
            "object_id": object_id,
            "reason": str(reason or "").strip() or None,
            "reloaded_webspaces": reloaded,
            "failed_webspaces": failed,
        }

    async def prewarm_materialization_sources(
        self, *, operations: BuilderPublicationOperations
    ) -> dict[str, Any]:
        started = time.perf_counter()

        def _warm() -> dict[str, Any]:
            runtime = operations.scenario_runtime_type()
            modes: dict[str, Any] = {}
            for mode in ("workspace", "dev"):
                mode_started = time.perf_counter()
                decls = runtime._collect_skill_decls(mode=mode)
                modes[mode] = {
                    "declarations": len(decls),
                    "fingerprint": str(
                        getattr(runtime, "_last_skill_decls_fingerprint", "") or ""
                    ),
                    "elapsed_ms": operations.elapsed_ms(mode_started),
                }
                operations.skill_sources_fingerprint_for_materialization(mode)
            return modes

        modes = await asyncio.to_thread(_warm)
        result = {
            "ok": True,
            "modes": modes,
            "elapsed_ms": operations.elapsed_ms(started),
        }
        operations.logger.info(
            "prewarmed webspace materialization sources result=%s", result
        )
        return result

    async def reload_workspace_webspaces_for_publication(
        self,
        object_type: str,
        object_id: str,
        *,
        operations: BuilderPublicationOperations,
    ) -> dict[str, Any]:
        object_type = str(object_type or "").strip().lower()
        object_id = str(object_id or "").strip()
        if object_type not in {"scenario", "skill"} or not object_id:
            return {"ok": False, "accepted": False, "error": "project_identity_required"}

        if object_type == "scenario":
            operations.scenarios_loader.invalidate_cache(
                scenario_id=object_id, space="workspace"
            )
        try:
            rows = list(operations.workspace_index.list_workspaces())
        except Exception:
            rows = []

        targets: list[tuple[str, str]] = []
        for row in rows:
            source_mode = str(
                getattr(row, "effective_source_mode", "workspace") or "workspace"
            ).strip().lower()
            if source_mode != "workspace":
                continue
            webspace_id = str(getattr(row, "workspace_id", "") or "").strip()
            if not webspace_id:
                continue
            try:
                state = await operations.describe_webspace_operational_state(webspace_id)
                scenario_id = str(
                    state.current_scenario or state.effective_home_scenario or ""
                ).strip()
            except Exception:
                scenario_id = str(
                    getattr(row, "effective_home_scenario", "") or ""
                ).strip()
            if not scenario_id:
                continue
            if object_type == "scenario":
                if scenario_id != object_id:
                    continue
            else:
                try:
                    manifest = operations.scenarios_loader.read_manifest(
                        scenario_id, space="workspace"
                    )
                    dependencies = {
                        str(item).strip()
                        for item in (manifest.get("depends") or [])
                        if str(item).strip()
                    }
                except Exception:
                    dependencies = set()
                if object_id not in dependencies:
                    continue
            targets.append((webspace_id, scenario_id))

        reloaded: list[str] = []
        failed: list[str] = []
        for webspace_id, scenario_id in targets:
            try:
                await operations.reload_webspace_from_scenario(
                    webspace_id,
                    scenario_id=scenario_id,
                    action=f"published_{object_type}_reload",
                    event_payload={
                        "source": "registry.publication",
                        "object_type": object_type,
                        "object_id": object_id,
                    },
                )
                reloaded.append(webspace_id)
            except Exception:
                failed.append(webspace_id)
                operations.logger.warning(
                    "failed to reload workspace webspace=%s after publishing %s:%s",
                    webspace_id,
                    object_type,
                    object_id,
                    exc_info=True,
                )
        return {
            "ok": not failed,
            "accepted": bool(targets),
            "object_type": object_type,
            "object_id": object_id,
            "reloaded_webspaces": reloaded,
            "failed_webspaces": failed,
        }
