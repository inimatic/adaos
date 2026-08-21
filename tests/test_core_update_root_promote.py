from __future__ import annotations

import json
import os

import pytest


@pytest.fixture(autouse=True)
def _restore_process_environment():
    keys = ("ADAOS_BASE_DIR", "ADAOS_ROOT_REPO_ROOT")
    previous = {key: os.environ.get(key) for key in keys}
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_candidate_root_promoter_refreshes_wrapper(monkeypatch, tmp_path, capsys) -> None:
    from adaos.apps import core_update_root_promote
    from adaos.services import autostart, core_update

    monkeypatch.setattr(
        core_update,
        "promote_root_from_slot",
        lambda *, slot: {"ok": True, "slot": slot, "transaction_state": "committed"},
    )
    monkeypatch.setattr(
        autostart,
        "refresh_runtime_wrapper",
        lambda **_kwargs: {"ok": True, "wrapper": str(tmp_path / "adaos-autostart.sh")},
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
    from adaos.services import autostart, core_update

    monkeypatch.setattr(core_update, "promote_root_from_slot", lambda *, slot: {"ok": True, "slot": slot})
    monkeypatch.setattr(
        autostart,
        "refresh_runtime_wrapper",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

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


def test_candidate_root_promoter_refreshes_wrapper_after_promotion(monkeypatch, tmp_path, capsys) -> None:
    from adaos.apps import core_update_root_promote
    from adaos.services import autostart, core_update

    events: list[str] = []

    def _promote(*, slot):
        events.append(f"promote:{slot}")
        return {"ok": True, "slot": slot, "transaction_state": "committed"}

    monkeypatch.setattr(core_update, "promote_root_from_slot", _promote)
    monkeypatch.setattr(
        autostart,
        "refresh_runtime_wrapper",
        lambda **_kwargs: events.append("wrapper") or {"ok": True},
    )

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
    assert events == ["promote:B", "wrapper"]
    assert json.loads(capsys.readouterr().out)["wrapper_refresh"]["ok"] is True
