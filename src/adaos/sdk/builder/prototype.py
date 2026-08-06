"""Stable SDK façade for executable Builder prototypes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def start_data_runtime(definition: Mapping[str, Any]):
    from adaos.services.builder.prototype_runtime import PrototypeDataRuntime

    return PrototypeDataRuntime.start(definition)


__all__ = ["start_data_runtime"]
