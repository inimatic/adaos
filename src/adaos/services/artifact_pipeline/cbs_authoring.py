"""Compact, package-local authoring for native CBS providers.

The author writes one descriptor and references already declared skill tools.
The compiler owns canonical contract boilerplate and produces a package-neutral
``BindingDefinition`` plus a package-bound delivery template.  The exact
``BindingDelivery`` is created only after the immutable package digest exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    canonical_json_bytes,
    sha256_digest,
)
from adaos.domain.capability_binding_state import (
    BindingDefinition,
    BindingDelivery,
    CapabilityBindingStateContractError,
    CapabilityContract,
)


CBS_PROVIDER_AUTHORING_SCHEMA = "adaos.cbs.provider_authoring.v1"
CBS_PROVIDER_COMPILATION_SCHEMA = "adaos.cbs.provider_compilation.v1"
CBS_PROVIDER_COMPILER_ID = "adaos.cbs.provider_compiler.v1"
CBS_PROVIDER_AUTHORING_PATH = "contracts/provider.cbs.yaml"
CAPABILITY_OUTPUT_PATH = "contracts/capability.contract.json"
BINDING_OUTPUT_PATH = "contracts/binding.definition.json"
_GENERATED_PATHS = (CAPABILITY_OUTPUT_PATH, BINDING_OUTPUT_PATH)


class CBSProviderAuthoringError(ValueError):
    """The compact provider descriptor cannot produce trusted CBS records."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise CBSProviderAuthoringError(f"YAML contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@lru_cache(maxsize=1)
def _authoring_validator() -> Draft202012Validator:
    path = (
        Path(__file__).resolve().parents[2]
        / "abi"
        / "cbs.provider_authoring.v1.schema.json"
    )
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CBSProviderAuthoringError(f"{field} must be an object")
    return dict(value)


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


@dataclass(frozen=True, slots=True)
class CBSProviderCompilation:
    capability: CapabilityContract
    binding: BindingDefinition
    generated_files: Mapping[str, bytes]
    package_metadata: Mapping[str, Any]


