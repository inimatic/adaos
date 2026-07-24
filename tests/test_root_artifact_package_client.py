from __future__ import annotations

from typing import Any

from adaos.services.root.client import RootHttpClient


class _Client(RootHttpClient):
    def __init__(self) -> None:
        super().__init__(verify=False, cert=("cert", "key"))
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, path, kwargs))
        return {"ok": True}


def test_artifact_package_registry_client_contract() -> None:
    client = _Client()
    digest = "sha256:" + "a" * 64
    plan = {"schema": "adaos.artifact.release_plan.v1"}

    client.put_artifact_package(digest=digest, archive_b64="cGFja2FnZQ==")
    client.get_artifact_package(digest=digest)
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
    )
    client.get_artifact_channel(project_id="recipes")

    assert [(method, path) for method, path, _ in client.calls] == [
        ("POST", "/v1/artifacts/packages"),
        ("GET", f"/v1/artifacts/packages/sha256%3A{'a' * 64}"),
        ("POST", "/v1/artifacts/projects/recipes/releases"),
        ("GET", f"/v1/artifacts/projects/recipes/releases/sha256%3A{'a' * 64}"),
        ("PUT", "/v1/artifacts/projects/recipes/channels/stable"),
        ("GET", "/v1/artifacts/projects/recipes/channels/stable"),
    ]
    assert client.calls[0][2]["cert"] == ("cert", "key")
    assert client.calls[2][2]["json"] == {
        "release_digest": digest,
        "release_plan": plan,
    }
