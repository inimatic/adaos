from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.services.builder.sources import BuilderProjectSourceService


def _service(tmp_path: Path) -> BuilderProjectSourceService:
    return BuilderProjectSourceService(state_dir=tmp_path, max_source_bytes=1024 * 1024)


def test_source_bundle_is_content_addressed_immutable_and_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.add_bytes(
        kind="skill",
        project_id="tlp_direction_skill",
        name="vision.md",
        payload="Первая постановка".encode(),
        media_type="text/markdown",
        role="review",
    )
    repeated = service.add_bytes(
        kind="skill",
        project_id="tlp_direction_skill",
        name="renamed.md",
        payload="Первая постановка".encode(),
        media_type="text/markdown",
        role="review",
    )
    second = service.add_bytes(
        kind="skill",
        project_id="tlp_direction_skill",
        name="notes.txt",
        payload=b"independent notes",
    )

    assert first["bundle"]["generation"] == 1
    assert repeated["idempotent"] is True
    assert repeated["bundle"]["digest"] == first["bundle"]["digest"]
    assert second["bundle"]["generation"] == 2
    assert second["bundle"]["digest"] != first["bundle"]["digest"]
    assert service.get_bundle(first["bundle"]["digest"]) == first["bundle"]
    assert service.read_source(first["source"]["digest"]) == "Первая постановка".encode()


def test_notebook_inventory_is_structural_and_marks_outputs_untrusted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "cells": [
            {"cell_type": "markdown", "source": ["# Hypothesis"], "metadata": {}},
            {
                "cell_type": "code",
                "source": ["import torch, numpy as np\nfrom pathlib import Path\n"],
                "execution_count": 2,
                "outputs": [{"output_type": "stream", "text": ["done"]}],
                "metadata": {},
            },
        ],
    }
    result = service.add_bytes(
        kind="skill",
        project_id="demo",
        name="study.ipynb",
        payload=json.dumps(notebook).encode(),
        media_type="application/x-ipynb+json",
    )
    analysis = result["source"]["analysis"]
    assert analysis["valid"] is True
    assert analysis["code_cells"] == 1
    assert analysis["output_records"] == 1
    assert {"torch", "numpy", "pathlib"}.issubset(set(analysis["imports"]))
    assert analysis["warnings"] == ["notebook_outputs_are_untrusted_source_material"]


def test_source_names_and_size_are_bounded(tmp_path: Path) -> None:
    service = BuilderProjectSourceService(state_dir=tmp_path, max_source_bytes=4)
    with pytest.raises(ValueError, match="plain file name"):
        service.add_bytes(kind="skill", project_id="demo", name="../escape.txt", payload=b"x")
    with pytest.raises(ValueError, match="max size"):
        service.add_bytes(kind="skill", project_id="demo", name="large.txt", payload=b"12345")

    count_bounded = BuilderProjectSourceService(
        state_dir=tmp_path / "count-bounded",
        max_source_bytes=4,
        max_sources_per_project=1,
    )
    count_bounded.add_bytes(kind="skill", project_id="demo", name="one.txt", payload=b"one")
    with pytest.raises(ValueError, match="source count exceeds limit"):
        count_bounded.add_bytes(kind="skill", project_id="demo", name="two.txt", payload=b"two")


def test_source_bundle_matches_published_abi(tmp_path: Path) -> None:
    service = _service(tmp_path)
    bundle = service.add_bytes(
        kind="skill", project_id="demo", name="source.txt", payload=b"source"
    )["bundle"]
    schema_path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / "builder.source_bundle.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(bundle)


def test_builder_source_api_uploads_lists_and_reads_current_bundle(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_project_source_service] = lambda: service
    client = TestClient(app)

    upload = client.put(
        "/api/builder/projects/skill/tlp_direction_skill/sources/vision.md",
        params={"role": "review"},
        content="Проверяемая постановка".encode(),
        headers={"content-type": "text/markdown"},
    )
    assert upload.status_code == 200
    uploaded = upload.json()
    digest = uploaded["source"]["digest"]

    listed = client.get("/api/builder/projects/skill/tlp_direction_skill/sources")
    assert listed.status_code == 200
    assert listed.json()["bundle"]["digest"] == uploaded["bundle"]["digest"]

    content = client.get(
        f"/api/builder/projects/skill/tlp_direction_skill/sources/{digest.removeprefix('sha256:')}/content"
    )
    assert content.status_code == 200
    assert content.content == "Проверяемая постановка".encode()

    service.max_source_bytes = 4
    too_large = client.put(
        "/api/builder/projects/skill/tlp_direction_skill/sources/large.txt",
        content=b"12345",
    )
    assert too_large.status_code == 413


def test_builder_artifact_api_streams_only_manifest_resolved_content(monkeypatch, tmp_path: Path) -> None:
    artifact = tmp_path / "review.pdf"
    artifact.write_bytes(b"%PDF-1.7\npreview")

    def _resolve(skill_id: str, group_id: str, artifact_id: str) -> dict[str, object]:
        assert (skill_id, group_id, artifact_id) == ("tlp_direction", "part0", "artifact-abcd")
        return {
            "artifact_id": artifact_id,
            "path": artifact.name,
            "native_path": str(artifact),
            "media_type": "application/octet-stream",
        }

    monkeypatch.setattr(builder_api.artifact_context, "resolve", _resolve)
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)

    response = client.get(
        "/api/builder/projects/skill/tlp_direction/artifacts/part0/artifact-abcd/content"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == artifact.read_bytes()
