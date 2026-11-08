"""Explicit one-time user endpoint tests on the same notification/outbox contract."""
from __future__ import annotations

import json
import time

from adaos.domain.notifications import NotificationConflict, NotificationError, ProducerBinding, digest, dump, token
from adaos.domain.personalization_access import SubjectRef
from adaos.services.notifications.runtime import NotificationService
from adaos.services.personalization_runtime import personalization_access_service

_MANIFEST = {"schema": "adaos.notifications.manifest.v1", "categories": [{
    "id": "endpoint.test", "title_key": "endpoint.test.title", "purpose_key": "endpoint.test.purpose",
    "source_event": "core.endpoint.test.v1", "payload_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "recipient_policy": "current_user", "default_mode": "center_only", "maximum_urgency": "normal",
    "sensitivity": "public", "lifetime_seconds": 120,
    "template": {"en": "Notification test. Notifications are working.", "ru": "Проверка уведомлений. Уведомления работают."}}]}


def send_test(ctx, principal_ref, subnet_ref, actor_ref, endpoint_id, *, expected_revision, operation_key,
              allow_quiet=False, locale="en", test_only=False, clock=time.time):
    service = NotificationService(ctx, clock=clock)
    if type(allow_quiet) is not bool:
        raise NotificationError("allow_quiet must be an explicit boolean")
    if not test_only:
        kind, _, identifier = actor_ref.partition(":")
        personalization_access_service(service.ctx).require_user_private_content_access(
            actor=SubjectRef(kind, identifier), subject=SubjectRef("user", principal_ref.removeprefix("user:")),
            action="preferences.write.self", resource="notifications", audit_success=False)
    elif not principal_ref.startswith("user:preview."):
        raise PermissionError("notification test fixture cannot address production users")
    token(operation_key, "operation_key")
    key = digest(["endpoint-test", principal_ref, operation_key])
    request = digest([subnet_ref, endpoint_id, expected_revision, allow_quiet, locale])
    authority = {"origin": "core.endpoint_probe.v1", "actor_ref": actor_ref,
                 "endpoint_id": endpoint_id, "allow_quiet": bool(allow_quiet)}
    binding = ProducerBinding("adaos.notifications", "core-probes:" + digest(subnet_ref), "endpoint-probes",
        "core:notifications.v1", principal_ref, subnet_ref, "core:notifications", "AdaOS", test_only=test_only, authority=authority)
    service.admit_catalog(binding, _MANIFEST)
    with service.store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        prior = con.execute("SELECT request_digest,receipt_json FROM notification_user_operations WHERE operation_key=?", (key,)).fetchone()
        if prior:
            if prior[0] != request:
                raise NotificationConflict("endpoint test operation conflict")
            return json.loads(prior[1])
        endpoint = con.execute("SELECT * FROM notification_endpoints WHERE endpoint_id=? AND principal_ref=? AND subnet_ref=?",
                               (endpoint_id, principal_ref, subnet_ref)).fetchone()
        if not endpoint or endpoint["status"] != "active":
            raise PermissionError("endpoint test requires an owned active endpoint")
        if type(expected_revision) is not int or endpoint["revision"] != expected_revision:
            raise NotificationConflict("endpoint test revision conflict")
        if json.loads(endpoint["capabilities_json"]).get("source_audience") != "personal":
            raise PermissionError("shared destination is not admitted for notification tests")
        if endpoint["transport"] == "web" and json.loads(endpoint["capabilities_json"]).get("permission") != "granted":
            raise PermissionError("device notification permission is not granted")
        prefs = service.preferences.read(principal_ref, subnet_ref, connection=con)
        authority["policy_revision"] = prefs["revision"]
        receipt = service.emit(binding, category_id="endpoint.test", event_id=key, source_id=key, source_revision=1,
                               occurred_at=service.clock(), payload={}, locale=locale, connection=con)
        record = service.get(principal_ref, receipt["notification_id"], connection=con)
        result = {"receipt": receipt, "decision": record["decision"], "expires_at": record["expires_at"],
                  "endpoint_id": endpoint_id, "one_shot": True, "preferences_changed": False}
        con.execute("INSERT INTO notification_user_operations VALUES(?,?,?,?,?)", (key, principal_ref, request, dump(result), service.clock()))
        con.commit()
    return result
