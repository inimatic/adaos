"""Named distribution sources shared by release contracts and node policy."""
from __future__ import annotations

import re
from typing import Any

DEFAULT_SOURCE_BRANCH = "main"
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

class SourcePolicyError(ValueError):
    pass


def validate_source_branch(value: Any) -> str:
    branch = str(value or "").strip()
    if (not branch or len(branch) > 128 or branch.startswith(("-", "/", "."))
            or branch.endswith(("/", ".")) or branch in {"HEAD", "detached", "@"}
            or any(token in branch for token in ("..", "@{", "//"))
            or any(char.isspace() or ord(char) < 32 or char in "~^:?*[\\" for char in branch)
            or any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
            or _COMMIT.fullmatch(branch)):
        raise SourcePolicyError("invalid_source_branch")
    return branch

