"""Exercise one selected Application release through scoped HTTP callers."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.domain.personalization_access import Grant, ScopeRef, SessionKey, SubjectRef
from adaos.services.applications.access_management import (
    ApplicationAccessManagementService,
)
from adaos.services.applications.runtime import get_application_service
from adaos.services.personalization_runtime import personalization_access_service


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _result(response: requests.Response) -> dict[str, Any]:
    payload = response.json()
    value = payload.get("result") if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("application_id")
    parser.add_argument("skill_name")
    parser.add_argument("--webspace-id", default="desktop")
    parser.add_argument("--hub", default="http://127.0.0.1:8778")
    parser.add_argument(
        "--expected-runtime",
        choices=("trial", "workspace"),
        default="trial",
    )
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Only an enrolled ENV_TYPE=dev node may run this probe")
    args.output.mkdir(parents=True, exist_ok=False)
    ctx = init_ctx(Settings.from_sources())
    platform_access = personalization_access_service()
    applications = get_application_service(Path(ctx.paths.state_dir()))
    management = ApplicationAccessManagementService(applications)
    selection = applications.store.get_runtime_selection(
        args.webspace_id, args.application_id
    )
    release = applications.store.get_release(
        args.application_id, selection.release_digest
    )
    selected_runtime = (
        "trial" if selection.runtime_root_ref.startswith("trial:") else "workspace"
    )
    if selected_runtime != args.expected_runtime:
        parser.error(
            f"The selected runtime is {selected_runtime}, expected {args.expected_runtime}"
        )

    run_id = "e2e-app-access-" + uuid4().hex[:12]
    scope = ScopeRef("skill", args.skill_name)
    owner_headers = {
        "X-AdaOS-Token": resolve_control_token(base_url=args.hub)
    }
    report: dict[str, Any] = {
        "schema": "adaos.e2e.application_permission_runtime.v1",
        "run_id": run_id,
        "application_id": args.application_id,
        "skill_name": args.skill_name,
        "webspace_id": args.webspace_id,
        "release_digest": selection.release_digest,
        "runtime_root_ref": selection.runtime_root_ref,
        "expected_runtime": args.expected_runtime,
        "permission_profile_digest": release.permission_profile.digest,
        "checks": [],
        "ok": False,
    }
    app_grants = []
    identities: dict[str, dict[str, Any]] = {}

    def save() -> None:
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def check(
        name: str,
        response: requests.Response,
        expected: int,
        *,
        expected_runtime: str | None = args.expected_runtime,
    ) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {"content_type": response.headers.get("Content-Type")}
        report["checks"].append(
            {
                "name": name,
                "status": response.status_code,
                "expected": expected,
                "runtime_source": response.headers.get("X-AdaOS-Runtime-Source"),
                "release_digest": response.headers.get("X-AdaOS-Release-Digest"),
                "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 2),
                "error": body.get("detail") if isinstance(body, dict) else None,
            }
        )
        save()
        if response.status_code != expected:
            raise RuntimeError(
                f"{name}: HTTP {response.status_code}, expected {expected}: {response.text[:500]}"
            )
        if (
            expected_runtime
            and 200 <= expected < 300
            and response.headers.get("X-AdaOS-Runtime-Source") != expected_runtime
        ):
            raise RuntimeError(f"{name}: request did not execute in {expected_runtime}")

    def call(
        client: requests.Session,
        role: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        intent: str,
    ) -> requests.Response:
        return client.post(
            f"{args.hub}/api/tools/call",
            headers=identities[role]["headers"],
            json={
                "tool": f"{args.skill_name}:{tool}",
                "arguments": arguments,
                "context": {"webspace_id": args.webspace_id},
                "intent": intent,
                "idempotency_key": f"{run_id}:{role}:{tool}:{uuid4().hex}",
            },
            timeout=90,
        )

    with requests.Session() as client:
        for role in ("viewer", "coordinator"):
            identity = f"{run_id}-{role}"
            subject = SubjectRef("user", identity)
            platform_access.put_user(
                subject,
                metadata={"test": True, "purpose": "Application permission runtime E2E"},
            )
            platform_access.put_session(
                SessionKey(
                    session_id=identity,
                    key_id=identity,
                    subject=subject,
                    expires_at=time.time() + 900,
                )
            )
            # Both callers pass the platform ceiling. The Application role is
            # therefore the boundary under test, rather than an earlier node grant.
            platform_access.put_grant(
                Grant(
                    grant_id=identity,
                    subject=subject,
                    scope=scope,
                    capabilities=("workspace.read", "workspace.write"),
                    issued_by=platform_access.owner,
                )
            )
            app_grants.append(
                management.access.grant_access(
                    args.application_id,
                    release_digest=selection.release_digest,
                    subject_ref=subject.ref(),
                    application_roles=(role,),
                    issuer_ref=platform_access.owner.ref(),
                    idempotency_key=identity,
                    permission_ceiling=tuple(release.permission_profile.flat_permissions),
                    constraints={"platform_role": "member", "webspace_id": args.webspace_id},
                )
            )
            issued = client.post(
                f"{args.hub}/api/personalization/admin/sessions/{identity}/tool-credential",
                headers=owner_headers,
                json={"skill_name": args.skill_name, "ttl_seconds": 600},
                timeout=30,
            )
            check(
                f"{role}-credential-issued",
                issued,
                200,
                expected_runtime=None,
            )
            identities[role] = {
                "identity": identity,
                "subject_ref": subject.ref(),
                "headers": {"Authorization": "Bearer " + issued.json()["access_token"]},
            }

        try:
            viewer_list = call(
                client,
                "viewer",
                "list_records",
                {"entity": "Volunteer", "search": "", "filters": {}},
                intent="read",
            )
            check("viewer-reads-migrated-roster", viewer_list, 200)
            if len(_result(viewer_list).get("items") or []) < 4:
                raise RuntimeError("Stable roster records were not present in Beta")

            denied_write = call(
                client,
                "viewer",
                "save_record",
                {
                    "entity": "Volunteer",
                    "values": {"name": "denied", "role": "Greeter"},
                },
                intent="mutation",
            )
            check("viewer-role-denies-write", denied_write, 403)

            denied_upload = client.put(
                f"{args.hub}/api/tools/{args.skill_name}/upload_photo/attachments",
                headers={**identities["viewer"]["headers"], "Content-Type": "image/png"},
                params={
                    "field_id": "photo",
                    "filename": "viewer-denied.png",
                    "read_tool": "read_photo",
                    "webspace_id": args.webspace_id,
                },
                data=PNG_1X1,
                timeout=90,
            )
            check("viewer-role-denies-photo-upload", denied_upload, 403)

            upload = client.put(
                f"{args.hub}/api/tools/{args.skill_name}/upload_photo/attachments",
                headers={
                    **identities["coordinator"]["headers"],
                    "Content-Type": "image/png",
                },
                params={
                    "field_id": "photo",
                    "filename": "permission-e2e.png",
                    "read_tool": "read_photo",
                    "webspace_id": args.webspace_id,
                },
                data=PNG_1X1,
                timeout=90,
            )
            check("coordinator-uploads-photo", upload, 200)
            photo_ref = upload.json()["ref"]

            marker = f"Permission E2E {run_id[-12:]}"
            saved = call(
                client,
                "coordinator",
                "save_record",
                {
                    "entity": "Volunteer",
                    "values": {
                        "name": marker,
                        "role": "Greeter",
                        "availability_notes": "Role-matrix acceptance",
                        "contact_phone": "+10000000099",
                        "contact_email": "permissions@example.test",
                        "age": 41,
                        "photo": photo_ref,
                    },
                },
                intent="mutation",
            )
            check("coordinator-creates-volunteer", saved, 200)
            saved_item = _result(saved).get("item") or {}
            if saved_item.get("name") != marker or saved_item.get("photo") != photo_ref:
                raise RuntimeError("Coordinator write did not retain the photo-backed record")

            viewer_read = call(
                client,
                "viewer",
                "get_record",
                {"entity": "Volunteer", "id": saved_item["id"]},
                intent="read",
            )
            check("viewer-reads-new-public-profile", viewer_read, 200)
            public_item = _result(viewer_read).get("item") or {}
            if "contact_phone" in public_item or "contact_email" in public_item:
                raise RuntimeError("Public viewer response disclosed coordinator-only contact data")

            denied_contact = call(
                client,
                "viewer",
                "get_contact",
                {"id": saved_item["id"]},
                intent="read",
            )
            check("viewer-role-denies-private-contact", denied_contact, 403)

            contact = call(
                client,
                "coordinator",
                "get_contact",
                {"id": saved_item["id"]},
                intent="read",
            )
            check("coordinator-reads-private-contact", contact, 200)
            if (_result(contact).get("item") or {}).get("contact_email") != "permissions@example.test":
                raise RuntimeError("Coordinator did not receive private contact data")

            downloaded = client.get(
                args.hub + photo_ref,
                headers=identities["viewer"]["headers"],
                timeout=90,
            )
            check("viewer-reads-authorized-photo", downloaded, 200)
            if downloaded.content != PNG_1X1:
                raise RuntimeError("Downloaded photo bytes differ from the admitted upload")

            audit = management.store.list_application_access_audit(
                args.application_id
            )
            relevant = [
                item
                for item in audit
                if item.get("subject_ref")
                in {value["subject_ref"] for value in identities.values()}
            ]
            outcomes = {
                str(item.get("outcome") or item.get("decision") or "")
                for item in relevant
            }
            if not {"allow", "deny"}.issubset(outcomes):
                raise RuntimeError("Application decision audit lacks allow/deny evidence")
            report["audit"] = {
                "events": len(relevant),
                "outcomes": sorted(outcomes),
                "subjects": sorted(value["subject_ref"] for value in identities.values()),
            }
            report["created_record"] = {
                "id": saved_item["id"],
                "name": marker,
                "photo_ref": photo_ref,
            }
            report["ok"] = True
        finally:
            for grant in app_grants:
                current = management.store.get_application_access_grant(grant.grant_id)
                management.access.revoke_access(
                    grant.grant_id,
                    issuer_ref=platform_access.owner.ref(),
                    expected_revision=current.revision,
                )
            for value in identities.values():
                identity = value["identity"]
                platform_access.revoke_grant(
                    identity, actor=platform_access.owner, reason="E2E cleanup"
                )
                platform_access.revoke_session(
                    identity, actor=platform_access.owner, reason="E2E cleanup"
                )
            report["cleanup"] = (
                "Application and platform grants revoked; sessions revoked; no raw credentials retained"
            )
            save()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
