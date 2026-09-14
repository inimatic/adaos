from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from adaos.sdk.developer import documents


@pytest.fixture
def root(tmp_path, monkeypatch):
    target = tmp_path / "app"
    target.mkdir()
    monkeypatch.setattr(documents.projects, "resolve_root", lambda *a: target)
    monkeypatch.setattr(documents.compositions, "resolve_root", lambda *a: target)
    monkeypatch.setattr(documents, "require_ctx", lambda *a: SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path / "state")))
    monkeypatch.setattr(documents.projects, "_publish_content_changed", lambda *a, **kw: None)
    return target


@pytest.mark.parametrize("kind", ["project", "scenario", "skill"])
def test_readme_create_utf8_and_stale_edit(root, kind):
    assert documents.read(kind, "demo")["digest"] == "missing"
    first = documents.write(kind, "demo", "# Описание\n", expected_digest="missing")
    assert (root / "README.md").read_text(encoding="utf-8") == "# Описание\n"
    second = documents.write(kind, "demo", "# Updated\n", expected_digest=first["digest"])
    with pytest.raises(ValueError, match="changed since"):
        documents.write(kind, "demo", "stale", expected_digest=first["digest"])
    assert documents.read(kind, "demo") == {k: v for k, v in second.items() if k != "ok"}


@pytest.mark.parametrize("path", ["../outside.md", "ui_revisions/README.md", "secret.json", "prompt_state.json"])
def test_document_rejects_escape_and_protected_paths(root, path):
    with pytest.raises((ValueError, documents.projects.DeveloperProjectError)):
        documents.write("project", "demo", "bad", path=path, expected_digest="missing")
    assert not list(root.rglob("*.md"))


def test_document_rejects_partial_and_invalid_utf8(root):
    (root / "README.md").write_bytes(b"x" * 131073)
    with pytest.raises(ValueError, match="128 KiB"):
        documents.read("project", "demo")
    (root / "README.md").write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        documents.read("project", "demo")


def test_concurrent_document_creators_have_one_winner(root):
    def create(text):
        try:
            return documents.write("project", "demo", text, expected_digest="missing")["ok"]
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, ["one", "two"])) == [False, True]
