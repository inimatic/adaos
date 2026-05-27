from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.services.skill import dependency_disk_guard as guard


def test_dependency_disk_guard_blocks_heavy_install_when_free_space_is_low(monkeypatch, tmp_path):
    monkeypatch.delenv("ADAOS_SKILL_DEP_DISK_GUARD", raising=False)
    monkeypatch.delenv("ADAOS_SKILL_DEP_DISK_HEAVY_FREE_GIB", raising=False)
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda _path: SimpleNamespace(free=4 * 1024 * 1024 * 1024))

    with pytest.raises(RuntimeError, match="not enough free disk space"):
        guard.ensure_dependency_disk_budget(
            tmp_path,
            ["torch==2.10.0", "transformers==4.57.5"],
            skill_name="media_indexer_skill",
        )


def test_dependency_disk_guard_uses_fixed_default_budget_for_heavy_installs(monkeypatch):
    monkeypatch.delenv("ADAOS_SKILL_DEP_DISK_HEAVY_FREE_GIB", raising=False)

    required = guard.dependency_disk_budget_bytes(
        [
            "easyocr==1.7.2",
            "faiss-cpu==1.13.2",
            "opencv-python-headless==4.13.0.92",
            "sentence-transformers==5.2.0",
            "torch==2.10.0",
            "transformers==4.57.5",
        ]
    )

    assert required == 5 * 1024 * 1024 * 1024


def test_dependency_disk_guard_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("ADAOS_SKILL_DEP_DISK_GUARD", "0")
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))

    guard.ensure_dependency_disk_budget(tmp_path, ["torch==2.10.0"], skill_name="demo")


def test_dependency_disk_budget_counts_specs_not_pip_flags(monkeypatch):
    monkeypatch.delenv("ADAOS_SKILL_DEP_DISK_HEAVY_FREE_GIB", raising=False)

    required = guard.dependency_disk_budget_bytes(
        ["-r", "requirements.in", "--no-deps", "tiny-lib==1.0"],
        has_requirements_file=True,
    )

    assert required == 7 * 1024 * 1024 * 1024
