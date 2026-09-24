"""Runtime execution helpers for skill tool invocation."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.services.logging import configure_skill_module_logging

_SKILL_SOURCE_SNAPSHOTS: dict[str, int | str] = {}
_MODULE_LOAD_LOCK = threading.RLock()
_MODULE_LOAD_COMPLETE = "__adaos_runtime_load_complete__"
_MODULE_LOAD_SLOW_SECONDS = 0.25
_PREPARED_IMPORT_CONTEXT: tuple[str, str, tuple[str, ...]] | None = None


@contextmanager
def isolated_skill_import_state(
    skill_dir: Path,
    *,
    extra_paths: Iterable[Path] | None = None,
):
    """Temporarily isolate short-name packages owned by one skill.

    Lifecycle hooks and migrations execute inside the node process but often
    use natural sibling imports such as ``from research.repository import``.
    Preserve modules belonging to another skill, load the target's packages
    for the bounded operation, then restore the prior process state.
    """

    global _PREPARED_IMPORT_CONTEXT

    skill_path = Path(skill_dir).resolve()
    with _MODULE_LOAD_LOCK:
        _PREPARED_IMPORT_CONTEXT = None
        original_sys_path = list(sys.path)
        local_roots = _local_import_roots(skill_path)
        previous_modules = {
            key: module
            for key, module in list(sys.modules.items())
            if key.split(".", 1)[0] in local_roots
        }
        for key in previous_modules:
            sys.modules.pop(key, None)
        import_paths = [skill_path, skill_path.parent]
        import_paths.extend(Path(extra).resolve() for extra in extra_paths or ())
        _prioritize_import_paths(import_paths)
        _bind_owned_namespace_packages(skill_path)
        importlib.invalidate_caches()
        try:
            yield
        finally:
            for key in list(sys.modules):
                if key.split(".", 1)[0] in local_roots:
                    sys.modules.pop(key, None)
            sys.modules.update(previous_modules)
            sys.path[:] = original_sys_path
            importlib.invalidate_caches()
            _PREPARED_IMPORT_CONTEXT = None


def execute_tool(
    skill_dir: Path,
    *,
    module: str | None,
    attr: str,
    payload: Mapping[str, Any],
    extra_paths: Iterable[Path] | None = None,
    source_revision: str | None = None,
) -> Any:
    """Execute a tool callable inside the skill package and return the result."""

    global _PREPARED_IMPORT_CONTEXT

    skill_path = Path(skill_dir).resolve()
    # Importing a source-backed skill is process-global work: importlib writes
    # the module into ``sys.modules`` before executing its body. Concurrent
    # first calls must therefore not observe that half-initialized module.
    # Keep execution outside this lock; only source snapshotting and import are
    # serialized.
    lock_started = time.perf_counter()
    with _MODULE_LOAD_LOCK:
        lock_wait_seconds = time.perf_counter() - lock_started
        load_started = time.perf_counter()
        module_name = module or "handlers.main"
        # Skill handlers commonly import sibling packages by their short name
        # (for example ``from research.manager import ...``). Keep the active
        # skill first and evict a same-named package left by another skill before
        # executing the handler. Without both operations, process-global import
        # state can route an otherwise valid skill to a sibling's package.
        import_paths = [skill_path, skill_path.parent]
        import_paths.extend(Path(extra).resolve() for extra in extra_paths or ())
        revision = str(source_revision or "").strip()
        prepared_key = (
            str(skill_path),
            revision,
            tuple(str(path) for path in import_paths),
        )
        if not revision or _PREPARED_IMPORT_CONTEXT != prepared_key:
            _prioritize_import_paths(import_paths)
            _purge_conflicting_local_modules(skill_path)
            _reload_skill_modules_if_sources_changed(
                skill_path,
                source_revision=revision or None,
            )
            _bind_owned_namespace_packages(skill_path)
            _PREPARED_IMPORT_CONTEXT = prepared_key if revision else None
        mod = _load_skill_module(skill_path, module_name)
        load_seconds = time.perf_counter() - load_started
    if lock_wait_seconds >= _MODULE_LOAD_SLOW_SECONDS or load_seconds >= _MODULE_LOAD_SLOW_SECONDS:
        import logging

        logging.getLogger("adaos.skill.runtime_runner").warning(
            "skill handler resolution slow skill=%s module=%s lock_wait_ms=%.1f load_ms=%.1f",
            skill_path.name,
            module_name,
            lock_wait_seconds * 1000.0,
            load_seconds * 1000.0,
        )
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
        def _invoke() -> Any:
            with io_meta(meta):
                if _should_expand_keywords(func, mapping):
                    return func(**_keyword_payload(func, mapping))
                return func(mapping)
    else:
        def _invoke() -> Any:
            if _should_expand_keywords(func, mapping):
                return func(**_keyword_payload(func, mapping))
            return func(mapping)

    invoke_started = time.perf_counter()
    try:
        return _invoke()
    finally:
        invoke_seconds = time.perf_counter() - invoke_started
        if invoke_seconds >= _MODULE_LOAD_SLOW_SECONDS:
            import logging

            logging.getLogger("adaos.skill.runtime_runner").warning(
                "skill handler execution slow skill=%s module=%s attr=%s invoke_ms=%.1f",
                skill_path.name,
                module_name,
                attr,
                invoke_seconds * 1000.0,
            )


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
    ordered = list(dict.fromkeys(str(Path(path).resolve()) for path in paths))
    keys = {os.path.normcase(path) for path in ordered}
    # Existing entries only need lexical deduplication, not filesystem traversal
    # for every entry and every promoted path while holding the import lock.
    retained = [existing for existing in sys.path
                if os.path.normcase(os.path.abspath(existing or ".")) not in keys]
    sys.path[:] = ordered + retained


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
        if child.is_dir() and any(child.glob("*.py")):
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


def _bind_owned_namespace_packages(skill_path: Path) -> None:
    # A later regular package on sys.path otherwise wins over the active skill's
    # namespace directory, even when the active path is first. Confine its search.
    for name in _local_import_roots(skill_path):
        directory = skill_path / name
        if not directory.is_dir() or (directory / "__init__.py").exists():
            continue
        existing = sys.modules.get(name)
        if existing is not None and list(getattr(existing, "__path__", [])) == [str(directory)]:
            continue
        spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        spec.submodule_search_locations = [str(directory)]
        sys.modules[name] = importlib.util.module_from_spec(spec)


def _purge_skill_source_modules(skill_path: Path) -> None:
    skill_pkg = skill_path.name
    namespace = _skill_namespace(skill_path)
    for key, module in list(sys.modules.items()):
        if key == "skills" or key.startswith("adaos.") or key.startswith("adaos_skill_"):
            continue
        skill_scoped = (
            key == f"skills.{skill_pkg}"
            or key.startswith(f"skills.{skill_pkg}.")
            or key == skill_pkg
            or key.startswith(f"{skill_pkg}.")
        )
        if skill_scoped or key == namespace or key.startswith(namespace + ".") or _module_file_is_under(module, skill_path):
            sys.modules.pop(key, None)


def _purge_skill_bytecode(skill_path: Path) -> None:
    """Remove interpreter caches when an exact source revision changes.

    A/B slots are reused.  Filesystems with coarse timestamps can otherwise
    make importlib accept bytecode from an older revision whose source has the
    same size and timestamp.
    """

    for cache_dir in skill_path.rglob("__pycache__"):
        if not cache_dir.is_dir():
            continue
        for bytecode in cache_dir.glob("*.py[co]"):
            try:
                bytecode.unlink()
            except OSError:
                continue


def _reload_skill_modules_if_sources_changed(
    skill_path: Path,
    *,
    source_revision: str | None = None,
) -> None:
    key = str(skill_path)
    revision = str(source_revision or "").strip()
    current: int | str = f"revision:{revision}" if revision else _source_snapshot_mtime_ns(skill_path)
    previous = _SKILL_SOURCE_SNAPSHOTS.get(key)
    if previous is None:
        # A/B activation changes the skill source path. The first invocation
        # from a freshly activated slot must not reuse a module imported from
        # the previous slot under the same ``skills.<name>`` package.
        _purge_skill_source_modules(skill_path)
        if revision:
            _purge_skill_bytecode(skill_path)
        importlib.invalidate_caches()
        _SKILL_SOURCE_SNAPSHOTS[key] = current
        return
    if previous == current:
        return
    _purge_skill_source_modules(skill_path)
    if revision:
        _purge_skill_bytecode(skill_path)
    importlib.invalidate_caches()
    _SKILL_SOURCE_SNAPSHOTS[key] = current


def _load_skill_module(skill_path: Path, module_name: str):
    skill_pkg = skill_path.name
    candidates: list[str] = []
    if _is_generic_handlers_module(module_name):
        loaded = _load_module_from_skill_source(skill_path, module_name)
        if loaded is not None:
            return loaded
        _purge_generic_skill_modules()
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


def _skill_namespace(skill_path: Path) -> str:
    path_key = hashlib.sha256(str(skill_path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"_adaos_runtime.{skill_path.name}.{path_key}"


def _ensure_skill_module_parents(skill_path: Path, module_name: str) -> str:
    namespace = _skill_namespace(skill_path)
    parts = namespace.split(".")
    for index in range(1, len(parts) + 1):
        name = ".".join(parts[:index])
        if name not in sys.modules:
            spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
            spec.submodule_search_locations = [str(skill_path)] if name == namespace else []
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            parent, _, child = name.rpartition(".")
            if parent:
                setattr(sys.modules[parent], child, package)
    parent = module_name.rpartition(".")[0]
    if parent:
        # Let importlib execute real __init__.py files (or namespace packages),
        # so both eager and lazy relative sibling imports have normal semantics.
        importlib.import_module(f"{namespace}.{parent}")
    return namespace


def _load_module_from_skill_source(skill_path: Path, module_name: str):
    relative = Path(*[segment for segment in str(module_name or "").split(".") if segment])
    # Build the file path without relying on platform-specific anchors.
    candidate_file = skill_path.joinpath(*relative.parts).with_suffix(".py")
    if not candidate_file.exists():
        return None
    try:
        from adaos.services.skills_loader_importlib import loaded_handler_module_for_path

        loaded_handler = loaded_handler_module_for_path(candidate_file)
    except Exception:
        loaded_handler = None
    if loaded_handler is not None:
        return loaded_handler
    namespace = _skill_namespace(skill_path)
    synthetic_name = f"{namespace}.{module_name}"
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
        _ensure_skill_module_parents(skill_path, module_name)
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(synthetic_name) is module:
            sys.modules.pop(synthetic_name, None)
        raise
    finally:
        # Tool execution imports handler modules only to resolve the requested
        # callable. Their decorators must not leak a second set of tools and
        # event subscriptions into the process-wide declaration registry.
        from adaos.sdk.core.decorators import retire_module_declarations

        retire_module_declarations({name for name in sys.modules if name.startswith(namespace + ".")} | {synthetic_name})
    setattr(module, _MODULE_LOAD_COMPLETE, True)
    return module
