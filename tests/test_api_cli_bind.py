from types import SimpleNamespace

from adaos.apps.cli.commands import api as api_cmd


def test_resolve_bind_keeps_supervisor_slot_port(monkeypatch) -> None:
    conf = SimpleNamespace(role="hub", local_api_url="http://127.0.0.1:8777")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "1")

    host, port = api_cmd._resolve_bind(
        conf,
        "127.0.0.1",
        8778,
        explicit_host=False,
        explicit_port=False,
    )

    assert (host, port) == ("127.0.0.1", 8778)


def test_resolve_bind_uses_persisted_url_for_unmanaged_default(monkeypatch) -> None:
    conf = SimpleNamespace(role="hub", local_api_url="http://127.0.0.1:8778")
    monkeypatch.setenv("ADAOS_SUPERVISOR_ENABLED", "0")

    host, port = api_cmd._resolve_bind(
        conf,
        "127.0.0.1",
        8777,
        explicit_host=False,
        explicit_port=False,
    )

    assert (host, port) == ("127.0.0.1", 8778)
