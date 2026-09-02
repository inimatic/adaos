from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator, Draft7Validator, ValidationError

from adaos.services import conversation_links, conversation_safety
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.runtime_paths import current_base_dir, current_repo_root, current_state_dir
from adaos.services.skill.validation import validate_data_route_contract


_ARTIFACT_ID_RE = re.compile(r"^[a-z0-9_.-]+$")
_SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".runtime"}
_TEXT_SUFFIXES = {
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".md",
    ".txt",
    ".intent",
    ".toml",
    ".ini",
    ".cfg",
    ".html",
    ".css",
    ".ts",
    ".js",
}
_VALID_ROUTES = {"yjs", "stream", "tool", "details", "tool/details", "skill-local", "disk", "360log", "disk/360log"}
_YJS_PATTERNS = (
    "y_py",
    "ypy_websocket",
    "YDoc",
    "apply_update",
    "encode_state_as_update",
    "encode_state_vector",
    "get_ydoc",
)
_MEMORY_NAME_RE = re.compile(r"(cache|history|histories|events|logs|frames|sessions|state|buffer|queue)", re.I)
_APPROVAL_PROFILES: dict[str, dict[str, Any]] = {
    "manual_only": {
        "id": "manual_only",
        "title": "Manual only",
        "summary": "Every Builder preview requires explicit human review before apply.",
        "auto_draft": False,
        "auto_preview": True,
        "auto_apply": False,
        "requires_human_review": "always",
    },
    "low_risk_auto_draft": {
        "id": "low_risk_auto_draft",
        "title": "Low-risk auto-draft",
        "summary": "Builder may create and preview low-risk drafts, but apply still requires review.",
        "auto_draft": True,
        "auto_preview": True,
        "auto_apply": False,
        "requires_human_review": "before_apply",
    },
    "low_risk_auto_apply": {
        "id": "low_risk_auto_apply",
        "title": "Low-risk auto-apply",
        "summary": "Only clean low-risk previews without mandatory-review classes are eligible for automatic apply.",
        "auto_draft": True,
        "auto_preview": True,
        "auto_apply": True,
        "requires_human_review": "on_policy_block",
    },
    "restricted_maintenance_repair": {
        "id": "restricted_maintenance_repair",
        "title": "Restricted maintenance repair",
        "summary": "Allows narrow descriptor, NLU-hint, and metadata repairs when no mandatory-review class is present.",
        "auto_draft": True,
        "auto_preview": True,
        "auto_apply": True,
        "requires_human_review": "on_policy_block",
        "allowed_surfaces": ["manifest", "nlu", "webui"],
    },
}
_MANDATORY_REVIEW_CLASSES: dict[str, str] = {
    "secrets": "Secrets or credential-like material changed.",
    "new_permissions": "Permissions or capability declarations changed.",
    "external_io": "Generated code may call external networks, filesystems, processes, or sockets.",
    "filesystem": "Generated code or tool hints may read or write local files outside a preview-only context.",
    "network": "Generated code or tool hints may call external networks or send data outside the node.",
    "cross_node": "Generated code or tool hints may read from or mutate another node.",
    "device_control": "Generated code or tool hints may control devices, endpoints, browsers, or relays.",
    "credential": "Generated code or tool hints may handle credentials, secrets, or tokens.",
    "destructive_actions": "Action hints include destructive or lifecycle-changing operations.",
    "endpoint_control": "Generated code or metadata may control endpoints, tunnels, browsers, or runtime routes.",
    "high_rate_streams": "Streams or projections can exceed low-risk event budgets.",
    "broad_nlu_patterns": "NLU examples or aliases are broad enough to affect unrelated utterances.",
    "service_processes": "Generated code may spawn or manage long-running processes.",
}
_SECRET_HINT_RE = re.compile(r"(secret|api[_-]?key|access[_-]?token|private[_-]?key|password|credential)", re.I)
_PERMISSION_HINT_RE = re.compile(r"^\+\s*(permissions|capabilities|security|scopes)\s*[:=]", re.I | re.M)
_EXTERNAL_IO_RE = re.compile(r"^\+\s*(import|from)\s+(requests|httpx|aiohttp|socket|websocket|urllib|ftplib|smtplib)\b", re.I | re.M)
_PROCESS_RE = re.compile(r"^\+\s*(import|from)\s+(subprocess|multiprocessing)\b|Popen\(|run\(", re.I | re.M)
_ENDPOINT_RE = re.compile(r"(endpoint|websocket|tunnel|route[_-]?reset|browser[_-]?route|control[_-]?plane)", re.I)
_DESTRUCTIVE_ACTION_RE = re.compile(r"(delete|remove|purge|drop|format|shutdown|restart|reset|rollback|deactivate|kill)", re.I)


class BuilderSourceRecoveryRequired(RuntimeError):
    def __init__(self, plan: Mapping[str, Any]):
        self.plan = dict(plan)
        status = str(plan.get("status") or "review_required")
        digest = str(plan.get("plan_digest") or "unavailable")
        super().__init__(
            "development source materialization requires reviewed recovery "
            f"(status={status}, plan={digest})"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("._-")
    return text or "builder"


def _rewrite_skill_template_refs(value: Any, artifact_id: str) -> Any:
    target = str(artifact_id or "").strip()
    if not target:
        return value
    if isinstance(value, dict):
        return {key: _rewrite_skill_template_refs(item, target) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_skill_template_refs(item, target) for item in value]
    if isinstance(value, str):
        label = target.replace("_", " ").replace("-", " ").strip().title() or target
        return value.replace("new_skill", target).replace("New Skill", label)
    return value


def _stable_suffix(*parts: object) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part or "").encode("utf-8", errors="ignore"))
        h.update(b"\0")
    return h.hexdigest()[:10]


def _issue(level: str, code: str, message: str, where: str | None = None) -> dict[str, str]:
    out = {"level": level, "code": code, "message": message}
    if where:
        out["where"] = where
    return out


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a YAML object")
    return data


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _merge_unique_list(existing: Any, incoming: Any) -> list[Any]:
    out: list[Any] = []
    for value in (existing if isinstance(existing, list) else []):
        if value not in out:
            out.append(value)
    for value in (incoming if isinstance(incoming, list) else []):
        if value not in out:
            out.append(value)
    return out


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in (patch or {}).items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        elif isinstance(value, list):
            base[key] = _merge_unique_list(base.get(key), value)
        else:
            base[key] = value
    return base


def _copytree(src: Path, dst: Path) -> None:
    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in _SKIP_DIRS}

    shutil.copytree(src, dst, ignore=_ignore)


