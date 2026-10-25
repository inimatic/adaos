from pathlib import Path

import pytest

from adaos.services.applications import sdk_compatibility as compatibility


def _audit(tmp_path, source, monkeypatch, value):
    (tmp_path / "handlers").mkdir()
    (tmp_path / "handlers/main.py").write_text(source, encoding="utf-8")
    def resolve(symbol):
        if symbol.endswith(".missing"):
            raise AttributeError(symbol)
        return value
    monkeypatch.setattr(compatibility, "_resolve", resolve)
    return compatibility.audit_sdk_usage(tmp_path)


@pytest.mark.parametrize("call,status", [
    ("api.run('x', limit=2)", "static_checks_passed"),
    ("api.run('x', removed=True)", "incompatible"),
    ("api.run()", "incompatible"),
    ("api.run(**options)", "static_checks_passed"),
    ("api.run(removed=True, **options)", "incompatible"),
])
def test_audit_signatures_without_running_consumers(tmp_path, monkeypatch, call, status):
    def run(name, *, limit=20):
        raise AssertionError("SDK function must not be executed")
    result = _audit(tmp_path, f"from adaos.sdk import api\n{call}\nraise RuntimeError('must not execute')", monkeypatch, run)
    assert result["status"] == status
    assert result["runtime_smoke_required"]


def test_reports_missing_sdk_symbols(tmp_path, monkeypatch):
    result = _audit(tmp_path, "from adaos.sdk.example import missing", monkeypatch, object())
    assert result["status"] == "incompatible"
    assert result["findings"][0]["code"] == "missing_sdk_symbol"


def test_optional_dependency_is_unknown_not_removed(monkeypatch):
    def missing(_):
        raise ModuleNotFoundError("optional backend unavailable", name="optional_backend")
    monkeypatch.setattr(compatibility.importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match="sdk_dependency_unavailable"):
        compatibility._resolve("adaos.sdk.example.run")


def test_local_dictionary_does_not_look_like_removed_sdk_get(tmp_path, monkeypatch):
    result = _audit(tmp_path, "from adaos.sdk.builder import prototype\n"
        "def example(prototype):\n    return prototype.get('title')\n"
        "def other():\n    prototype = {}\n    return prototype.get('title')\n",
        monkeypatch, object())
    assert result["status"] == "static_checks_passed"
    assert result["checked_calls"] == 0
