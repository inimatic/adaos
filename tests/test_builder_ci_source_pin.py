import json
import sys
from pathlib import Path

import pytest

from scripts import prepare_builder_ci_source as gate


def test_local_beta_pin_cannot_silently_fetch_the_old_remote_builder(tmp_path, monkeypatch):
    pin = tmp_path / "pin.json"
    pin.write_text(json.dumps({"source_state": "local_beta_pending_publication"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prepare", "--pin", str(pin), "--checkout", str(tmp_path / "checkout")])
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: pytest.fail("unpublished checkpoint must not be fetched"))
    with pytest.raises(SystemExit, match="not published"):
        gate.main()
    assert not (tmp_path / "checkout").exists()