def _source_tree_snapshot(root: Path) -> dict[str, Any]:
    source = Path(root).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source tree not found: {source}")
    files: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(source)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"source tree must not contain symlinks: {relative.as_posix()}")
        if not path.is_file():
            continue
        payload = path.read_bytes()
        files.append(
            {
                "path": relative.as_posix(),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    identity = json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        "root": str(source),
        "file_count": len(files),
        "size_bytes": sum(int(item["size"]) for item in files),
        "digest": "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "files": files,
    }


def _is_text_file(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return True
    try:
        chunk = path.read_bytes()[:1024]
    except Exception:
        return False
    return b"\0" not in chunk


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.name


def _load_abi_schema(name: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return _read_json(root / "abi" / name)


def _load_runtime_skill_schema() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return _read_json(root / "services" / "skill" / "skill_schema.json")


def _ctx_path(paths: Any, attr: str) -> Path | None:
    try:
        getter = getattr(paths, attr)
        raw = getter() if callable(getter) else getter
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _ctx_config_dev_workspace(ctx: Any) -> Path | None:
    try:
        config = getattr(ctx, "config", None)
        base_dir = _ctx_path(ctx.paths, "base_dir")
        if base_dir is None:
            return None
        workspace = str(getattr(getattr(config, "dev_settings", None), "workspace", "") or "").strip()
        if workspace and workspace != "dev":
            path = Path(workspace).expanduser()
            if not path.is_absolute():
                path = base_dir / path
            return path.resolve()
        subnet_id = str(getattr(config, "subnet_id", "") or "").strip()
        if subnet_id:
            return (base_dir / "dev" / subnet_id).resolve()
    except Exception:
        return None
    return None


def _config_dev_workspace_from_base(base_dir: Path) -> Path | None:
    node_path = Path(base_dir).expanduser().resolve() / "node.yaml"
    try:
        data = yaml.safe_load(node_path.read_text(encoding="utf-8-sig")) if node_path.is_file() else {}
    except (OSError, ValueError, yaml.YAMLError):
        data = {}
    payload = data if isinstance(data, Mapping) else {}
    dev = payload.get("dev") if isinstance(payload.get("dev"), Mapping) else {}
    workspace = str(dev.get("workspace") or "").strip()
    if workspace and workspace != "dev":
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            path = Path(base_dir) / path
        return path.resolve()
    subnet = payload.get("subnet") if isinstance(payload.get("subnet"), Mapping) else {}
    subnet_id = str(
        payload.get("subnet_id")
        or subnet.get("id")
        or subnet.get("bootstrap_id")
        or ""
    ).strip()
    if subnet_id:
        return (Path(base_dir) / "dev" / subnet_id).resolve()
    return (Path(base_dir) / "dev").resolve()


@dataclass(slots=True)
class BuilderWorkspaceService:
    """Create draft workspaces and preview bundles without mutating runtime state."""

    state_dir: Path | None = None
    builder_root: Path | None = None
    repo_root: Path | None = None
    workspace_root: Path | None = None
    skills_root: Path | None = None
    scenarios_root: Path | None = None
    dev_skills_root: Path | None = None
    dev_scenarios_root: Path | None = None
    developer_service: Any | None = None

    @classmethod
    def from_context(cls) -> "BuilderWorkspaceService":
        repo_root = current_repo_root()
        state_dir = current_state_dir()
        base_dir = current_base_dir()
        builder_root = None
        workspace_root = None
        skills_root = None
        scenarios_root = None
        dev_workspace = None
        dev_skills_root = None
        dev_scenarios_root = None
        developer_service = None
        try:
            from adaos.services.agent_context import get_ctx
            from adaos.services.root.service import RootDeveloperService

            ctx = get_ctx()
            developer_service = RootDeveloperService()
            workspace_root = _ctx_path(ctx.paths, "workspace_dir")
            skills_root = _ctx_path(ctx.paths, "skills_dir")
            scenarios_root = _ctx_path(ctx.paths, "scenarios_dir")
            dev_workspace = _ctx_config_dev_workspace(ctx)
            if dev_workspace is not None:
                dev_skills_root = dev_workspace / "skills"
                dev_scenarios_root = dev_workspace / "scenarios"
            else:
                dev_skills_root = _ctx_path(ctx.paths, "dev_skills_dir")
                dev_scenarios_root = _ctx_path(ctx.paths, "dev_scenarios_dir")
        except Exception:
            if repo_root is not None:
                workspace_root = repo_root / ".adaos" / "workspace"
                skills_root = workspace_root / "skills"
                scenarios_root = workspace_root / "scenarios"
        if workspace_root is None:
            workspace_root = (base_dir / "workspace").resolve()
        if skills_root is None:
            skills_root = workspace_root / "skills"
        if scenarios_root is None:
            scenarios_root = workspace_root / "scenarios"
        if dev_workspace is None:
            dev_workspace = _config_dev_workspace_from_base(base_dir)
        if dev_workspace is not None:
            if dev_skills_root is None:
                dev_skills_root = dev_workspace / "skills"
            if dev_scenarios_root is None:
                dev_scenarios_root = dev_workspace / "scenarios"
        if developer_service is None:
            try:
                from adaos.services.root.service import RootDeveloperService

                developer_service = RootDeveloperService()
            except Exception:
                developer_service = None
        return cls(
            state_dir=state_dir,
            builder_root=builder_root,
            repo_root=repo_root,
            workspace_root=workspace_root,
            skills_root=skills_root,
            scenarios_root=scenarios_root,
            dev_skills_root=dev_skills_root,
            dev_scenarios_root=dev_scenarios_root,
            developer_service=developer_service,
        )

    @property
    def root(self) -> Path:
        if self.builder_root is not None:
            path = Path(self.builder_root).expanduser().resolve()
        else:
            path = Path(self.state_dir or current_state_dir()) / "builder"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def drafts_dir(self) -> Path:
        path = self.root / "drafts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def previews_dir(self) -> Path:
        path = self.root / "previews"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def realize_requests_dir(self) -> Path:
        path = self.root / "realize_requests"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def approval_profiles(self) -> list[dict[str, Any]]:
        return [dict(profile) for profile in _APPROVAL_PROFILES.values()]

    def _dev_artifact_root(self, kind: str, artifact_id: str) -> Path:
        if kind == "skill":
            root = self.dev_skills_root
            if root is None and self.repo_root is not None:
                root = Path(self.repo_root) / ".adaos" / "dev" / "skills"
        elif kind == "scenario":
            root = self.dev_scenarios_root
            if root is None and self.repo_root is not None:
                root = Path(self.repo_root) / ".adaos" / "dev" / "scenarios"
        else:
            raise ValueError("kind must be skill or scenario")
        if root is None:
            raise ValueError("AdaOS dev workspace is not available in the current context")
        return (Path(root).expanduser().resolve() / artifact_id).resolve()

    def _dev_projects_root(self) -> Path:
        for root in (self.dev_scenarios_root, self.dev_skills_root):
            if root is not None:
                return (Path(root).expanduser().resolve().parent / "projects").resolve()
        raise ValueError("AdaOS dev workspace is not available in the current context")

    def _workspace_artifact_root(self, kind: str, artifact_id: str) -> Path | None:
        normalized = str(kind or "").strip().lower().rstrip("s")
        if normalized not in {"skill", "scenario"}:
            return None
        roots: list[Path] = []
        if normalized == "skill":
            if self.skills_root is not None:
                roots.append(Path(self.skills_root))
            if self.workspace_root is not None:
                roots.append(Path(self.workspace_root) / "skills")
        else:
            if self.scenarios_root is not None:
                roots.append(Path(self.scenarios_root))
            if self.workspace_root is not None:
                roots.append(Path(self.workspace_root) / "scenarios")
        if self.repo_root is not None:
            roots.append(Path(self.repo_root) / ".adaos" / "workspace" / f"{normalized}s")
        for root in dict.fromkeys(root.resolve() for root in roots):
            candidate = (root / artifact_id).resolve()
            if candidate.is_dir():
                return candidate
        return None

    def _workspace_projects_root(self) -> Path | None:
        roots: list[Path] = []
        if self.workspace_root is not None:
            roots.append(Path(self.workspace_root) / "projects")
        if self.repo_root is not None:
            roots.append(Path(self.repo_root) / ".adaos" / "workspace" / "projects")
        for root in dict.fromkeys(root.resolve() for root in roots):
            if root.is_dir():
                return root
        return None

    def _dev_project_root(self, project_id: str) -> Path | None:
        try:
            return (self._dev_projects_root() / _slug(project_id)).resolve()
        except ValueError:
            return None

    def _workspace_project_root(self, project_id: str) -> Path | None:
        root = self._workspace_projects_root()
        if root is None:
            return None
        candidate = (root / _slug(project_id)).resolve()
        return candidate if candidate.is_dir() else None

    def _read_workspace_project_manifest(self, project_id: str) -> tuple[Path, dict[str, Any]] | None:
        root = self._workspace_project_root(project_id)
        if root is None:
            return None
        for name in ("project.yaml", "project.yml", "project.json"):
            path = root / name
            if not path.is_file():
                continue
            try:
                data = _read_json(path) if path.suffix == ".json" else _read_yaml(path)
            except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError):
                data = {}
            if isinstance(data, Mapping):
                return path, dict(data)
        return None

    @staticmethod
    def _project_owned_component_refs(manifest: Mapping[str, Any]) -> list[str]:
        components = manifest.get("components") if isinstance(manifest.get("components"), Mapping) else {}
        refs = [
            str(item.get("ref") or "").strip()
            for item in components.get("owned") or []
            if isinstance(item, Mapping) and str(item.get("ref") or "").strip()
        ]
        return list(dict.fromkeys(refs))

    def _workspace_project_ids_owning_ref(self, component_ref: str) -> list[str]:
        token = str(component_ref or "").strip()
        if not token:
            return []
        root = self._workspace_projects_root()
        if root is None:
            return []
        matches: list[str] = []
        for child in sorted(item for item in root.iterdir() if item.is_dir()):
            manifest_info = self._read_workspace_project_manifest(child.name)
            if manifest_info is None:
                continue
            _, manifest = manifest_info
            if token in self._project_owned_component_refs(manifest):
                project_id = str(manifest.get("id") or child.name).strip() or child.name
                if project_id not in matches:
                    matches.append(project_id)
        return matches

    def _dev_project_ids_owning_ref(self, component_ref: str) -> list[str]:
        token = str(component_ref or "").strip()
        if not token:
            return []
        root = self._dev_projects_root()
        if not root.is_dir():
            return []
        matches: list[str] = []
        for child in sorted(item for item in root.iterdir() if item.is_dir()):
            manifest_path = child / "project.yaml"
            if not manifest_path.is_file():
                continue
            try:
                manifest = _read_yaml(manifest_path)
            except (OSError, ValueError, yaml.YAMLError):
                continue
            if token in self._project_owned_component_refs(manifest):
                project_id = str(manifest.get("id") or child.name).strip() or child.name
                if project_id not in matches:
                    matches.append(project_id)
        return matches

    @staticmethod
    def _default_component_project_id(kind: str, artifact_id: str) -> str:
        token = _slug(artifact_id)
        suffix = f"_{kind}"
        if token.endswith(suffix) and len(token) > len(suffix):
            token = token[: -len(suffix)]
        return token

    def ensure_owning_dev_project(
        self,
        *,
        kind: str,
        artifact_id: str,
        project_id: str | None = None,
        actor: str = "builder.qualifier",
    ) -> dict[str, Any]:
        """Ensure a standalone DEV component has one authoritative owning Project."""

        normalized_kind = str(kind or "").strip().lower().rstrip("s")
        artifact_token = _slug(artifact_id)
        if normalized_kind not in {"skill", "scenario"}:
            raise ValueError("kind must be skill or scenario")
        component_ref = f"{normalized_kind}:{artifact_token}"
        owners = self._dev_project_ids_owning_ref(component_ref)
        if len(owners) == 1:
            root = self._dev_project_root(owners[0])
            return {
                "schema": "adaos.builder.owning_project_resolution.v1",
                "status": "source_available",
                "created": False,
                "component_ref": component_ref,
                "project_id": owners[0],
                "project_ref": f"project:{owners[0]}",
                "project_source_path": str(root) if root is not None else None,
            }
        if len(owners) > 1:
            return {
                "schema": "adaos.builder.owning_project_resolution.v1",
                "status": "ambiguous",
                "created": False,
                "component_ref": component_ref,
                "project_ids": owners,
                "reason": "component is owned by multiple DEV Projects",
            }
        workspace_owners = self._workspace_project_ids_owning_ref(component_ref)
        if workspace_owners:
            return {
                "schema": "adaos.builder.owning_project_resolution.v1",
                "status": "needs_materialization",
                "created": False,
                "component_ref": component_ref,
                "project_id": workspace_owners[0] if len(workspace_owners) == 1 else None,
                "project_ids": workspace_owners,
                "reason": (
                    "owning Workspace Project must be materialized"
                    if len(workspace_owners) == 1
                    else "component has ambiguous Workspace Project owners"
                ),
            }
        component_root = self._dev_artifact_root(normalized_kind, artifact_token)
        if not component_root.is_dir():
            return {
                "schema": "adaos.builder.owning_project_resolution.v1",
                "status": "needs_source",
                "created": False,
                "component_ref": component_ref,
                "reason": "component DEV source is unavailable",
            }

        requested_id = _slug(
            project_id
            or self._default_component_project_id(normalized_kind, artifact_token)
        )
        projects_root = self._dev_projects_root()
        projects_root.mkdir(parents=True, exist_ok=True)
        candidate_id = requested_id
        candidate_root = projects_root / candidate_id
        if candidate_root.exists():
            candidate_id = _slug(f"{requested_id}_project")
            candidate_root = projects_root / candidate_id
        if candidate_root.exists():
            suffix = hashlib.sha256(component_ref.encode("utf-8")).hexdigest()[:8]
            candidate_id = _slug(f"{requested_id}_{suffix}")
            candidate_root = projects_root / candidate_id
        if candidate_root.exists():
            raise ValueError(f"unable to allocate owning DEV Project for {component_ref}")

        manifest_name = "skill.yaml" if normalized_kind == "skill" else "scenario.yaml"
        component_manifest: dict[str, Any] = {}
        if (component_root / manifest_name).is_file():
            try:
                component_manifest = _read_yaml(component_root / manifest_name)
            except (OSError, ValueError, yaml.YAMLError):
                component_manifest = {}
        title = str(
            component_manifest.get("title")
            or component_manifest.get("name")
            or artifact_token.replace("_", " ").replace("-", " ").title()
        ).strip()
        description = str(component_manifest.get("description") or "").strip()
        owned = {
            "ref": component_ref,
            "role": "primary",
            "exposure": "application",
            "lifecycle": "bound",
            "relations": ["presents" if normalized_kind == "scenario" else "uses"],
        }
        entrypoints = (
            [
                {
                    "id": "default",
                    "presentation": component_ref,
                    "default": True,
                    "bindings": {},
                }
            ]
            if normalized_kind == "scenario"
            else []
        )
        payload = {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": candidate_id,
            "version": "0.1.0",
            "profiles": ["adaos.builder.component_project.v1"],
            "components": {"owned": [owned], "dependencies": []},
            "entrypoints": entrypoints,
            "catalog": {
                "title": title,
                "description": description,
                "categories": ["development"],
                "tags": ["builder-managed", "legacy-component"],
            },
            "publication": {
                "stage": "alpha",
                "visibility": "unlisted",
                "channel": "stable",
            },
            "install": {
                "default": False,
                "features": [
                    {
                        "id": "default",
                        "title": title,
                        "default": True,
                        "optional": False,
                        "components": [component_ref],
                    }
                ],
            },
            "compatibility": {},
            "lifecycle": {
                "uninstall": {
                    "components": "remove_if_unreferenced",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            },
            "created_at": _now_iso(),
            "created_by": str(actor or "builder.qualifier"),
        }
        from adaos.sdk.developer.compositions import validate

        validated = validate(payload)
        _write_yaml(candidate_root / "project.yaml", validated)
        return {
            "schema": "adaos.builder.owning_project_resolution.v1",
            "status": "created",
            "created": True,
            "component_ref": component_ref,
            "project_id": candidate_id,
            "project_ref": f"project:{candidate_id}",
            "project_source_path": str(candidate_root.resolve()),
            "manifest_path": str((candidate_root / "project.yaml").resolve()),
        }

    def development_source_status(
        self,
        *,
        kind: str,
        artifact_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower().rstrip("s")
        artifact_id = _slug(artifact_id)
        if normalized_kind not in {"project", "scenario", "skill"}:
            return {
                "status": "needs_materialization",
                "source": "unknown",
                "target_type": normalized_kind or "unknown",
                "target_id": artifact_id or None,
                "options": ["materialize_dev_source", "create_local_fork", "create_runtime_overlay", "defer"],
                "default_option": "materialize_dev_source",
            }
        if normalized_kind == "project":
            dev_project = self._dev_project_root(artifact_id)
            if dev_project is not None and dev_project.is_dir():
                return {
                    "status": "source_available",
                    "source": "dev",
                    "target_type": "project",
                    "target_id": artifact_id,
                    "project_id": artifact_id,
                    "dev_source_path": str(dev_project),
                    "options": ["use_existing_dev_source"],
                    "default_option": "use_existing_dev_source",
                }
            workspace_project = self._workspace_project_root(artifact_id)
            source = "workspace" if workspace_project is not None else "unknown"
            return {
                "status": "needs_materialization",
                "source": source,
                "target_type": "project",
                "target_id": artifact_id,
                "project_id": artifact_id,
                "source_path": str(workspace_project) if workspace_project is not None else None,
                "options": ["materialize_dev_source", "create_local_fork", "create_runtime_overlay", "defer"],
                "default_option": "materialize_dev_source",
            }
        try:
            dev_artifact = self._dev_artifact_root(normalized_kind, artifact_id)
        except ValueError:
            dev_artifact = None
        workspace_artifact = self._workspace_artifact_root(
            normalized_kind,
            artifact_id,
        )
        owners = (
            [project_id]
            if project_id
            else self._workspace_project_ids_owning_ref(
                f"{normalized_kind}:{artifact_id}"
            )
        )
        owners = list(dict.fromkeys(str(item).strip() for item in owners if str(item).strip()))
        if len(owners) == 1:
            owning_project_id = owners[0]
            workspace_project = self._workspace_project_root(owning_project_id)
            dev_project = self._dev_project_root(owning_project_id)
            if workspace_project is not None and (dev_project is None or not dev_project.is_dir()):
                return {
                    "status": "needs_materialization",
                    "source": "workspace",
                    "reason": "owning_project_not_in_devspace",
                    "target_type": normalized_kind,
                    "target_id": artifact_id,
                    "project_id": owning_project_id,
                    "project_ids": owners,
                    "source_path": str(workspace_artifact) if workspace_artifact is not None else None,
                    "project_source_path": str(workspace_project),
                    "orphaned_dev_source_path": (
                        str(dev_artifact)
                        if dev_artifact is not None and dev_artifact.is_dir()
                        else None
                    ),
                    "options": [
                        "materialize_dev_source",
                        "create_local_fork",
                        "create_runtime_overlay",
                        "defer",
                    ],
                    "default_option": "materialize_dev_source",
                }
        elif len(owners) > 1:
            return {
                "status": "needs_materialization",
                "source": "workspace",
                "reason": "ambiguous_project_owners",
                "target_type": normalized_kind,
                "target_id": artifact_id,
                "project_id": None,
                "project_ids": owners,
                "ambiguous_project_owners": owners,
                "source_path": str(workspace_artifact) if workspace_artifact is not None else None,
                "options": ["create_local_fork", "defer"],
                "default_option": "defer",
            }
        if dev_artifact is not None and dev_artifact.is_dir():
            return {
                "status": "source_available",
                "source": "dev",
                "target_type": normalized_kind,
                "target_id": artifact_id,
                "project_id": project_id or None,
                "dev_source_path": str(dev_artifact),
                "options": ["use_existing_dev_source"],
                "default_option": "use_existing_dev_source",
            }
        source = "workspace" if workspace_artifact is not None or owners else "unknown"
        return {
            "status": "needs_materialization",
            "source": source,
            "target_type": normalized_kind,
            "target_id": artifact_id,
            "project_id": owners[0] if len(owners) == 1 else None,
            "project_ids": owners,
            "ambiguous_project_owners": owners if len(owners) > 1 else [],
            "source_path": str(workspace_artifact) if workspace_artifact is not None else None,
            "options": ["materialize_dev_source", "create_local_fork", "create_runtime_overlay", "defer"],
            "default_option": "materialize_dev_source",
        }

    def development_source_recovery_plan(
        self,
        *,
        kind: str,
        artifact_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        from adaos.services.builder.source_recovery import BuilderSourceRecoveryService

        if self.workspace_root is None:
            raise ValueError("AdaOS workspace is not available in the current context")
        if self.dev_skills_root is None or self.dev_scenarios_root is None:
            raise ValueError("AdaOS dev workspace is not available in the current context")
        return BuilderSourceRecoveryService(
            state_dir=Path(self.state_dir or current_state_dir()),
            workspace_root=Path(self.workspace_root),
            dev_skills_root=Path(self.dev_skills_root),
            dev_scenarios_root=Path(self.dev_scenarios_root),
            dev_projects_root=self._dev_projects_root(),
        ).plan(
            kind=kind,
            artifact_id=artifact_id,
            project_id=project_id,
        )

    def apply_development_source_recovery(
        self,
        *,
        kind: str,
        artifact_id: str,
        expected_plan_digest: str,
        decisions: Mapping[str, str] | None = None,
        project_id: str | None = None,
        actor: str = "builder",
    ) -> dict[str, Any]:
        from adaos.services.builder.source_recovery import BuilderSourceRecoveryService

        if self.workspace_root is None:
            raise ValueError("AdaOS workspace is not available in the current context")
        if self.dev_skills_root is None or self.dev_scenarios_root is None:
            raise ValueError("AdaOS dev workspace is not available in the current context")
        return BuilderSourceRecoveryService(
            state_dir=Path(self.state_dir or current_state_dir()),
            workspace_root=Path(self.workspace_root),
            dev_skills_root=Path(self.dev_skills_root),
            dev_scenarios_root=Path(self.dev_scenarios_root),
            dev_projects_root=self._dev_projects_root(),
        ).apply(
            kind=kind,
            artifact_id=artifact_id,
            expected_plan_digest=expected_plan_digest,
            decisions=decisions,
            project_id=project_id,
            actor=actor,
        )

    def create_local_fork(
        self,
        *,
        kind: str,
        artifact_id: str,
        project_id: str | None = None,
        actor: str = "builder",
    ) -> dict[str, Any]:
        """Fork current Workspace source into DEV with a digest-bound receipt."""

        normalized_kind = str(kind or "").strip().lower().rstrip("s")
        artifact_token = _slug(artifact_id)
        if normalized_kind not in {"skill", "scenario"}:
            raise ValueError("local fork kind must be skill or scenario")
        actor_token = str(actor or "").strip() or "builder"
        component_ref = f"{normalized_kind}:{artifact_token}"
        workspace_owners = self._workspace_project_ids_owning_ref(component_ref)
        requested_project = _slug(project_id) if project_id else ""
        if requested_project:
            if workspace_owners and requested_project not in workspace_owners:
                raise ValueError(
                    f"Workspace Project {requested_project!r} does not own {component_ref}"
                )
            owner_ids = [requested_project]
        else:
            owner_ids = workspace_owners
        if len(owner_ids) > 1:
            raise ValueError(
                f"local fork requires one Workspace Project owner for {component_ref}: "
                + ", ".join(owner_ids)
            )

        entries: list[dict[str, Any]] = []
        if owner_ids:
            owner_id = owner_ids[0]
            manifest_info = self._read_workspace_project_manifest(owner_id)
            if manifest_info is None:
                raise FileNotFoundError(f"workspace project source not found: {owner_id}")
            manifest_path, manifest = manifest_info
            entries.append(
                {
                    "kind": "project",
                    "id": owner_id,
                    "source": manifest_path.parent.resolve(),
                    "target": (self._dev_projects_root() / owner_id).resolve(),
                }
            )
            refs = self._project_owned_component_refs(manifest)
            if component_ref not in refs:
                raise ValueError(f"Workspace Project {owner_id!r} does not own {component_ref}")
        else:
            owner_id = ""
            refs = [component_ref]

        for ref in refs:
            ref_kind, _, ref_id = ref.partition(":")
            ref_kind = ref_kind.strip().lower().rstrip("s")
            ref_id = _slug(ref_id)
            if ref_kind not in {"skill", "scenario"} or not ref_id:
                continue
            source = self._workspace_artifact_root(ref_kind, ref_id)
            if source is None:
                raise FileNotFoundError(f"workspace {ref_kind} source not found: {ref_id}")
            entries.append(
                {
                    "kind": ref_kind,
                    "id": ref_id,
                    "source": source.resolve(),
                    "target": self._dev_artifact_root(ref_kind, ref_id),
                }
            )

        state_root = Path(self.state_dir or current_state_dir()) / "builder" / "source_forks"
        state_root.mkdir(parents=True, exist_ok=True)
        with mutation_lock(state_root / ".mutation.lock", timeout_s=30.0):
            for entry in entries:
                entry["source_snapshot"] = _source_tree_snapshot(entry["source"])
                target = Path(entry["target"])
                if not target.is_dir():
                    entry["target_status"] = "missing"
                    continue
                target_snapshot = _source_tree_snapshot(target)
                if target_snapshot["digest"] != entry["source_snapshot"]["digest"]:
                    raise ValueError(
                        "local fork intersects divergent DEV source: "
                        f"{entry['kind']}:{entry['id']}"
                    )
                entry["target_status"] = "identical"

            fork_identity = {
                "component_ref": component_ref,
                "project_id": owner_id or None,
                "sources": [
                    {
                        "kind": entry["kind"],
                        "id": entry["id"],
                        "digest": entry["source_snapshot"]["digest"],
                    }
                    for entry in entries
                ],
            }
            identity_json = json.dumps(
                fork_identity,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity_digest = "sha256:" + hashlib.sha256(
                identity_json.encode("utf-8")
            ).hexdigest()
            fork_id = f"source_fork.{identity_digest.removeprefix('sha256:')[:26]}"
            receipt_path = state_root / f"{fork_id}.json"
            if receipt_path.is_file():
                receipt = _read_json(receipt_path)
                return {**receipt, "idempotent": True}

            created_targets: list[Path] = []
            created_project_root: Path | None = None
            copied: list[dict[str, Any]] = []
            try:
                for entry in entries:
                    source = Path(entry["source"])
                    target = Path(entry["target"])
                    if entry["target_status"] == "identical":
                        status = "already_present"
                    else:
                        result = self._copy_workspace_dir(
                            source_root=source,
                            target_root=target,
                            kind=str(entry["kind"]),
                            artifact_id=str(entry["id"]),
                        )
                        status = str(result.get("status") or "materialized")
                        if status == "materialized":
                            created_targets.append(target)
                    if _source_tree_snapshot(source)["digest"] != entry["source_snapshot"]["digest"]:
                        raise RuntimeError(
                            f"Workspace source changed during local fork: {entry['kind']}:{entry['id']}"
                        )
                    target_snapshot = _source_tree_snapshot(target)
                    if target_snapshot["digest"] != entry["source_snapshot"]["digest"]:
                        raise RuntimeError(
                            f"DEV source verification failed after local fork: {entry['kind']}:{entry['id']}"
                        )
                    copied.append(
                        {
                            "kind": entry["kind"],
                            "name": entry["id"],
                            "status": status,
                            "source_root": str(source),
                            "artifact_root": str(target),
                            "source_digest": entry["source_snapshot"]["digest"],
                        }
                    )

                project_resolution = self.ensure_owning_dev_project(
                    kind=normalized_kind,
                    artifact_id=artifact_token,
                    project_id=owner_id or None,
                    actor=actor_token,
                )
                if project_resolution.get("status") not in {"created", "source_available"}:
                    raise RuntimeError("local fork did not establish an owning DEV Project")
                if project_resolution.get("created"):
                    created_project_root = Path(
                        str(project_resolution.get("project_source_path") or "")
                    )
                receipt = {
                    "schema": "adaos.builder.local_source_fork.v1",
                    "fork_id": fork_id,
                    "strategy": "create_local_fork",
                    "status": "materialized",
                    "component_ref": component_ref,
                    "project_id": project_resolution.get("project_id"),
                    "project_ref": project_resolution.get("project_ref"),
                    "source_digest": identity_digest,
                    "components": copied,
                    "project_resolution": project_resolution,
                    "actor": actor_token,
                    "created_at": _now_iso(),
                    "idempotent": False,
                }
                atomic_write_json(receipt_path, receipt)
            except Exception:
                if created_project_root is not None and created_project_root.is_dir():
                    shutil.rmtree(created_project_root)
                for target in reversed(created_targets):
                    if target.is_dir():
                        shutil.rmtree(target)
                raise
            return receipt

    def materialize_dev_source(
        self,
        *,
        kind: str,
        artifact_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower().rstrip("s")
        artifact_id = _slug(artifact_id)
        if normalized_kind not in {"project", "scenario", "skill"}:
            raise ValueError("kind must be project, scenario, or skill")
        status = self.development_source_status(
            kind=normalized_kind,
            artifact_id=artifact_id,
            project_id=project_id,
        )
        recovery_plan = self.development_source_recovery_plan(
            kind=normalized_kind,
            artifact_id=artifact_id,
            project_id=project_id,
        )
        if status.get("status") == "source_available":
            return {
                "ok": True,
                "status": "already_present",
                "strategy": "materialize_dev_source",
                "target_type": normalized_kind,
                "target_id": artifact_id,
                "development_source": status,
                "source_recovery_plan": recovery_plan,
                "components": [],
            }
        if recovery_plan.get("workspace_lock_digest") and not recovery_plan.get(
            "safe_to_apply"
        ):
            raise BuilderSourceRecoveryRequired(recovery_plan)

        project_ids: list[str] = []
        if normalized_kind == "project":
            project_ids = [artifact_id]
        elif project_id:
            project_ids = [_slug(project_id)]
        else:
            project_ids = self._workspace_project_ids_owning_ref(f"{normalized_kind}:{artifact_id}")[:1]

        components: list[dict[str, Any]] = []
        if project_ids:
            for current_project_id in project_ids:
                manifest_info = self._read_workspace_project_manifest(current_project_id)
                if manifest_info is None:
                    raise FileNotFoundError(f"workspace project source not found: {current_project_id}")
                manifest_path, manifest = manifest_info
                project_source_root = manifest_path.parent
                project_target_root = self._dev_projects_root() / _slug(current_project_id)
                components.append(
                    self._copy_workspace_dir(
                        source_root=project_source_root,
                        target_root=project_target_root,
                        kind="project",
                        artifact_id=current_project_id,
                    )
                )
                refs = self._project_owned_component_refs(manifest)
                requested_ref = f"{normalized_kind}:{artifact_id}" if normalized_kind != "project" else ""
                if requested_ref and requested_ref not in refs:
                    refs.append(requested_ref)
                for ref in refs:
                    ref_kind, _, ref_id = ref.partition(":")
                    ref_kind = ref_kind.strip().lower().rstrip("s")
                    ref_id = _slug(ref_id)
                    if ref_kind not in {"skill", "scenario"} or not ref_id:
                        continue
                    source_root = self._workspace_artifact_root(ref_kind, ref_id)
                    if source_root is None:
                        components.append(
                            {
                                "kind": ref_kind,
                                "name": ref_id,
                                "status": "missing_workspace_source",
                                "source_root": None,
                                "artifact_root": None,
                            }
                        )
                        continue
                    target_root = self._dev_artifact_root(ref_kind, ref_id)
                    components.append(
                        self._copy_workspace_dir(
                            source_root=source_root,
                            target_root=target_root,
                            kind=ref_kind,
                            artifact_id=ref_id,
                        )
                    )
        else:
            source_root = self._workspace_artifact_root(normalized_kind, artifact_id)
            if source_root is None:
                raise FileNotFoundError(f"workspace {normalized_kind} source not found: {artifact_id}")
            target_root = self._dev_artifact_root(normalized_kind, artifact_id)
            components.append(
                self._copy_workspace_dir(
                    source_root=source_root,
                    target_root=target_root,
                    kind=normalized_kind,
                    artifact_id=artifact_id,
                )
            )

        materialized = [item for item in components if item.get("status") == "materialized"]
        missing = [item for item in components if item.get("status") == "missing_workspace_source"]
        return {
            "ok": not missing,
            "status": "materialized" if materialized else "already_present",
            "strategy": "materialize_dev_source",
            "target_type": normalized_kind,
            "target_id": artifact_id,
            "project_id": project_ids[0] if len(project_ids) == 1 else None,
            "components": components,
            "source_recovery_plan": recovery_plan,
            "development_source": self.development_source_status(
                kind=normalized_kind,
                artifact_id=artifact_id,
                project_id=project_ids[0] if len(project_ids) == 1 else project_id,
            ),
        }

    def _copy_workspace_dir(
        self,
        *,
        source_root: Path,
        target_root: Path,
        kind: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        source = Path(source_root).expanduser().resolve()
        target = Path(target_root).expanduser().resolve()
        if target.is_dir():
            return {
                "kind": kind,
                "name": artifact_id,
                "status": "already_present",
                "source_root": str(source),
                "artifact_root": str(target),
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.materializing"
        if staging.exists():
            shutil.rmtree(staging)
        _copytree(source, staging)
        staging.replace(target)
        return {
            "kind": kind,
            "name": artifact_id,
            "status": "materialized",
            "source_root": str(source),
            "artifact_root": str(target),
        }

    def _require_developer_service(self) -> Any:
        if self.developer_service is None:
            raise RuntimeError(
                "BuilderWorkspaceService requires RootDeveloperService; "
                "construct it with from_context() or inject the core developer service"
            )
        return self.developer_service

    def checkpoint_artifact(
        self,
        *,
        kind: str,
        artifact_id: str,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower().rstrip("s")
        artifact_id = _slug(artifact_id)
        if normalized_kind not in {"skill", "scenario"}:
            raise ValueError("kind must be skill or scenario")
        service = self._require_developer_service()
        push_kwargs: dict[str, Any] = {"message": message}
        if metadata:
            push_kwargs["metadata"] = dict(metadata)
        result = (
            service.push_skill(artifact_id, **push_kwargs)
            if normalized_kind == "skill"
            else service.push_scenario(artifact_id, **push_kwargs)
        )
        return {
            "ok": True,
            "kind": normalized_kind,
            "name": str(getattr(result, "name", artifact_id) or artifact_id),
            "stored_path": str(getattr(result, "stored_path", "") or ""),
            "sha256": str(getattr(result, "sha256", "") or ""),
            "bytes_uploaded": int(getattr(result, "bytes_uploaded", 0) or 0),
            "version": getattr(result, "version", None),
            "updated_at": getattr(result, "updated_at", None),
            "commit": getattr(result, "commit", None),
            "message": getattr(result, "message", None) or " ".join(str(message or "").split()).strip() or None,
            "metadata": dict(getattr(result, "metadata", None) or metadata or {}),
            "package_digest": getattr(result, "package_digest", None),
            "source_revision": getattr(result, "source_revision", None),
            "source_tree": getattr(result, "source_tree", None),
        }

    def create_draft(
        self,
        *,
        kind: str,
        artifact_id: str,
        source_idea: str,
        task_id: str | None = None,
        source: dict[str, Any] | None = None,
        template_id: str | None = None,
        target_kind: str | None = None,
        descriptor_changes: dict[str, Any] | None = None,
        links: dict[str, Any] | None = None,
        target_root: str | Path | None = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        kind = str(kind or "").strip().lower()
        artifact_id = _slug(artifact_id)
        if not _ARTIFACT_ID_RE.match(artifact_id):
            raise ValueError("artifact_id must match ^[a-z0-9_.-]+$")
        if kind == "descriptor_fix":
            return self._create_descriptor_fix_draft(
                artifact_id=artifact_id,
                source_idea=source_idea,
                task_id=task_id,
            source=source,
            target_kind=target_kind,
            descriptor_changes=descriptor_changes,
            links=links,
            target_root=target_root,
            webspace_id=webspace_id,
        )
        if kind not in {"skill", "scenario"}:
            raise ValueError("kind must be skill, scenario, or descriptor_fix")

        template_id = template_id or ("skill_default" if kind == "skill" else "scenario_default")
        draft_id = self._new_draft_id(artifact_id)
        draft_dir = self.drafts_dir() / draft_id
        try:
            expected_artifact_root = self._dev_artifact_root(kind, artifact_id)
        except ValueError:
            expected_artifact_root = None
        if expected_artifact_root is not None and expected_artifact_root.exists():
            raise ValueError(
                f"{kind} '{artifact_id}' already exists in DEV workspace at {expected_artifact_root}; "
                "use descriptor_fix or remove/rename the DEV artifact first"
            )
        developer_service = self._require_developer_service()
        created = (
            developer_service.create_skill(artifact_id, template=template_id)
            if kind == "skill"
            else developer_service.create_scenario(artifact_id, template=template_id)
        )
        artifact_root = Path(getattr(created, "path", "")).expanduser().resolve()
        if expected_artifact_root is not None and artifact_root != expected_artifact_root:
            raise RuntimeError(
                f"Core developer service created {kind} at unexpected path {artifact_root}; "
                f"expected {expected_artifact_root}"
            )

        if kind == "skill":
            self._patch_skill_template(artifact_root, artifact_id, source_idea)
        else:
            self._patch_scenario_template(artifact_root, artifact_id, source_idea)

        builder_ref = conversation_links.ensure_builder_conversation(webspace_id)
        context_packet = conversation_links.builder_context_packet(webspace_id)
        draft = self._draft_payload(
            draft_id=draft_id,
            task_id=task_id,
            status="draft",
            source=source,
            artifact_kind=kind,
            artifact_id=artifact_id,
            template_id=template_id,
            artifact_root=artifact_root,
            source_idea=source_idea,
            links=links,
            conversation_ref=builder_ref,
            context_packet=context_packet,
            assumptions=[
                "Draft workspace is isolated from active runtime state.",
                "Apply/activation requires a separate approval and lifecycle step.",
            ],
            risk_notes=[
                "Generated behavior and permissions must be reviewed before runtime apply.",
            ],
            expected_tests=[
                "schema validation",
                "Builder preview bundle",
            ],
        )
        self._persist_draft(draft_dir, artifact_root, draft)
        return {"ok": True, "draft": draft, "draft_dir": str(draft_dir), "artifact_root": str(artifact_root)}

    def load_draft(self, draft_id: str) -> dict[str, Any]:
        path = self.drafts_dir() / str(draft_id).strip() / "builder.draft.json"
        if not path.exists():
            raise FileNotFoundError(f"Builder draft not found: {draft_id}")
        return _read_json(path)

    def preview(
        self,
        *,
        draft_id: str,
        approval_profile: str | None = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        draft_id = str(draft_id or "").strip()
        draft_dir = self.drafts_dir() / draft_id
        draft = self.load_draft(draft_id)
        existing_links = draft.get("links") if isinstance(draft.get("links"), dict) else {}
        existing_conversation = existing_links.get("conversation") if isinstance(existing_links.get("conversation"), dict) else {}
        resolved_webspace_id = str(
            webspace_id
            or existing_conversation.get("webspace_id")
            or (draft.get("metadata") or {}).get("webspace_id")
            or "default"
        ).strip()
        builder_ref = conversation_links.ensure_builder_conversation(resolved_webspace_id)
        context_packet = conversation_links.builder_context_packet(resolved_webspace_id)
        profile_id = self._normalize_approval_profile(approval_profile)
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        artifact_root = self._draft_artifact_root(draft_dir, artifact)
        target_root = self._preview_target_root(draft)
        artifact_kind = str(artifact.get("kind") or "").strip()
        preview_id = f"preview.{draft_id}.{_stable_suffix(_now_iso(), artifact_root)}"

        diff = self._diff_roots(target_root, artifact_root)
        schemas = self._schema_report(artifact_kind, artifact_root)
        route_plan = self._route_plan_report(artifact_kind, artifact_root)
        static_checks = self._static_checks(artifact_root)
        ui_preview = self._ui_preview(artifact_kind, artifact_root)
        action_preview = self._action_preview(artifact_kind, artifact_root)
        nlu_probe = self._nlu_probe(draft, artifact_root)
        bootstrap = self._scenario_dependency_bootstrap_report(artifact_kind, artifact_root)
        blast_radius = self._blast_radius_report(diff, action_preview, ui_preview, route_plan)
        test_plan = self._test_plan(draft, artifact_kind)
        risk_summary = self._risk_summary(draft, schemas, route_plan, static_checks, blast_radius, bootstrap)
        review_policy = self._review_policy_report(
            draft=draft,
            profile_id=profile_id,
            diff=diff,
            route_plan=route_plan,
            static_checks=static_checks,
            blast_radius=blast_radius,
            action_preview=action_preview,
            nlu_probe=nlu_probe,
            bootstrap=bootstrap,
        )
        human_review = self._human_review_summary(draft, risk_summary, review_policy)

        preview = {
            "ok": not any(item.get("level") == "error" for group in (schemas, route_plan, static_checks) for item in group.get("issues", [])),
            "preview_id": preview_id,
            "draft_id": draft_id,
            "created_at": _now_iso(),
            "artifact": artifact,
            "diff": diff,
            "schemas": schemas,
            "route_plan": route_plan,
            "nlu_probe": nlu_probe,
            "action_preview": action_preview,
            "ui_preview": ui_preview,
            "test_plan": test_plan,
            "risk_summary": risk_summary,
            "static_checks": static_checks,
            "blast_radius": blast_radius,
            "scenario_dependency_bootstrap": bootstrap,
            "review_policy": review_policy,
            "human_review": human_review,
            "conversation": builder_ref,
            "context_packet": context_packet,
            "source_refs": {
                "conversation_id": builder_ref.get("conversation_id"),
                "channel_id": builder_ref.get("channel_id"),
                "builder_task_id": draft.get("task_id"),
            },
            "summary": {
                "changed_files": len(diff.get("files") or []),
                "schema_ok": schemas.get("ok"),
                "route_plan_ok": route_plan.get("ok"),
                "static_ok": static_checks.get("ok"),
                "approval_profile": profile_id,
                "review_decision": review_policy.get("decision"),
                "human_review_required": human_review.get("required"),
            },
        }
        _write_json(self.previews_dir() / f"{preview_id}.json", preview)
        self._mark_draft_previewed(draft_dir, draft, preview_id)
        return {"ok": True, "preview": preview}

    def create_realize_request(
        self,
        *,
        draft_id: str | None = None,
        target: dict[str, Any] | None = None,
        artifacts: dict[str, Any] | None = None,
        repo: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        mcp: dict[str, Any] | None = None,
        acceptance: dict[str, Any] | None = None,
        links: dict[str, Any] | None = None,
        source_session_id: str | None = None,
        source_conversation_id: str | None = None,
        user_subnet_id: str | None = None,
        submit_remote: bool = False,
        create_pending_action: bool = True,
    ) -> dict[str, Any]:
        from adaos.services.skill_factory import SkillFactoryService

        draft: dict[str, Any] | None = None
        if draft_id:
            draft = self.load_draft(draft_id)

        payload: dict[str, Any] = {
            "draft": draft or {},
            "target": target or {},
            "artifacts": artifacts or {},
            "repo": repo or {},
            "constraints": constraints or {},
            "mcp": mcp or {},
            "acceptance": acceptance or {},
            "links": links or {},
            "source_session_id": source_session_id,
            "source_conversation_id": source_conversation_id,
            "user_subnet_id": user_subnet_id,
        }
        if draft_id:
            payload["links"]["draft_id"] = draft_id
            payload["artifacts"].setdefault("draft_id", draft_id)

        factory = SkillFactoryService(state_dir=self.state_dir)
        request = factory.normalize_realize_request(payload)
        request_dir = self.realize_requests_dir() / request["request_id"]
        pending_action: dict[str, Any] | None = None
        pending_action_error: str | None = None
        if create_pending_action:
            try:
                from adaos.services.pending_actions import publish_pending_action

                webspace_id = str((request.get("links") or {}).get("conversation", {}).get("webspace_id") or "desktop")
                pending_action = publish_pending_action(
                    webspace_id=webspace_id,
                    action_id=f"builder.realize.{request['request_id']}",
                    kind="builder.realize_request",
                    title="Realize Builder draft",
                    summary=f"Remote realization request for {request['target']['type']}:{request['target']['id']}.",
                    request_text=str((request.get("source") or {}).get("text") or ""),
                    producer={"type": "system", "system_id": "builder"},
                    owner_scope={"webspace_id": webspace_id},
                    domain_ref={
                        "kind": "builder.realize_request",
                        "request_id": request["request_id"],
                        "draft_id": (request.get("links") or {}).get("draft_id"),
                        "target": request.get("target"),
                    },
                    allowed_actions=["approve", "refuse", "postpone"],
                    response_route={"type": "event", "topic": "builder.realize_request.response"},
                    payload_ref={
                        "type": "builder.realize_request",
                        "path": str(request_dir / "realize_request.json"),
                    },
                    metadata={"schema": request["schema"], "target": request.get("target")},
                )
                request.setdefault("links", {})["pending_action_id"] = pending_action.get("id")
            except Exception as exc:
                pending_action_error = f"{type(exc).__name__}: {exc}"

        _write_json(request_dir / "realize_request.json", request)

        remote: dict[str, Any] | None = None
        mode = "local_fallback"
        if submit_remote:
            try:
                remote = factory.submit_realize_request(request)
                task = remote.get("task") if isinstance(remote, dict) else None
                if isinstance(task, dict):
                    request.setdefault("links", {})["skill_factory_task_id"] = task.get("task_id")
                    _write_json(request_dir / "realize_request.json", request)
                mode = "remote_queued"
            except Exception as exc:
                remote = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                mode = "local_fallback"

        return {
            "ok": True,
            "mode": mode,
            "remote_submitted": bool(remote and remote.get("ok") and mode == "remote_queued"),
            "realize_request": request,
            "request_dir": str(request_dir),
            "remote": remote,
            "pending_action": pending_action,
            "pending_action_error": pending_action_error,
        }

    def load_preview(self, preview_id: str) -> dict[str, Any]:
        path = self.previews_dir() / f"{str(preview_id).strip()}.json"
        if not path.exists():
            raise FileNotFoundError(f"Builder preview not found: {preview_id}")
        return _read_json(path)

    def _new_draft_id(self, artifact_id: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"draft.{artifact_id}.{stamp}"

    def _patch_skill_template(self, artifact_root: Path, artifact_id: str, source_idea: str) -> None:
        manifest = artifact_root / "skill.yaml"
        data = _read_yaml(manifest)
        data["name"] = artifact_id
        data.setdefault("version", "0.1.0")
        data["description"] = data.get("description") or source_idea
        data.setdefault("llm_hints", {})
        data["llm_hints"]["description"] = source_idea
        data["llm_hints"].setdefault("examples", [])
        data.setdefault("nlu_hints", {})
        data["nlu_hints"].setdefault("examples", [])
        data = _rewrite_skill_template_refs(data, artifact_id)
        _write_yaml(manifest, data)

    def _patch_scenario_template(self, artifact_root: Path, artifact_id: str, source_idea: str) -> None:
        manifest = artifact_root / "scenario.yaml"
        data = _read_yaml(manifest)
        data["id"] = artifact_id
        data.setdefault("version", "0.1.0")
        data["name"] = artifact_id
        data["description"] = data.get("description") or source_idea
        data.setdefault("llm_hints", {})
        data["llm_hints"]["description"] = source_idea
        data.setdefault("nlu", {})
        data["nlu"].setdefault("nlu_hints", {})
        data["nlu"]["nlu_hints"].setdefault("examples", [])
        _write_yaml(manifest, data)
        content = artifact_root / "scenario.json"
        if content.exists():
            payload = _read_json(content)
            payload["id"] = artifact_id
            payload.setdefault("version", str(data.get("version") or "0.1.0"))
            payload["name"] = artifact_id
            payload["description"] = payload.get("description") or source_idea
            _write_json(content, payload)

    def _create_descriptor_fix_draft(
        self,
        *,
        artifact_id: str,
        source_idea: str,
        task_id: str | None,
        source: dict[str, Any] | None,
        target_kind: str | None,
        descriptor_changes: dict[str, Any] | None,
        links: dict[str, Any] | None,
        target_root: str | Path | None,
        webspace_id: str | None,
    ) -> dict[str, Any]:
        target_kind = str(target_kind or "skill").strip().lower()
        if target_kind not in {"skill", "scenario"}:
            raise ValueError("descriptor_fix target_kind must be skill or scenario")
        source_root = self._resolve_target_root(target_kind, artifact_id, explicit=target_root)
        draft_id = self._new_draft_id(f"{artifact_id}.descriptor")
        draft_dir = self.drafts_dir() / draft_id
        artifact_root = self._dev_artifact_root(target_kind, artifact_id)
        if not artifact_root.exists():
            _copytree(source_root, artifact_root)
        materialization = self._materialize_descriptor_fix(
            artifact_root=artifact_root,
            target_kind=target_kind,
            target_id=artifact_id,
            source_idea=source_idea,
            descriptor_changes=descriptor_changes or {},
        )
        builder_ref = conversation_links.ensure_builder_conversation(webspace_id)
        context_packet = conversation_links.builder_context_packet(webspace_id)
        draft = self._draft_payload(
            draft_id=draft_id,
            task_id=task_id,
            status="draft",
            source=source,
            artifact_kind=target_kind,
            artifact_id=artifact_id,
            template_id="descriptor_fix",
            artifact_root=artifact_root,
            source_idea=source_idea,
            links=links,
            conversation_ref=builder_ref,
            context_packet=context_packet,
            assumptions=[
                "Descriptor fix only updates reviewable manifest, webui, and NLU hint surfaces.",
                "No runtime action implementation is generated by this draft.",
            ],
            risk_notes=[
                "Broad aliases or examples can affect NLU routing and need preview review.",
            ],
            expected_tests=[
                "schema validation",
                "NLU phrase probe",
                "blast-radius preview",
            ],
        )
        draft["metadata"]["target_root"] = str(source_root)
        draft["materialization"] = materialization
        self._persist_draft(draft_dir, artifact_root, draft)
        return {"ok": True, "draft": draft, "draft_dir": str(draft_dir), "artifact_root": str(artifact_root)}

    def _materialize_descriptor_fix(
        self,
        *,
        artifact_root: Path,
        target_kind: str,
        target_id: str,
        source_idea: str,
        descriptor_changes: dict[str, Any],
    ) -> dict[str, Any]:
        llm_hints = dict(descriptor_changes.get("llm_hints") or {})
        nlu_hints = dict(descriptor_changes.get("nlu_hints") or {})
        description = str(descriptor_changes.get("description") or source_idea or "").strip()
        if description and "description" not in llm_hints:
            llm_hints["description"] = description
        examples = descriptor_changes.get("examples")
        if examples and "examples" not in nlu_hints:
            nlu_hints["examples"] = examples if isinstance(examples, list) else [str(examples)]

        touched: list[dict[str, str]] = []
        manifest_path = self._descriptor_manifest_path(artifact_root, target_kind)
        if manifest_path is not None:
            self._patch_descriptor_manifest(manifest_path, target_kind, description, llm_hints, nlu_hints)
            touched.append({"path": _relative_to(manifest_path, artifact_root), "surface": "manifest"})

        webui_path = artifact_root / "webui.json"
        if target_kind == "skill" or descriptor_changes.get("webui"):
            webui = _read_json(webui_path) if webui_path.exists() else {}
            webui.setdefault("nlu", {})
            webui["nlu"].setdefault("llm_hints", {})
            webui["nlu"].setdefault("nlu_hints", {})
            _deep_merge(webui["nlu"]["llm_hints"], llm_hints)
            _deep_merge(webui["nlu"]["nlu_hints"], nlu_hints)
            if isinstance(descriptor_changes.get("webui"), dict):
                _deep_merge(webui, descriptor_changes["webui"])
            _write_json(webui_path, webui)
            touched.append({"path": "webui.json", "surface": "webui"})

        nlu_hint_path = artifact_root / "builder.nlu_hints.json"
        _write_json(
            nlu_hint_path,
            {
                "target": {"kind": target_kind, "id": target_id},
                "source_idea": source_idea,
                "llm_hints": llm_hints,
                "nlu_hints": nlu_hints,
                "created_by": "adaos.builder",
            },
        )
        touched.append({"path": "builder.nlu_hints.json", "surface": "nlu_hint_file"})

        interpreter_path = artifact_root / "interpreter" / "intents.yml"
        if interpreter_path.exists():
            try:
                data = _read_yaml(interpreter_path)
                items = data.setdefault("intents", [])
                if isinstance(items, list):
                    items.append(
                        {
                            "intent": f"{target_id}.descriptor_fix",
                            "description": description,
                            "examples": nlu_hints.get("examples") or [],
                        }
                    )
                    _write_yaml(interpreter_path, data)
                    touched.append({"path": "interpreter/intents.yml", "surface": "nlu_hint_file"})
            except Exception:
                pass

        intents_dir = artifact_root / "intents"
        if intents_dir.exists() and isinstance(nlu_hints.get("examples"), list) and nlu_hints["examples"]:
            path = intents_dir / "builder_descriptor_fix.intent"
            path.write_text("\n".join(str(item) for item in nlu_hints["examples"]) + "\n", encoding="utf-8")
            touched.append({"path": "intents/builder_descriptor_fix.intent", "surface": "nlu_hint_file"})

        patch_path = artifact_root / "descriptor.patch.json"
        _write_json(
            patch_path,
            {
                "kind": "descriptor_fix",
                "target": {"kind": target_kind, "id": target_id},
                "description": description,
                "llm_hints": llm_hints,
                "nlu_hints": nlu_hints,
                "touched": touched,
            },
        )
        touched.append({"path": "descriptor.patch.json", "surface": "patch_manifest"})
        return {"touched": touched, "description": description}

    def _descriptor_manifest_path(self, artifact_root: Path, target_kind: str) -> Path | None:
        names = ("skill.yaml",) if target_kind == "skill" else ("scenario.yaml",)
        for name in names:
            path = artifact_root / name
            if path.exists():
                return path
        return None

    def _patch_descriptor_manifest(
        self,
        path: Path,
        target_kind: str,
        description: str,
        llm_hints: dict[str, Any],
        nlu_hints: dict[str, Any],
    ) -> None:
        is_json = path.suffix.lower() == ".json"
        data = _read_json(path) if is_json else _read_yaml(path)
        if description:
            data["description"] = description
        data.setdefault("llm_hints", {})
        _deep_merge(data["llm_hints"], llm_hints)
        if target_kind == "skill":
            data.setdefault("nlu_hints", {})
            _deep_merge(data["nlu_hints"], nlu_hints)
        else:
            data.setdefault("nlu", {})
            data["nlu"].setdefault("nlu_hints", {})
            _deep_merge(data["nlu"]["nlu_hints"], nlu_hints)
        if is_json:
            _write_json(path, data)
        else:
            _write_yaml(path, data)

    def _draft_payload(
        self,
        *,
        draft_id: str,
        task_id: str | None,
        status: str,
        source: dict[str, Any] | None,
        artifact_kind: str,
        artifact_id: str,
        template_id: str,
        artifact_root: Path,
        source_idea: str,
        links: dict[str, Any] | None,
        assumptions: list[str],
        risk_notes: list[str],
        expected_tests: list[str],
        conversation_ref: dict[str, Any] | None = None,
        context_packet: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = task_id or f"btask.{_stable_suffix(source_idea, artifact_kind, artifact_id)}"
        source_payload = source if isinstance(source, dict) and source.get("type") else {"type": "human_idea", "text": source_idea}
        file_refs = self._file_refs(artifact_root)
        quality = self._quality_gates(artifact_kind)
        now = _now_iso()
        merged_links = dict(links or {})
        merged_links.setdefault("builder_task_id", task_id)
        if conversation_ref:
            merged_links.setdefault("conversation", {k: v for k, v in conversation_ref.items() if k != "stored"})
        return {
            "$schema": "../../../src/adaos/abi/builder.draft.v1.schema.json",
            "draft_id": draft_id,
            "task_id": task_id,
            "status": status,
            "source": source_payload,
            "artifact": {
                "kind": artifact_kind,
                "id": artifact_id,
                "template_id": template_id,
                "draft_root": str(artifact_root),
                "files": file_refs,
            },
            "metadata": {
                "source_idea": source_idea,
                "assumptions": assumptions,
                "risk_notes": risk_notes,
                "expected_tests": expected_tests,
                "route_plan_required": artifact_kind == "skill",
                "human_review_required": False,
                "webspace_id": (conversation_ref or {}).get("webspace_id"),
                "context_packet": context_packet,
            },
            "quality_gates": quality,
            "links": merged_links,
            "created_by": "adaos.builder",
            "created_at": now,
            "updated_at": now,
        }

    def _quality_gates(self, artifact_kind: str) -> dict[str, list[str]]:
        if artifact_kind == "skill":
            return {
                "schemas": ["skill.schema.json", "webui.v1.schema.json"],
                "tests": ["skill validation", "handler import smoke", "Builder static checks"],
                "previews": ["diff", "route plan", "NLU probe", "action preview", "UI preview"],
                "requires_human_approval": [
                    "new permissions",
                    "external IO",
                    "service runtime",
                    "high-rate streams",
                    "destructive actions",
                ],
            }
        return {
            "schemas": ["scenario.schema.json"],
            "tests": ["scenario validation", "dependency bootstrap preview"],
            "previews": ["diff", "NLU probe", "action preview", "UI preview", "dependency bootstrap"],
            "requires_human_approval": [
                "new skill dependencies",
                "endpoint control",
                "broad NLU triggers",
                "external IO",
                "destructive actions",
            ],
        }

    def _file_refs(self, root: Path) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            rel = _relative_to(path, root)
            refs.append({"path": rel, "role": self._file_role(rel), "required": rel in {"skill.yaml", "scenario.yaml"}})
        return refs

    def _file_role(self, rel: str) -> str:
        if rel in {"skill.yaml", "scenario.yaml"}:
            return "manifest"
        if rel.startswith("handlers/") and rel.endswith(".py"):
            return "handler"
        if rel.startswith("tests/"):
            return "test"
        if rel == "webui.json":
            return "webui"
        if "nlu" in rel or rel.startswith("intents/") or rel.startswith("interpreter/"):
            return "nlu"
        if rel.endswith(".md"):
            return "doc"
        return "other"

    def _persist_draft(self, draft_dir: Path, artifact_root: Path, draft: dict[str, Any]) -> None:
        draft_dir.mkdir(parents=True, exist_ok=True)
        _write_json(draft_dir / "builder.draft.json", draft)
        _write_json(artifact_root / "builder.draft.json", draft)

    def _mark_draft_previewed(self, draft_dir: Path, draft: dict[str, Any], preview_id: str) -> None:
        draft["status"] = "previewed"
        draft.setdefault("links", {})["preview_id"] = preview_id
        draft["updated_at"] = _now_iso()
        _write_json(draft_dir / "builder.draft.json", draft)
        artifact_root = self._draft_artifact_root(draft_dir, draft.get("artifact") or {})
        if artifact_root.exists():
            _write_json(artifact_root / "builder.draft.json", draft)

    def _draft_artifact_root(self, draft_dir: Path, artifact: dict[str, Any]) -> Path:
        raw = str(artifact.get("draft_root") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.is_absolute():
                return path.resolve()
            return (draft_dir / raw).resolve()
        return (draft_dir / "artifact").resolve()

    def _resolve_target_root(self, kind: str, artifact_id: str, explicit: str | Path | None = None) -> Path:
        if explicit:
            path = Path(explicit).expanduser().resolve()
            if path.exists() and path.is_dir():
                return path
            raise FileNotFoundError(f"target_root not found: {path}")
        roots: list[Path] = []
        if kind == "skill":
            if self.skills_root is not None:
                roots.append(Path(self.skills_root))
            if self.workspace_root is not None:
                roots.append(Path(self.workspace_root) / "skills")
        else:
            if self.scenarios_root is not None:
                roots.append(Path(self.scenarios_root))
            if self.workspace_root is not None:
                roots.append(Path(self.workspace_root) / "scenarios")
        if self.repo_root is not None:
            roots.append(Path(self.repo_root) / ".adaos" / "workspace" / ("skills" if kind == "skill" else "scenarios"))
        for root in roots:
            candidate = (root / artifact_id).resolve()
            if candidate.exists() and candidate.is_dir():
                return candidate
        raise FileNotFoundError(f"{kind} target not found: {artifact_id}")

    def _preview_target_root(self, draft: dict[str, Any]) -> Path | None:
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        raw = str(metadata.get("target_root") or "").strip()
        if raw:
            path = Path(raw).expanduser().resolve()
            return path if path.exists() else None
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        kind = str(artifact.get("kind") or "").strip()
        artifact_id = str(artifact.get("id") or "").strip()
        if kind in {"skill", "scenario"} and artifact_id:
            try:
                return self._resolve_target_root(kind, artifact_id)
            except Exception:
                return None
        return None

    def _diff_roots(self, before_root: Path | None, after_root: Path) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        before_files = self._collect_text_files(before_root) if before_root else {}
        after_files = self._collect_text_files(after_root)
        for rel in sorted(set(before_files) | set(after_files)):
            before = before_files.get(rel, "")
            after = after_files.get(rel, "")
            if before == after:
                continue
            before_lines = before.splitlines(keepends=True)
            after_lines = after.splitlines(keepends=True)
            patch = "".join(
                difflib.unified_diff(
                    before_lines,
                    after_lines,
                    fromfile=f"before/{rel}",
                    tofile=f"after/{rel}",
                    lineterm="",
                )
            )
            files.append(
                {
                    "path": rel,
                    "status": "added" if rel not in before_files else "deleted" if rel not in after_files else "modified",
                    "patch": patch[:20000],
                    "truncated": len(patch) > 20000,
                }
            )
        return {"files": files, "target_root": str(before_root) if before_root else None, "draft_root": str(after_root)}

    def _collect_text_files(self, root: Path | None) -> dict[str, str]:
        if root is None or not root.exists():
            return {}
        out: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.name == "builder.draft.json":
                continue
            if not _is_text_file(path):
                continue
            rel = _relative_to(path, root)
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    out[rel] = path.read_text(encoding="utf-8-sig")
                except Exception:
                    continue
            except Exception:
                continue
        return out

    def _schema_report(self, artifact_kind: str, artifact_root: Path) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        checks: list[dict[str, Any]] = []
        if artifact_kind == "skill":
            manifest = artifact_root / "skill.yaml"
            if not manifest.exists():
                issues.append(_issue("error", "schema.skill_manifest_missing", "skill.yaml is missing", "skill.yaml"))
            else:
                data = _read_yaml(manifest)
                checks.append(self._validate_schema("skill.schema.json", _load_abi_schema("skill.schema.json"), data, "draft7"))
                checks.append(self._validate_schema("services/skill_schema.json", _load_runtime_skill_schema(), data, "draft202012"))
            webui = artifact_root / "webui.json"
            if webui.exists():
                checks.append(self._validate_schema("webui.v1.schema.json", _load_abi_schema("webui.v1.schema.json"), _read_json(webui), "draft202012"))
        elif artifact_kind == "scenario":
            manifest_path = self._descriptor_manifest_path(artifact_root, "scenario")
            if manifest_path is None:
                issues.append(_issue("error", "schema.scenario_manifest_missing", "scenario.yaml is missing", "scenario.yaml"))
            else:
                data = _read_yaml(manifest_path)
                checks.append(self._validate_schema("scenario.schema.json", _load_abi_schema("scenario.schema.json"), data, "draft7"))
        else:
            issues.append(_issue("warning", "schema.unknown_artifact_kind", f"unknown artifact kind: {artifact_kind}", None))
        for check in checks:
            issues.extend(check.get("issues") or [])
        return {"ok": not any(item.get("level") == "error" for item in issues), "checks": checks, "issues": issues}

    def _validate_schema(self, name: str, schema: dict[str, Any], payload: dict[str, Any], draft: str) -> dict[str, Any]:
        try:
            if draft == "draft7":
                Draft7Validator(schema).validate(payload)
            else:
                Draft202012Validator(schema).validate(payload)
            return {"name": name, "ok": True, "issues": []}
        except ValidationError as exc:
            where = ".".join(str(part) for part in exc.absolute_path) or None
            return {"name": name, "ok": False, "issues": [_issue("error", f"schema.{name}.invalid", exc.message, where)]}

    def _route_plan_report(self, artifact_kind: str, artifact_root: Path) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        routes: list[dict[str, Any]] = []
        receivers: list[dict[str, Any]] = []
        projections: list[dict[str, Any]] = []
        if artifact_kind == "skill":
            manifest = artifact_root / "skill.yaml"
            data = _read_yaml(manifest) if manifest.exists() else {}
            raw_routes = data.get("data_routes") if isinstance(data.get("data_routes"), list) else []
            projections = data.get("data_projections") if isinstance(data.get("data_projections"), list) else []
            if not raw_routes:
                issues.append(_issue("warning", "route_plan.missing", "skill has no data_routes; browser-facing output may be unrouted", "skill.yaml:data_routes"))
            for idx, route in enumerate(raw_routes):
                if not isinstance(route, dict):
                    issues.append(_issue("error", "route_plan.invalid_item", "data_routes item must be an object", f"data_routes[{idx}]"))
                    continue
                routes.append(route)
                route_kind = str(route.get("route") or "").strip()
                surface = str(route.get("surface") or "").strip()
                if not surface:
                    issues.append(_issue("error", "route_plan.surface_missing", "data route is missing surface", f"data_routes[{idx}].surface"))
                if route_kind not in _VALID_ROUTES:
                    issues.append(_issue("error", "route_plan.route_invalid", f"invalid route: {route_kind}", f"data_routes[{idx}].route"))
                if route_kind == "stream" and not route.get("receiver"):
                    issues.append(_issue("error", "route_plan.stream_receiver_missing", "stream route needs receiver", f"data_routes[{idx}].receiver"))
                if route_kind == "yjs" and not route.get("projection_slot"):
                    issues.append(_issue("warning", "route_plan.yjs_projection_missing", "Yjs route should name projection_slot", f"data_routes[{idx}].projection_slot"))
                if not isinstance(route.get("budget"), dict):
                    issues.append(_issue("warning", "route_plan.budget_missing", "data route should declare budget", f"data_routes[{idx}].budget"))
            for contract_issue in validate_data_route_contract(data):
                issues.append(
                    _issue(
                        contract_issue.level,
                        contract_issue.code.replace("data_routes.", "route_plan."),
                        contract_issue.message,
                        contract_issue.where,
                    )
                )
            webui = artifact_root / "webui.json"
            if webui.exists():
                webui_data = _read_json(webui)
                receivers = self._extract_webui_receivers(webui_data)
                for receiver in receivers:
                    if not isinstance(receiver.get("budget"), dict):
                        issues.append(_issue("warning", "route_plan.receiver_budget_missing", "webui stream receiver should declare budget", f"webui.receivers.{receiver.get('id') or ''}"))
                    route = receiver.get("route")
                    if isinstance(route, dict) and route.get("kind") not in {None, "stream"}:
                        issues.append(_issue("warning", "route_plan.receiver_route_unexpected", "webui receiver route should be kind=stream", f"webui.receivers.{receiver.get('id') or ''}.route"))
        return {
            "ok": not any(item.get("level") == "error" for item in issues),
            "routes": routes,
            "projections": projections,
            "receivers": receivers,
            "issues": issues,
        }

    def _extract_webui_receivers(self, webui: dict[str, Any]) -> list[dict[str, Any]]:
        raw = ((webui.get("webio") or {}).get("receivers") if isinstance(webui.get("webio"), dict) else None)
        if raw is None and isinstance(webui.get("receivers"), dict):
            raw = webui.get("receivers")
        receivers: list[dict[str, Any]] = []
        if isinstance(raw, dict):
            for key, value in raw.items():
                item = dict(value or {}) if isinstance(value, dict) else {}
                item.setdefault("id", str(key))
                receivers.append(item)
        elif isinstance(raw, list):
            for value in raw:
                if isinstance(value, dict):
                    receivers.append(dict(value))
        return receivers

    def _static_checks(self, artifact_root: Path) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        for path in sorted(artifact_root.rglob("*.py")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            rel = _relative_to(path, artifact_root)
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for pattern in _YJS_PATTERNS:
                if pattern in text:
                    issues.append(_issue("error", "static.unsafe_direct_yjs", f"direct Yjs symbol used: {pattern}", rel))
                    break
            issues.extend(self._memory_ast_issues(path, rel, text))
        return {"ok": not any(item.get("level") == "error" for item in issues), "issues": issues}

    def _memory_ast_issues(self, path: Path, rel: str, text: str) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            return issues
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                if not any(_MEMORY_NAME_RE.search(name) for name in names):
                    continue
                if isinstance(value, (ast.List, ast.Dict, ast.Set)):
                    issues.append(_issue("warning", "static.unbounded_memory", f"module-level mutable state may be unbounded: {', '.join(names)}", rel))
                elif isinstance(value, ast.Call):
                    func_name = getattr(value.func, "id", "") or getattr(value.func, "attr", "")
                    if func_name in {"list", "dict", "set"}:
                        issues.append(_issue("warning", "static.unbounded_memory", f"module-level mutable state may be unbounded: {', '.join(names)}", rel))
                    if func_name == "deque" and not any(kw.arg == "maxlen" for kw in value.keywords):
                        issues.append(_issue("warning", "static.unbounded_memory", f"deque without maxlen: {', '.join(names)}", rel))
        return issues

    def _ui_preview(self, artifact_kind: str, artifact_root: Path) -> dict[str, Any]:
        webui = artifact_root / "webui.json"
        if artifact_kind != "skill" or not webui.exists():
            return {"available": False, "widgets": [], "modals": [], "data_bindings": [], "receivers": []}
        data = _read_json(webui)
        registry = data.get("registry") if isinstance(data.get("registry"), dict) else {}
        catalog = data.get("catalog") if isinstance(data.get("catalog"), dict) else {}
        widgets = []
        widgets.extend(catalog.get("widgets") if isinstance(catalog.get("widgets"), list) else [])
        widgets.extend(data.get("widgets") if isinstance(data.get("widgets"), list) else [])
        modals = registry.get("modals") if isinstance(registry.get("modals"), list) else []
        data_bindings = self._collect_data_bindings(data)
        receivers = self._extract_webui_receivers(data)
        return {
            "available": True,
            "widgets": widgets,
            "modals": modals,
            "data_bindings": data_bindings,
            "receivers": receivers,
        }

    def _collect_data_bindings(self, data: Any) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []

        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                ds = value.get("dataSource")
                if isinstance(ds, dict):
                    bindings.append({"path": path or ".", "dataSource": ds})
                for key, child in value.items():
                    walk(child, f"{path}.{key}" if path else str(key))
            elif isinstance(value, list):
                for idx, child in enumerate(value):
                    walk(child, f"{path}[{idx}]")

        walk(data, "")
        return bindings[:100]

    def _action_preview(self, artifact_kind: str, artifact_root: Path) -> dict[str, Any]:
        hints: list[dict[str, Any]] = []
        if artifact_kind == "skill":
            manifest = artifact_root / "skill.yaml"
            data = _read_yaml(manifest) if manifest.exists() else {}
            hints.extend(self._hint_actions(data.get("llm_hints")))
            hints.extend(self._hint_actions(data.get("nlu_hints")))
            webui = artifact_root / "webui.json"
            if webui.exists():
                webui_data = _read_json(webui)
                hints.extend(self._hint_actions(webui_data.get("llm_hints")))
                hints.extend(self._hint_actions(webui_data.get("nlu")))
        elif artifact_kind == "scenario":
            manifest_path = self._descriptor_manifest_path(artifact_root, "scenario")
            data = _read_json(manifest_path) if manifest_path and manifest_path.suffix == ".json" else _read_yaml(manifest_path) if manifest_path else {}
            hints.extend(self._hint_actions(data.get("llm_hints")))
            hints.extend(self._hint_actions(data.get("nlu")))
        actions: list[dict[str, Any]] = []
        for item in hints:
            action = dict(item)
            action["action_risk"] = conversation_safety.classify_action_risk(action)
            actions.append(action)
        return {"actions": actions, "count": len(actions)}

    def _hint_actions(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, dict):
            return []
        out: list[dict[str, Any]] = []
        for key in ("primary_actions", "actions"):
            for item in (raw.get(key) if isinstance(raw.get(key), list) else []):
                if isinstance(item, dict):
                    out.append(dict(item))
        for value in raw.values():
            if isinstance(value, dict):
                out.extend(self._hint_actions(value))
        return out

    def _nlu_probe(self, draft: dict[str, Any], artifact_root: Path) -> dict[str, Any]:
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        phrase = str((draft.get("acceptance") or {}).get("replay_phrase") if isinstance(draft.get("acceptance"), dict) else "" or "")
        if not phrase:
            phrase = str(metadata.get("source_idea") or "")
        examples = self._collect_nlu_examples(artifact_root)
        return {
            "status": "preview_only",
            "replay_phrase": phrase,
            "candidate_examples": examples[:25],
            "note": "No dispatch is emitted by Builder preview.",
        }

    def _collect_nlu_examples(self, artifact_root: Path) -> list[str]:
        examples: list[str] = []
        for path in (artifact_root / "builder.nlu_hints.json",):
            if path.exists():
                data = _read_json(path)
                hints = data.get("nlu_hints") if isinstance(data.get("nlu_hints"), dict) else {}
                for value in (hints.get("examples") if isinstance(hints.get("examples"), list) else []):
                    examples.append(str(value))
        for path in sorted((artifact_root / "intents").glob("*.intent")) if (artifact_root / "intents").exists() else []:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip():
                    examples.append(line.strip())
        return list(dict.fromkeys(examples))

    def _scenario_dependency_bootstrap_report(self, artifact_kind: str, artifact_root: Path) -> dict[str, Any]:
        if artifact_kind != "scenario":
            return {"available": False, "items": [], "status": "not_applicable"}
        manifest_path = self._descriptor_manifest_path(artifact_root, "scenario")
        data = _read_json(manifest_path) if manifest_path and manifest_path.suffix == ".json" else _read_yaml(manifest_path) if manifest_path else {}
        required = []
        for item in (data.get("depends") if isinstance(data.get("depends"), list) else []):
            required.append(str(item))
        runtime_skills = ((data.get("runtime") or {}).get("skills") if isinstance(data.get("runtime"), dict) else {}) or {}
        if isinstance(runtime_skills, dict):
            for item in (runtime_skills.get("required") if isinstance(runtime_skills.get("required"), list) else []):
                required.append(str(item))
        required = list(dict.fromkeys(item for item in required if item.strip()))
        items = []
        for name in required:
            present = self._skill_exists(name)
            items.append({"name": name, "ok": present, "status": "present" if present else "missing"})
        return {
            "available": True,
            "status": "ok" if all(item["ok"] for item in items) else "blocked",
            "required": required,
            "items": items,
            "failed": [item["name"] for item in items if not item["ok"]],
        }

    def _skill_exists(self, name: str) -> bool:
        roots = []
        if self.skills_root is not None:
            roots.append(Path(self.skills_root))
        if self.workspace_root is not None:
            roots.append(Path(self.workspace_root) / "skills")
        if self.repo_root is not None:
            roots.append(Path(self.repo_root) / ".adaos" / "workspace" / "skills")
        return any((root / name).exists() for root in roots)

    def _blast_radius_report(
        self,
        diff: dict[str, Any],
        action_preview: dict[str, Any],
        ui_preview: dict[str, Any],
        route_plan: dict[str, Any],
    ) -> dict[str, Any]:
        files = [item.get("path") for item in diff.get("files") or [] if isinstance(item, dict)]
        surfaces = []
        if any(str(path).endswith(("skill.yaml", "scenario.yaml")) for path in files):
            surfaces.append("manifest")
        if any(str(path).endswith("webui.json") for path in files):
            surfaces.append("webui")
        if any("intent" in str(path) or "nlu" in str(path) for path in files):
            surfaces.append("nlu")
        if action_preview.get("count"):
            surfaces.append("actions")
        if (ui_preview.get("widgets") or ui_preview.get("modals") or ui_preview.get("data_bindings")):
            surfaces.append("browser_ui")
        if route_plan.get("routes") or route_plan.get("receivers"):
            surfaces.append("data_routes")
        risk = "medium" if {"nlu", "actions"} & set(surfaces) else "low"
        if "data_routes" in surfaces and any(item.get("level") == "error" for item in route_plan.get("issues") or []):
            risk = "high"
        return {"surfaces": sorted(set(surfaces)), "risk": risk, "changed_files": files}

    def _test_plan(self, draft: dict[str, Any], artifact_kind: str) -> list[str]:
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        quality = draft.get("quality_gates") if isinstance(draft.get("quality_gates"), dict) else {}
        tests = []
        tests.extend(str(item) for item in (metadata.get("expected_tests") if isinstance(metadata.get("expected_tests"), list) else []))
        tests.extend(str(item) for item in (quality.get("tests") if isinstance(quality.get("tests"), list) else []))
        if artifact_kind == "skill":
            tests.append("adaos skill validate <draft-artifact> --preview")
        elif artifact_kind == "scenario":
            tests.append("adaos scenario validate <draft-artifact> --preview")
        return list(dict.fromkeys(tests))

    def _risk_summary(
        self,
        draft: dict[str, Any],
        schemas: dict[str, Any],
        route_plan: dict[str, Any],
        static_checks: dict[str, Any],
        blast_radius: dict[str, Any],
        bootstrap: dict[str, Any],
    ) -> list[dict[str, Any]]:
        risks: list[dict[str, Any]] = []
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        for note in (metadata.get("risk_notes") if isinstance(metadata.get("risk_notes"), list) else []):
            risks.append({"level": "info", "code": "draft.risk_note", "message": str(note)})
        for group_name, group in (("schemas", schemas), ("route_plan", route_plan), ("static_checks", static_checks)):
            for item in group.get("issues") or []:
                risks.append({"level": item.get("level", "warning"), "code": f"{group_name}.{item.get('code')}", "message": item.get("message"), "where": item.get("where")})
        if blast_radius.get("risk") in {"medium", "high"}:
            risks.append({"level": "warning", "code": "blast_radius.review", "message": f"blast radius risk is {blast_radius.get('risk')}", "surfaces": blast_radius.get("surfaces")})
        if bootstrap.get("status") == "blocked":
            risks.append({"level": "error", "code": "scenario_dependencies.missing", "message": "scenario has missing required skills", "failed": bootstrap.get("failed")})
        return risks

    def _normalize_approval_profile(self, value: str | None) -> str:
        profile_id = str(value or "manual_only").strip().lower().replace("-", "_")
        if profile_id not in _APPROVAL_PROFILES:
            allowed = ", ".join(sorted(_APPROVAL_PROFILES))
            raise ValueError(f"unknown Builder approval profile: {value}; allowed: {allowed}")
        return profile_id

    def _review_policy_report(
        self,
        *,
        draft: dict[str, Any],
        profile_id: str,
        diff: dict[str, Any],
        route_plan: dict[str, Any],
        static_checks: dict[str, Any],
        blast_radius: dict[str, Any],
        action_preview: dict[str, Any],
        nlu_probe: dict[str, Any],
        bootstrap: dict[str, Any],
    ) -> dict[str, Any]:
        profile = dict(_APPROVAL_PROFILES[profile_id])
        action_risk = self._action_risk_report(draft=draft, action_preview=action_preview)
        mandatory = self._mandatory_review_findings(
            draft=draft,
            diff=diff,
            route_plan=route_plan,
            static_checks=static_checks,
            action_preview=action_preview,
            nlu_probe=nlu_probe,
            bootstrap=bootstrap,
            action_risk=action_risk,
        )
        policy_blocks = self._approval_policy_blocks(
            profile=profile,
            mandatory=mandatory,
            blast_radius=blast_radius,
            route_plan=route_plan,
            static_checks=static_checks,
            bootstrap=bootstrap,
        )
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        if metadata.get("human_review_required", False):
            policy_blocks.append({"code": "draft_metadata_requires_review"})
        required = profile.get("requires_human_review") == "always" or bool(policy_blocks)
        auto_apply_eligible = bool(profile.get("auto_apply")) and not required
        decision = "auto_apply_eligible" if auto_apply_eligible else "human_review_required"
        return {
            "profile": profile,
            "mandatory_classes": mandatory,
            "policy_blocks": policy_blocks,
            "required_before_apply": required,
            "auto_apply_eligible": auto_apply_eligible,
            "decision": decision,
            "evidence": {
                "blast_radius_risk": blast_radius.get("risk"),
                "surfaces": blast_radius.get("surfaces") or [],
                "route_plan_ok": route_plan.get("ok"),
                "static_ok": static_checks.get("ok"),
                "scenario_dependency_status": bootstrap.get("status"),
                "action_risk": action_risk,
            },
        }

    def _action_risk_report(
        self,
        *,
        draft: dict[str, Any],
        action_preview: dict[str, Any],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
        if source:
            items.append(
                {
                    "source": "draft.source",
                    "action": {key: source.get(key) for key in ("type", "side_effect_class") if source.get(key) is not None},
                    "risk": conversation_safety.classify_action_risk(source),
                }
            )
        for idx, action in enumerate(action_preview.get("actions") or []):
            if not isinstance(action, dict):
                continue
            risk = action.get("action_risk")
            if not isinstance(risk, dict):
                risk = conversation_safety.classify_action_risk(action)
            items.append(
                {
                    "source": "action_preview",
                    "index": idx,
                    "action": {key: action.get(key) for key in ("id", "name", "title", "tool", "target", "side_effect_class") if action.get(key) is not None},
                    "risk": risk,
                }
            )
        order = {"safe": 0, "ui_navigation": 1, "local_write": 2, "filesystem": 3, "network": 3, "cross_node": 4, "device_control": 4, "credential": 5}
        max_risk = "safe"
        for item in items:
            risk_class = str((item.get("risk") or {}).get("risk_class") or "safe")
            if order.get(risk_class, 0) > order.get(max_risk, 0):
                max_risk = risk_class
        return {
            "schema": "adaos.builder.action_risk_review.v1",
            "max_risk_class": max_risk,
            "approval_required": any(bool((item.get("risk") or {}).get("approval_required")) for item in items),
            "items": items,
        }

    def _mandatory_review_findings(
        self,
        *,
        draft: dict[str, Any],
        diff: dict[str, Any],
        route_plan: dict[str, Any],
        static_checks: dict[str, Any],
        action_preview: dict[str, Any],
        nlu_probe: dict[str, Any],
        bootstrap: dict[str, Any],
        action_risk: dict[str, Any],
    ) -> list[dict[str, Any]]:
        findings: dict[str, dict[str, Any]] = {}

        def add(code: str, evidence: Any = None) -> None:
            if code not in _MANDATORY_REVIEW_CLASSES:
                return
            item = findings.setdefault(
                code,
                {
                    "class": code,
                    "description": _MANDATORY_REVIEW_CLASSES[code],
                    "evidence": [],
                },
            )
            if evidence is not None and evidence not in item["evidence"]:
                item["evidence"].append(evidence)

        for item in diff.get("files") or []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            patch = str(item.get("patch") or "")
            if _SECRET_HINT_RE.search(path) or any(_SECRET_HINT_RE.search(line) for line in patch.splitlines() if line.startswith("+")):
                add("secrets", path)
            if _PERMISSION_HINT_RE.search(patch):
                add("new_permissions", path)
            if _EXTERNAL_IO_RE.search(patch):
                add("external_io", path)
            if _PROCESS_RE.search(patch):
                add("service_processes", path)
            if _ENDPOINT_RE.search(patch):
                add("endpoint_control", path)

        for issue in static_checks.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            code = str(issue.get("code") or "")
            if code == "static.unsafe_direct_yjs":
                add("endpoint_control", issue.get("where"))
            if code == "static.unbounded_memory":
                add("service_processes", issue.get("where"))

        for route in route_plan.get("routes") or []:
            if not isinstance(route, dict):
                continue
            if str(route.get("route") or "").strip() == "stream" and self._route_budget_rate(route.get("budget")) > 10:
                add("high_rate_streams", route.get("surface") or route.get("receiver"))
            surface = str(route.get("surface") or "")
            if _ENDPOINT_RE.search(surface):
                add("endpoint_control", surface)

        for receiver in route_plan.get("receivers") or []:
            if not isinstance(receiver, dict):
                continue
            if self._route_budget_rate(receiver.get("budget")) > 10:
                add("high_rate_streams", receiver.get("id"))

        for action in action_preview.get("actions") or []:
            if not isinstance(action, dict):
                continue
            text = " ".join(str(action.get(key) or "") for key in ("id", "name", "title", "description", "label", "tool", "target"))
            if _DESTRUCTIVE_ACTION_RE.search(text):
                add("destructive_actions", text[:160])
            if _ENDPOINT_RE.search(text):
                add("endpoint_control", text[:160])

        for item in action_risk.get("items") or []:
            if not isinstance(item, dict):
                continue
            risk = item.get("risk") if isinstance(item.get("risk"), dict) else {}
            if not risk.get("mandatory_review"):
                continue
            risk_class = str(risk.get("risk_class") or "").strip()
            if risk_class:
                add(risk_class, {"source": item.get("source"), "action": item.get("action"), "reasons": risk.get("reasons")})

        for example in nlu_probe.get("candidate_examples") or []:
            text = str(example or "").strip()
            if not text:
                continue
            token_count = len(text.split())
            if token_count <= 1 or ".*" in text or "*" in text:
                add("broad_nlu_patterns", text[:160])

        if bootstrap.get("status") == "blocked":
            add("new_permissions", {"missing_dependencies": bootstrap.get("failed") or []})

        source = draft.get("source") if isinstance(draft.get("source"), dict) else {}
        side_effect = str(source.get("side_effect_class") or "").strip().lower()
        if side_effect in {"external_io", "network", "destructive", "endpoint_control", "secrets"}:
            if side_effect == "destructive":
                add("destructive_actions", side_effect)
            elif side_effect == "network":
                add("external_io", side_effect)
            else:
                add(side_effect if side_effect in _MANDATORY_REVIEW_CLASSES else "external_io", side_effect)

        return [findings[key] for key in sorted(findings)]

    def _route_budget_rate(self, budget: Any) -> float:
        if not isinstance(budget, dict):
            return 0.0
        for key in ("events_per_second", "max_events_per_second", "rate_hz", "max_hz", "fps"):
            try:
                value = float(budget.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0.0

    def _approval_policy_blocks(
        self,
        *,
        profile: dict[str, Any],
        mandatory: list[dict[str, Any]],
        blast_radius: dict[str, Any],
        route_plan: dict[str, Any],
        static_checks: dict[str, Any],
        bootstrap: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if mandatory:
            blocks.append({"code": "mandatory_review_class", "classes": [item["class"] for item in mandatory]})
        if profile.get("requires_human_review") == "before_apply":
            blocks.append({"code": "profile_requires_review_before_apply", "profile": profile.get("id")})
        if blast_radius.get("risk") in {"medium", "high"} and profile.get("id") == "low_risk_auto_apply":
            blocks.append({"code": "not_low_risk", "risk": blast_radius.get("risk")})
        allowed_surfaces = set(profile.get("allowed_surfaces") or [])
        if allowed_surfaces:
            surfaces = set(blast_radius.get("surfaces") or [])
            disallowed = sorted(surfaces - allowed_surfaces)
            if disallowed:
                blocks.append({"code": "surface_not_allowed_by_profile", "surfaces": disallowed})
        if not route_plan.get("ok", True):
            blocks.append({"code": "route_plan_errors"})
        if not static_checks.get("ok", True):
            blocks.append({"code": "static_check_errors"})
        if bootstrap.get("status") == "blocked":
            blocks.append({"code": "scenario_dependency_blocked", "failed": bootstrap.get("failed") or []})
        return blocks

    def _human_review_summary(
        self,
        draft: dict[str, Any],
        risk_summary: list[dict[str, Any]],
        review_policy: dict[str, Any],
    ) -> dict[str, Any]:
        quality = draft.get("quality_gates") if isinstance(draft.get("quality_gates"), dict) else {}
        reasons = list(quality.get("requires_human_approval") or [])
        reasons.extend(item.get("code") for item in risk_summary if item.get("level") in {"error", "warning"})
        reasons.extend(item.get("class") for item in review_policy.get("mandatory_classes") or [])
        reasons.extend(item.get("code") for item in review_policy.get("policy_blocks") or [])
        reasons = [str(item) for item in reasons if item]
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        required = bool(
            metadata.get("human_review_required", False)
            or review_policy.get("required_before_apply")
            or any(item.get("level") == "error" for item in risk_summary)
        )
        return {
            "required": required,
            "reasons": list(dict.fromkeys(reasons)),
            "profile_id": (review_policy.get("profile") or {}).get("id"),
            "decision": review_policy.get("decision"),
        }
