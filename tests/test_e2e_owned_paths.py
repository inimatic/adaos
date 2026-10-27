from pathlib import Path

import pytest

from adaos.e2e.paths import owned_subdirectory


def link_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")


def test_output_must_be_a_strict_child(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    assert owned_subdirectory(root / "run" / "result", root) == root / "run" / "result"
    for refused in (root, root / ".." / "outside", tmp_path / "artifacts-other" / "run"):
        with pytest.raises(ValueError):
            owned_subdirectory(refused, root)


def test_configured_root_can_be_relocated(tmp_path):
    target = tmp_path / "another-drive"
    target.mkdir()
    root = tmp_path / "artifacts"
    link_directory(root, target)
    assert owned_subdirectory(root / "run", root) == target / "run"
    assert owned_subdirectory(target / "run", root) == target / "run"
    with pytest.raises(ValueError):
        owned_subdirectory(target, root)


def test_nested_link_cannot_escape_configured_root(tmp_path):
    root, outside = tmp_path / "artifacts", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link_directory(root / "escape", outside)
    with pytest.raises(ValueError):
        owned_subdirectory(root / "escape" / "run", root)
