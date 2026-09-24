"""Trusted projections and evidence for reusable content-addressed deliveries."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any
import zipfile

import yaml


_PACKAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_STRICT_TOOL_EFFECTS = {
    "safe",
    "none",
    "read",
    "read_only",
    "readonly",
    "ui_navigation",
    "local_write",
    "runtime_write",
    "external_write",
    "device_control",
}


def shared_delivery_interface(
    state_dir: Path, delivery: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Read a bounded public interface from an exact verified skill archive."""

    package = delivery.get("package")
    if not isinstance(package, Mapping) or package.get("kind") != "skill":
        return None
    package_digest = str(package.get("digest") or "").strip().lower()
    if not _PACKAGE_DIGEST.fullmatch(package_digest):
        return None
    digest_hex = package_digest.removeprefix("sha256:")
    archive_path = (
        Path(state_dir)
        / "artifact_pipeline"
        / "packages"
        / "sha256"
        / digest_hex[:2]
        / f"{digest_hex}.zip"
    )
    try:
        archive_bytes = archive_path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(archive_bytes).hexdigest() != digest_hex:
        return None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest_info = archive.getinfo("skill.yaml")
            package_manifest_info = archive.getinfo(".adaos/package-manifest.json")
            if (
                manifest_info.file_size > 512_000
                or package_manifest_info.file_size > 512_000
            ):
                return None
            manifest_bytes = archive.read(manifest_info)
            package_manifest_bytes = archive.read(package_manifest_info)
            handler_bytes = archive.read("handlers/main.py")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    if len(handler_bytes) > 2_000_000:
        return None
    try:
        manifest = yaml.safe_load(manifest_bytes.decode("utf-8"))
        package_manifest = json.loads(package_manifest_bytes.decode("utf-8"))
        handler_tree = ast.parse(handler_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, yaml.YAMLError, SyntaxError):
        return None
    if not isinstance(manifest, Mapping) or not isinstance(package_manifest, Mapping):
        return None
    expected_id = str(package.get("id") or "").strip()
    expected_version = str(package.get("version") or "").strip()
    if (
        str(manifest.get("name") or "").strip() != expected_id
        or str(manifest.get("version") or "").strip() != expected_version
        or str(package_manifest.get("artifact_id") or "").strip() != expected_id
        or str(package_manifest.get("version") or "").strip() != expected_version
    ):
        return None
    exports = manifest.get("exports")
    if not isinstance(exports, Mapping):
        exports = {}
    exported = {
        str(name).strip() for name in exports.get("tools") or [] if str(name).strip()
    }
    tools = [
        dict(tool)
        for tool in manifest.get("tools") or []
        if isinstance(tool, Mapping) and str(tool.get("name") or "").strip() in exported
    ]
    entry_symbols: list[dict[str, Any]] = []
    for node in handler_tree.body:
        if (
            not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            or node.name not in exported
        ):
            continue
        parameters = [
            item.arg
            for item in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            )
            if item.arg not in {"self", "cls"}
        ]
        if node.args.vararg is not None:
            parameters.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg is not None:
            parameters.append(f"**{node.args.kwarg.arg}")
        entry_symbols.append(
            {
                "name": node.name,
                "async": isinstance(node, ast.AsyncFunctionDef),
                "parameters": parameters,
            }
        )
    public_manifest = {
        key: copy.deepcopy(manifest[key])
        for key in (
            "name",
            "version",
            "description",
            "default_tool",
            "dependencies",
            "capabilities",
            "exports",
            "data_routes",
            "data_lifecycle",
        )
        if key in manifest
    }
    public_manifest["tools"] = tools
    interface: dict[str, Any] = {
        "schema": "adaos.builder.shared_delivery_interface.v1",
        "authority": "content_addressed_package_archive",
        "package": dict(package),
        "archive_digest_verified": True,
        "skill_manifest_digest": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        "package_manifest_digest": "sha256:"
        + hashlib.sha256(package_manifest_bytes).hexdigest(),
        "public_manifest": public_manifest,
        "entry_symbols": sorted(entry_symbols, key=lambda item: item["name"]),
        "consumer_test_seam": {
            "mode": "mock_exported_tool_boundary",
            "instruction": (
                "Consumer tests must fake exported tool replies using these exact "
                "input/output schemas. Do not import, copy, or modify provider source."
            ),
            "provider_conformance_owner": expected_id,
        },
    }
    interface["interface_digest"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                interface,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    return interface


def portable_contract_reuse_bundle(
    state_dir: Path, webui: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Project installed portable contracts into a bounded authoring input."""

    from adaos.services.builder.cbs_intent import validate_cbs_intent
    from adaos.services.builder.prototype_stage import prototype_cbs_intent
    from adaos.services.capability_binding_state import PortableContractCatalog

    raw_intent = prototype_cbs_intent(webui)
    if raw_intent is None:
        return None
    intent = validate_cbs_intent(raw_intent)
    catalog = PortableContractCatalog(
        Path(state_dir) / "capability-binding-state" / "portable"
    )
    requirements: list[dict[str, Any]] = []
    for raw_requirement in intent.get("requirements") or []:
        requirement = dict(raw_requirement)
        matches = catalog.matching_capabilities(
            str(requirement["capability_ref"]),
            str(requirement["contract_range"]),
        )
        if not matches:
            continue
        selected = matches[0]
        reusable_bindings = []
        for binding in catalog.matching_bindings(
            selected.capability_ref, selected.version
        ):
            deliveries = catalog.deliveries_for_binding(binding.digest)
            delivery_values = [item.to_dict() for item in deliveries]
            reusable_binding: dict[str, Any] = {
                "binding_definition": binding.to_dict(),
                "deliveries": delivery_values,
            }
            delivery_interfaces = [
                interface
                for item in delivery_values
                if (interface := shared_delivery_interface(Path(state_dir), item))
                is not None
            ]
            if delivery_interfaces:
                reusable_binding["delivery_interfaces"] = delivery_interfaces
            reusable_bindings.append(reusable_binding)
        requirements.append(
            {
                "requirement_id": str(requirement["id"]),
                "capability_ref": selected.capability_ref,
                "contract_range": str(requirement["contract_range"]),
                "selected_version": selected.version,
                "selected_digest": selected.digest,
                "contract": selected.to_dict(),
                "reusable_bindings": reusable_bindings,
            }
        )
    if not requirements:
        return None
    return {
        "schema": "adaos.builder.portable_contract_reuse.v1",
        "policy": (
            "An installed identity is canonical. Generate the exact same "
            "CapabilityContract digest by mapping conforming adapter tools, or "
            "use an explicit incompatible major identity outside the accepted "
            "requirement. Never relabel incompatible schemas as the installed "
            "version. Prefer an admitted reusable delivery over creating another "
            "physical provider. Treat its package as a shared dependency and do "
            "not modify or copy it. Application-specific presentation tools may "
            "remain outside the capability operation mapping."
        ),
        "requirements": requirements,
    }


def shared_delivery_effect_checks(
    state_dir: Path,
    *,
    project: Mapping[str, Any],
    webui: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Verify strict effects metadata for exact shared CBS dependencies.

    The consumer source only selects an exact shared package. Provider-owned
    effects are therefore proved from the selected content-addressed archive,
    not by asking the consumer to copy the provider manifest or its tests.
    """

    components = project.get("components")
    dependencies = (
        components.get("dependencies") if isinstance(components, Mapping) else None
    )
    exact_dependencies: dict[tuple[str, str], dict[str, Any]] = {}
    for item in dependencies or ():
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("ref") or "").strip()
        version_spec = str(item.get("version") or "").strip()
        if not ref.startswith("skill:") or not version_spec.startswith("=="):
            continue
        skill_id = ref.split(":", 1)[1].strip()
        version = version_spec.removeprefix("==").strip()
        if skill_id and version:
            exact_dependencies[(skill_id, version)] = dict(item)
    if not exact_dependencies:
        return [], []

    bundle = portable_contract_reuse_bundle(Path(state_dir), webui)
    if not isinstance(bundle, Mapping):
        return [], []

    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_packages: set[tuple[str, str, str]] = set()
    for requirement in bundle.get("requirements") or ():
        if not isinstance(requirement, Mapping):
            continue
        for reusable in requirement.get("reusable_bindings") or ():
            if not isinstance(reusable, Mapping):
                continue
            interfaces_by_digest = {
                str(dict(interface.get("package") or {}).get("digest") or ""): interface
                for interface in reusable.get("delivery_interfaces") or ()
                if isinstance(interface, Mapping)
            }
            binding = (
                reusable.get("binding_definition")
                if isinstance(reusable.get("binding_definition"), Mapping)
                else {}
            )
            for delivery in reusable.get("deliveries") or ():
                if not isinstance(delivery, Mapping):
                    continue
                package = (
                    delivery.get("package")
                    if isinstance(delivery.get("package"), Mapping)
                    else {}
                )
                package_key = (
                    str(package.get("id") or "").strip(),
                    str(package.get("version") or "").strip(),
                )
                if package_key not in exact_dependencies:
                    continue
                digest = str(package.get("digest") or "").strip().lower()
                identity = (*package_key, digest)
                if identity in seen_packages:
                    continue
                seen_packages.add(identity)
                interface = interfaces_by_digest.get(digest)
                package_ref = (
                    f"package:skill/{package_key[0]}@{package_key[1]}#{digest}"
                )
                if not isinstance(interface, Mapping):
                    errors.append(
                        f"{package_ref}: exact shared delivery archive is missing, "
                        "corrupt, or does not match its package identity"
                    )
                    continue
                public_manifest = interface.get("public_manifest")
                tools = (
                    public_manifest.get("tools")
                    if isinstance(public_manifest, Mapping)
                    else None
                )
                violations: list[str] = []
                if not isinstance(tools, list) or not tools:
                    violations.append("no exported public tools")
                    tools = []
                for index, tool in enumerate(tools):
                    if not isinstance(tool, Mapping):
                        violations.append(f"tools[{index}]: invalid declaration")
                        continue
                    name = str(tool.get("name") or f"tools[{index}]").strip()
                    side_effects = (
                        str(tool.get("side_effects") or "")
                        .strip()
                        .lower()
                        .replace("-", "_")
                    )
                    if not side_effects:
                        violations.append(f"{name}: missing side_effects")
                    elif side_effects not in _STRICT_TOOL_EFFECTS:
                        violations.append(
                            f"{name}: unsupported side_effects {side_effects!r}"
                        )
                if violations:
                    errors.append(
                        f"{package_ref}: shared public tool effect contract is incomplete: "
                        + "; ".join(violations)
                    )
                    continue
                checks.append(
                    {
                        "kind": "shared_delivery.public_tool_effects.strict",
                        "path": package_ref,
                        "ok": True,
                        "tools": len(tools),
                        "package": dict(package),
                        "interface_digest": str(
                            interface.get("interface_digest") or ""
                        ),
                        "skill_manifest_digest": str(
                            interface.get("skill_manifest_digest") or ""
                        ),
                        "capability_ref": str(requirement.get("capability_ref") or ""),
                        "binding_definition_digest": str(
                            delivery.get("binding_definition_digest")
                            or binding.get("digest")
                            or ""
                        ),
                    }
                )
    return checks, errors


__all__ = [
    "portable_contract_reuse_bundle",
    "shared_delivery_effect_checks",
    "shared_delivery_interface",
]
