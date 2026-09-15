import json
from types import SimpleNamespace

import pytest

from adaos.sdk.developer import modal_settings


def test_explicit_dev_edit_preserves_trial_and_rejects_stale_source(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    trial = tmp_path / "trial"
    dev.mkdir()
    trial.mkdir()
    payload = {"ui": {"modals": {"picker": {"schema": {"id": "picker"}, "presentation": {"kind": "fullscreen"}}}}}
    for root in (dev, trial):
        (root / "webui.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(modal_settings.projects, "resolve_root", lambda *a: dev)
    monkeypatch.setattr(modal_settings.projects, "_publish_content_changed", lambda *a, **kw: None)
    monkeypatch.setattr(modal_settings, "require_ctx", lambda *a: SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path / "state")))
    before = modal_settings.read("scenario", "example", "picker")
    result = modal_settings.write("scenario", "example", "picker", width=80, height=90, expected_digest=before["digest"])
    assert result["presentation"] == {"kind": "modal", "size": {"width": 80, "height": 90}}
    assert json.loads((trial / "webui.json").read_text()) == payload
    with pytest.raises(ValueError, match="changed"):
        modal_settings.write("scenario", "example", "picker", width=60, height=70, expected_digest=before["digest"])
    with pytest.raises(ValueError, match="unambiguous"):
        modal_settings.read("scenario", "example", "missing")
    with pytest.raises(ValueError, match="dimensions"):
        modal_settings.write("scenario", "example", "picker", width=float("nan"), height=70, expected_digest=result["digest"])
