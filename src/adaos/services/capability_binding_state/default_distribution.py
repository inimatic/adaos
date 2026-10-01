"""Publication-time default resolved-distribution composition."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest

from .registry_projection import SemanticRegistryProjection


DEFAULT_DISTRIBUTION_SCHEMA = "adaos.semantic_registry.default_distribution.v1"


def _portable_evidence(
    projection: SemanticRegistryProjection, application: Mapping[str, Any]
) -> list[dict[str, Any]]:
    index = projection.read_index()
    records = dict(index.get("records") or {})
    result: list[dict[str, Any]] = []
    for digests in dict(application.get("portable_artifacts") or {}).values():
        for digest in digests or ():
            entry = records.get(str(digest))
            if not isinstance(entry, Mapping) or entry.get("schema") != "adaos.evidence.claim.v1":
                continue
            result.append(projection.read_portable_record(str(digest)))
    return result


def build_default_distribution_query(
    *,
    projection: SemanticRegistryProjection,
    project_id: str,
    project_release_digest: str,
    registry_revision: str,
    environment_profile: Mapping[str, Any],
) -> dict[str, Any]:
    application = projection.read_application_release(project_id, project_release_digest)
    requirements = [dict(item) for item in application["requirements"]]
    claims = _portable_evidence(projection, application)
    required_claim_kinds = sorted(
        {
            str(kind)
            for requirement in requirements
            for kind in dict(requirement.get("evidence_threshold") or {}).get(
                "required_claim_kinds", ()
            )
        }
    )
    observations: dict[str, dict[str, str]] = {}
    issued_at: list[str] = []
    for claim in claims:
        if str(claim.get("issued_at") or "").strip():
            issued_at.append(str(claim["issued_at"]))
        for dependency in claim.get("dependencies") or ():
            if not isinstance(dependency, Mapping):
                continue
            ref = str(dependency.get("ref") or "").strip()
            version = str(dependency.get("observed_version") or "").strip()
            if ref and version:
                observation = {"ref": ref, "observed_version": version}
                fingerprint = str(dependency.get("fingerprint") or "").strip()
                if fingerprint:
                    observation["fingerprint"] = fingerprint
                observations[ref] = observation
    as_of = max(issued_at) if issued_at else datetime.now(timezone.utc).isoformat()
    index = projection.read_index()
    return {
        "schema": "adaos.semantic_registry.query.v1",
        "query_ref": "registry-query:default-distribution/"
        + project_release_digest.removeprefix("sha256:"),
        "snapshot": {
            "registry_revision": registry_revision,
            "index_digest": index["index_digest"],
        },
        "requirements": requirements,
        "environment_profile": dict(environment_profile),
        "target_mode": "production",
        "policy_inputs": {
            # An Application declares capability requirements, not ownership of the
            # provider delivering them.  Leaving this list empty lets the registry
            # select independently published, policy/evidence-admitted bindings.
            "allowed_publisher_refs": [],
            "denied_publisher_refs": [],
            "required_authorities": sorted(
                str(authority)
                for authority in environment_profile.get("authorities", ())
            ),
        },
        "evidence_inputs": {
            "as_of": as_of,
            "required_claim_kinds": required_claim_kinds,
            "accepted_results": ["verified"],
            "dependency_observations": [observations[key] for key in sorted(observations)],
        },
    }


def default_distribution_descriptor(
    *, exported: Any, registry_revision: str
) -> dict[str, Any]:
    manifest = dict(exported.manifest)
    descriptor: dict[str, Any] = {
        "schema": DEFAULT_DISTRIBUTION_SCHEMA,
        "project_id": manifest["project_id"],
        "project_release_digest": manifest["project_release_digest"],
        "registry_revision": registry_revision,
        "registry_index_digest": dict(manifest["snapshot"])["index_digest"],
        "bundle_digest": exported.bundle_digest,
        "manifest_digest": manifest["manifest_digest"],
        "query_digest": manifest["query_digest"],
        "query_result_digest": manifest["query_result_digest"],
        "package_digests": list(manifest["package_digests"]),
        "portable_record_digests": list(manifest["portable_record_digests"]),
        "activation_performed": False,
        "local_authority_created": False,
    }
    descriptor["descriptor_digest"] = canonical_payload_digest(descriptor)
    return descriptor


__all__ = [
    "DEFAULT_DISTRIBUTION_SCHEMA",
    "build_default_distribution_query",
    "default_distribution_descriptor",
]
