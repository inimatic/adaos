from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.skill.artifacts import (
    resolve_skill_file_path,
    safe_upload_relative_path,
    skill_upload_max_bytes,
    skill_upload_dir,
    store_skill_upload,
)
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


def test_safe_upload_relative_path_removes_traversal() -> None:
    assert safe_upload_relative_path("../../model final.pth").as_posix() == "model_final.pth"
    assert safe_upload_relative_path("nested\\frames.zip").as_posix() == "nested/frames.zip"
    assert safe_upload_relative_path("").as_posix() == "upload.bin"


def test_skill_upload_max_bytes_defaults_to_large_local_dataset_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_SKILL_UPLOAD_MAX_BYTES", raising=False)
    assert skill_upload_max_bytes() == 1024 * 1024 * 1024


def test_skill_upload_max_bytes_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAOS_SKILL_UPLOAD_MAX_BYTES", "12345")
    assert skill_upload_max_bytes() == 12345


@pytest.mark.anyio
async def test_store_skill_upload_writes_skill_owned_file(tmp_path: Path) -> None:
    async def chunks():
        yield b"abc"
        yield b"def"

    result = await store_skill_upload(
        skills_root=tmp_path / "skills",
        skill_name="demo_skill",
        filename="frames.zip",
        chunks=chunks(),
        purpose="frames",
        content_type="application/zip",
        max_bytes=1024,
    )

    artifact = result["artifact_ref"]
    stored = Path(artifact["path"])
    assert stored.read_bytes() == b"abcdef"
    assert skill_upload_dir(tmp_path / "skills", "demo_skill", purpose="frames") in stored.parents
    assert artifact["skill"] == "demo_skill"
    assert artifact["purpose"] == "frames"
    assert artifact["kind"] == "skill_file"
    assert artifact["artifact_id"].startswith("skill_file:demo_skill:frames:")
    assert artifact["relative_path"] == "uploads/frames/frames.zip"
    assert artifact["uri"].startswith("file:")
    assert artifact["mime"] == "application/zip"
    assert artifact["size_bytes"] == 6


def test_skill_upload_dir_rejects_invalid_skill_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid skill name"):
        skill_upload_dir(tmp_path / "skills", "../demo", purpose="frames")


def test_resolve_skill_file_path_stays_inside_skill_files_dir(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name="demo_skill")

    target = resolve_skill_file_path(
        skills_root=skills_root,
        skill_name="demo_skill",
        relative_path="uploads/photos/pic.gif",
    )

    assert target == (env.files_dir() / "uploads" / "photos" / "pic.gif").resolve()


def test_resolve_skill_file_path_rejects_unsafe_paths(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    for relative_path in ("../pic.gif", "/uploads/pic.gif", "uploads//pic.gif", "C:/tmp/pic.gif"):
        with pytest.raises(ValueError):
            resolve_skill_file_path(
                skills_root=skills_root,
                skill_name="demo_skill",
                relative_path=relative_path,
            )


@pytest.mark.anyio
async def test_store_skill_upload_rejects_oversized_payload(tmp_path: Path) -> None:
    async def chunks():
        yield b"abcdef"

    with pytest.raises(ValueError, match="max size"):
        await store_skill_upload(
            skills_root=tmp_path / "skills",
            skill_name="demo_skill",
            filename="frames.zip",
            chunks=chunks(),
            max_bytes=3,
        )
