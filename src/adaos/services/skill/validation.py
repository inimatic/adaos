# src/adaos/sdk/skill_validator.py

from __future__ import annotations
import ast, os, re, sys, json, subprocess, importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import copy
import yaml
from jsonschema import Draft202012Validator, ValidationError
import importlib.resources as ir

from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.webui_contract import validate_webui_contract

SCHEMA_PATH = Path(__file__).with_name("skill_schema.json")
WEBUI_SCHEMA_RES = ("adaos.abi", "webui.v1.schema.json")
_SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".runtime"}
_YJS_PATTERNS = (
    "y_py",
    "ypy_websocket",
    "YDoc",
    "apply_update",
    "encode_state_as_update",
    "encode_state_vector",
    "get_ydoc",
)
_TRANSCRIPT_FILE_RE = re.compile(r"(transcript|chat_history|conversation_history|voice_chat|dialog_history)", re.I)
_UNBOUNDED_NAME_RE = re.compile(r"(cache|history|histories|events|logs|frames|sessions|state|buffer|queue|transcript)", re.I)
_TRANSPORT_MEMORY_PATTERNS = (
    "voice_chat.messages",
    "voice_chat_skill",
    "telegram_chat_id",
    "telegram_message_id",
    "slack_channel",
)
_CONVERSATION_SDK_PATTERNS = (
    "adaos.sdk.conversation",
    "from adaos.sdk import conversation",
    "conversation.context(",
    "conversation.open(",
    "conversation.current(",
)
_MEMORY_SDK_PATTERNS = (
    "adaos.sdk.memory",
    "from adaos.sdk import memory",
    "memory.remember(",
    "memory.propose_write(",
    "memory.write_policy(",
    "memory.record_consent(",
)


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    where: str | None = None


@dataclass
class ValidationReport:
    ok: bool
    issues: List[Issue]


