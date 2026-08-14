"""Runtime execution helpers for skill tool invocation."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.services.logging import configure_skill_module_logging

_SKILL_SOURCE_SNAPSHOTS: dict[str, int] = {}
_MODULE_LOAD_LOCK = threading.RLock()
_MODULE_LOAD_COMPLETE = "__adaos_runtime_load_complete__"


def execute_tool(
    skill_dir: Path,
    *,
    module: str | None,
    attr: str,
    payload: Mapping[str, Any],
    extra_paths: Iterable[Path] | None = None,
) -> Any:
    """Execute a tool callable inside the skill package and return the result."""

    skill_path = Path(skill_dir).resolve()
    # Importing a source-backed skill is process-global work: importlib writes
    # the module into ``sys.modules`` before executing its body. Concurrent
    # first calls must therefore not observe that half-initialized module.
    # Keep execution outside this lock; only source snapshotting and import are
    # serialized.
    with _MODULE_LOAD_LOCK:
        # Skill handlers commonly import sibling packages by their short name
        # (for example ``from research.manager import ...``). Keep the active
        # skill first and evict a same-named package left by another skill before
        # executing the handler. Without both operations, process-global import
        # state can route an otherwise valid skill to a sibling's package.
        import_paths = [skill_path, skill_path.parent]
        import_paths.extend(Path(extra).resolve() for extra in extra_paths or ())
        _prioritize_import_paths(import_paths)
        _purge_conflicting_local_modules(skill_path)
        _reload_skill_modules_if_sources_changed(skill_path)
        module_name = module or "handlers.main"
        mod = _load_skill_module(skill_path, module_name)
    func = getattr(mod, attr)
    if not callable(func):
        raise TypeError(f"attribute '{attr}' from module '{module_name}' is not callable")

    mapping = dict(payload)
    meta = mapping.get("_meta")
    try:
        from adaos.sdk.io.context import io_meta  # pylint: disable=import-outside-toplevel
    except Exception:
        io_meta = None

    if io_meta is not None and isinstance(meta, Mapping):
        with io_meta(meta):
            if _should_expand_keywords(func, mapping):
                return func(**_keyword_payload(func, mapping))
            return func(mapping)

    if _should_expand_keywords(func, mapping):
        return func(**_keyword_payload(func, mapping))
    return func(mapping)


def _keyword_payload(func, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import inspect

        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            filtered = dict(payload)
            if "_meta" not in sig.parameters:
                filtered.pop("_meta", None)
            return filtered

        allowed = {
            p.name
            for p in params
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        return {key: value for key, value in payload.items() if key in allowed}
    except Exception:
        filtered = dict(payload)
        filtered.pop("_meta", None)
        return filtered


def _should_expand_keywords(func, payload: Mapping[str, Any]) -> bool:
    try:
        import inspect

        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        keyword_payload = _keyword_payload(func, payload)
        if not params:
            return not keyword_payload
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return True
        if any(p.kind == inspect.Parameter.KEYWORD_ONLY for p in params):
            return True
        positional = [p for p in params if p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
        if len(positional) > 1:
            return True
        if len(positional) == 1:
            param = positional[0]
            if param.name in keyword_payload:
                return True
            if not keyword_payload and param.default is not inspect._empty:
                return True
        return False
    except Exception:
        return False


def _is_generic_handlers_module(module_name: str) -> bool:
    token = str(module_name or "").strip()
    return token == "handlers" or token == "handlers.main" or token.startswith("handlers.")


def _purge_generic_handlers_modules() -> None:
    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            sys.modules.pop(key, None)


def _purge_generic_skill_modules() -> None:
    for key in list(sys.modules.keys()):
        if key in {"handlers", "service"} or key.startswith(("handlers.", "service.")):
            sys.modules.pop(key, None)


def _source_snapshot_mtime_ns(skill_path: Path) -> int:
    latest = 0
    for source in skill_path.rglob("*.py"):
        if "__pycache__" in source.parts:
            continue
        try:
            latest = max(latest, int(source.stat().st_mtime_ns))
        except OSError:
            continue
    return latest


def _module_file_is_under(module: Any, root: Path) -> bool:
    raw = getattr(module, "__file__", None)
    if not raw:
        return False
    try:
        Path(raw).resolve().relative_to(root)
    except Exception:
        return False
    return True


def _prioritize_import_paths(paths: Iterable[Path]) -> None:
    ordered = [str(Path(path).resolve()) for path in paths]
    for path_text in reversed(ordered):
        path_key = str(Path(path_text)).casefold()
        sys.path[:] = [
            existing
            for existing in sys.path
            if str(Path(existing or ".").resolve()).casefold() != path_key
        ]
        sys.path.insert(0, path_text)


def _local_import_roots(skill_path: Path) -> set[str]:
    roots: set[str] = set()
    try:
        children = list(skill_path.iterdir())
    except OSError:
        return roots
    for child in children:
        name = child.name
        if name.startswith(".") or name in {"__pycache__", "adaos", "skills"}:
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            roots.add(name)
        elif child.is_file() and child.suffix == ".py" and child.stem != "__init__":
            roots.add(child.stem)
    return roots


def _purge_conflicting_local_modules(skill_path: Path) -> None:
    """Evict short-name imports owned by another skill runtime.

    This is compatibility isolation for existing skills that use absolute
    sibling imports. New skills should still prefer a unique package namespace;
    Python's module table remains process-global outside handler loading.
    """

    local_roots = _local_import_roots(skill_path)
    if not local_roots:
        return
    for key, loaded in list(sys.modules.items()):
        root = key.split(".", 1)[0]
        if root not in local_roots:
            continue
        if not _module_file_is_under(loaded, skill_path):
            sys.modules.pop(key, None)


def _purge_skill_source_modules(skill_path: Path) -> None:
    skill_pkg = skill_path.name
    for key, module in list(sys.modules.items()):
        if key == "skills" or key.startswith("adaos.") or key.startswith("adaos_skill_"):
            continue
        skill_scoped = (
            key == f"skills.{skill_pkg}"
            or key.startswith(f"skills.{skill_pkg}.")
            or key == skill_pkg
            or key.startswith(f"{skill_pkg}.")
        )
        if skill_scoped or _module_file_is_under(module, skill_path):
            sys.modules.pop(key, None)


def _reload_skill_modules_if_sources_changed(skill_path: Path) -> None:
    key = str(skill_path)
    current = _source_snapshot_mtime_ns(skill_path)
    previous = _SKILL_SOURCE_SNAPSHOTS.get(key)
    if previous is None:
        # A/B activation changes the skill source path. The first invocation
        # from a freshly activated slot must not reuse a module imported from
        # the previous slot under the same ``skills.<name>`` package.
        _purge_skill_source_modules(skill_path)
        importlib.invalidate_caches()
        _SKILL_SOURCE_SNAPSHOTS[key] = current
        return
    if previous == current:
        return
    _purge_skill_source_modules(skill_path)
    importlib.invalidate_caches()
    _SKILL_SOURCE_SNAPSHOTS[key] = current


def _load_skill_module(skill_path: Path, module_name: str):
    skill_pkg = skill_path.name
    candidates: list[str] = []
    if _is_generic_handlers_module(module_name):
        _purge_generic_skill_modules()
        loaded = _load_module_from_skill_source(skill_path, module_name)
        if loaded is not None:
            return loaded
        candidates.extend(
            [
                f"skills.{skill_pkg}.{module_name}",
                f"{skill_pkg}.{module_name}",
            ]
        )
    candidates.append(module_name)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            if _is_generic_handlers_module(candidate):
                _purge_generic_handlers_modules()
            return importlib.import_module(candidate)
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return importlib.import_module(module_name)


def _load_module_from_skill_source(skill_path: Path, module_name: str):
    relative = Path(*[segment for segment in str(module_name or "").split(".") if segment])
    # Build the file path without relying on platform-specific anchors.
    candidate_file = skill_path.joinpath(*relative.parts).with_suffix(".py")
    if not candidate_file.exists():
        return None
    path_key = hashlib.sha256(str(skill_path.resolve()).encode("utf-8")).hexdigest()[:12]
    synthetic_name = f"_adaos_runtime.{skill_path.name}.{path_key}.{module_name}"
    existing = sys.modules.get(synthetic_name)
    if (
        existing is not None
        and _module_file_is_under(existing, skill_path)
        and getattr(existing, _MODULE_LOAD_COMPLETE, False) is True
    ):
        return existing
    # A failed or interrupted import may have left a module object in the
    # cache. Never reuse it as an active skill handler.
    if existing is not None:
        sys.modules.pop(synthetic_name, None)
    spec = importlib.util.spec_from_file_location(synthetic_name, candidate_file)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    configure_skill_module_logging(synthetic_name)
    sys.modules[synthetic_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(synthetic_name) is module:
            sys.modules.pop(synthetic_name, None)
        raise
    setattr(module, _MODULE_LOAD_COMPLETE, True)
    return module
