"""Safe YAML parsing with the installed C parser when available.

Both loaders use PyYAML's safe constructor. This does not skip validation of
the rest of a manifest or enable Python/object constructors.
"""
from typing import Any

import yaml


def load_safe_yaml(document: str | bytes) -> Any:
    return yaml.load(document, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
