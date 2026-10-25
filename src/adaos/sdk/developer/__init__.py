"""Public developer-workspace SDK."""

from __future__ import annotations

from . import (
    artifact_context,
    compositions,
    conversational,
    projects,
    prompt_context,
    prototypes,
    ui,
    validation,
)

# Mutable source operations are platform capabilities, not access to an
# immutable Trial's filesystem. Pure schema/compiler helpers remain portable.
from adaos.sdk.core.authoring import bind_authoring_operations as _bind_authoring

for _module in (projects, prompt_context, artifact_context):
    _bind_authoring(_module, exclude=("media_type_for_name",))
_bind_authoring(compositions, exclude=("validate", "normalized_definition"))
_bind_authoring(prototypes, ("materialize_resources", "carry_forward_resources"))

__all__ = [
    "artifact_context",
    "compositions",
    "conversational",
    "projects",
    "prompt_context",
    "prototypes",
    "ui",
    "validation",
]
