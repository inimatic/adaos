"""Runtime execution helpers for skill tool invocation."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Iterable, Mapping


def execute_tool(
    skill_dir: Path,
    *,
    module: str | None,
    attr: str,
    payload: Mapping[str, Any],
    extra_paths: Iterable[Path] | None = None,
) -> Any:
    """Execute a tool callable inside the skill package and return the result."""

    import sys

    skill_path = Path(skill_dir).resolve()
    # Ensure both the skill package root and its parent (which usually
    # contains the ``skills.<name>`` namespace) are visible on sys.path.
    for p in (skill_path, skill_path.parent):
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)

    for extra in extra_paths or ():
        extra_path = Path(extra).resolve()
        if str(extra_path) not in sys.path:
            sys.path.insert(0, str(extra_path))

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
    import sys

    for key in list(sys.modules.keys()):
        if key == "handlers" or key.startswith("handlers."):
            sys.modules.pop(key, None)


def _load_skill_module(skill_path: Path, module_name: str):
    skill_pkg = skill_path.name
    candidates: list[str] = []
    if _is_generic_handlers_module(module_name):
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

    if _is_generic_handlers_module(module_name):
        loaded = _load_module_from_skill_source(skill_path, module_name)
        if loaded is not None:
            return loaded

    if last_error is not None:
        raise last_error
    return importlib.import_module(module_name)


def _load_module_from_skill_source(skill_path: Path, module_name: str):
    import sys

    relative = Path(*[segment for segment in str(module_name or "").split(".") if segment])
    # Build the file path without relying on platform-specific anchors.
    candidate_file = skill_path.joinpath(*relative.parts).with_suffix(".py")
    if not candidate_file.exists():
        return None
    synthetic_name = f"_adaos_runtime.{skill_path.name}.{module_name}"
    spec = importlib.util.spec_from_file_location(synthetic_name, candidate_file)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[synthetic_name] = module
    spec.loader.exec_module(module)
    return module
