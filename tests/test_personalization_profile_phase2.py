from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.scenario.projection_service import ProjectionService
from adaos.services.user import profile as profile_module


class _FakeKV:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value: object) -> None:
        self._data[key] = value


def _ctx(owner_id: str = "masha") -> SimpleNamespace:
    return SimpleNamespace(
        kv=_FakeKV(),
        bus=object(),
        settings=SimpleNamespace(owner_id=owner_id, subnet_id="family-subnet"),
    )


def test_phase2_profile_preferences_header_and_redacted_audit(tmp_path, monkeypatch) -> None:
    events: list[tuple[str, dict[str, object], str]] = []
    fake_ctx = _ctx()
    access = PersonalizationAccessService(
        PersonalizationAccessStore(tmp_path / "access.json"),
        owner=profile_module.SubjectRef("user", "masha"),
    )
    monkeypatch.setattr(
        profile_module,
        "emit",
        lambda bus, topic, payload, source: events.append((topic, dict(payload), source)),
    )

    service = profile_module.UserProfileService(fake_ctx, access=access)
    profile = service.update_profile(
        {
            "display_name": "Masha",
            "language": "ru",
            "theme": "light",
        }
    )
    preferences = service.update_preferences({"theme": "dark", "memory_privacy": "local"})
    header = service.header_settings()

    assert profile.display_name == "Masha"
    assert profile.settings["language"] == "ru"
    assert preferences["theme"] == "dark"
    assert header["theme"] == "dark"
    assert header["current_subnet"] == "family-subnet"
    assert header["role_status"] == {"value": "owner", "editable": False}
    assert fake_ctx.kv.get("users/masha/profile.v0")["schema_version"].startswith("adaos.personalization_access")
    assert sorted(events[1][1]["keys"]) == ["memory_privacy", "theme"]
    assert events[1][1]["settings"] == {"theme": "dark", "memory_privacy": "local"}
    assert float(events[1][1]["preferences_revision"]) > 0

    audit = access.list_audit(subject=profile_module.SubjectRef("user", "masha"), limit=20)
    assert any(item["event_type"] == "profile.updated" for item in audit)
    preference_audit = [item for item in audit if item["event_type"] == "preference.updated"]
    assert preference_audit
    assert preference_audit[0]["redacted_diff"]["preference"] == "<redacted>"


def test_profile_updates_persist_access_state_once_per_operation(tmp_path, monkeypatch) -> None:
    fake_ctx = _ctx()
    store = PersonalizationAccessStore(tmp_path / "access.json")
    store._MAX_AUDIT_RECORDS = 2
    access = PersonalizationAccessService(
        store,
        owner=profile_module.SubjectRef("user", "masha"),
    )
    service = profile_module.UserProfileService(fake_ctx, access=access)
    original_save = store._save_now
    save_count = 0

    def counted_save() -> None:
        nonlocal save_count
        save_count += 1
        original_save()

    monkeypatch.setattr(store, "_save_now", counted_save)

    service.update_profile({"display_name": "Masha", "language": "ru"})
    assert save_count == 1

    service.update_preferences({"theme": "dark", "memory_privacy": "local"})
    assert save_count == 2

    service.header_settings()
    service.header_settings()
    assert save_count == 2
    assert len(store.snapshot()["audit"]) == 2


def test_phase2_profile_rejects_role_membership_policy_fields() -> None:
    service = profile_module.UserProfileService(_ctx())

    with pytest.raises(ValueError, match="profile settings cannot contain access policy keys"):
        service.update_profile({"role": "owner"})


def test_phase2_sdk_profile_helpers_preserve_compatibility(monkeypatch) -> None:
    from adaos.sdk.data import profile as sdk_profile
    from adaos.sdk.data import ctx as sdk_ctx

    fake_ctx = _ctx()
    monkeypatch.setattr(sdk_profile, "require_ctx", lambda _feature=None: fake_ctx)
    monkeypatch.setattr(sdk_ctx, "require_ctx", lambda _feature=None: fake_ctx)

    assert sdk_profile.update_settings({"preferred_name": "Masha"})["preferred_name"] == "Masha"
    assert sdk_profile.update_preferences({"theme": "dark"})["theme"] == "dark"
    assert sdk_profile.get_profile()["preferred_name"] == "Masha"
    assert sdk_ctx.current_user.profile()["preferred_name"] == "Masha"
    assert sdk_ctx.current_user.preferences()["theme"] == "dark"
    assert sdk_ctx.current_user.header_settings()["theme"] == "dark"


def test_phase2_projection_service_routes_profile_preferences_to_kv() -> None:
    target = SimpleNamespace(backend="kv", path=None, webspace_id=None)
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])
    fake_ctx = _ctx()
    service = ProjectionService(ctx=fake_ctx, registry=registry)

    asyncio.run(service.apply("current_user", "profile.preferences", {"theme": "dark"}))

    raw = fake_ctx.kv.get("users/masha/preferences.v0")
    assert raw["theme"]["value"] == "dark"
