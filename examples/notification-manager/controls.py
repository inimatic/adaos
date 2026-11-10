"""SDK-only recipient controls; deploy only with a reviewed manage grant."""
from adaos.sdk import notifications


def mark_visible_revision_read(notification_id, shown_revision, recipient_revision, operation_key):
    return notifications.mark_read(notification_id, content_revision=shown_revision,
        expected_revision=recipient_revision, operation_key=operation_key)


def snooze(notification_id, until, recipient_revision, operation_key):
    return notifications.snooze(notification_id, until=until, expected_revision=recipient_revision,
        operation_key=operation_key)


def mute_category(application_id, category_id):
    settings = notifications.get_preferences()
    scope = {"application_id": application_id, "category_id": category_id}
    settings["rules"] = [row for row in settings["rules"] if row["scope"] != scope]
    settings["rules"].append({"scope": scope, "choice": {"mode": "off", "endpoint_ids": []}})
    return notifications.set_preferences(settings, expected_revision=settings["revision"])


def inspect_delivery(endpoint_id):
    """Read factual delivery evidence; this grants no send or fallback."""
    return notifications.inspect_endpoint(endpoint_id, limit=20)


def test_delivery(endpoint_id, endpoint_revision, operation_key, *, allow_quiet=False):
    """Call only for an explicit user test; keep its command's stable key."""
    return notifications.test_endpoint(endpoint_id, expected_revision=endpoint_revision,
        operation_key=operation_key, allow_quiet=allow_quiet)