def compile_cbs_provider_files(
    files: Mapping[str, bytes],
    *,
    kind: str,
    allow_generated_outputs: bool = False,
) -> CBSProviderCompilation | None:
    """Compile one bounded provider descriptor from package source bytes."""

    raw_descriptor = files.get(CBS_PROVIDER_AUTHORING_PATH)
    if raw_descriptor is None:
        return None
    if kind != "skill":
        raise CBSProviderAuthoringError(
            "compact CBS provider authoring is supported only for skill packages"
        )
    if not allow_generated_outputs:
        collisions = sorted(path for path in _GENERATED_PATHS if path in files)
        if collisions:
            raise CBSProviderAuthoringError(
                "compiler-owned CBS outputs must not be authored directly: "
                + ", ".join(collisions)
            )
    try:
        descriptor = yaml.load(
            raw_descriptor.decode("utf-8"), Loader=_UniqueKeyLoader
        )
    except (UnicodeError, yaml.YAMLError) as exc:
        raise CBSProviderAuthoringError(
            f"cannot parse {CBS_PROVIDER_AUTHORING_PATH}: {exc}"
        ) from exc
    descriptor = _mapping(descriptor, field=CBS_PROVIDER_AUTHORING_PATH)
    errors = sorted(
        _authoring_validator().iter_errors(descriptor),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path)
        suffix = f" at {location}" if location else ""
        raise CBSProviderAuthoringError(
            f"invalid {CBS_PROVIDER_AUTHORING_SCHEMA}{suffix}: {first.message}"
        )

    try:
        manifest = yaml.safe_load(files["skill.yaml"].decode("utf-8")) or {}
    except (KeyError, UnicodeError, yaml.YAMLError) as exc:
        raise CBSProviderAuthoringError(f"cannot parse skill.yaml: {exc}") from exc
    manifest = _mapping(manifest, field="skill.yaml")
    raw_tools = manifest.get("tools")
    if not isinstance(raw_tools, list):
        raise CBSProviderAuthoringError("skill.yaml tools must be an array")
    tools: dict[str, Mapping[str, Any]] = {}
    for raw_tool in raw_tools:
        tool = _mapping(raw_tool, field="skill.yaml.tools[]")
        name = str(tool.get("name") or "").strip()
        if not name or name in tools:
            raise CBSProviderAuthoringError(
                "skill.yaml tool names must be non-empty and unique"
            )
        tools[name] = tool

    capability_source = _mapping(descriptor["capability"], field="capability")
    operations: list[dict[str, Any]] = []
    operation_ids: set[str] = set()
    referenced_tools: set[str] = set()
    for raw_operation in capability_source["operations"]:
        operation = _mapping(raw_operation, field="capability.operations[]")
        operation_id = str(operation["operation_id"])
        tool_name = str(operation["tool"])
        if operation_id in operation_ids:
            raise CBSProviderAuthoringError(
                f"duplicate capability operation_id: {operation_id}"
            )
        if tool_name in referenced_tools:
            raise CBSProviderAuthoringError(
                f"skill tool is mapped by more than one capability operation: {tool_name}"
            )
        tool = tools.get(tool_name)
        if tool is None:
            raise CBSProviderAuthoringError(
                f"capability operation references unknown skill tool: {tool_name}"
            )
        input_schema = tool.get("input_schema")
        output_schema = tool.get("output_schema")
        if not isinstance(input_schema, Mapping) or not isinstance(
            output_schema, Mapping
        ):
            raise CBSProviderAuthoringError(
                f"skill tool {tool_name} must declare input_schema and output_schema"
            )
        operation_ids.add(operation_id)
        referenced_tools.add(tool_name)
        operations.append(
            {
                "operation_id": operation_id,
                "input_schema": dict(input_schema),
                "output_schema": dict(output_schema),
                "errors": list(operation["errors"]),
            }
        )

    requested_authorities = {
        str(value) for value in _list(manifest.get("capabilities"))
    }
    capability_authorities = [
        str(value)
        for value in _list(capability_source.get("authority_requirements"))
    ]
    missing_authorities = sorted(set(capability_authorities) - requested_authorities)
    if missing_authorities:
        raise CBSProviderAuthoringError(
            "CBS authority requirements are absent from skill.yaml capabilities: "
            + ", ".join(missing_authorities)
        )

    binding_source = _mapping(descriptor["binding"], field="binding")
    binding_authorities = [
        str(value)
        for value in _list(
            binding_source.get("authority_requirements", capability_authorities)
        )
    ]
    missing_binding_authorities = sorted(
        set(binding_authorities) - requested_authorities
    )
    if missing_binding_authorities:
        raise CBSProviderAuthoringError(
            "binding authority requirements are absent from skill.yaml capabilities: "
            + ", ".join(missing_binding_authorities)
        )
    physical_member = str(binding_source["physical_member"])
    if physical_member not in files:
        raise CBSProviderAuthoringError(
            f"binding physical_member is not packaged: {physical_member}"
        )

    try:
        capability = CapabilityContract.create(
            capability_ref=str(capability_source["ref"]),
            version=str(capability_source["version"]),
            title=str(capability_source["title"]),
            operations=operations,
            invariants=_list(capability_source.get("invariants")),
            effects=_list(capability_source.get("effects")),
            authority_requirements=capability_authorities,
            dependencies=_list(capability_source.get("dependencies")),
            state_ports=_list(capability_source.get("state_ports")),
            conformance_refs=_list(capability_source.get("conformance_refs")),
            compatibility=_mapping(
                capability_source.get("compatibility") or {},
                field="capability.compatibility",
            ),
        )
        binding = BindingDefinition.create(
            binding_definition_ref=str(binding_source["ref"]),
            version=str(binding_source["version"]),
            capability_ref=capability.capability_ref,
            capability_version=capability.version,
            entry_protocol=str(binding_source["entry_protocol"]),
            implementation_entrypoint=str(binding_source["logical_entrypoint"]),
            state_support=_list(binding_source.get("state_support")),
            modes=list(binding_source["modes"]),
            environment_constraints={
                "profile_classes": _list(
                    binding_source.get("profile_classes") or ["local"]
                ),
                "provider_features": _list(
                    binding_source.get("provider_features")
                ),
            },
            authority_requirements=binding_authorities,
            conformance_obligations=_list(
                binding_source.get("conformance_obligations")
                or ["capability_conformance"]
            ),
        )
    except CapabilityBindingStateContractError as exc:
        raise CBSProviderAuthoringError(str(exc)) from exc

    generated_files = {
        CAPABILITY_OUTPUT_PATH: canonical_json_bytes(capability.to_dict()),
        BINDING_OUTPUT_PATH: canonical_json_bytes(binding.to_dict()),
    }
    origin = str(_mapping(descriptor["authorship"], field="authorship")["origin"])
    metadata = {
        "schema": CBS_PROVIDER_COMPILATION_SCHEMA,
        "compiler_id": CBS_PROVIDER_COMPILER_ID,
        "source": {
            "path": CBS_PROVIDER_AUTHORING_PATH,
            "digest": sha256_digest(raw_descriptor),
            "origin": origin,
        },
        "generated_contracts": [
            {
                "kind": "capability_contract",
                "path": CAPABILITY_OUTPUT_PATH,
                "ref": capability.capability_ref,
                "version": capability.version,
                "digest": capability.digest,
            },
            {
                "kind": "binding_definition",
                "path": BINDING_OUTPUT_PATH,
                "ref": binding.binding_definition_ref,
                "version": str(binding.to_dict()["version"]),
                "digest": binding.digest,
            },
        ],
        "delivery_templates": [
            {
                "binding_definition_ref": binding.binding_definition_ref,
                "binding_definition_digest": binding.digest,
                "logical_entrypoint": str(binding_source["logical_entrypoint"]),
                "physical_member": physical_member,
            }
        ],
        "authorship_counts": {
            "human_authored": 1 if origin == "human_authored" else 0,
            "builder_inferred": 1 if origin == "builder_inferred" else 0,
            "compiler_generated": len(generated_files),
        },
    }
    return CBSProviderCompilation(
        capability=capability,
        binding=binding,
        generated_files=generated_files,
        package_metadata=metadata,
    )