def _load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_webui_schema() -> Dict[str, Any]:
    try:
        res = ir.files(WEBUI_SCHEMA_RES[0]) / WEBUI_SCHEMA_RES[1]
        with ir.as_file(res) as fp:
            return json.loads(Path(fp).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to load WebUI schema: {exc}")


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise RuntimeError(f"failed to read yaml: {e}")


def validate_webui_file_contract(skill_dir: Path, *, skill_name: str | None = None) -> List[Issue]:
    """Validate only webui.json schema and UI addressing/domain contract."""

    issues: List[Issue] = []
    webui = skill_dir / "webui.json"
    if not webui.exists():
        return issues
    try:
        raw = json.loads(webui.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(raw, dict):
            issues.append(Issue("error", "webui.invalid_type", "webui.json must be a JSON object", "webui.json"))
            return issues
        Draft202012Validator(_load_webui_schema()).validate(raw)
        for issue in validate_webui_contract(raw, skill_id=skill_name, source="webui.json"):
            issues.append(Issue(issue.level, issue.code, issue.message, issue.where))
    except ValidationError as e:
        issues.append(Issue("error", "webui.schema.invalid", f"webui.json schema violation: {e.message}", "webui.json"))
    except Exception as e:
        issues.append(Issue("error", "webui.read.failed", f"failed to read/parse webui.json: {e}", "webui.json"))
    return issues


def _normalize_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    s = copy.deepcopy(spec or {})
    if s.get("description") is None:
        s["description"] = ""
    if not isinstance(s.get("dependencies"), list):
        s["dependencies"] = []
    if not isinstance(s.get("tools"), list):
        s["tools"] = []
    if not isinstance(s.get("exports"), dict):
        s["exports"] = {}
    if not isinstance(s["exports"].get("tools"), list):
        s["exports"]["tools"] = []
    if not isinstance(s.get("events"), dict):
        s["events"] = {}
    if not isinstance(s["events"].get("subscribe"), list):
        s["events"]["subscribe"] = []
    if not isinstance(s["events"].get("publish"), list):
        s["events"]["publish"] = []
    if not isinstance(s.get("data_projections"), list):
        s["data_projections"] = []
    if not isinstance(s.get("data_routes"), list):
        s["data_routes"] = []
    return s


def _static_checks(skill_dir: Path, install_mode: bool) -> List[Issue]:
    issues: List[Issue] = []
    sy = skill_dir / "skill.yaml"
    if not sy.exists():
        issues.append(Issue("error", "missing.skill_yaml", "skill.yaml not found", str(sy)))
        return issues
    raw = _read_yaml(sy)
    data = _normalize_spec(raw)
    try:
        schema = _load_schema()
        Draft202012Validator(schema).validate(data)
    except ValidationError as e:
        issues.append(Issue("error", "schema.invalid", f"skill.yaml schema violation: {e.message}", "skill.yaml"))
        return issues

    handler = skill_dir / "handlers" / "main.py"
    if not handler.exists():
        issues.append(Issue("error", "missing.handler", "handlers/main.py not found", str(handler)))

    tools = data.get("tools") or []
    names = [t.get("name") for t in tools if isinstance(t, dict)]
    if len(names) != len(set(names)):
        issues.append(Issue("error", "tools.duplicate_names", "duplicate tool names in skill.yaml", "tools[]"))

    for t in tools:
        if not isinstance(t, dict):
            issues.append(Issue("error", "tools.invalid_item", "tool item must be an object", "tools[]"))
            continue
        if not isinstance(t.get("input_schema"), dict):
            issues.append(Issue("error", "tools.input_schema.invalid", f"tool '{t.get('name')}' input_schema must be object", "tools[].input_schema"))
        if t.get("output_schema") is not None and not isinstance(t.get("output_schema"), dict):
            issues.append(
                Issue("warning" if not install_mode else "error", "tools.output_schema.invalid", f"tool '{t.get('name')}' output_schema should be object", "tools[].output_schema")
            )

    ev = data.get("events") or {}
    for key in ("subscribe", "publish"):
        arr = ev.get(key) or []
        for i, v in enumerate(arr):
            if not isinstance(v, str) or not v.strip():
                issues.append(Issue("error", f"events.{key}.invalid", f"events.{key}[{i}] must be non-empty string", f"events.{key}[{i}]"))

    # webui.json (optional): validate declarative WebUI contributions and
    # cross-link the public skill interface with modal routes/actions.
    issues.extend(validate_webui_file_contract(skill_dir, skill_name=str(data.get("name") or "")))
    issues.extend(_conversation_native_static_checks(skill_dir, manifest=data, install_mode=install_mode))
    return issues


def _conversation_native_static_checks(skill_dir: Path, *, manifest: Dict[str, Any], install_mode: bool) -> List[Issue]:
    issues: List[Issue] = []
    uses_conversation_sdk = False
    uses_memory_sdk = False
    for path in sorted(skill_dir.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = _relative_to(path, skill_dir)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        uses_conversation_sdk = uses_conversation_sdk or any(pattern in text for pattern in _CONVERSATION_SDK_PATTERNS)
        uses_memory_sdk = uses_memory_sdk or any(pattern in text for pattern in _MEMORY_SDK_PATTERNS)
        for pattern in _YJS_PATTERNS:
            if pattern in text:
                issues.append(
                    Issue(
                        "error",
                        "conversation.unsafe_direct_yjs",
                        f"direct Yjs symbol used: {pattern}; generated skills must use declared projections/routes",
                        rel,
                    )
                )
                break
        for pattern in _TRANSPORT_MEMORY_PATTERNS:
            if pattern in text:
                issues.append(
                    Issue(
                        "error" if install_mode else "warning",
                        "conversation.transport_owned_memory",
                        f"transport-owned chat/memory reference used: {pattern}; use adaos.sdk.conversation/memory",
                        rel,
                    )
                )
        issues.extend(_conversation_memory_ast_issues(path, rel, text, install_mode=install_mode))
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = _relative_to(path, skill_dir)
        if _TRANSCRIPT_FILE_RE.search(path.name):
            issues.append(
                Issue(
                    "error" if install_mode else "warning",
                    "conversation.raw_transcript_file",
                    "raw transcript/chat history files are not allowed as the primary conversation store",
                    rel,
                )
            )
    issues.extend(_conversation_manifest_policy_issues(manifest, uses_conversation_sdk=uses_conversation_sdk, uses_memory_sdk=uses_memory_sdk))
    return issues


def _conversation_manifest_policy_issues(
    manifest: Dict[str, Any],
    *,
    uses_conversation_sdk: bool,
    uses_memory_sdk: bool,
) -> List[Issue]:
    issues: List[Issue] = []
    conversation = manifest.get("conversation") if isinstance(manifest.get("conversation"), dict) else {}
    if uses_conversation_sdk and not conversation:
        issues.append(
            Issue(
                "warning",
                "conversation.manifest_missing",
                "skill uses adaos.sdk.conversation but skill.yaml has no conversation declaration",
                "skill.yaml:conversation",
            )
        )
    if uses_memory_sdk and not _manifest_has_memory_route_or_policy(manifest, conversation):
        issues.append(
            Issue(
                "warning",
                "conversation.memory_policy_missing",
                "skill uses adaos.sdk.memory but skill.yaml declares no skill-local memory route or conversation memory policy",
                "skill.yaml:data_routes",
            )
        )
    return issues


def _manifest_has_memory_route_or_policy(manifest: Dict[str, Any], conversation: Dict[str, Any]) -> bool:
    if isinstance(conversation.get("memory"), dict) or isinstance(conversation.get("memory_policy"), dict):
        return True
    for route in manifest.get("data_routes") or []:
        if not isinstance(route, dict):
            continue
        route_kind = str(route.get("route") or "").strip()
        path = str(route.get("path") or "").strip().lower()
        if route_kind == "skill-local" and ("skill_memory" in path or "memory" in path):
            return True
    return False


def _conversation_memory_ast_issues(path: Path, rel: str, text: str, *, install_mode: bool) -> List[Issue]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    issues: List[Issue] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not any(_UNBOUNDED_NAME_RE.search(name) for name in names):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Dict, ast.Set)):
            issues.append(
                Issue(
                    "error" if install_mode else "warning",
                    "conversation.unbounded_process_memory",
                    f"module-level mutable state may become unbounded: {', '.join(names)}",
                    rel,
                )
            )
        elif isinstance(value, ast.Call):
            func_name = getattr(value.func, "id", "") or getattr(value.func, "attr", "")
            if func_name in {"list", "dict", "set"}:
                issues.append(
                    Issue(
                        "error" if install_mode else "warning",
                        "conversation.unbounded_process_memory",
                        f"module-level mutable state may become unbounded: {', '.join(names)}",
                        rel,
                    )
                )
            if func_name == "deque" and not any(kw.arg == "maxlen" for kw in value.keywords):
                issues.append(
                    Issue(
                        "error" if install_mode else "warning",
                        "conversation.unbounded_process_memory",
                        f"deque without maxlen may become unbounded: {', '.join(names)}",
                        rel,
                    )
                )
    return issues


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return str(path)


def _dynamic_checks(skill_name: str, skill_dir: Path, install_mode: bool, probe_tools: bool) -> List[Issue]:
    """
    Импортируем handlers/main.py в ОТДЕЛЬНОМ процессе Python
    и сверяем экспорт инструментов/подписок.
    """
    code = f"""
import os, json, importlib.util
os.environ['ADAOS_VALIDATE'] = '1'
mod_name = 'adaos_skill_{skill_name}_handlers_main'
handler_file = r'{(skill_dir / 'handlers' / 'main.py').as_posix()}'
spec = importlib.util.spec_from_file_location(mod_name, handler_file)
if spec is None or spec.loader is None:
    print(json.dumps({{"ok": False, "error": "spec/load failure"}}))
    raise SystemExit(0)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# попытка получить новые публичные реестры (с fallback на старые)
try:
    from adaos.sdk.core.decorators import tools_registry, subscriptions
    mod_tools = (tools_registry.get(mod_name) or {{}})
    subs = [t for (t, _fn) in subscriptions]
except Exception:
    try:
        from adaos.sdk.core.decorators import _TOOLS, _SUBSCRIPTIONS
        mod_tools = (_TOOLS.get(mod_name) or {{}})
        subs = [t for (t, _fn) in _SUBSCRIPTIONS]
    except Exception:
        mod_tools, subs = {{}}, []

required_dp = getattr(module, 'REQUIRES_DATA_PROJECTIONS', None)
exports = list(mod_tools.keys())
print(json.dumps({{"ok": True, "tools": exports, "subs": subs, "requires_data_projections": required_dp}}))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    issues: List[Issue] = []
    if proc.returncode != 0:
        issues.append(Issue("error", "import.failed", f"handler import failed: {proc.stderr.strip() or proc.stdout.strip()}"))
        return issues
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        issues.append(Issue("error", "introspect.invalid_json", "introspection did not return valid JSON"))
        return issues
    if not payload.get("ok"):
        issues.append(Issue("error", "introspect.failed", str(payload)))
        return issues

    data = _normalize_spec(_read_yaml(skill_dir / "skill.yaml"))
    declared_tools = [t.get("name") for t in (data.get("tools") or []) if isinstance(t, dict)]
    exported_tools = set(payload.get("tools") or [])
    for name in declared_tools:
        if name not in exported_tools:
            issues.append(Issue("error", "tools.missing_export", f"tool '{name}' declared in skill.yaml but not exported by @tool", "tools[].name"))

    declared_subs = set((data.get("events") or {}).get("subscribe") or [])
    exported_subs = set(payload.get("subs") or [])
    for topic in declared_subs:
        if topic not in exported_subs:
            issues.append(Issue("error", "events.missing_sub", f"no @subscribe handler for '{topic}'", "events.subscribe[]"))

    # If handlers/main.py explicitly declares that it requires data_projections
    # but the manifest does not provide them, surface a validation issue so
    # skill authors/LLM programmers can fix the manifest.
    required_dp = payload.get("requires_data_projections")
    manifest_dp = data.get("data_projections") or []
    if required_dp and not manifest_dp:
        issues.append(
            Issue(
                "warning" if not install_mode else "error",
                "data_projections.missing",
                "handlers/main.py declares REQUIRES_DATA_PROJECTIONS but skill.yaml has no data_projections section",
                "data_projections",
            )
        )

    # probe_tools оставим на будущее (без исполнения)
    return issues


@dataclass(slots=True)
class SkillValidationService:
    ctx: AgentContext

    def validate(
        self,
        skill_name: Optional[str] = None,
        *,
        strict: bool = False,
        install_mode: bool = False,
        probe_tools: bool = False,
        # новый параметр: явный путь к каталогу навыка — валидируем без смены skill_ctx
        skill_path: Optional[Path] = None,
    ) -> ValidationReport:
        """
        Валидация навыка:
        - статическая проверка skill.yaml и структуры
        - динамическая проверка экспортов/подписок через импорт handlers/main.py в отдельном процессе
        """
        ctx = self.ctx or get_ctx()

        # Ветка 1: явный путь — не трогаем skill_ctx
        if skill_path is not None:
            path = Path(skill_path)
            if not path.exists() or not path.is_dir():
                return ValidationReport(False, [Issue("error", "skill.path.missing", f"skill path not found or not a directory: {path}")])
            name = skill_name or path.name
            return self._validate_loaded(name=name, skill_dir=path, strict=strict, install_mode=install_mode, probe_tools=probe_tools)

        # Ветка 2: по имени через skill_ctx, с безопасным восстановлением предыдущего контекста
        previous = ctx.skill_ctx.get()
        try:
            if skill_name:
                if not ctx.skill_ctx.set(skill_name, ctx.paths.skills_dir() / skill_name):
                    return ValidationReport(False, [Issue("error", "skill.context.missing", f"skill '{skill_name}' not found")])

            current = ctx.skill_ctx.get()
            if current is None or getattr(current, "path", None) is None:
                return ValidationReport(False, [Issue("error", "skill.context.missing", "current skill not set")])

            return self._validate_loaded(
                name=current.name,
                skill_dir=Path(current.path),
                strict=strict,
                install_mode=install_mode,
                probe_tools=probe_tools,
            )
        finally:
            # корректно восстановим контекст
            if previous is None:
                ctx.skill_ctx.clear()
            else:
                ctx.skill_ctx.set(previous.name, Path(previous.path))

    # Новый публичный метод — валидация «по каталогу» (локация-агностичная)
    def validate_path(
        self,
        path: Path,
        *,
        name: Optional[str] = None,
        strict: bool = False,
        install_mode: bool = False,
        probe_tools: bool = False,
    ) -> ValidationReport:
        path = Path(path)
        if not path.exists() or not path.is_dir():
            return ValidationReport(False, [Issue("error", "skill.path.missing", f"skill path not found or not a directory: {path}")])
        return self._validate_loaded(
            name=name or path.name,
            skill_dir=path,
            strict=strict,
            install_mode=install_mode,
            probe_tools=probe_tools,
        )

    # Внутренняя общая логика
    def _validate_loaded(
        self,
        *,
        name: str,
        skill_dir: Path,
        strict: bool,
        install_mode: bool,
        probe_tools: bool,
    ) -> ValidationReport:
        issues: List[Issue] = []
        issues += _static_checks(skill_dir, bool(install_mode))

        # эскалируем предупреждения в ошибки при strict=True
        if strict:
            issues = [Issue("error", i.code, i.message, getattr(i, "where", None)) if i.level == "warning" else i for i in issues]

        # если уже есть фатальные ошибки структуры — не продолжаем
        if any(i.level == "error" for i in issues):
            ok = not any(i.level == "error" for i in issues)
            return ValidationReport(ok, issues)

        issues += _dynamic_checks(name, skill_dir, bool(install_mode), bool(probe_tools))
        if strict:
            issues = [Issue("error", i.code, i.message, getattr(i, "where", None)) if i.level == "warning" else i for i in issues]
        ok = not any(i.level == "error" for i in issues)
        return ValidationReport(ok, issues)
