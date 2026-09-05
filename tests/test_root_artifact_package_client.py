from __future__ import annotations

from typing import Any

from adaos.services.root.client import RootHttpClient


class _Client(RootHttpClient):
    def __init__(self) -> None:
        super().__init__(verify=False, cert=("cert", "key"))
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if kwargs.get("response_bytes"):
            return b"package"
        return {"ok": True}


def test_artifact_package_registry_client_contract() -> None:
    client = _Client()
    digest = "sha256:" + "a" * 64
    plan = {"schema": "adaos.artifact.release_plan.v1"}

    client.put_artifact_package(digest=digest, archive_b64="cGFja2FnZQ==")
    client.put_artifact_package_bytes(digest=digest, archive=b"package")
    client.get_artifact_package(digest=digest)
    client.get_artifact_package_bytes(digest=digest)
    client.put_project_release(
        project_id="recipes",
        release_digest=digest,
        release_plan=plan,
    )
    client.get_project_release(project_id="recipes", release_digest=digest)
    client.set_artifact_channel(
        project_id="recipes",
        channel="stable",
        release_digest=digest,
        expected_release_digest=None,
    )
    client.get_artifact_channel(project_id="recipes")
    client.clear_artifact_channel(
        project_id="recipes",
        channel="stable",
        expected_release_digest=digest,
    )
    attestation = {"schema": "adaos.artifact.attestation.v1"}
    attestation_set = {"schema": "adaos.artifact.release_attestation_set.v1"}
    client.put_artifact_attestation(attestation=attestation)
    client.list_artifact_attestations(
        subject_kind="package",
        subject_digest=digest,
    )
    client.put_release_attestation_set(
        project_id="recipes",
        release_digest=digest,
        attestation_set=attestation_set,
    )
    client.get_release_attestation_set(
        project_id="recipes",
        release_digest=digest,
    )

    assert [(method, path) for method, path, _ in client.calls] == [
        ("POST", "/v1/artifacts/packages"),
        ("PUT", f"/v1/artifacts/packages/sha256%3A{'a' * 64}/content"),
        ("GET", f"/v1/artifacts/packages/sha256%3A{'a' * 64}"),
        ("GET", f"/v1/artifacts/packages/sha256%3A{'a' * 64}/content"),
        ("POST", "/v1/artifacts/projects/recipes/releases"),
        ("GET", f"/v1/artifacts/projects/recipes/releases/sha256%3A{'a' * 64}"),
        ("PUT", "/v1/artifacts/projects/recipes/channels/stable"),
        ("GET", "/v1/artifacts/projects/recipes/channels/stable"),
        ("DELETE", "/v1/artifacts/projects/recipes/channels/stable"),
        ("POST", "/v1/artifacts/attestations"),
        ("GET", f"/v1/artifacts/attestations/package/sha256%3A{'a' * 64}"),
        (
            "PUT",
            f"/v1/artifacts/projects/recipes/releases/sha256%3A{'a' * 64}/attestations",
        ),
        (
            "GET",
            f"/v1/artifacts/projects/recipes/releases/sha256%3A{'a' * 64}/attestations",
        ),
    ]
    assert client.calls[0][2]["cert"] == ("cert", "key")
    assert client.calls[1][2]["data"] == b"package"
    assert client.calls[1][2]["headers"] == {
        "Content-Type": "application/vnd.adaos.artifact-package+zip"
    }
    assert client.calls[1][2]["cert"] == ("cert", "key")
    assert client.calls[3][2]["response_bytes"] is True
    assert client.calls[4][2]["json"] == {
        "release_digest": digest,
        "release_plan": plan,
    }
    assert client.calls[6][2]["json"] == {
        "release_digest": digest,
        "expected_release_digest": None,
    }
    assert client.calls[8][2]["json"] == {"expected_release_digest": digest}
    assert client.calls[9][2]["json"] == {"attestation": attestation}
    assert client.calls[11][2]["json"] == {"attestation_set": attestation_set}
