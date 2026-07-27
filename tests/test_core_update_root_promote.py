from __future__ import annotations

import json


def test_candidate_root_promoter_refreshes_wrapper(monkeypatch, tmp_path, capsys) -> None:
    from adaos.apps import core_update_root_promote
    from adaos.apps import bootstrap
    from adaos.services import autostart, core_update

    monkeypatch.setattr(
        core_update,
        "promote_root_from_slot",
        lambda *, slot: {"ok": True, "slot": slot, "transaction_state": "committed"},
    )
    fake_ctx = object()
    fake_spec = object()
    monkeypatch.setattr(bootstrap, "init_ctx", lambda: fake_ctx)
    monkeypatch.setattr(autostart, "default_spec", lambda ctx, *, host, port: fake_spec)
    monkeypatch.setattr(
        autostart,
        "refresh_wrapper",
        lambda ctx, spec: {"ok": True, "wrapper": str(tmp_path / "adaos-autostart.sh")},
    )

    rc = core_update_root_promote.main(
        [
            "--slot",
            "B",
            "--base-dir",
            str(tmp_path / "base"),
            "--root-repo-root",
            str(tmp_path / "root"),
            "--runtime-host",
            "127.0.0.1",
            "--runtime-port",
            "8777",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["transaction_state"] == "committed"
    assert payload["wrapper_refresh"]["ok"] is True


def test_candidate_root_promoter_fails_closed_when_wrapper_refresh_fails(monkeypatch, tmp_path, capsys) -> None:
    from adaos.apps import core_update_root_promote
    from adaos.apps import bootstrap
    from adaos.services import autostart, core_update

    monkeypatch.setattr(core_update, "promote_root_from_slot", lambda *, slot: {"ok": True, "slot": slot})
    monkeypatch.setattr(bootstrap, "init_ctx", lambda: object())
    monkeypatch.setattr(autostart, "default_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(autostart, "refresh_wrapper", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    rc = core_update_root_promote.main(
        [
            "--slot",
            "A",
            "--base-dir",
            str(tmp_path / "base"),
            "--root-repo-root",
            str(tmp_path / "root"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["wrapper_refresh"]["error_type"] == "OSError"


def test_candidate_root_promoter_initializes_context_before_promotion(monkeypatch, tmp_path, capsys) -> None:
    from adaos.apps import bootstrap, core_update_root_promote
    from adaos.services import autostart, core_update

    events: list[str] = []
    fake_ctx = object()

    def _init_ctx():
        events.append("context")
        return fake_ctx

    def _promote(*, slot):
        events.append(f"promote:{slot}")
        return {"ok": True, "slot": slot, "transaction_state": "committed"}

    monkeypatch.setattr(bootstrap, "init_ctx", _init_ctx)
    monkeypatch.setattr(core_update, "promote_root_from_slot", _promote)
    monkeypatch.setattr(autostart, "default_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(autostart, "refresh_wrapper", lambda *args, **kwargs: {"ok": True})

    rc = core_update_root_promote.main(
        [
            "--slot",
            "B",
            "--base-dir",
            str(tmp_path / "base"),
            "--root-repo-root",
            str(tmp_path / "root"),
        ]
    )

    assert rc == 0
    assert events == ["context", "promote:B"]
    assert json.loads(capsys.readouterr().out)["wrapper_refresh"]["ok"] is True
