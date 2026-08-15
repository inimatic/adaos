# src/adaos/sdk/skill_validator.py

from __future__ import annotations
import ast, os, re, shlex, sys, json, subprocess, importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import copy
import yaml
from jsonschema import Draft202012Validator, ValidationError
import importlib.resources as ir

from adaos.domain.personalization_access import CAPABILITY_VOCABULARY, validate_capability
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.conversational_pipeline import compile_conversational_package
from adaos.services.webui_contract import validate_webui_contract
from adaos.services.workflow_artifacts import WorkflowArtifactError, load_manifest_bound_workflow
from adaos.services.skill.dependency_disk_guard import heavy_dependency_names, heavy_import_dependency_names

SCHEMA_PATH = Path(__file__).with_name("skill_schema.json")
WEBUI_SCHEMA_RES = ("adaos.abi", "webui.v1.schema.json")
_SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".runtime", "tests"}
_YJS_PATTERNS = (
    "y_py",
    "ypy_websocket",
    "YDoc",
    "apply_update",
    "encode_state_as_update",
    "encode_state_vector",
    "get_ydoc",
)
_DIRECT_PROJECTION_WRITE_CALLS = {
    "adaos.services.yjs.doc.async_get_ydoc",
    "adaos.services.yjs.doc.get_ydoc",
    "adaos.services.yjs.doc.mutate_live_room",
    "adaos.services.yjs.gateway.mutate_live_room",
    "y_py.apply_update",
}
_ASYNC_SUBSCRIPTION_BLOCKING_CALLS = {
    "adaos.sdk.data.skill_env_get",
    "adaos.sdk.data.skill_env_set",
    "adaos.sdk.data.skill_memory_get",
    "adaos.sdk.data.skill_memory_set",
    "adaos.sdk.data.skill_env.delete_env",
    "adaos.sdk.data.skill_env.get_env",
    "adaos.sdk.data.skill_env.read_env",
    "adaos.sdk.data.skill_env.set_env",
    "adaos.sdk.data.skill_env.write_env",
    "adaos.sdk.data.skill_memory.get",
    "adaos.sdk.data.skill_memory.set",
    "builtins.open",
    "httpx.delete",
    "httpx.get",
    "httpx.head",
    "httpx.patch",
    "httpx.post",
    "httpx.put",
    "open",
    "os.popen",
    "os.replace",
    "os.system",
    "requests.delete",
    "requests.get",
    "requests.head",
    "requests.patch",
    "requests.post",
    "requests.put",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "shutil.move",
    "shutil.rmtree",
    "socket.create_connection",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
    "time.sleep",
    "urllib.request.urlopen",
    "urllib.request.urlretrieve",
}
_ASYNC_BLOCKING_METHOD_SUFFIXES = {
    ".exists",
    ".glob",
    ".is_dir",
    ".is_file",
    ".iterdir",
    ".mkdir",
    ".open",
    ".read_bytes",
    ".read_text",
    ".rename",
    ".rglob",
    ".stat",
    ".touch",
    ".unlink",
    ".write_bytes",
    ".write_text",
}
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
_PERSONALIZATION_USES = {
    "browser_automation",
    "devices",
    "memory",
    "preferences",
    "profile",
    "tools",
    "user_private",
    "workspace",
}
_PERSONALIZATION_PERMISSION_PREFIXES = {
    "memory": ("memory.",),
    "preferences": ("preferences.",),
    "profile": ("profile.",),
    "devices": ("devices.",),
    "browser_automation": ("tools.invoke.browser_automation",),
    "tools": ("tools.",),
    "workspace": ("workspace.",),
    "user_private": ("profile.", "preferences.", "memory."),
}


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
    try:
        load_manifest_bound_workflow(
            skill_dir,
            manifest_name="skill.yaml",
            allow_legacy_inline=False,
        )
    except WorkflowArtifactError as exc:
        issues.append(Issue("error", "workflow.invalid", str(exc), "workflow.json"))
    if isinstance(data.get("conversational"), dict):
        conversational = compile_conversational_package(
            skill_dir,
            manifest_name="skill.yaml",
        )
        issues.extend(
            Issue(
                str(item.get("severity") or "error"),
                str(item.get("code") or "conversational.invalid"),
                str(item.get("message") or "conversational package validation failed"),
                str(item.get("path") or "conversational"),
            )
            for item in conversational.validation.report.get("diagnostics") or []
        )
    issues.extend(validate_data_route_contract(data))
    issues.extend(validate_provider_contract_declarations(data, install_mode=install_mode))
    issues.extend(_sdk_only_import_issues(skill_dir, manifest=data))
    issues.extend(_direct_projection_write_issues(skill_dir))
    issues.extend(_async_subscription_blocking_issues(skill_dir))
    issues.extend(_personalization_manifest_policy_issues(data, install_mode=install_mode))
    issues.extend(validate_dependency_isolation_contract(skill_dir, data, install_mode=install_mode))
    issues.extend(_conversation_native_static_checks(skill_dir, manifest=data, install_mode=install_mode))
    return issues


