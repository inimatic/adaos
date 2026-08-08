from pathlib import Path

from adaos.domain.blob_storage import BlobStorageRequirements
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
