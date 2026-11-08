"""Bind notification SDK operations to verified runtime identity and live grants."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import secrets
import time

import yaml

from adaos.domain.notifications import NotificationError, NotificationUnavailable, ProducerBinding, compile_manifest, digest, dump
from adaos.domain.personalization_access import SubjectRef
from adaos.sdk import access
from adaos.services.agent_context import get_ctx
from adaos.services.personalization_runtime import current_subnet_id, personalization_access_service
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.notifications.runtime import NotificationService


def principal(ctx, *, write=False):
    actor = access.caller()
    user = access.current_user()
    if not actor or not user:
        raise PermissionError("notification operation requires a verified user")
    subject = SubjectRef("user", user["id"])
    personalization_access_service(ctx).require_user_private_content_access(
        actor=SubjectRef(actor["kind"], actor["id"]), subject=subject,
        action="preferences.write.self" if write else "preferences.read.self",
        resource="notifications", audit_success=False)
    return subject.ref()


def _application_store(ctx):
    from adaos.services.applications.store import ApplicationStore
    return ApplicationStore(Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()))


def _grant(ctx, application, permission, principal_ref):
    from adaos.domain.application_access import evaluate_application_access
    from adaos.services.applications.store import ApplicationStoreError
    store = _application_store(ctx)
    try:
        release = store.get_release(application["application_id"], application["release_digest"])
    except (OSError, ApplicationStoreError) as exc:
        raise PermissionError("notification Application release is no longer available") from exc
    grants = store.list_application_access_grants(application["application_id"], subject_ref=principal_ref)
    chain = {**application, "subject_ref": principal_ref}
    capability = application.get("app_capability") or permission
    for grant in grants:
        decision = evaluate_application_access(profile=release.permission_profile, roles=release.application_roles,
            grant=grant, permission_id=permission, app_capability=capability, actor_chain=chain,
            component_capabilities=(permission,))
        if decision.decision == "allow":
            return release
    raise PermissionError(f"current Application grant does not allow {permission}")


def _manifest(ctx, capability):
    decision = require_skill_capability(ctx, capability)
    path = Path(decision.manifest_path)
    if path.stat().st_size > 131072:
        raise NotificationError("skill manifest exceeds notification admission budget")
    raw = path.read_bytes()
    return path.parent.resolve(), yaml.safe_load(raw), "sha256:" + digest(raw.hex())


class NotificationSDKRuntime:
    def __init__(self, ctx=None):
        try:
            self.ctx = ctx or get_ctx()
        except RuntimeError as exc:
            raise NotificationUnavailable("notification operations require an admitted Core runtime") from exc
        self.service = NotificationService(self.ctx)

    def management(self, *, write=False) -> tuple[str, str]:
        require_skill_capability(self.ctx, "notifications.manage")
        user = principal(self.ctx, write=write)
        application = access.application()
        if not application or application.get("subject_ref") != user:
            raise PermissionError("notification manager needs a verified Application context")
        if application.get("runtime_source") == "dev":
            permissions = application.get("permission_profile") or {}
            declared = {p.get("id") for kind in ("required", "optional") for p in permissions.get(kind, [])}
            if "notifications.manage" not in declared:
                raise PermissionError("Application did not declare notification management")
            # The same controls work against synthetic Preview state; real
            # recipient preferences and endpoint policy remain outside this scope.
            user = "user:preview." + digest([user, application["application_id"]])
        else:
            _grant(self.ctx, application, "notifications.manage", user)
        return user, "subnet:" + current_subnet_id(self.ctx).removeprefix("subnet:")

    def producer(self):
        user = principal(self.ctx)
        root, manifest, manifest_digest = _manifest(self.ctx, "notifications.send")
        application = access.application()
        current = self.ctx.skill_ctx.get()
        if not application or application.get("subject_ref") != user:
            raise PermissionError("notification publisher needs a verified Application context")
        if application.get("component_ref") != "skill:" + current.name:
            raise PermissionError("notification component does not match the admitted Application")
        dev = application.get("runtime_source") == "dev"
        app_id = str(application["application_id"])
        subnet = "subnet:" + current_subnet_id(self.ctx).removeprefix("subnet:")
        if dev:
            permissions = application.get("permission_profile") or {}
            declared = {p.get("id") for kind in ("required", "optional") for p in permissions.get(kind, [])}
            if "notifications.send" not in declared:
                raise PermissionError("DEV Application did not declare notifications.send")
            user = "user:preview." + digest([user, application["application_id"]])
        else:
            release = _grant(self.ctx, application, "notifications.send", user)
            selected = _application_store(self.ctx).get_runtime_selection(str(application.get("webspace_id") or ""), app_id)
            if (selected.release_digest != application["release_digest"]
                or selected.runtime_root_ref != application.get("runtime_root_ref", "workspace")):
                raise PermissionError("notification Application runtime selection changed")
        pointer = manifest.get("notifications")
        if not isinstance(pointer, dict) or set(pointer) != {"manifest"} or not isinstance(pointer["manifest"], str):
            raise NotificationUnavailable("active component must declare notifications.manifest")
        path = (root / pointer["manifest"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 131072:
            raise NotificationError("notification manifest escapes its admitted package or exceeds budget")
        declaration = yaml.safe_load(path.read_text(encoding="utf8"))
        compiled = compile_manifest(declaration, root=path.parent)
        if not dev:
            disclosures = {str(row.get("id")): dict(row) for row in release.permission_profile.notifications}
            if any(any(disclosures.get(item["id"], {}).get(k) != v for k, v in item.items())
                   for item in compiled["permission_disclosure"]):
                raise PermissionError("notification declaration does not match the admitted release disclosure")
        authority = {key: application[key] for key in (
            "app_capability", "webspace_id", "runtime_root_ref", "session_ref", "device_ref",
            "session_trusted", "device_trusted") if key in application}
        actor = access.caller()
        authority["actor_ref"] = f"{actor['kind']}:{actor['id']}"
        authority["component_ref"] = "skill:" + current.name
        authority["manifest_digest"] = manifest_digest
        binding = ProducerBinding(
            application_id=app_id, instance_id="application-instance:" + digest([subnet, app_id]),
            namespace="development:" + app_id if dev else (
                str(application.get("runtime_root_ref")) if application.get("runtime_source") == "trial" else "installed"),
            release_digest=str(application["release_digest"]), principal_ref=user, subnet_ref=subnet,
            author_ref="skill:" + current.name, author_label=str(application.get("application_title") or current.name),
            test_only=dev, authority=authority)
        self.service.admit_catalog(binding, declaration, root=path.parent)
        return binding

    def capture(self, *, lifetime_seconds=86400) -> dict:
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 86400:
            raise NotificationError("publisher lifetime must be 1..86400 seconds")
        binding = self.producer()
        handle = "notification-publisher." + secrets.token_hex(24)
        expires = time.time() + lifetime_seconds
        with self.service.store.connect() as con:
            con.execute("INSERT INTO notification_publisher_grants VALUES(?,?,?,?,?,'active')",
                (digest(handle), binding.authority["component_ref"], binding.authority["manifest_digest"],
                 dump(asdict(binding)), expires))
            con.commit()
        return {"publisher_ref": handle, "expires_at": expires, "test_only": binding.test_only}

    def captured(self, publisher_ref):
        if not isinstance(publisher_ref, str) or len(publisher_ref) > 200:
            raise PermissionError("invalid captured notification publisher")
        _, _, manifest_digest = _manifest(self.ctx, "notifications.send")
        current = self.ctx.skill_ctx.get()
        with self.service.store.connect() as con:
            row = con.execute("SELECT * FROM notification_publisher_grants WHERE handle_digest=?", (digest(publisher_ref),)).fetchone()
        if (not row or row["status"] != "active" or row["expires_at"] <= time.time()
            or row["skill_ref"] != "skill:" + current.name or row["manifest_digest"] != manifest_digest):
            raise PermissionError("captured notification publisher is unavailable or belongs to another package")
        value = json.loads(row["binding_json"])
        value["admitted_recipients"] = tuple(value.get("admitted_recipients", []))
        binding = ProducerBinding(**value)
        if binding.test_only:
            # The caller may be absent in a Core background continuation. The
            # handle still cannot enable any external endpoint or real namespace.
            return binding
        authority = binding.authority
        selected = _application_store(self.ctx).get_runtime_selection(
            str(authority.get("webspace_id") or ""), binding.application_id)
        if (selected.release_digest != binding.release_digest or
            selected.runtime_root_ref != authority.get("runtime_root_ref", "workspace")):
            raise PermissionError("captured notification publisher runtime retired")
        from adaos.services.notifications.delivery import live_application_authority
        if not live_application_authority({"test_only": False, "application_id": binding.application_id,
            "release_digest": binding.release_digest, "principal_ref": binding.principal_ref,
            "producer_binding": asdict(binding)}, self.ctx):
            raise PermissionError("captured notification publisher permission was revoked")
        return binding
