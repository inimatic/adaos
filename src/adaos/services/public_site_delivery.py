from __future__ import annotations

from dataclasses import dataclass
import json
import mimetypes
from pathlib import Path
import re
from typing import Any, Mapping, Protocol, Sequence

from adaos.services.site_studio.contracts import (
    SiteStudioContractError,
    canonical_json,
    digest,
    validate_contract,
)


_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_REQUIRED = {
    "site.deployment.json",
    "site.projection.json",
    "components/release-set.json",
    ".well-known/adaos-site.json",
    "sitemap.xml",
    "robots.txt",
}
_MEDIA_TYPES = {
    ".css": "text/css",
    ".html": "text/html",
    ".js": "text/javascript",
    ".json": "application/json",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".webm": "video/webm",
    ".xml": "application/xml",
}


class PublicSiteDeliveryError(ValueError):
    pass


class PublicSiteDeliveryAdapter(Protocol):
    """Provider-neutral PS3 delivery boundary.

    Implementations may return provider receipts, but provider identifiers do
    not participate in any canonical site, bundle, pointer, or Client identity.
    """

    def put_immutable_asset(
        self, artifact: Mapping[str, Any], payload: bytes
    ) -> Mapping[str, Any]: ...

    def put_projection(
        self, artifact: Mapping[str, Any], payload: bytes
    ) -> Mapping[str, Any]: ...

    def put_component_package(
        self, artifact: Mapping[str, Any], payload: bytes
    ) -> Mapping[str, Any]: ...

    def put_component_release_set(
        self, artifact: Mapping[str, Any], payload: bytes
    ) -> Mapping[str, Any]: ...

    def put_deployment(
        self, artifact: Mapping[str, Any], payload: bytes
    ) -> Mapping[str, Any]: ...

    def publish_deployment_pointer(
        self,
        pointer: Mapping[str, Any],
        *,
        expected_pointer_digest: str | None,
    ) -> Mapping[str, Any]: ...

    def resolve_request(self, host: str, path: str) -> Mapping[str, Any] | None: ...

    def verify_domain(
        self, hostname: str, proof: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def bind_domain(
        self, hostname: str, site_id: str, *, base_path: str = "/"
    ) -> Mapping[str, Any]: ...

    def purge_or_revalidate(self, urls: Sequence[str]) -> Mapping[str, Any]: ...

    def report_delivery_health(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PublicSiteDeliveryBundle:
    manifest: Mapping[str, Any]
    payloads: Mapping[str, bytes]

    @property
    def deployment_digest(self) -> str:
        return str(self.manifest["deployment_digest"])

    @property
    def site_id(self) -> str:
        return str(self.manifest["site_id"])


def build_delivery_bundle(root: Path) -> PublicSiteDeliveryBundle:
    """Admit a compiled public-site directory as an immutable delivery bundle."""

    root = root.resolve(strict=True)
    if not root.is_dir():
        raise PublicSiteDeliveryError("public-site bundle root must be a directory")
    files: dict[str, Path] = {}
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise PublicSiteDeliveryError("public-site bundles cannot contain symlinks")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        _validate_relative_path(relative)
        files[relative] = candidate
    missing = sorted(_REQUIRED - files.keys())
    if missing:
        raise PublicSiteDeliveryError(f"public-site bundle is incomplete: {missing[0]}")

    deployment = _read_json(files["site.deployment.json"])
    projection = _read_json(files["site.projection.json"])
    release_set = _read_json(files["components/release-set.json"])
    _verify_semantic_artifact(
        deployment, "deployment_digest", "public_site_deployment.v1.schema.json"
    )
    _verify_semantic_artifact(
        projection, "projection_digest", "site_projection.v1.schema.json"
    )
    _verify_semantic_artifact(
        release_set, "set_digest", "site_component_release_set.v1.schema.json"
    )
    if (
        deployment["site_id"] != projection["site_id"]
        or deployment["projection_digest"] != projection["projection_digest"]
        or deployment["component_release_set"]["id"] != release_set["set_id"]
        or deployment["component_release_set"]["digest"] != release_set["set_digest"]
        or deployment["renderer_abi"] != release_set["renderer_abi"]
    ):
        raise PublicSiteDeliveryError("deployment, projection, and component set disagree")

    required_packages = {
        (entry["family"], entry["digest"])
        for entry in deployment["required_packages"]
    }
    admitted_packages: set[tuple[str, str]] = set()
    for entry in release_set["packages"]:
        ref = str(entry["artifact_ref"])
        package_path = files.get(ref)
        if package_path is None:
            raise PublicSiteDeliveryError(f"component package is missing: {ref}")
        package = _read_json(package_path)
        _verify_semantic_artifact(
            package, "digest", "site_component_package.v1.schema.json"
        )
        if (
            package["family"] != entry["family"]
            or package["package_id"] != entry["package_id"]
            or package["version"] != entry["version"]
            or package["digest"] != entry["digest"]
        ):
            raise PublicSiteDeliveryError(f"component package lock mismatch: {ref}")
        admitted_packages.add((package["family"], package["digest"]))
    if not required_packages.issubset(admitted_packages):
        raise PublicSiteDeliveryError("a required component package is unavailable")

    _verify_projection_references(root, files, projection)
    _verify_assets(files, projection, deployment)

    payloads: dict[str, bytes] = {}
    artifacts = []
    for relative, path in sorted(files.items()):
        raw = path.read_bytes()
        object_digest = digest(raw)
        payloads[object_digest] = raw
        artifacts.append(
            {
                "path": relative,
                "digest": object_digest,
                "size_bytes": len(raw),
                "media_type": _media_type(path),
                "kind": _artifact_kind(relative),
            }
        )
    body = {
        "schema": "adaos.public_site_delivery_bundle.v1",
        "layout": "adaos.public-site.bundle.v1",
        "site_id": deployment["site_id"],
        "deployment_digest": deployment["deployment_digest"],
        "projection_digest": projection["projection_digest"],
        "component_set_digest": release_set["set_digest"],
        "renderer_abi": deployment["renderer_abi"],
        "artifacts": artifacts,
    }
    manifest = {**body, "bundle_digest": digest(body)}
    validate_contract("public_site_delivery_bundle.v1.schema.json", manifest)
    return PublicSiteDeliveryBundle(manifest=manifest, payloads=payloads)


def build_delivery_pointer(
    bundle: PublicSiteDeliveryBundle,
    client_release: Mapping[str, Any],
    *,
    published_at: str,
    generation: int,
    previous_pointer_digest: str | None = None,
) -> dict[str, Any]:
    release = dict(client_release)
    _verify_semantic_artifact(
        release, "release_digest", "public_site_client_release.v1.schema.json"
    )
    if release["renderer_abi"] != bundle.manifest["renderer_abi"]:
        raise PublicSiteDeliveryError("Client release renderer ABI mismatch")
    if generation < 1:
        raise PublicSiteDeliveryError("pointer generation must be positive")
    if previous_pointer_digest and not _DIGEST.fullmatch(previous_pointer_digest):
        raise PublicSiteDeliveryError("previous pointer digest is invalid")
    body = {
        "schema": "adaos.public_site_delivery_pointer.v1",
        "site_id": bundle.site_id,
        "deployment_digest": bundle.deployment_digest,
        "bundle_digest": bundle.manifest["bundle_digest"],
        "renderer_abi": bundle.manifest["renderer_abi"],
        "client_release": release,
        "artifacts": list(bundle.manifest["artifacts"]),
        "published_at": published_at,
        "generation": generation,
        **(
            {"previous_pointer_digest": previous_pointer_digest}
            if previous_pointer_digest
            else {}
        ),
    }
    pointer = {**body, "pointer_digest": digest(body)}
    validate_contract("public_site_delivery_pointer.v1.schema.json", pointer)
    return pointer


def publish_delivery(
    adapter: PublicSiteDeliveryAdapter,
    bundle: PublicSiteDeliveryBundle,
    pointer: Mapping[str, Any],
    *,
    expected_pointer_digest: str | None = None,
) -> dict[str, Any]:
    """Upload immutable content before the single mutable pointer transition."""

    if pointer.get("bundle_digest") != bundle.manifest["bundle_digest"]:
        raise PublicSiteDeliveryError("pointer does not address the admitted bundle")
    if expected_pointer_digest and not _DIGEST.fullmatch(expected_pointer_digest):
        raise PublicSiteDeliveryError("expected pointer digest is invalid")
    operations = []
    methods = {
        "asset": adapter.put_immutable_asset,
        "projection": adapter.put_projection,
        "component_package": adapter.put_component_package,
        "component_release_set": adapter.put_component_release_set,
        "deployment": adapter.put_deployment,
    }
    for artifact in bundle.manifest["artifacts"]:
        artifact = dict(artifact)
        payload = bundle.payloads[str(artifact["digest"])]
        receipt = dict(methods[str(artifact["kind"])](artifact, payload))
        operations.append(
            {
                "path": artifact["path"],
                "digest": artifact["digest"],
                "status": receipt.get("status", "confirmed"),
            }
        )
    pointer_receipt = dict(
        adapter.publish_deployment_pointer(
            pointer, expected_pointer_digest=expected_pointer_digest
        )
    )
    return {
        "schema": "adaos.public_site_delivery_receipt.v1",
        "site_id": bundle.site_id,
        "deployment_digest": bundle.deployment_digest,
        "bundle_digest": bundle.manifest["bundle_digest"],
        "pointer_digest": pointer["pointer_digest"],
        "operations": operations,
        "pointer_status": pointer_receipt.get("status", "confirmed"),
        "provider_receipt": pointer_receipt,
    }


def _verify_projection_references(
    root: Path, files: Mapping[str, Path], projection: Mapping[str, Any]
) -> None:
    for route in projection["routes"]:
        for ref_key, digest_key in (
            ("artifact_ref", "artifact_digest"),
            ("page_graph_ref", "page_graph_digest"),
            ("source_map_ref", "source_map_digest"),
        ):
            ref = str(route[ref_key])
            path = files.get(ref)
            if path is None:
                raise PublicSiteDeliveryError(f"projection artifact is missing: {ref}")
            if digest(_read_json(path)) != route[digest_key]:
                raise PublicSiteDeliveryError(f"projection artifact digest mismatch: {ref}")
    for resource in projection["resources"]:
        ref = str(resource["ref"])
        path = files.get(ref)
        if path is None or digest(_read_json(path)) != resource["digest"]:
            raise PublicSiteDeliveryError(f"resource artifact digest mismatch: {ref}")
    discovery_ref = str(projection["discovery_ref"])
    if discovery_ref not in files:
        raise PublicSiteDeliveryError("site discovery artifact is missing")
    del root


def _verify_assets(
    files: Mapping[str, Path],
    projection: Mapping[str, Any],
    deployment: Mapping[str, Any],
) -> None:
    declared = sorted(str(asset["digest"]) for asset in projection["assets"])
    if declared != sorted(str(value) for value in deployment["asset_digests"]):
        raise PublicSiteDeliveryError("deployment asset lock mismatch")
    for asset in projection["assets"]:
        ref = str(asset["public_path"])
        path = files.get(ref)
        if path is None:
            raise PublicSiteDeliveryError(f"public asset is missing: {ref}")
        raw = path.read_bytes()
        if digest(raw) != asset["digest"] or len(raw) != asset["size_bytes"]:
            raise PublicSiteDeliveryError(f"public asset digest mismatch: {ref}")


def _verify_semantic_artifact(
    value: Mapping[str, Any], digest_key: str, schema_name: str
) -> None:
    validate_contract(schema_name, value)
    expected = str(value.get(digest_key) or "")
    body = {key: item for key, item in value.items() if key != digest_key}
    if not _DIGEST.fullmatch(expected) or digest(body) != expected:
        raise PublicSiteDeliveryError(f"{schema_name} semantic digest mismatch")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicSiteDeliveryError(f"invalid JSON artifact: {path.name}") from error
    if not isinstance(value, dict):
        raise PublicSiteDeliveryError(f"JSON artifact must be an object: {path.name}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicSiteDeliveryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_relative_path(value: str) -> None:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
    ):
        raise PublicSiteDeliveryError(f"invalid public artifact path: {value}")


def _artifact_kind(path: str) -> str:
    if path == "site.projection.json":
        return "projection"
    if path == "site.deployment.json":
        return "deployment"
    if path == "components/release-set.json":
        return "component_release_set"
    if path.startswith("components/packages/") and path.endswith(".package.json"):
        return "component_package"
    return "asset"


def _media_type(path: Path) -> str:
    media_type = _MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    return media_type or "application/octet-stream"


def canonical_payload(value: Mapping[str, Any]) -> bytes:
    """Canonical JSON representation used by delivery adapters and tests."""

    try:
        return (canonical_json(value) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SiteStudioContractError("delivery payload is not canonical JSON") from error