def binding_deliveries_for_package(
    package: ArtifactPackageRef,
    package_metadata: Mapping[str, Any] | None,
) -> tuple[BindingDelivery, ...]:
    """Bind verified delivery templates to one exact immutable package."""

    if package_metadata is None:
        return ()
    if package_metadata.get("schema") != CBS_PROVIDER_COMPILATION_SCHEMA:
        raise CBSProviderAuthoringError("unsupported CBS package metadata schema")
    templates = package_metadata.get("delivery_templates")
    if not isinstance(templates, list) or any(
        not isinstance(item, Mapping) for item in templates
    ):
        raise CBSProviderAuthoringError("CBS delivery_templates must be objects")
    deliveries = [
        BindingDelivery.create(
            binding_definition_ref=str(item.get("binding_definition_ref") or ""),
            binding_definition_digest=str(
                item.get("binding_definition_digest") or ""
            ),
            logical_entrypoint=str(item.get("logical_entrypoint") or ""),
            package={
                "kind": package.kind,
                "id": package.artifact_id,
                "version": package.version,
                "digest": package.digest,
            },
            physical_member=str(item.get("physical_member") or ""),
        )
        for item in templates
    ]
    return tuple(sorted(deliveries, key=lambda item: item.digest))


__all__ = [
    "BINDING_OUTPUT_PATH",
    "CAPABILITY_OUTPUT_PATH",
    "CBS_PROVIDER_AUTHORING_PATH",
    "CBS_PROVIDER_AUTHORING_SCHEMA",
    "CBS_PROVIDER_COMPILATION_SCHEMA",
    "CBS_PROVIDER_COMPILER_ID",
    "CBSProviderAuthoringError",
    "CBSProviderCompilation",
    "binding_deliveries_for_package",
    "compile_cbs_provider_files",
]
