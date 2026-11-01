import json
import sys
from types import SimpleNamespace
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


def test_explicit_local_qualification_exports_its_exact_pin_not_the_published_default(tmp_path, monkeypatch):
    source=tmp_path/'source';source.mkdir()
    pin=tmp_path/'qualified-pin.json'
    pin.write_text(json.dumps({'source_state':'local_beta_pending_publication','source_path':'skills/builder_skill',
        'package_digest':'sha256:qualified','version':'fixture'}),encoding='utf-8')
    environment_file=tmp_path/'runner.env'
    monkeypatch.setenv('GITHUB_ENV',str(environment_file))
    monkeypatch.setattr(gate,'artifact_source_snapshot',lambda root:{'digest':'sha256:snapshot'})
    monkeypatch.setattr(gate,'build_artifact_package',lambda *args,**kwargs:SimpleNamespace(ref=SimpleNamespace(digest='sha256:qualified')))
    monkeypatch.setattr(sys,'argv',['prepare','--pin',str(pin),'--source',str(source),
        '--checkout',str(tmp_path/'unused-checkout')])
    gate.main()
    assert environment_file.read_text(encoding='utf-8').splitlines()==[
        'ADAOS_TEST_BUILDER_SOURCE='+str(source.resolve()),'ADAOS_TEST_BUILDER_PIN='+str(pin.resolve())]
    assert not (tmp_path/'unused-checkout').exists()
