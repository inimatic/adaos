import json
import time

import pytest

from adaos.domain.personalization_access import DeviceKey, ScopeRef, SessionKey, SubjectRef
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.policy.caller import CallerAccessDenied
from adaos.services.policy.session_credentials import authenticate_tool_credential, issue_tool_credential


@pytest.fixture
def access(tmp_path):
    service = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / "access.json"))
    service.put_session(SessionKey(session_id="session.reader", key_id="reader-key",
                                  subject=SubjectRef("user", "reader"), expires_at=time.time() + 600))
    return service


def issue(access):
    return issue_tool_credential(access, session_id="session.reader", skill_name="sample", actor=access.owner)


def test_scoped_credential_binds_existing_session_and_does_not_create_grants(access):
    result = issue(access)
    assert authenticate_tool_credential(access, result["access_token"]) == (SubjectRef("session", "session.reader"), ScopeRef("skill", "sample"))
    assert result["expires_at"] <= access.store.get_session("session.reader")["expires_at"]
    snapshot = access.store.snapshot()
    assert not snapshot["grants"]
    assert result["access_token"] not in json.dumps(snapshot)
    assert set(snapshot["sessions"]) == {"session.reader"}
    reloaded = PersonalizationAccessService(PersonalizationAccessStore(access.store.path))
    assert authenticate_tool_credential(reloaded, result["access_token"])[0].id == "session.reader"


def test_credential_rotation_invalidates_previous_value(access):
    first, second = issue(access), issue(access)
    assert first["access_token"] != second["access_token"]
    with pytest.raises(CallerAccessDenied):
        authenticate_tool_credential(access, first["access_token"])
    assert authenticate_tool_credential(access, second["access_token"])


@pytest.mark.parametrize("change", ["revoked", "expired", "credential_expired", "wrong_purpose", "wrong_scope", "tampered", "missing_device"])
def test_scoped_credentials_fail_closed(access, change):
    token = issue(access)["access_token"]
    if change == "revoked":
        access.revoke_session("session.reader", actor=access.owner)
    elif change == "expired":
        access.store.update_session("session.reader", {"expires_at": time.time() - 1})
    elif change == "missing_device":
        access.store.update_session("session.reader", {"device_id": "missing"})
    elif change == "tampered":
        token = token[:-1] + ("A" if token[-1] != "A" else "B")
    else:
        credential = access.store.get_session("session.reader")["tool_credential"]
        credential.update({"expires_at": time.time() - 1} if change == "credential_expired" else
                          {"purpose": "root_mcp"} if change == "wrong_purpose" else {"scope": {"kind": "subnet", "id": "all"}})
        access.store.update_session("session.reader", {"tool_credential": credential})
    with pytest.raises(CallerAccessDenied):
        authenticate_tool_credential(access, token)


def test_owner_only_issuance_and_no_identity_from_a_token_id(access):
    with pytest.raises(CallerAccessDenied):
        issue_tool_credential(access, session_id="session.reader", skill_name="sample", actor=SubjectRef("user", "reader"))
    for token in ("session.reader", "root-mcp-token", "dev-local-token", "adaos-session-v1.owner.AA"):
        with pytest.raises(CallerAccessDenied):
            authenticate_tool_credential(access, token)
    with pytest.raises(ValueError):
        issue_tool_credential(access, session_id="session.reader", skill_name="sample", actor=access.owner, ttl_seconds=3601)


def test_revoked_bound_device_denies_session_credential(access):
    access.store.put_device_key(DeviceKey(user_id="reader", device_id="reader-device", key_id="key", public_key_ref="key-ref"))
    access.store.update_session("session.reader", {"device_id": "reader-device"})
    token = issue(access)["access_token"]
    access.store.update_device_key("reader-device", {"status": "revoked"})
    with pytest.raises(CallerAccessDenied):
        authenticate_tool_credential(access, token)


@pytest.mark.parametrize("expiry", ["NaN", "Infinity", "invalid", 0])
def test_invalid_expiry_never_grants_an_unbounded_credential(access, expiry):
    token = issue(access)["access_token"]
    access.store.update_session("session.reader", {"expires_at": expiry})
    with pytest.raises(CallerAccessDenied):
        issue(access)
    with pytest.raises(CallerAccessDenied):
        authenticate_tool_credential(access, token)


def test_bound_device_must_belong_to_session_subject(access):
    access.store.put_device_key(DeviceKey(user_id="other", device_id="other-device", key_id="key", public_key_ref="key-ref"))
    access.store.update_session("session.reader", {"device_id": "other-device"})
    with pytest.raises(CallerAccessDenied, match="session_device_subject_mismatch"):
        issue(access)
