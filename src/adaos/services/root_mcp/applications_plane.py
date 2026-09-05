from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from .model import ROOT_MCP_RESPONSE_SCHEMA, RootMcpSurface, RootMcpToolContract, schema_object


def _sdk():
    from adaos.sdk import applications

    return applications


def _builder_sdk():
    from adaos.sdk.builder import applications

    return applications


def _builder_contracts() -> list[RootMcpToolContract]:
    response = deepcopy(ROOT_MCP_RESPONSE_SCHEMA)
    metadata = {
        "published_by": "plane:applications",
        "adapter": "adaos.sdk.builder.applications",
    }
    application_id = {"application_id": {"type": "string", "minLength": 1, "maxLength": 128}}
    mutation = {
        **application_id,
        "expected_revision": {"type": "integer", "minimum": 0},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    mutation_required = ["application_id", "expected_revision", "idempotency_key"]
    source_webspace = {"type": "string", "minLength": 1, "maxLength": 128}
    candidate_id = {"type": "string", "minLength": 1, "maxLength": 180}
    digest_or_null = {
        "type": ["string", "null"], "pattern": "^sha256:[0-9a-f]{64}$",
    }
    reports = {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 128},
        "maxItems": 200,
        "uniqueItems": True,
    }
    return [
        RootMcpToolContract(
            id="applications.development.list_operations",
            title="List Application development operations",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="List durable bounded Builder lifecycle operations for one Application.",
            input_schema=schema_object(properties={**application_id}),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            metadata={**metadata, "handler": "applications_development_list_operations"},
        ),
        RootMcpToolContract(
            id="applications.development.get_operation",
            title="Get Application development operation",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Read one durable Builder lifecycle operation and recovery state.",
            input_schema=schema_object(
                properties={"operation_id": {"type": "string", "minLength": 1, "maxLength": 180}},
                required=["operation_id"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            metadata={**metadata, "handler": "applications_development_get_operation"},
        ),
        RootMcpToolContract(
            id="applications.development.create",
            title="Create Application through Builder",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Create an Application definition and bounded primary composition from a template.",
            input_schema=schema_object(
                properties={
                    **mutation,
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "template": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_.-]{0,63}$"},
                    "visibility": {"enum": ["private", "public"]},
                },
                required=[*mutation_required, "title", "summary"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_create"},
        ),
        RootMcpToolContract(
            id="applications.development.materialize",
            title="Materialize Application DEV revision",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Materialize one named revision into the isolated DEV runtime.",
            input_schema=schema_object(
                properties={
                    **mutation,
                    "revision": {"type": "string", "minLength": 1, "maxLength": 180},
                    "source_webspace_id": source_webspace,
                },
                required=[*mutation_required, "revision"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_materialize"},
        ),
        RootMcpToolContract(
            id="applications.development.preview",
            title="Open Application DEV Preview",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Select the Application in the isolated DEV Preview without Workspace mutation.",
            input_schema=schema_object(
                properties={**mutation, "source_webspace_id": source_webspace},
                required=mutation_required,
            ),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_preview"},
        ),
        RootMcpToolContract(
            id="applications.development.create_trial",
            title="Create Application Trial",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Create one immutable Candidate and isolated local Trial from reviewed DEV source.",
            input_schema=schema_object(
                properties={**mutation, "source_webspace_id": source_webspace},
                required=mutation_required,
            ),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_create_trial"},
        ),
        RootMcpToolContract(
            id="applications.development.decide_trial",
            title="Decide Application Trial",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Record the human acceptance or rejection of the exact active Trial.",
            input_schema=schema_object(
                properties={**mutation, "accepted": {"type": "boolean"}},
                required=[*mutation_required, "accepted"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.develop",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_decide_trial"},
        ),
        RootMcpToolContract(
            id="applications.development.publish_link_trial",
            title="Publish link-only Application Trial",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Publish an accepted Candidate as an exact access-controlled link-only Trial.",
            input_schema=schema_object(
                properties={**mutation, "candidate_id": candidate_id, "addresses_report_ids": reports},
                required=[*mutation_required, "candidate_id"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.publish",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_publish_link_trial"},
        ),
        RootMcpToolContract(
            id="applications.development.publish_prerelease",
            title="Publish Application prerelease",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Move the canonical prerelease channel to one accepted immutable Candidate.",
            input_schema=schema_object(
                properties={
                    **mutation,
                    "candidate_id": candidate_id,
                    "expected_prerelease_digest": digest_or_null,
                    "addresses_report_ids": reports,
                },
                required=[*mutation_required, "candidate_id", "expected_prerelease_digest"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.publish",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_publish_prerelease"},
        ),
        RootMcpToolContract(
            id="applications.development.promote_stable",
            title="Promote Application stable",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Promote the exact accepted Trial or prerelease digest to stable.",
            input_schema=schema_object(
                properties={
                    **mutation,
                    "candidate_id": candidate_id,
                    "expected_stable_digest": digest_or_null,
                },
                required=[*mutation_required, "candidate_id", "expected_stable_digest"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.publish",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_promote_stable"},
        ),
        RootMcpToolContract(
            id="applications.development.publish_stable_source",
            title="Publish stable Application source",
            surface=RootMcpSurface.DEVELOPMENT,
            summary="Project the exact public stable source closure to the configured Git destination.",
            input_schema=schema_object(
                properties={
                    **mutation,
                    "release_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "release_notes": {"type": "string", "maxLength": 20000},
                },
                required=[*mutation_required, "release_digest", "release_notes"],
            ),
            output_schema=deepcopy(response),
            required_capability="applications.publish",
            side_effects="write",
            metadata={**metadata, "handler": "applications_development_publish_stable_source"},
        ),
    ]


def contracts() -> list[RootMcpToolContract]:
    def response() -> dict[str, Any]:
        return deepcopy(ROOT_MCP_RESPONSE_SCHEMA)

    published = {"published_by": "plane:applications", "adapter": "adaos.sdk.applications"}
    identity = {
        "application_id": {"type": "string"},
        "expected_revision": {"type": "integer", "minimum": 0},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    evidence_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "maxLength": 80},
            "mime_type": {"type": "string", "maxLength": 120},
            "size_bytes": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
            "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "artifact_ref": {"type": "string", "minLength": 1, "maxLength": 300},
            "url": {"type": "string", "maxLength": 2000},
            "archive": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "expanded_size_bytes": {
                        "type": "integer", "minimum": 0, "maximum": 50_000_000,
                    },
                    "entries": {
                        "type": "array", "items": {"type": "string", "maxLength": 500},
                        "maxItems": 1000,
                    },
                },
                "required": ["expanded_size_bytes", "entries"],
            },
        },
        "required": ["mime_type", "size_bytes", "digest", "artifact_ref"],
    }
    return [
        RootMcpToolContract(
            id="applications.list",
            title="List Applications",
            surface=RootMcpSurface.OPERATIONS,
            summary="List bounded installed and Catalog Application models.",
            input_schema=schema_object(properties={"installed_only": {"type": "boolean"}}),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list"},
        ),
        RootMcpToolContract(
            id="applications.show",
            title="Show Application",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read one Application with exact installation, channel, and operation state.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}, required=["application_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_show"},
        ),
        RootMcpToolContract(
            id="applications.list_releases",
            title="List Application releases",
            surface=RootMcpSurface.OPERATIONS,
            summary="List immutable releases and effective channel bindings for one Application.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}, required=["application_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list_releases"},
        ),
        RootMcpToolContract(
            id="applications.list_operations",
            title="List Application operations",
            surface=RootMcpSurface.OPERATIONS,
            summary="Poll durable Application operations after reconnect.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list_operations"},
        ),
        RootMcpToolContract(
            id="applications.poll_operation_events",
            title="Poll Application operation events",
            surface=RootMcpSurface.OPERATIONS,
            summary="Resume the durable Application operation event stream from an opaque cursor.",
            input_schema=schema_object(
                properties={
                    "application_id": {"type": ["string", "null"]},
                    "cursor": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                }
            ),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_poll_operation_events"},
        ),
        RootMcpToolContract(
            id="applications.get_operation",
            title="Get Application operation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read one durable operation and structured recovery reason.",
            input_schema=schema_object(properties={"operation_id": {"type": "string"}}, required=["operation_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_get_operation"},
        ),
        RootMcpToolContract(
            id="applications.list_trial_access",
            title="List Trial access grants",
            surface=RootMcpSurface.OPERATIONS,
            summary="List bounded Trial grant metadata without capability bearer tokens.",
            input_schema=schema_object(properties={"application_id": {"type": "string"}}),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_list_trial_access"},
        ),
        RootMcpToolContract(
            id="applications.get_prerelease_rollout",
            title="Get prerelease rollout",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read the one current prerelease staged-rollout policy and aggregate health.",
            input_schema=schema_object(
                properties={"application_id": {"type": "string"}}, required=["application_id"]
            ),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_get_prerelease_rollout"},
        ),
        RootMcpToolContract(
            id="applications.list_development_reports",
            title="List Development Reports",
            surface=RootMcpSurface.OPERATIONS,
            summary="List bounded local Development Reports and their public state.",
            input_schema=schema_object(),
            output_schema=response(),
            required_capability="applications.report",
            metadata={**published, "handler": "applications_list_development_reports"},
        ),
        RootMcpToolContract(
            id="applications.get_development_report_status",
            title="Get Development Report status",
            surface=RootMcpSurface.OPERATIONS,
            summary="Read the public publisher status of one local Development Report.",
            input_schema=schema_object(
                properties={"report_id": {"type": "string"}}, required=["report_id"]
            ),
            output_schema=response(),
            required_capability="applications.report",
            metadata={**published, "handler": "applications_get_development_report_status"},
        ),
        RootMcpToolContract(
            id="applications.list_development_report_intakes",
            title="List publisher Development Report intakes",
            surface=RootMcpSurface.OPERATIONS,
            summary="List publisher-local quarantined and accepted report intake metadata.",
            input_schema=schema_object(),
            output_schema=response(),
            required_capability="applications.publisher.read",
            metadata={**published, "handler": "applications_list_development_report_intakes"},
        ),
        RootMcpToolContract(
            id="applications.list_development_report_appeals",
            title="List Development Report appeals",
            surface=RootMcpSurface.OPERATIONS,
            summary="List appeal state for reports created by the local subnet.",
            input_schema=schema_object(properties={"report_id": {"type": ["string", "null"]}}),
            output_schema=response(),
            required_capability="applications.report",
            metadata={**published, "handler": "applications_list_development_report_appeals"},
        ),
        RootMcpToolContract(
            id="applications.list_publisher_development_report_appeals",
            title="List publisher Development Report appeals",
            surface=RootMcpSurface.OPERATIONS,
            summary="List encrypted appeals received for publisher-local report intakes.",
            input_schema=schema_object(properties={"report_id": {"type": ["string", "null"]}}),
            output_schema=response(),
            required_capability="applications.publisher.read",
            metadata={**published, "handler": "applications_list_publisher_development_report_appeals"},
        ),
        RootMcpToolContract(
            id="applications.get_development_report_triage",
            title="Explain Development Report triage",
            surface=RootMcpSurface.OPERATIONS,
            summary="Return publisher-local duplicate evidence, factual reporter history, and privacy policy.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "threshold": {"type": "number", "minimum": 0.5, "maximum": 1.0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                },
                required=["report_id"],
            ),
            output_schema=response(),
            required_capability="applications.publisher.read",
            metadata={**published, "handler": "applications_get_development_report_triage"},
        ),
        RootMcpToolContract(
            id="applications.submit_development_report",
            title="Submit Development Report",
            surface=RootMcpSurface.OPERATIONS,
            summary="Validate, encrypt, and durably queue a report for the Application publisher.",
            input_schema=schema_object(
                properties={
                    "application_id": {"type": "string"},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                    "details": {"type": "string", "minLength": 1, "maxLength": 16000},
                    "evidence": {"type": "array", "items": evidence_item, "maxItems": 20},
                    "installed_release_digest": {"type": ["string", "null"], "pattern": "^sha256:[0-9a-f]{64}$"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["application_id", "summary", "details", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.report",
            side_effects="write",
            metadata={**published, "handler": "applications_submit_development_report"},
        ),
        RootMcpToolContract(
            id="applications.sync_development_reports",
            title="Synchronize Development Reports",
            surface=RootMcpSurface.OPERATIONS,
            summary="Consume bounded encrypted inbox work and retry the durable report outbox.",
            input_schema=schema_object(
                properties={
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.report",
            side_effects="write",
            metadata={**published, "handler": "applications_sync_development_reports"},
        ),
        RootMcpToolContract(
            id="applications.triage_development_report",
            title="Triage Development Report",
            surface=RootMcpSurface.OPERATIONS,
            summary="Record an explicit publisher decision without automatically creating a Dev Ticket.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "outcome": {"enum": ["triaged", "declined", "duplicate"]},
                    "reason_code": {"type": ["string", "null"]},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["report_id", "outcome", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.publisher.triage",
            side_effects="write",
            metadata={**published, "handler": "applications_triage_development_report"},
        ),
        RootMcpToolContract(
            id="applications.accept_development_report",
            title="Accept Development Report",
            surface=RootMcpSurface.OPERATIONS,
            summary="Accept a quarantined report and create its publisher-local Dev Ticket exactly once.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "policy_ref": {"type": ["string", "null"]},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["report_id", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.publisher.triage",
            side_effects="write",
            metadata={**published, "handler": "applications_accept_development_report"},
        ),
        RootMcpToolContract(
            id="applications.set_development_report_status",
            title="Set Development Report work status",
            surface=RootMcpSurface.OPERATIONS,
            summary="Publish a validated work or exact addressed-release status to the reporter.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "status": {"enum": ["planned", "prerelease_available", "released"]},
                    "reason_code": {"type": ["string", "null"]},
                    "release_digest": {"type": ["string", "null"], "pattern": "^sha256:[0-9a-f]{64}$"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["report_id", "status", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.publisher.triage",
            side_effects="write",
            metadata={**published, "handler": "applications_set_development_report_status"},
        ),
        RootMcpToolContract(
            id="applications.submit_development_report_appeal",
            title="Appeal Development Report decision",
            surface=RootMcpSurface.OPERATIONS,
            summary="Redact, encrypt, and submit an appeal of a declined or duplicate report.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["report_id", "statement", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.report",
            side_effects="write",
            metadata={**published, "handler": "applications_submit_development_report_appeal"},
        ),
        RootMcpToolContract(
            id="applications.resolve_development_report_appeal",
            title="Resolve Development Report appeal",
            surface=RootMcpSurface.OPERATIONS,
            summary="Return a visible encrypted resolution and optionally reopen publisher triage.",
            input_schema=schema_object(
                properties={
                    "appeal_id": {"type": "string"},
                    "resolution": {"enum": ["reopened", "corrected", "upheld"]},
                    "rationale": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["appeal_id", "resolution", "rationale", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.publisher.triage",
            side_effects="write",
            metadata={**published, "handler": "applications_resolve_development_report_appeal"},
        ),
        RootMcpToolContract(
            id="applications.verify_development_report_release",
            title="Verify Development Report release",
            surface=RootMcpSurface.OPERATIONS,
            summary="Verify whether the exact installed addressed release resolves the report.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "outcome": {"enum": ["verified", "still_reproduces"]},
                    "release_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["report_id", "outcome", "release_digest", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.report",
            side_effects="write",
            metadata={**published, "handler": "applications_verify_development_report_release"},
        ),
        RootMcpToolContract(
            id="applications.request_development_report_resync",
            title="Request Development Report resync",
            surface=RootMcpSurface.OPERATIONS,
            summary="Request a bounded replay of signed report status events after a local cursor.",
            input_schema=schema_object(
                properties={
                    "report_id": {"type": "string"},
                    "after_revision": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
                },
                required=["report_id", "after_revision", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.report",
            side_effects="write",
            metadata={**published, "handler": "applications_request_development_report_resync"},
        ),
        RootMcpToolContract(
            id="applications.plan",
            title="Plan Application mutation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Persist a reviewable install, update, remove, or update-track plan without applying it.",
            input_schema=schema_object(
                properties={
                    **identity,
                    "kind": {"enum": ["install", "update", "remove", "select_track"]},
                    "release_digest": {"type": ["string", "null"]},
                    "data_policy": {"enum": ["retain", "delete", "snapshot_then_delete"]},
                    "update_track": {"enum": ["stable", "prerelease"]},
                    "update_policy": {"enum": ["notify", "auto_compatible", "pinned"]},
                    "paused": {"type": "boolean"},
                    "pinned_release_digest": {"type": ["string", "null"]},
                    "access_redemption_id": {"type": ["string", "null"]},
                },
                required=["application_id", "kind", "expected_revision", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.plan",
            side_effects="write",
            metadata={**published, "handler": "applications_plan"},
        ),
        RootMcpToolContract(
            id="applications.apply",
            title="Apply reviewed Application plan",
            surface=RootMcpSurface.OPERATIONS,
            summary="Apply one exact reviewed plan and return its durable operation receipt.",
            input_schema=schema_object(
                properties={
                    "operation_id": {"type": "string"},
                    "plan_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=["operation_id", "plan_digest", "idempotency_key"],
            ),
            output_schema=response(),
            required_capability="applications.apply",
            side_effects="write",
            metadata={**published, "handler": "applications_apply"},
        ),
        RootMcpToolContract(
            id="applications.explain_plan",
            title="Explain Application plan",
            surface=RootMcpSurface.OPERATIONS,
            summary="Explain exact release, compatibility, snapshot, removal, and conflict decisions.",
            input_schema=schema_object(properties={"operation_id": {"type": "string"}}, required=["operation_id"]),
            output_schema=response(),
            required_capability="applications.read",
            metadata={**published, "handler": "applications_explain_plan"},
        ),
        RootMcpToolContract(
            id="applications.issue_trial_access",
            title="Issue Trial access",
            surface=RootMcpSurface.OPERATIONS,
            summary="Issue one targeted, expiring, bounded Trial capability link.",
            input_schema=schema_object(
                properties={
                    "application_id": {"type": "string"},
                    "recipient_subnet_ref": {"type": "string", "pattern": "^subnet:"},
                    "recipient_key_ref": {"type": "string"},
                    "scope": {"enum": ["exact_release", "follow_prerelease"]},
                    "release_digest": {"type": ["string", "null"]},
                    "expires_at": {"type": "string", "format": "date-time"},
                    "allowed_zones": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 16},
                    "max_uses": {"type": "integer", "minimum": 1, "maximum": 100},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=[
                    "application_id", "recipient_subnet_ref", "recipient_key_ref", "scope",
                    "expires_at", "allowed_zones", "idempotency_key",
                ],
            ),
            output_schema=response(),
            required_capability="applications.apply",
            side_effects="write",
            metadata={**published, "handler": "applications_issue_trial_access", "sensitive_output_paths": ["result.link"]},
        ),
        RootMcpToolContract(
            id="applications.revoke_trial_access",
            title="Revoke Trial access",
            surface=RootMcpSurface.OPERATIONS,
            summary="Revoke one Trial capability using optimistic revision control.",
            input_schema=schema_object(
                properties={
                    "grant_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                },
                required=["grant_id", "expected_revision"],
            ),
            output_schema=response(),
            required_capability="applications.apply",
            side_effects="write",
            metadata={**published, "handler": "applications_revoke_trial_access"},
        ),
        RootMcpToolContract(
            id="applications.resolve_trial_link",
            title="Resolve Trial link",
            surface=RootMcpSurface.OPERATIONS,
            summary="Redeem a capability link for the authenticated subnet, key, and zone.",
            input_schema=schema_object(
                properties={
                    "link": {"type": "string", "pattern": "^adaos://applications/trial/"},
                    "recipient_key_ref": {"type": "string"},
                    "redemption_id": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=["link", "recipient_key_ref", "redemption_id"],
            ),
            output_schema=response(),
            required_capability="applications.trial.redeem",
            side_effects="write",
            metadata={**published, "handler": "applications_resolve_trial_link", "sensitive_input_paths": ["link"]},
        ),
        RootMcpToolContract(
            id="applications.plan_trial_link_install",
            title="Plan Trial link installation",
            surface=RootMcpSurface.OPERATIONS,
            summary="Redeem a Trial link and persist an exact reviewed installation plan.",
            input_schema=schema_object(
                properties={
                    "link": {"type": "string", "pattern": "^adaos://applications/trial/"},
                    "recipient_key_ref": {"type": "string"},
                    "redemption_id": {"type": "string", "minLength": 1, "maxLength": 240},
                    "expected_revision": {"type": "integer", "minimum": 0},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
                    "data_policy": {"enum": ["retain", "delete", "snapshot_then_delete"]},
                },
                required=[
                    "link", "recipient_key_ref", "redemption_id",
                    "expected_revision", "idempotency_key",
                ],
            ),
            output_schema=response(),
            required_capability="applications.trial.install",
            side_effects="write",
            metadata={
                **published,
                "handler": "applications_plan_trial_link_install",
                "sensitive_input_paths": ["link"],
            },
        ),
        RootMcpToolContract(
            id="applications.set_prerelease_rollout",
            title="Set prerelease rollout",
            surface=RootMcpSurface.OPERATIONS,
            summary="Set, pause, or explicitly resume one sticky prerelease rollout.",
            input_schema=schema_object(
                properties={
                    "application_id": {"type": "string"},
                    "release_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "percentage": {"type": "integer", "minimum": 0, "maximum": 100},
                    "paused": {"type": "boolean"},
                    "minimum_health_subnets": {"type": "integer", "minimum": 1, "maximum": 10000},
                    "failure_threshold": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    "expected_revision": {"type": "integer", "minimum": 0},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
                    "resume_after_halt": {"type": "boolean"},
                },
                required=[
                    "application_id", "release_digest", "percentage", "paused",
                    "minimum_health_subnets", "failure_threshold", "expected_revision",
                    "idempotency_key",
                ],
            ),
            output_schema=response(),
            required_capability="applications.publish",
            side_effects="write",
            metadata={**published, "handler": "applications_set_prerelease_rollout"},
        ),
        RootMcpToolContract(
            id="applications.record_prerelease_health",
            title="Record prerelease health",
            surface=RootMcpSurface.OPERATIONS,
            summary="Record one evidence-bound subscriber health outcome for rollout halt policy.",
            input_schema=schema_object(
                properties={
                    "application_id": {"type": "string"},
                    "release_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "outcome": {"enum": ["healthy", "failed"]},
                    "installation_revision": {"type": "integer", "minimum": 1},
                    "evidence_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "observed_at": {"type": "string", "format": "date-time"},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
                },
                required=[
                    "application_id", "release_digest", "outcome",
                    "installation_revision", "evidence_digest", "observed_at",
                    "idempotency_key",
                ],
            ),
            output_schema=response(),
            required_capability="applications.apply",
            side_effects="write",
            metadata={**published, "handler": "applications_record_prerelease_health"},
        ),
        *_builder_contracts(),
    ]


def _context(arguments: Mapping[str, Any]) -> tuple[str, str]:
    raw = arguments.get("_mcp_context")
    context = dict(raw) if isinstance(raw, Mapping) else {}
    scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
    auth = context.get("auth_context") if isinstance(context.get("auth_context"), Mapping) else {}
    actor = str(context.get("actor") or auth.get("actor") or "").strip()
    subnet_id = str(scope.get("subnet_id") or auth.get("subnet_id") or "").strip()
    if not actor:
        raise ValueError("MCP actor context is required")
    if not subnet_id:
        raise ValueError("MCP subnet context is required")
    subnet_ref = subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"
    return actor, subnet_ref


def _application_id(arguments: Mapping[str, Any]) -> str:
    value = str(arguments.get("application_id") or "").strip()
    if not value:
        raise ValueError("application_id is required")
    return value


def _mcp_mutation_context(
    arguments: Mapping[str, Any], capability: str
) -> dict[str, str]:
    actor_ref, subnet_ref = _context(arguments)
    idempotency_key = str(arguments.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    return {
        "actor_ref": actor_ref,
        "subnet_ref": subnet_ref,
        "capability": capability,
        "idempotency_key": idempotency_key,
    }


def _handle_list(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"applications": _sdk().list_applications(installed_only=bool(arguments.get("installed_only", False)))}


def _handle_show(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"application": _sdk().get_application(_application_id(arguments))}


def _handle_releases(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"releases": _sdk().list_releases(_application_id(arguments))}


def _handle_operations(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    application_id = str(arguments.get("application_id") or "").strip() or None
    return {"operations": _sdk().list_operations(application_id)}


def _handle_operation_events(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return _sdk().poll_operation_events(
        application_id=str(arguments.get("application_id") or "").strip() or None,
        cursor=str(arguments.get("cursor") or "").strip() or None,
        limit=int(arguments.get("limit") or 50),
    )


def _handle_get_operation(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    operation_id = str(arguments.get("operation_id") or "").strip()
    if not operation_id:
        raise ValueError("operation_id is required")
    return {"operation": _sdk().get_operation(operation_id)}


def _handle_list_trial_access(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    application_id = str(arguments.get("application_id") or "").strip() or None
    return {"grants": _sdk().list_trial_access(application_id)}


def _handle_get_prerelease_rollout(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"rollout": _sdk().get_prerelease_rollout(_application_id(arguments))}


def _handle_list_development_reports(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"reports": _sdk().list_development_reports()}


def _handle_development_report_status(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    report_id = str(arguments.get("report_id") or "").strip()
    if not report_id:
        raise ValueError("report_id is required")
    return {
        "report": _sdk().get_development_report(report_id),
        "status": _sdk().get_development_report_status(report_id),
    }


def _handle_development_report_intakes(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {"intakes": _sdk().list_development_report_intakes()}


def _handle_development_report_appeals(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    report_id = str(arguments.get("report_id") or "").strip() or None
    return {"appeals": _sdk().list_development_report_appeals(report_id)}


def _handle_publisher_development_report_appeals(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    report_id = str(arguments.get("report_id") or "").strip() or None
    return {"appeals": _sdk().list_publisher_development_report_appeals(report_id)}


def _handle_development_report_triage(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    report_id = str(arguments.get("report_id") or "").strip()
    if not report_id:
        raise ValueError("report_id is required")
    return _sdk().get_development_report_triage(
        report_id,
        threshold=float(arguments.get("threshold", 0.65)),
        limit=int(arguments.get("limit", 10)),
    )


def _handle_submit_development_report(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_submit_report": True, "request": request}
    return _sdk().submit_development_report(
        _application_id(arguments),
        summary=str(arguments.get("summary") or ""),
        details=str(arguments.get("details") or ""),
        evidence=tuple(arguments.get("evidence") or ()),
        installed_release_digest=(
            str(arguments.get("installed_release_digest") or "").strip() or None
        ),
        **_mcp_mutation_context(arguments, "applications.report"),
    )


def _handle_sync_development_reports(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_sync_reports": True, "limit": int(arguments.get("limit", 20))}
    return _sdk().sync_development_reports(
        limit=int(arguments.get("limit", 20)),
        **_mcp_mutation_context(arguments, "applications.report"),
    )


def _handle_triage_development_report(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_triage_report": True, "request": request}
    return _sdk().triage_development_report(
        str(arguments.get("report_id") or ""),
        outcome=str(arguments.get("outcome") or ""),
        reason_code=str(arguments.get("reason_code") or "").strip() or None,
        **_mcp_mutation_context(arguments, "applications.publisher.triage"),
    )


def _handle_accept_development_report(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_accept_report": True, "request": request}
    return _sdk().accept_development_report(
        str(arguments.get("report_id") or ""),
        policy_ref=str(arguments.get("policy_ref") or "").strip() or None,
        **_mcp_mutation_context(arguments, "applications.publisher.triage"),
    )


def _handle_set_development_report_status(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_set_report_status": True, "request": request}
    return _sdk().set_development_report_status(
        str(arguments.get("report_id") or ""),
        status=str(arguments.get("status") or ""),
        reason_code=str(arguments.get("reason_code") or "").strip() or None,
        release_digest=str(arguments.get("release_digest") or "").strip() or None,
        **_mcp_mutation_context(arguments, "applications.publisher.triage"),
    )


def _handle_submit_development_report_appeal(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_submit_appeal": True, "request": request}
    return _sdk().submit_development_report_appeal(
        str(arguments.get("report_id") or ""),
        statement=str(arguments.get("statement") or ""),
        **_mcp_mutation_context(arguments, "applications.report"),
    )


def _handle_resolve_development_report_appeal(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_resolve_appeal": True, "request": request}
    return _sdk().resolve_development_report_appeal(
        str(arguments.get("appeal_id") or ""),
        resolution=str(arguments.get("resolution") or ""),
        rationale=str(arguments.get("rationale") or ""),
        **_mcp_mutation_context(arguments, "applications.publisher.triage"),
    )


def _handle_verify_development_report_release(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_verify_report_release": True, "request": request}
    return _sdk().verify_development_report_release(
        str(arguments.get("report_id") or ""),
        outcome=str(arguments.get("outcome") or ""),
        release_digest=str(arguments.get("release_digest") or ""),
        **_mcp_mutation_context(arguments, "applications.report"),
    )


def _handle_request_development_report_resync(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_request_report_resync": True, "request": request}
    return _sdk().request_development_report_resync(
        str(arguments.get("report_id") or ""),
        after_revision=int(arguments.get("after_revision", 0)),
        limit=int(arguments.get("limit", 100)),
        **_mcp_mutation_context(arguments, "applications.report"),
    )


def _builder_request(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in arguments.items() if key != "_mcp_context"}


def _handle_development_list_operations(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    application_id = str(arguments.get("application_id") or "").strip() or None
    return {"operations": _builder_sdk().list_development_operations(application_id)}


def _handle_development_get_operation(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    operation_id = str(arguments.get("operation_id") or "").strip()
    if not operation_id:
        raise ValueError("operation_id is required")
    return {"operation": _builder_sdk().get_development_operation(operation_id)}


def _handle_development_create(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_create_application": True, "request": _builder_request(arguments)}
    return _builder_sdk().create_application(
        _application_id(arguments),
        title=str(arguments.get("title") or ""),
        summary=str(arguments.get("summary") or ""),
        template=str(arguments.get("template") or "empty"),
        visibility=str(arguments.get("visibility") or "private"),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.develop"),
    )


def _handle_development_materialize(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_materialize_application": True, "request": _builder_request(arguments)}
    return _builder_sdk().materialize_application(
        _application_id(arguments),
        revision=str(arguments.get("revision") or ""),
        source_webspace_id=str(arguments.get("source_webspace_id") or "desktop"),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.develop"),
    )


def _handle_development_preview(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_preview_application": True, "request": _builder_request(arguments)}
    return _builder_sdk().preview_development(
        _application_id(arguments),
        source_webspace_id=str(arguments.get("source_webspace_id") or "desktop"),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.develop"),
    )


def _handle_development_create_trial(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_create_trial": True, "request": _builder_request(arguments)}
    return _builder_sdk().create_trial(
        _application_id(arguments),
        source_webspace_id=str(arguments.get("source_webspace_id") or "desktop"),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.develop"),
    )


def _handle_development_decide_trial(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_decide_trial": True, "request": _builder_request(arguments)}
    return _builder_sdk().decide_trial(
        _application_id(arguments),
        accepted=bool(arguments.get("accepted")),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.develop"),
    )


def _handle_development_publish_link_trial(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_publish_link_trial": True, "request": _builder_request(arguments)}
    return _builder_sdk().publish_link_trial(
        _application_id(arguments),
        str(arguments.get("candidate_id") or ""),
        addresses_report_ids=tuple(arguments.get("addresses_report_ids") or ()),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.publish"),
    )


def _handle_development_publish_prerelease(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_publish_prerelease": True, "request": _builder_request(arguments)}
    return _builder_sdk().publish_prerelease(
        _application_id(arguments),
        str(arguments.get("candidate_id") or ""),
        expected_prerelease_digest=(
            str(arguments.get("expected_prerelease_digest") or "").strip() or None
        ),
        addresses_report_ids=tuple(arguments.get("addresses_report_ids") or ()),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.publish"),
    )


def _handle_development_promote_stable(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_promote_stable": True, "request": _builder_request(arguments)}
    return _builder_sdk().promote_stable(
        _application_id(arguments),
        str(arguments.get("candidate_id") or ""),
        expected_stable_digest=(
            str(arguments.get("expected_stable_digest") or "").strip() or None
        ),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.publish"),
    )


def _handle_development_publish_stable_source(
    arguments: dict[str, Any], *, dry_run: bool
) -> dict[str, Any]:
    if dry_run:
        return {"would_publish_stable_source": True, "request": _builder_request(arguments)}
    return _builder_sdk().publish_stable_source(
        _application_id(arguments),
        str(arguments.get("release_digest") or ""),
        release_notes=str(arguments.get("release_notes") or ""),
        expected_revision=int(arguments.get("expected_revision") or 0),
        **_mcp_mutation_context(arguments, "applications.publish"),
    )


def _handle_plan(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_plan": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    application_id = _application_id(arguments)
    kind = str(arguments.get("kind") or "").strip()
    common = {
        "expected_revision": int(arguments.get("expected_revision") or 0),
        "actor_ref": actor_ref,
        "subnet_ref": subnet_ref,
        "capability": "applications.plan",
        "idempotency_key": str(arguments.get("idempotency_key") or "").strip(),
    }
    sdk = _sdk()
    if kind == "install":
            operation = sdk.plan_install(
            application_id,
            release_digest=str(arguments.get("release_digest") or "").strip() or None,
            data_policy=str(arguments.get("data_policy") or "retain"),
            access_redemption_id=str(arguments.get("access_redemption_id") or "").strip() or None,
            **common,
        )
    elif kind == "update":
        operation = sdk.plan_update(
            application_id,
            release_digest=str(arguments.get("release_digest") or "").strip() or None,
            access_redemption_id=str(arguments.get("access_redemption_id") or "").strip() or None,
            **common,
        )
    elif kind == "remove":
        operation = sdk.plan_remove(
            application_id,
            data_policy=str(arguments.get("data_policy") or "retain"),
            **common,
        )
    elif kind == "select_track":
        operation = sdk.plan_update_track(
            application_id,
            update_track=str(arguments.get("update_track") or "stable"),
            update_policy=str(arguments.get("update_policy") or "notify"),
            paused=bool(arguments.get("paused", False)),
            pinned_release_digest=str(arguments.get("pinned_release_digest") or "").strip() or None,
            **common,
        )
    else:
        raise ValueError("kind must be install, update, remove, or select_track")
    return {"operation": operation}


def _handle_apply(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_apply": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    return {
        "operation": _sdk().apply_operation(
            str(arguments.get("operation_id") or ""),
            plan_digest=str(arguments.get("plan_digest") or ""),
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability="applications.apply",
            idempotency_key=str(arguments.get("idempotency_key") or ""),
        )
    }


def _handle_explain(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    operation_id = str(arguments.get("operation_id") or "").strip()
    if not operation_id:
        raise ValueError("operation_id is required")
    return {"explanation": _sdk().explain_plan(operation_id)}


def _handle_issue_trial_access(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_issue": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    return {
        "trial_access": _sdk().issue_trial_access(
            _application_id(arguments),
            publisher_ref=subnet_ref,
            recipient_subnet_ref=str(arguments.get("recipient_subnet_ref") or ""),
            recipient_key_ref=str(arguments.get("recipient_key_ref") or ""),
            scope=str(arguments.get("scope") or ""),
            release_digest=str(arguments.get("release_digest") or "").strip() or None,
            expires_at=str(arguments.get("expires_at") or ""),
            allowed_zones=tuple(arguments.get("allowed_zones") or ()),
            max_uses=int(arguments.get("max_uses") or 1),
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability="applications.apply",
            idempotency_key=str(arguments.get("idempotency_key") or ""),
        )
    }


def _handle_revoke_trial_access(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_revoke": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    return {
        "grant": _sdk().revoke_trial_access(
            str(arguments.get("grant_id") or ""),
            publisher_ref=subnet_ref,
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability="applications.apply",
            expected_revision=int(arguments.get("expected_revision") or 0),
        )
    }


def _handle_resolve_trial_link(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key not in {"_mcp_context", "link"}}
    if dry_run:
        return {"would_redeem": True, "request": request}
    raw = arguments.get("_mcp_context")
    context = dict(raw) if isinstance(raw, Mapping) else {}
    scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
    actor_ref, subnet_ref = _context(arguments)
    zone = str(scope.get("zone") or "").strip()
    if not zone:
        raise ValueError("MCP zone context is required")
    return {
        "redemption": _sdk().resolve_trial_link(
            str(arguments.get("link") or ""),
            recipient_subnet_ref=subnet_ref,
            recipient_key_ref=str(arguments.get("recipient_key_ref") or ""),
            zone=zone,
            actor_ref=actor_ref,
            capability="applications.trial.redeem",
            redemption_id=str(arguments.get("redemption_id") or ""),
        )
    }


def _handle_plan_trial_link_install(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {
        key: value for key, value in arguments.items() if key not in {"_mcp_context", "link"}
    }
    if dry_run:
        return {"would_plan_trial_install": True, "request": request}
    raw = arguments.get("_mcp_context")
    context = dict(raw) if isinstance(raw, Mapping) else {}
    scope = context.get("scope") if isinstance(context.get("scope"), Mapping) else {}
    actor_ref, subnet_ref = _context(arguments)
    zone = str(scope.get("zone") or "").strip()
    if not zone:
        raise ValueError("MCP zone context is required")
    return _sdk().plan_trial_link_install(
        str(arguments.get("link") or ""),
        recipient_key_ref=str(arguments.get("recipient_key_ref") or ""),
        zone=zone,
        redemption_id=str(arguments.get("redemption_id") or ""),
        expected_revision=int(arguments.get("expected_revision") or 0),
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability="applications.trial.install",
        idempotency_key=str(arguments.get("idempotency_key") or ""),
        data_policy=str(arguments.get("data_policy") or "retain"),
    )


def _handle_set_prerelease_rollout(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_set_rollout": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    return {
        "rollout": _sdk().set_prerelease_rollout(
            _application_id(arguments),
            release_digest=str(arguments.get("release_digest") or ""),
            percentage=int(arguments.get("percentage") or 0),
            paused=bool(arguments.get("paused", False)),
            minimum_health_subnets=int(arguments.get("minimum_health_subnets") or 0),
            failure_threshold=float(arguments.get("failure_threshold") or 0.0),
            expected_revision=int(arguments.get("expected_revision") or 0),
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability="applications.publish",
            idempotency_key=str(arguments.get("idempotency_key") or ""),
            resume_after_halt=bool(arguments.get("resume_after_halt", False)),
        )
    }


def _handle_record_prerelease_health(arguments: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    request = {key: value for key, value in arguments.items() if key != "_mcp_context"}
    if dry_run:
        return {"would_record_health": True, "request": request}
    actor_ref, subnet_ref = _context(arguments)
    return _sdk().record_prerelease_health(
        _application_id(arguments),
        str(arguments.get("release_digest") or ""),
        outcome=str(arguments.get("outcome") or ""),
        installation_revision=int(arguments.get("installation_revision") or 0),
        evidence_digest=str(arguments.get("evidence_digest") or ""),
        observed_at=str(arguments.get("observed_at") or ""),
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability="applications.apply",
        idempotency_key=str(arguments.get("idempotency_key") or ""),
    )


def handlers() -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "applications.list": _handle_list,
        "applications.show": _handle_show,
        "applications.list_releases": _handle_releases,
        "applications.list_operations": _handle_operations,
        "applications.poll_operation_events": _handle_operation_events,
        "applications.get_operation": _handle_get_operation,
        "applications.list_trial_access": _handle_list_trial_access,
        "applications.get_prerelease_rollout": _handle_get_prerelease_rollout,
        "applications.list_development_reports": _handle_list_development_reports,
        "applications.get_development_report_status": _handle_development_report_status,
        "applications.list_development_report_intakes": _handle_development_report_intakes,
        "applications.list_development_report_appeals": _handle_development_report_appeals,
        "applications.list_publisher_development_report_appeals": _handle_publisher_development_report_appeals,
        "applications.get_development_report_triage": _handle_development_report_triage,
        "applications.submit_development_report": _handle_submit_development_report,
        "applications.sync_development_reports": _handle_sync_development_reports,
        "applications.triage_development_report": _handle_triage_development_report,
        "applications.accept_development_report": _handle_accept_development_report,
        "applications.set_development_report_status": _handle_set_development_report_status,
        "applications.submit_development_report_appeal": _handle_submit_development_report_appeal,
        "applications.resolve_development_report_appeal": _handle_resolve_development_report_appeal,
        "applications.verify_development_report_release": _handle_verify_development_report_release,
        "applications.request_development_report_resync": _handle_request_development_report_resync,
        "applications.plan": _handle_plan,
        "applications.apply": _handle_apply,
        "applications.explain_plan": _handle_explain,
        "applications.issue_trial_access": _handle_issue_trial_access,
        "applications.revoke_trial_access": _handle_revoke_trial_access,
        "applications.resolve_trial_link": _handle_resolve_trial_link,
        "applications.plan_trial_link_install": _handle_plan_trial_link_install,
        "applications.set_prerelease_rollout": _handle_set_prerelease_rollout,
        "applications.record_prerelease_health": _handle_record_prerelease_health,
        "applications.development.list_operations": _handle_development_list_operations,
        "applications.development.get_operation": _handle_development_get_operation,
        "applications.development.create": _handle_development_create,
        "applications.development.materialize": _handle_development_materialize,
        "applications.development.preview": _handle_development_preview,
        "applications.development.create_trial": _handle_development_create_trial,
        "applications.development.decide_trial": _handle_development_decide_trial,
        "applications.development.publish_link_trial": _handle_development_publish_link_trial,
        "applications.development.publish_prerelease": _handle_development_publish_prerelease,
        "applications.development.promote_stable": _handle_development_promote_stable,
        "applications.development.publish_stable_source": _handle_development_publish_stable_source,
    }


__all__ = ["contracts", "handlers"]
