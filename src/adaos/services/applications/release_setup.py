"""Compile immutable release setup without Builder session dependencies."""
from dataclasses import replace
import json
from typing import Any, Mapping
import yaml
from .setup import compile_setup_contract


def with_release_setup_contract(envelope, runtime):
    """Compile setup from immutable release manifests and access declarations."""

    component_manifests: dict[str, Mapping[str, Any]] = {}
    for package in runtime.packages:
        source = runtime.verified_source(package)
        value: Any = None
        if package.kind == "skill":
            path = source / "skill.yaml"
            if path.is_file():
                value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        elif package.kind == "scenario":
            path = source / "scenario.json"
            if path.is_file():
                value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, Mapping):
            component_manifests[f"{package.kind}:{package.artifact_id}"] = dict(value)

    connected_accounts = []
    for provider in envelope.permission_profile.external_providers:
        provider_id = str(provider.get("id") or "").strip().lower()
        if not provider_id:
            continue
        account_id = str(provider.get("account_id") or provider_id).strip().lower()
        connected_accounts.append(
            {
                "id": account_id,
                "title": str(provider.get("title") or provider_id),
                "purpose": str(
                    provider.get("purpose")
                    or f"Connect the declared {provider_id} account"
                ),
                "required": bool(provider.get("required", True)),
                "scopes": list(provider.get("scopes") or ()),
            }
        )
    contract = compile_setup_contract(
        application_id=envelope.application_id,
        release_digest=envelope.release_digest,
        component_manifests=component_manifests,
        permission_profile=envelope.permission_profile.to_dict(),
        connected_accounts=connected_accounts,
        placement_required=True,
    )
    return replace(envelope, setup_contract=contract)
