from pathlib import Path

import pytest

from adaos.domain.blob_storage import BlobStorageRequirements
from adaos.sdk.data.blob import store
from adaos.services.storage.blob import (
    BlobStorageBroker,
    LocalBlobStorageProvider,
    ProvisionedBlobStorageProvider,
)


def test_blob_broker_keeps_local_binding_opaque_and_owner_scoped(tmp_path: Path) -> None:
    broker = BlobStorageBroker((LocalBlobStorageProvider(),))
    binding = broker.bind(
        owner_ref="skill:fixture",
        logical_name="artifacts",
        requirements=BlobStorageRequirements(locality="any"),
        scope_root=tmp_path,
    )

    assert binding.provider_id == "filesystem"
    assert binding.locator == "skill-data:files/artifacts"
    assert "://" not in str(binding.to_dict())
    assert broker.service_uri(binding, owner_ref="skill:fixture") == (tmp_path / "artifacts").resolve().as_uri()


def test_local_blob_bindings_are_distinct_per_runtime_scope(tmp_path: Path) -> None:
    broker = BlobStorageBroker((LocalBlobStorageProvider(),))
    requirements = BlobStorageRequirements(locality="any")
    first = broker.bind(
        owner_ref="skill:fixture",
        logical_name="artifacts",
        requirements=requirements,
        scope_root=tmp_path / "stable",
    )
    second = broker.bind(
        owner_ref="skill:fixture",
        logical_name="artifacts",
        requirements=requirements,
        scope_root=tmp_path / "dev",
    )

    assert first.binding_id != second.binding_id
    assert broker.service_uri(first, owner_ref=first.owner_ref) != broker.service_uri(
        second,
        owner_ref=second.owner_ref,
    )


def test_blob_broker_prefers_provisioned_object_binding_without_exposing_uri(tmp_path: Path) -> None:
    broker = BlobStorageBroker(
        (
            LocalBlobStorageProvider(),
            ProvisionedBlobStorageProvider("s3://adaos-research", secret_ref="core:storage/blob"),
        )
    )
    binding = broker.bind(
        owner_ref="skill:mlflow_tracker_skill",
        logical_name="artifacts",
        requirements=BlobStorageRequirements(locality="any"),
        scope_root=tmp_path,
        prefer_provisioned=True,
    )

    assert binding.provider_id == "object"
    assert binding.secret_ref == "core:storage/blob"
    assert "s3://" not in str(binding.to_dict())
    assert broker.service_uri(binding, owner_ref=binding.owner_ref).startswith("s3://adaos-research/")


def test_sdk_blob_store_is_content_addressed_and_owner_isolated(_autocontext) -> None:
    ctx = _autocontext
    for name in ("alpha_skill", "beta_skill"):
        source = Path(ctx.paths.skills_dir()) / name
        source.mkdir(parents=True, exist_ok=True)
        (source / "skill.yaml").write_text(
            f"name: {name}\nversion: 0.1.0\ncapabilities:\n  - storage.blob\n",
            encoding="utf-8",
        )
    assert ctx.skill_ctx.set("alpha_skill", Path(ctx.paths.skills_dir()) / "alpha_skill")
    alpha = store("calibrations")
    blob = alpha.put_json("contract.json", {"accepted": True})
    path = alpha.materialize_path(blob)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == '{"accepted":true}'
    assert blob["digest"].startswith("sha256:")

    assert ctx.skill_ctx.set("beta_skill", Path(ctx.paths.skills_dir()) / "beta_skill")
    beta = store("calibrations")
    assert beta.put_json("contract.json", {"accepted": True})["binding_id"] != blob["binding_id"]
    with pytest.raises(ValueError, match="another active skill"):
        alpha.materialize_path(blob)
