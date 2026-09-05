"""Bounded Builder facade for the Application development lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.domain.application import Application
from adaos.sdk.core._ctx import require_ctx
from adaos.services.applications import (
    ApplicationDevelopmentCoordinator,
    StableSourceProjectionService,
    get_application_distribution_service,
    get_application_service,
    get_stable_source_publisher,
)
from adaos.services.artifact_pipeline.runtime_trust import artifact_signing_public_identity


def _ctx():
    return require_ctx("sdk.builder.applications")


def _state_dir() -> Path:
    return Path(_ctx().paths.state_dir()).expanduser().resolve()


def _application_service():
    return get_application_service(_state_dir())


def _coordinator() -> ApplicationDevelopmentCoordinator:
    return ApplicationDevelopmentCoordinator(_state_dir())


def _application(application_id: str, expected_revision: int) -> Application:
    application = _application_service().store.get_application(application_id)
    if application.revision != expected_revision:
        raise ValueError(
            f"Application revision conflict: expected {expected_revision}, observed {application.revision}"
        )
    return application


def _primary_scenario(application: Application) -> str:
    refs = [
        str(item.get("presentation_ref") or "")
        for item in application.entrypoints
        if str(item.get("presentation_ref") or "").startswith("scenario:")
    ]
    if not refs:
        raise ValueError("Application has no scenario entrypoint for Builder Preview")
    return refs[0].split(":", 1)[1]


def publisher_context() -> dict[str, Any]:
    ctx = _ctx()
    config = ctx.config
    subnet_id = str(config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id)
    subnet_ref = subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"
    zone_id = str(getattr(config, "zone_id", None) or "local").strip().lower()
    try:
        from adaos.services.subnet_alias import load_subnet_alias

        display_name = str(load_subnet_alias(subnet_id=subnet_id) or "").strip()
    except Exception:
        display_name = ""
    short_ref = subnet_id[-12:] if len(subnet_id) > 12 else subnet_id
    signing = artifact_signing_public_identity()
    return {
        "schema": "adaos.application.publisher_context.v1",
        "publisher_ref": subnet_ref,
        "display_name": display_name or f"Publisher {short_ref}",
        "subnet_short_ref": short_ref,
        "home_zone": zone_id,
        "release_key_ref": signing["release_key_ref"],
        "release_key_fingerprint": signing["key_id"],
        "release_key_algorithm": signing["algorithm"],
        "release_key_issuer": signing["issuer"],
        "trust_relation": "local",
    }


def create_application(
    application_id: str,
    *,
    title: str,
    summary: str,
    template: str = "empty",
    visibility: str = "private",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    publisher = publisher_context()
    if subnet_ref != publisher["publisher_ref"]:
        raise ValueError("Builder subnet does not match the local publisher identity")
    intent = {
        "title": str(title),
        "summary": str(summary),
        "template": str(template),
        "visibility": str(visibility),
        "publisher_key_fingerprint": publisher["release_key_fingerprint"],
    }

    def execute() -> Mapping[str, Any]:
        from adaos.sdk.developer import compositions

        service = _application_service()
        try:
            existing = service.store.get_application(application_id)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if existing.revision != expected_revision or expected_revision == 0:
                raise ValueError("Application already exists with another revision")
            return {"ok": True, "duplicate": True, "application": existing.to_dict()}
        if expected_revision != 0:
            raise ValueError("new Application expected_revision must be zero")
        try:
            project = compositions.get(application_id)
            component_ref = f"scenario:{application_id}"
            if component_ref not in {
                str(item.get("ref") or "") for item in project["components"]["owned"]
            }:
                raise ValueError("existing Project does not own the Application scenario")
            composition = {"ok": True, "project": project, "created_component": False}
        except compositions.ProjectCompositionNotFound:
            composition = compositions.create_with_primary_component(
                application_id,
                kind="scenario",
                component_id=application_id,
                template=template,
                title=title,
                description=summary,
                entrypoints=(
                    {
                        "id": "main",
                        "presentation": f"scenario:{application_id}",
                        "default": True,
                        "bindings": {},
                    },
                ),
                compatibility={"required_entrypoints": ["main"]},
                actor=actor_ref,
            )
        application = Application(
            application_id=application_id,
            legacy_project_id=application_id,
            publisher_ref=subnet_ref,
            slug=application_id,
            display={"title": title, "summary": summary},
            visibility=visibility,  # type: ignore[arg-type]
            entrypoints=(
                {"entrypoint_id": "main", "presentation_ref": f"scenario:{application_id}"},
            ),
            publisher={
                key: publisher[key]
                for key in (
                    "publisher_ref",
                    "display_name",
                    "subnet_short_ref",
                    "release_key_ref",
                    "release_key_fingerprint",
                    "home_zone",
                    "trust_relation",
                )
            },
        )
        saved = service.register(application, expected_revision=0)
        return {"ok": True, "application": saved.to_dict(), "composition": composition}

    return _coordinator().execute(
        "create",
        application_id,
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability=capability,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        intent=intent,
        callback=execute,
    )


def materialize_application(
    application_id: str,
    *,
    revision: str,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"revision": revision, "source_webspace_id": source_webspace_id}

    def execute() -> Mapping[str, Any]:
        from . import preview

        application = _application(application_id, expected_revision)
        scenario_id = _primary_scenario(application)
        ready = preview.ensure(
            source_webspace_id,
            active_draft_id=scenario_id,
            runtime_scenario_id=scenario_id,
            wait_for_rebuild=True,
        )
        result = preview.materialize_revision(
            webspace_id=preview.dev_webspace_id(source_webspace_id),
            scenario_id=scenario_id,
            revision=str(revision or "").strip() or None,
            preview_stage="prototype",
            preview_label=f"proto: {scenario_id}",
        )
        return {"ok": bool(result.get("ok", True)), "preview": ready, "materialization": result}

    return _coordinator().execute(
        "materialize", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def preview_development(
    application_id: str,
    *,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"source_webspace_id": source_webspace_id, "stage": "development"}

    def execute() -> Mapping[str, Any]:
        from . import preview

        application = _application(application_id, expected_revision)
        return preview.select_project(
            "scenario",
            _primary_scenario(application),
            source_webspace_id=source_webspace_id,
            ensure_ready=True,
            wait_for_rebuild=True,
        )

    return _coordinator().execute(
        "preview", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def create_trial(
    application_id: str,
    *,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"source_webspace_id": source_webspace_id}

    def execute() -> Mapping[str, Any]:
        from . import lifecycle

        application = _application(application_id, expected_revision)
        return lifecycle.prepare_trial(
            "scenario",
            _primary_scenario(application),
            actor=actor_ref,
            idempotency_key=idempotency_key,
            source_webspace_id=source_webspace_id,
            publication_project_ref=f"project:{application.legacy_project_id}",
        )

    return _coordinator().execute(
        "create_trial", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def decide_trial(
    application_id: str,
    *,
    accepted: bool,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"accepted": bool(accepted)}

    def execute() -> Mapping[str, Any]:
        from . import lifecycle

        application = _application(application_id, expected_revision)
        return lifecycle.decide_trial(
            "scenario", _primary_scenario(application), accepted=accepted,
            actor=actor_ref, idempotency_key=idempotency_key,
        )

    return _coordinator().execute(
        "decide_trial", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def _publish_trial(
    application_id: str,
    candidate_id: str,
    *,
    mode: str,
    expected_prerelease_digest: str | None,
    addresses_report_ids: Sequence[str],
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    action = "publish_prerelease" if mode == "prerelease" else "publish_trial"
    bounded_reports = tuple(sorted({str(item).strip() for item in addresses_report_ids if str(item).strip()}))
    if len(bounded_reports) > 200:
        raise ValueError("addresses_report_ids exceeds 200 items")
    intent = {
        "candidate_id": candidate_id,
        "mode": mode,
        "expected_prerelease_digest": expected_prerelease_digest,
        "addresses_report_ids": list(bounded_reports),
    }

    def execute() -> Mapping[str, Any]:
        application = _application(application_id, expected_revision)
        if application.publisher_ref != subnet_ref:
            raise ValueError("only the local Application publisher may publish a Trial")
        return get_application_distribution_service().publish_trial(
            application_id,
            candidate_id,
            publisher_ref=subnet_ref,
            mode=mode,
            expected_prerelease_digest=expected_prerelease_digest,
            addresses_report_ids=bounded_reports,
        )

    return _coordinator().execute(
        action, application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def publish_link_trial(
    application_id: str,
    candidate_id: str,
    *,
    addresses_report_ids: Sequence[str] = (),
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    return _publish_trial(
        application_id, candidate_id, mode="link_only", expected_prerelease_digest=None,
        addresses_report_ids=addresses_report_ids, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
    )


def publish_prerelease(
    application_id: str,
    candidate_id: str,
    *,
    expected_prerelease_digest: str | None,
    addresses_report_ids: Sequence[str] = (),
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    return _publish_trial(
        application_id, candidate_id, mode="prerelease",
        expected_prerelease_digest=expected_prerelease_digest,
        addresses_report_ids=addresses_report_ids, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
    )


def promote_stable(
    application_id: str,
    candidate_id: str,
    *,
    expected_stable_digest: str | None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"candidate_id": candidate_id, "expected_stable_digest": expected_stable_digest}

    def execute() -> Mapping[str, Any]:
        application = _application(application_id, expected_revision)
        if application.publisher_ref != subnet_ref:
            raise ValueError("only the local Application publisher may promote stable")
        return get_application_distribution_service().promote_stable(
            application_id,
            candidate_id,
            publisher_ref=subnet_ref,
            expected_stable_digest=expected_stable_digest,
        )

    return _coordinator().execute(
        "promote_stable", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def publish_stable_source(
    application_id: str,
    release_digest: str,
    *,
    release_notes: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"release_digest": release_digest, "release_notes": str(release_notes)[:20_000]}

    def execute() -> Mapping[str, Any]:
        _application(application_id, expected_revision)
        return StableSourceProjectionService(
            _application_service(), publisher=get_stable_source_publisher()
        ).publish(
            application_id,
            release_digest,
            publisher_ref=subnet_ref,
            release_notes=release_notes,
        )

    return _coordinator().execute(
        "publish_stable_source", application_id, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
        intent=intent, callback=execute,
    )


def get_development_operation(operation_id: str) -> dict[str, Any]:
    return _coordinator().get(operation_id)


def list_development_operations(application_id: str | None = None) -> list[dict[str, Any]]:
    return _coordinator().list(application_id)


__all__ = [
    "create_application",
    "create_trial",
    "decide_trial",
    "get_development_operation",
    "list_development_operations",
    "materialize_application",
    "preview_development",
    "promote_stable",
    "publish_link_trial",
    "publish_prerelease",
    "publish_stable_source",
    "publisher_context",
]
