from __future__ import annotations

import sys
from types import SimpleNamespace

from adaos.apps import bootstrap
from adaos.services.agent_context import get_ctx, set_ctx
from adaos.services.settings import Settings


def test_headless_linux_auto_vault_does_not_probe_keyring(monkeypatch) -> None:
    probed = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(get_keyring=lambda: probed.append(True)),
    )

    assert bootstrap._usable_os_keyring(mode="auto") is False
    assert probed == []


def test_explicit_unavailable_keyring_is_rejected(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            get_keyring=lambda: SimpleNamespace(priority=0),
        ),
    )

    assert bootstrap._usable_os_keyring(mode="keyring") is False


def test_build_publishes_complete_context_before_loading_node_config(
    _autocontext,
    monkeypatch,
    tmp_path,
) -> None:
    previous = _autocontext
    observed = {}
    node_config = object()

    def _load_config(*, ctx):
        observed["ctx"] = ctx
        observed["published"] = get_ctx()
        return node_config

    monkeypatch.setattr(bootstrap, "load_config", _load_config)
    settings = Settings.from_sources().with_overrides(
        base_dir=str(tmp_path / "runtime"),
        profile="bootstrap-test",
    )

    try:
        built = bootstrap._CtxHolder._build(settings)
    finally:
        set_ctx(previous)

    assert observed["ctx"] is built
    assert observed["published"] is built
    assert built.config is node_config
