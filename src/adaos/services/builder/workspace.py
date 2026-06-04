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
from typing import Any

import yaml
from jsonschema import Draft202012Validator, Draft7Validator, ValidationError

from adaos.services.runtime_paths import current_repo_root, current_state_dir


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_.-]+", "_", text).strip("._-")
    return text or "builder"


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


def _template_dir(kind: str, template_id: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    if kind == "skill":
        path = root / "skills_templates" / template_id
    elif kind == "scenario":
        path = root / "scenario_templates" / template_id
    else:
        raise ValueError(f"unsupported template kind: {kind}")
    if not path.exists():
        raise FileNotFoundError(f"Builder template not found: {path}")
    return path


@dataclass(slots=True)
class BuilderWorkspaceService:
    """Create draft workspaces and preview bundles without mutating runtime state."""

    state_dir: Path | None = None
    repo_root: Path | None = None
    workspace_root: Path | None = None
    skills_root: Path | None = None
    scenarios_root: Path | None = None

    @classmethod
    def from_context(cls) -> "BuilderWorkspaceService":
        repo_root = current_repo_root()
        state_dir = current_state_dir()
        workspace_root = None
        skills_root = None
        scenarios_root = None
        try:
            from adaos.services.agent_context import get_ctx

            ctx = get_ctx()
            workspace_root = Path(ctx.paths.workspace_dir()).expanduser().resolve()
            skills_root = Path(ctx.paths.skills_dir()).expanduser().resolve()
            scenarios_root = Path(ctx.paths.scenarios_dir()).expanduser().resolve()
        except Exception:
            if repo_root is not None:
                workspace_root = repo_root / ".adaos" / "workspace"
                skills_root = workspace_root / "skills"
                scenarios_root = workspace_root / "scenarios"
        return cls(
            state_dir=state_dir,
            repo_root=repo_root,
            workspace_root=workspace_root,
            skills_root=skills_root,
            scenarios_root=scenarios_root,
        )

    @property
    def root(self) -> Path:
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
            )
        if kind not in {"skill", "scenario"}:
            raise ValueError("kind must be skill, scenario, or descriptor_fix")

        template_id = template_id or ("skill_default" if kind == "skill" else "scenario_default")
        draft_id = self._new_draft_id(artifact_id)
        draft_dir = self.drafts_dir() / draft_id
        artifact_root = draft_dir / "artifact"
        _copytree(_template_dir(kind, template_id), artifact_root)

        if kind == "skill":
            self._patch_skill_template(artifact_root, artifact_id, source_idea)
        else:
            self._patch_scenario_template(artifact_root, artifact_id, source_idea)

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

    def preview(self, *, draft_id: str) -> dict[str, Any]:
        draft_id = str(draft_id or "").strip()
        draft_dir = self.drafts_dir() / draft_id
        draft = self.load_draft(draft_id)
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
        human_review = self._human_review_summary(draft, risk_summary)

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
            "human_review": human_review,
            "summary": {
                "changed_files": len(diff.get("files") or []),
                "schema_ok": schemas.get("ok"),
                "route_plan_ok": route_plan.get("ok"),
                "static_ok": static_checks.get("ok"),
                "human_review_required": human_review.get("required"),
            },
        }
        _write_json(self.previews_dir() / f"{preview_id}.json", preview)
        self._mark_draft_previewed(draft_dir, draft, preview_id)
        return {"ok": True, "preview": preview}

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
        _write_yaml(manifest, data)

    def _patch_scenario_template(self, artifact_root: Path, artifact_id: str, source_idea: str) -> None:
        manifest = artifact_root / "scenario.json"
        data = _read_json(manifest)
        data["id"] = artifact_id
        data.setdefault("version", "0.1.0")
        data["name"] = data.get("name") or artifact_id
        data["description"] = data.get("description") or source_idea
        data.setdefault("llm_hints", {})
        data["llm_hints"]["description"] = source_idea
        data.setdefault("nlu", {})
        data["nlu"].setdefault("nlu_hints", {})
        data["nlu"]["nlu_hints"].setdefault("examples", [])
        _write_json(manifest, data)

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
    ) -> dict[str, Any]:
        target_kind = str(target_kind or "skill").strip().lower()
        if target_kind not in {"skill", "scenario"}:
            raise ValueError("descriptor_fix target_kind must be skill or scenario")
        source_root = self._resolve_target_root(target_kind, artifact_id, explicit=target_root)
        draft_id = self._new_draft_id(f"{artifact_id}.descriptor")
        draft_dir = self.drafts_dir() / draft_id
        artifact_root = draft_dir / "artifact"
        _copytree(source_root, artifact_root)
        materialization = self._materialize_descriptor_fix(
            artifact_root=artifact_root,
            target_kind=target_kind,
            target_id=artifact_id,
            source_idea=source_idea,
            descriptor_changes=descriptor_changes or {},
        )
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
        names = ("skill.yaml", "skill.yml") if target_kind == "skill" else ("scenario.json", "scenario.yaml", "scenario.yml")
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
    ) -> dict[str, Any]:
        task_id = task_id or f"btask.{_stable_suffix(source_idea, artifact_kind, artifact_id)}"
        source_payload = source if isinstance(source, dict) and source.get("type") else {"type": "human_idea", "text": source_idea}
        file_refs = self._file_refs(artifact_root)
        quality = self._quality_gates(artifact_kind)
        now = _now_iso()
        merged_links = dict(links or {})
        merged_links.setdefault("builder_task_id", task_id)
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
                "human_review_required": True,
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
            refs.append({"path": rel, "role": self._file_role(rel), "required": rel in {"skill.yaml", "scenario.json"}})
        return refs

    def _file_role(self, rel: str) -> str:
        if rel in {"skill.yaml", "skill.yml", "scenario.json", "scenario.yaml", "scenario.yml"}:
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
                issues.append(_issue("error", "schema.scenario_manifest_missing", "scenario.json/scenario.yaml is missing", "scenario"))
            else:
                data = _read_json(manifest_path) if manifest_path.suffix == ".json" else _read_yaml(manifest_path)
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
        return {"actions": hints, "count": len(hints)}

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
        if any(str(path).endswith(("skill.yaml", "scenario.json", "scenario.yaml")) for path in files):
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

    def _human_review_summary(self, draft: dict[str, Any], risk_summary: list[dict[str, Any]]) -> dict[str, Any]:
        quality = draft.get("quality_gates") if isinstance(draft.get("quality_gates"), dict) else {}
        reasons = list(quality.get("requires_human_approval") or [])
        reasons.extend(item.get("code") for item in risk_summary if item.get("level") in {"error", "warning"})
        reasons = [str(item) for item in reasons if item]
        metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
        required = bool(metadata.get("human_review_required", True) or any(item.get("level") == "error" for item in risk_summary))
        return {"required": required, "reasons": list(dict.fromkeys(reasons))}