def validate_provider_contract_declarations(
    manifest: Dict[str, Any],
    *,
    install_mode: bool,
) -> List[Issue]:
    """Ensure a declared provider port is backed by public skill tools."""

    tools = {
        str(item.get("name") or "").strip()
        for item in manifest.get("tools") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    contracts = manifest.get("provider_contracts") or []
    if not isinstance(contracts, list):
        return [
            Issue(
                "error" if install_mode else "warning",
                "provider_contracts.invalid",
                "provider_contracts must be a list",
                "provider_contracts",
            )
        ]
    issues: List[Issue] = []
    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(contracts):
        where = f"provider_contracts[{index}]"
        if not isinstance(item, dict):
            issues.append(Issue("error", "provider_contracts.invalid", "provider contract must be an object", where))
            continue
        contract = str(item.get("contract") or "").strip()
        capability = str(item.get("capability") or "").strip()
        identity = (contract, capability)
        if not contract or not capability:
            issues.append(
                Issue(
                    "error" if install_mode else "warning",
                    "provider_contracts.identity_missing",
                    "provider contract requires non-empty contract and capability",
                    where,
                )
            )
        elif identity in identities:
            issues.append(Issue("error", "provider_contracts.duplicate", "provider contract identity is duplicated", where))
        identities.add(identity)
        operations = item.get("operations") or []
        if not isinstance(operations, list) or not operations:
            issues.append(
                Issue(
                    "error" if install_mode else "warning",
                    "provider_contracts.operations_missing",
                    "provider contract requires at least one public operation",
                    f"{where}.operations",
                )
            )
            continue
        missing = sorted(
            str(operation).strip()
            for operation in operations
            if str(operation).strip() not in tools
        )
        if missing:
            issues.append(
                Issue(
                    "error",
                    "provider_contracts.operations_unexported",
                    f"provider contract operations are not declared tools: {', '.join(missing)}",
                    f"{where}.operations",
                )
            )
    return issues


def _manifest_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_dependency_isolation_contract(
    skill_dir: Path,
    manifest: Dict[str, Any],
    *,
    install_mode: bool,
) -> List[Issue]:
    """Predict dependency-isolation failures before packaging or activation."""

    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    environment = runtime.get("env") if isinstance(runtime.get("env"), dict) else {}
    runtime_kind = str(runtime.get("kind") or "").strip().lower()
    raw_mode = environment.get("dependency_mode") or environment.get("install_mode")
    if raw_mode is None and runtime_kind != "service":
        raw_mode = environment.get("mode")
    mode = str(raw_mode or "auto").strip().lower()
    level = "error" if install_mode else "warning"
    issues: List[Issue] = []

    if mode == "venv" and runtime_kind != "service":
        issues.append(
            Issue(
                level,
                "runtime.dependencies.invalid_mode",
                "runtime.env.mode: venv requires runtime.kind: service",
                "runtime.env.mode",
            )
        )
    elif mode not in {"", "auto", "vendor", "shared", "core", "global", "venv"}:
        issues.append(
            Issue(
                level,
                "runtime.dependencies.invalid_mode",
                f"unsupported Python dependency install mode: {mode}",
                "runtime.env",
            )
        )

    dependency_args = [str(item) for item in manifest.get("dependencies") or []]
    requirements = Path(skill_dir) / "requirements.in"
    if requirements.is_file():
        try:
            for line in requirements.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    dependency_args.extend(shlex.split(stripped, comments=True, posix=True))
        except (OSError, UnicodeError, ValueError) as exc:
            issues.append(
                Issue(
                    level,
                    "runtime.dependencies.requirements_unreadable",
                    f"requirements.in cannot be evaluated by dependency policy: {exc}",
                    "requirements.in",
                )
            )
            return issues

    heavy = heavy_dependency_names(dependency_args)
    imported_roots: set[str] = set()
    for source in Path(skill_dir).rglob("*.py"):
        if any(part in {".git", ".runtime", "vendor", "artifacts", "__pycache__"} for part in source.parts):
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
    imported_heavy = set(heavy_import_dependency_names(imported_roots))
    declared_heavy = set(heavy)
    undeclared_heavy = sorted(imported_heavy - declared_heavy)
    if undeclared_heavy:
        issues.append(
            Issue(
                level,
                "runtime.dependencies.heavy_undeclared",
                "heavy/native imports require an explicit dependency declaration: "
                + ", ".join(undeclared_heavy),
                "dependencies",
            )
        )
    allow_keys = (
        "allow_heavy_dependencies",
        "allow_native_dependencies",
        "allow_unsafe_dependencies",
        "allow_heavy_vendor",
        "allow_native_vendor",
    )
    explicitly_allowed = any(
        _manifest_flag(config.get(key))
        for config in (environment, runtime)
        for key in allow_keys
    )
    if heavy and not explicitly_allowed:
        boundary = (
            "Keep heavy dependencies behind the service runtime boundary or explicitly set "
            "runtime.env.allow_heavy_dependencies: true for a controlled transitional install."
            if runtime_kind == "service"
            else "Use a service runtime boundary or explicitly set runtime.env.allow_heavy_dependencies: true "
            "for a controlled transitional install."
        )
        issues.append(
            Issue(
                level,
                "runtime.dependencies.heavy_isolation",
                f"heavy/native Python dependencies ({', '.join(heavy)}) violate the default isolation policy. {boundary}",
                "dependencies",
            )
        )
    return issues


def validate_data_route_contract(manifest: Dict[str, Any]) -> List[Issue]:
    """Validate causal, bounded browser data routes beyond JSON Schema shape."""

    issues: List[Issue] = []
    tools = {
        str(item.get("name") or "").strip()
        for item in manifest.get("tools") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    projections = {
        str(item.get("slot") or "").strip()
        for item in manifest.get("data_projections") or []
        if isinstance(item, dict) and str(item.get("slot") or "").strip()
    }
    tool_routes = {"tool", "details", "tool/details"}

    for index, route in enumerate(manifest.get("data_routes") or []):
        if not isinstance(route, dict):
            continue
        where = f"skill.yaml:data_routes[{index}]"
        kind = str(route.get("route") or "").strip()
        budget = route.get("budget") if isinstance(route.get("budget"), dict) else {}
        read_policy = route.get("read_policy") if isinstance(route.get("read_policy"), dict) else {}

        for field in ("first_paint", "recovery", "guard_visibility"):
            if not route.get(field):
                issues.append(Issue("warning", f"data_routes.{field}_missing", f"data route must declare {field}", f"{where}.{field}"))
        if not budget:
            issues.append(Issue("warning", "data_routes.budget_missing", "browser data route must declare a bounded budget", f"{where}.budget"))

        if kind in {"yjs", "stream", *tool_routes} and not budget.get("max_payload_bytes"):
            issues.append(Issue("warning", "data_routes.payload_budget_missing", f"{kind} route must declare budget.max_payload_bytes", f"{where}.budget.max_payload_bytes"))
        if kind == "stream" and not route.get("receiver"):
            issues.append(Issue("error", "data_routes.receiver_missing", "stream route must reference an exact receiver", f"{where}.receiver"))
        if kind == "yjs":
            slot = str(route.get("projection_slot") or "").strip()
            if not slot:
                issues.append(Issue("error", "data_routes.projection_missing", "Yjs route must reference an exact projection_slot", f"{where}.projection_slot"))
            elif slot not in projections:
                issues.append(Issue("error", "data_routes.projection_unknown", f"projection_slot '{slot}' is not declared in data_projections", f"{where}.projection_slot"))

        if kind in tool_routes:
            tool_name = str(route.get("tool") or "").strip()
            if not tool_name:
                issues.append(Issue("error", "data_routes.tool_missing", f"{kind} route must reference an exact tool", f"{where}.tool"))
            elif tool_name not in tools:
                issues.append(Issue("error", "data_routes.tool_unknown", f"tool '{tool_name}' is not declared in tools", f"{where}.tool"))
            if not read_policy:
                issues.append(Issue("warning", "data_routes.read_policy_missing", "tool-backed browser reads must declare their causal read_policy", f"{where}.read_policy"))
            else:
                triggers = set(read_policy.get("triggers") or [])
                if read_policy.get("mode") == "live":
                    issues.append(Issue("error", "data_routes.tool_live_read", "tool/details cannot be a live steady-state data route", f"{where}.read_policy.mode"))
                if "targeted_invalidation" in triggers and not read_policy.get("invalidation_tags"):
                    issues.append(Issue("error", "data_routes.invalidation_tags_missing", "targeted_invalidation requires one or more invalidation_tags", f"{where}.read_policy.invalidation_tags"))
            if budget.get("snapshot_policy") in {"on_subscribe", "on_subscribe_if_stale"}:
                issues.append(Issue("error", "data_routes.tool_snapshot_policy", "subscription snapshot_policy is invalid for a tool/details route", f"{where}.budget.snapshot_policy"))

    return issues


def _sdk_only_import_issues(skill_dir: Path, *, manifest: Dict[str, Any]) -> List[Issue]:
    """Enforce the opt-in SDK boundary for runtime skill code."""

    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    if runtime.get("sdk_only") is not True:
        return []

    issues: List[Issue] = []
    for path in sorted(skill_dir.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = _relative_to(path, skill_dir)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.append(node.module)
            for module in modules:
                if module == "adaos.sdk" or module.startswith("adaos.sdk."):
                    continue
                if module == "adaos" or module.startswith("adaos."):
                    issues.append(
                        Issue(
                            "error",
                            "runtime.sdk_only_import",
                            f"runtime.sdk_only permits only adaos.sdk imports; found {module}",
                            rel,
                        )
                    )
    return issues


def _ast_dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _ast_dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _direct_projection_write_issues(skill_dir: Path) -> List[Issue]:
    """Warn when skill code bypasses projection/SDK ownership for Yjs writes."""

    issues: List[Issue] = []
    for path in sorted(skill_dir.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = _relative_to(path, skill_dir)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue

        symbol_aliases: dict[str, str] = {}
        module_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    module_aliases[local] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for alias in node.names:
                    symbol_aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

        violations: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _ast_dotted_name(node.func)
            resolved = symbol_aliases.get(called, called)
            root, dot, suffix = resolved.partition(".")
            if resolved not in _DIRECT_PROJECTION_WRITE_CALLS and dot and root in module_aliases:
                resolved = f"{module_aliases[root]}.{suffix}"
            if resolved in _DIRECT_PROJECTION_WRITE_CALLS:
                violations.append((int(getattr(node, "lineno", 0) or 0), resolved))
        if not violations:
            continue
        first_line = min(line for line, _call in violations if line > 0)
        calls = ", ".join(sorted({call for _line, call in violations}))
        issues.append(
            Issue(
                "warning",
                "projection.direct_yjs_write",
                "direct write-capable Yjs access bypasses the declared projection contract "
                f"({calls}); use ctx_* setters/ProjectionService, or adaos.sdk.web.yjs for non-projection access",
                f"{rel}:{first_line}",
            )
        )
    return issues


def _async_subscription_blocking_issues(skill_dir: Path) -> List[Issue]:
    """Find synchronous I/O reachable from any async skill function.

    Detached tasks share the core event loop with channel handling just like
    async subscriptions do. The local call graph catches common helper
    wrappers so moving a blocking call one function away does not bypass
    strict validation.
    """

    issues: List[Issue] = []
    for path in sorted(skill_dir.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = _relative_to(path, skill_dir)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue

        symbol_aliases: dict[str, str] = {}
        module_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                for alias in node.names:
                    symbol_aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        function_class_scopes: dict[str, str | None] = {}

        def _collect_functions(
            body: list[ast.stmt],
            *,
            prefix: str = "",
            class_scope: str | None = None,
        ) -> None:
            for statement in body:
                if isinstance(statement, ast.ClassDef):
                    nested_class = f"{prefix}{statement.name}"
                    _collect_functions(
                        statement.body,
                        prefix=f"{nested_class}.",
                        class_scope=nested_class,
                    )
                    continue
                if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                key = f"{prefix}{statement.name}"
                functions[key] = statement
                function_class_scopes[key] = class_scope
                _collect_functions(
                    statement.body,
                    prefix=f"{key}.",
                    class_scope=class_scope,
                )

        _collect_functions(tree.body)
        direct_blocking: dict[str, list[tuple[int, str]]] = {name: [] for name in functions}
        local_calls: dict[str, set[str]] = {name: set() for name in functions}

        def _resolve_call(call: ast.Call) -> str:
            called = _ast_dotted_name(call.func)
            resolved = symbol_aliases.get(called, called)
            root, dot, suffix = resolved.partition(".")
            if dot and root in module_aliases:
                resolved = f"{module_aliases[root]}.{suffix}"
            return resolved

        def _local_function_key(called: str, *, function_key: str) -> str | None:
            class_scope = function_class_scopes.get(function_key)
            if class_scope and called.startswith(("self.", "cls.")):
                candidate = f"{class_scope}.{called.split('.', 1)[1]}"
                if candidate in functions:
                    return candidate
            if called in functions:
                return called
            parent = function_key.rpartition(".")[0]
            while parent:
                candidate = f"{parent}.{called}"
                if candidate in functions:
                    return candidate
                parent = parent.rpartition(".")[0]
            return None

        class _BlockingCallVisitor(ast.NodeVisitor):
            def __init__(self, function_name: str) -> None:
                self.function_name = function_name

            def visit_FunctionDef(self, nested: ast.FunctionDef) -> None:
                return

            def visit_AsyncFunctionDef(self, nested: ast.AsyncFunctionDef) -> None:
                return

            def visit_Lambda(self, nested: ast.Lambda) -> None:
                return

            def visit_Call(self, call: ast.Call) -> None:
                resolved = _resolve_call(call)
                called = _ast_dotted_name(call.func)
                local_function = _local_function_key(called, function_key=self.function_name)
                if local_function:
                    local_calls[self.function_name].add(local_function)
                is_blocking = (
                    resolved in _ASYNC_SUBSCRIPTION_BLOCKING_CALLS
                    or resolved.endswith(".result")
                    or any(
                        resolved == suffix.lstrip(".") or resolved.endswith(suffix)
                        for suffix in _ASYNC_BLOCKING_METHOD_SUFFIXES
                    )
                )
                if is_blocking:
                    direct_blocking[self.function_name].append(
                        (int(getattr(call, "lineno", 0) or 0), resolved)
                    )
                self.generic_visit(call)

        for name, function in functions.items():
            visitor = _BlockingCallVisitor(name)
            for statement in function.body:
                visitor.visit(statement)

        reachable_blocking = {name: list(items) for name, items in direct_blocking.items()}
        changed = True
        while changed:
            changed = False
            for name, calls in local_calls.items():
                inherited = list(reachable_blocking[name])
                for called in calls:
                    inherited.extend(reachable_blocking.get(called) or [])
                deduped = list(dict.fromkeys(inherited))
                if deduped != reachable_blocking[name]:
                    reachable_blocking[name] = deduped
                    changed = True

        for function_name, node in functions.items():
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            violations = reachable_blocking.get(function_name) or []
            if not violations:
                continue
            subscribed = any(
                isinstance(decorator, ast.Call)
                and _ast_dotted_name(decorator.func).split(".")[-1] == "subscribe"
                for decorator in node.decorator_list
            )
            positive_lines = [line for line, _call in violations if line > 0]
            first_line = min(positive_lines) if positive_lines else int(getattr(node, "lineno", 0) or 0)
            calls = ", ".join(sorted({call for _line, call in violations}))
            issue_code = (
                "runtime.async_subscription_blocking_call"
                if subscribed
                else "runtime.async_task_blocking_call"
            )
            issues.append(
                Issue(
                    "warning",
                    issue_code,
                    f"async skill function '{function_name}' can reach synchronous blocking APIs ({calls}); "
                    "move the operation behind await asyncio.to_thread(...) or a bounded SDK worker",
                    f"{rel}:{first_line}",
                )
            )
    return issues


def runtime_async_blocking_issues(skill_dir: Path) -> List[Issue]:
    """Return channel-safety violations that must block runtime handler import."""

    return _async_subscription_blocking_issues(Path(skill_dir))


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _manifest_permission_lists(manifest: Dict[str, Any]) -> tuple[list[str], list[str]]:
    required: list[str] = []
    optional: list[str] = []
    permissions = manifest.get("permissions")
    if isinstance(permissions, dict):
        required.extend(_as_string_list(permissions.get("required")))
        optional.extend(_as_string_list(permissions.get("optional")))
    personalization = manifest.get("personalization")
    if isinstance(personalization, dict):
        required.extend(_as_string_list(personalization.get("required_permissions")))
        optional.extend(_as_string_list(personalization.get("optional_permissions")))
    return required, optional


def _known_capability(value: str) -> bool:
    if value in CAPABILITY_VOCABULARY:
        return True
    if value.endswith(".*"):
        prefix = value[:-1]
        return any(str(item).startswith(prefix) for item in CAPABILITY_VOCABULARY)
    return False


def _personalization_manifest_policy_issues(manifest: Dict[str, Any], *, install_mode: bool) -> List[Issue]:
    issues: List[Issue] = []
    personalization = manifest.get("personalization")
    if personalization is not None and not isinstance(personalization, dict):
        return [
            Issue(
                "error",
                "personalization.invalid",
                "skill.yaml personalization section must be an object",
                "skill.yaml:personalization",
            )
        ]
    personalization_data = personalization if isinstance(personalization, dict) else {}
    uses = _as_string_list(personalization_data.get("uses"))
    for value in uses:
        if value not in _PERSONALIZATION_USES:
            issues.append(
                Issue(
                    "error",
                    "personalization.uses.invalid",
                    f"unsupported personalization use: {value}",
                    "skill.yaml:personalization.uses",
                )
            )
    for key in ("role_variants", "user_variants", "device_variants"):
        value = personalization_data.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            issues.append(
                Issue(
                    "error",
                    f"personalization.{key}.invalid",
                    f"personalization.{key} must be an object or list",
                    f"skill.yaml:personalization.{key}",
                )
            )
    required, optional = _manifest_permission_lists(manifest)
    for capability in [*required, *optional]:
        try:
            validate_capability(capability)
        except Exception as exc:
            issues.append(
                Issue(
                    "error",
                    "permissions.capability.invalid",
                    f"invalid capability declaration {capability!r}: {exc}",
                    "skill.yaml:permissions",
                )
            )
            continue
        if not _known_capability(capability):
            issues.append(
                Issue(
                    "error",
                    "permissions.capability.unknown",
                    f"unknown capability declaration: {capability}",
                    "skill.yaml:permissions",
                )
            )
    required_set = set(required)
    for use in uses:
        prefixes = _PERSONALIZATION_PERMISSION_PREFIXES.get(use) or ()
        if not prefixes:
            continue
        if any(any(item.startswith(prefix) for prefix in prefixes) for item in required_set):
            continue
        issues.append(
            Issue(
                "error" if install_mode else "warning",
                "personalization.permissions_missing",
                f"personalization use '{use}' has no matching required permission declaration",
                "skill.yaml:personalization.required_permissions",
            )
        )
    return issues


def _conversation_native_static_checks(skill_dir: Path, *, manifest: Dict[str, Any], install_mode: bool) -> List[Issue]:
    issues: List[Issue] = []
    uses_conversation_sdk = False
    uses_memory_sdk = False
    declared_transport_tokens = _declared_transport_tokens(skill_dir, manifest)
    skill_name = str(manifest.get("name") or "").strip()
    bounded_memory_names = _bounded_process_memory_names(manifest)
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
                        "error" if install_mode else "warning",
                        "conversation.unsafe_direct_yjs",
                        f"direct Yjs symbol used: {pattern}; generated skills must use declared projections/routes",
                        rel,
                    )
                )
                break
        for pattern in _TRANSPORT_MEMORY_PATTERNS:
            if pattern in text:
                if _transport_memory_pattern_allowed(pattern, declared_transport_tokens, skill_name):
                    continue
                issues.append(
                    Issue(
                        "error" if install_mode else "warning",
                        "conversation.transport_owned_memory",
                        f"transport-owned chat/memory reference used: {pattern}; use adaos.sdk.conversation/memory",
                        rel,
                    )
                )
        issues.extend(
            _conversation_memory_ast_issues(
                path,
                rel,
                text,
                install_mode=install_mode,
                bounded_memory_names=bounded_memory_names,
            )
        )
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


def _declared_transport_tokens(skill_dir: Path, manifest: Dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for route in manifest.get("data_routes") or []:
        if not isinstance(route, dict):
            continue
        for key in ("receiver", "projection_slot", "tool", "owner"):
            value = str(route.get(key) or "").strip()
            if value:
                tokens.add(value)
    webui_path = skill_dir / "webui.json"
    if webui_path.exists():
        try:
            webui = json.loads(webui_path.read_text(encoding="utf-8-sig") or "{}")
        except Exception:
            webui = {}
        receivers = ((webui.get("webio") or {}).get("receivers") or {}) if isinstance(webui, dict) else {}
        if isinstance(receivers, dict):
            tokens.update(str(key) for key in receivers.keys() if str(key).strip())
    return tokens


def _transport_memory_pattern_allowed(pattern: str, declared_tokens: set[str], skill_name: str) -> bool:
    if pattern == skill_name:
        return True
    return pattern in declared_tokens


def _memory_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _bounded_process_memory_names(manifest: Dict[str, Any]) -> set[str]:
    budget = manifest.get("memory_budget") if isinstance(manifest.get("memory_budget"), dict) else {}
    names: set[str] = set()
    for cache in budget.get("caches") or []:
        if not isinstance(cache, dict):
            continue
        name = str(cache.get("name") or "").strip()
        if not name:
            continue
        has_bound = any(cache.get(key) is not None for key in ("max_items", "max_bytes", "ttl_seconds"))
        if has_bound and str(cache.get("cleanup_hook") or "").strip():
            names.add(_memory_token(name))
    return names


def _module_memory_name_is_bounded(name: str, bounded_memory_names: set[str]) -> bool:
    token = _memory_token(name.strip("_"))
    if not token:
        return False
    return any(token in declared or declared in token for declared in bounded_memory_names)


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


def _conversation_memory_ast_issues(
    path: Path,
    rel: str,
    text: str,
    *,
    install_mode: bool,
    bounded_memory_names: set[str],
) -> List[Issue]:
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
        unbounded_names = [name for name in names if not _module_memory_name_is_bounded(name, bounded_memory_names)]
        if not unbounded_names:
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Dict, ast.Set)):
            issues.append(
                Issue(
                    "error" if install_mode else "warning",
                    "conversation.unbounded_process_memory",
                    f"module-level mutable state may become unbounded: {', '.join(unbounded_names)}",
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
                        f"module-level mutable state may become unbounded: {', '.join(unbounded_names)}",
                        rel,
                    )
                )
            if func_name == "deque" and not any(kw.arg == "maxlen" for kw in value.keywords):
                issues.append(
                    Issue(
                        "error" if install_mode else "warning",
                        "conversation.unbounded_process_memory",
                        f"deque without maxlen may become unbounded: {', '.join(unbounded_names)}",
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
import os, json, importlib.util, sys, types
os.environ['ADAOS_VALIDATE'] = '1'
if 'y_py' not in sys.modules:
    sys.modules['y_py'] = types.SimpleNamespace(
        YDoc=object,
        apply_update=lambda *args, **kwargs: None,
        encode_state_as_update=lambda *args, **kwargs: b'',
        encode_state_vector=lambda *args, **kwargs: b'',
    )
if 'ypy_websocket' not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules['ypy_websocket'] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules['ypy_websocket.ystore'] = ystore_mod
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
