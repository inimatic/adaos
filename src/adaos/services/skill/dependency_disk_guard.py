from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Iterable


_GIB = 1024 * 1024 * 1024
_HEAVY_DEP_NAMES = {
    "easyocr",
    "faiss",
    "faiss-cpu",
    "faiss-gpu",
    "opencv-contrib-python",
    "opencv-python",
    "opencv-python-headless",
    "sentence-transformers",
    "static-ffmpeg",
    "tensorflow",
    "tensorflow-cpu",
    "tensorflow-gpu",
    "tensorflow-intel",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_bytes(name: str, default_gib: float) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default_gib * _GIB)
    try:
        return max(0, int(float(raw) * _GIB))
    except Exception:
        return int(default_gib * _GIB)


def _format_gib(value: int) -> str:
    return f"{value / _GIB:.1f}GiB"


def _install_specs(args: Iterable[str]) -> list[str]:
    specs: list[str] = []
    skip_next = False
    value_options = {"-r", "--requirement", "-c", "--constraint", "-f", "--find-links", "-i", "--index-url"}
    for raw in args:
        token = str(raw or "").strip()
        if not token:
            continue
        if skip_next:
            skip_next = False
            continue
        if token in value_options:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        specs.append(token)
    return specs


def _normalize_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).strip("-").lower()


def _package_name_from_spec(spec: str) -> str:
    raw = str(spec or "").strip()
    if not raw or raw.startswith(("-", ".", "~")):
        return ""
    if " @ " in raw:
        raw = raw.split(" @ ", 1)[0].strip()
    lowered = raw.lower()
    if lowered.startswith(("git+", "http:", "https:", "ssh:", "file:")):
        return ""
    if "/" in raw or "\\" in raw:
        return ""
    raw = raw.split(";", 1)[0].strip()
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", raw)
    return _normalize_package_name(match.group(1)) if match else ""


def heavy_dependency_names(args: Iterable[str]) -> list[str]:
    names = {
        name
        for spec in _install_specs(args)
        for name in [_package_name_from_spec(spec)]
        if name in _HEAVY_DEP_NAMES
    }
    return sorted(names)


def dependency_args_contain_heavy_packages(args: Iterable[str]) -> bool:
    return bool(heavy_dependency_names(args))


def dependency_disk_budget_bytes(args: Iterable[str], *, has_requirements_file: bool = False) -> int:
    specs = _install_specs(args)
    if dependency_args_contain_heavy_packages(specs):
        return _env_bytes("ADAOS_SKILL_DEP_DISK_HEAVY_FREE_GIB", 5.0)

    base = _env_bytes("ADAOS_SKILL_DEP_DISK_BASE_FREE_GIB", 2.0)
    per_spec = _env_bytes("ADAOS_SKILL_DEP_DISK_PER_SPEC_GIB", 1.0)
    req_file = _env_bytes("ADAOS_SKILL_DEP_DISK_REQUIREMENTS_GIB", 4.0) if has_requirements_file else 0
    return base + per_spec * len(specs) + req_file


def ensure_dependency_disk_budget(
    target_path: Path,
    args: Iterable[str],
    *,
    has_requirements_file: bool = False,
    skill_name: str = "",
) -> None:
    if not _env_bool("ADAOS_SKILL_DEP_DISK_GUARD", True):
        return
    required = dependency_disk_budget_bytes(args, has_requirements_file=has_requirements_file)
    if required <= 0:
        return
    probe = Path(target_path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    free = int(getattr(usage, "free", 0))
    if free >= required:
        return
    label = f" for skill '{skill_name}'" if skill_name else ""
    raise RuntimeError(
        "not enough free disk space to install Python dependencies"
        f"{label}: required>={_format_gib(required)}, available={_format_gib(free)}, path={probe}"
    )
