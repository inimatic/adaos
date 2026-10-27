"""Canonical containment checks for explicitly owned E2E output directories."""
from pathlib import Path


def owned_subdirectory(path: str | Path, root: str | Path) -> Path:
    """Accept a strict child of the configured root, including relocated roots.

    Resolve both sides: an artifact root may itself be a symlink or Windows
    junction. Links inside that root still cannot escape its canonical target.
    """
    boundary = Path(root).expanduser().resolve()
    output = Path(path).expanduser().resolve()
    if output == boundary or not output.is_relative_to(boundary):
        raise ValueError("An explicit owned subdirectory is required")
    return output
