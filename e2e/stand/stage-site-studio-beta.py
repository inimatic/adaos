"""Stage the reviewed workbench and owned adapters from an explicit immutable baseline.

This does not prepare, accept, install or publish an Application release.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import yaml

from site_studio_beta import APP, SKILL, connected_beta

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


REPLACEMENTS = {
    "chat": '''def chat(text: str = "", **payload: Any) -> dict[str, Any]:
    from .authoring_chat import discuss
    return discuss(text, payload.get("subject", "site"), payload.get("block_id", ""), payload.get("references"), payload.get("web_search", False))''',
    "discuss": '''def discuss(text: str, subject: str = 'site', block_id: str = '', references: list[dict] | None = None, web_search: bool = False):
    from .authoring_chat import discuss as run
    return run(text, subject, block_id, references, web_search)''',
    "resume_discussion": '''def resume_discussion(job_id: str):
    from .authoring_chat import owns, resume
    if owns(job_id):
        return resume(job_id)
    from .discussion import resume as legacy_resume
    return legacy_resume(job_id)''',
    "query_discussion": '''def query_discussion():
    from .discussion import query_discussion as query
    from .authoring_chat import discussion_projection
    return discussion_projection(query())''',
    "query_preview": '''def query_preview(candidate_change_set_id: str = ""):
    from contextlib import closing
    from .discussion import authorize
    from .authoring import _database
    from .compiler import current
    authorize()
    if candidate_change_set_id:
        from .authoring_chat import candidate_preview
        return candidate_preview(candidate_change_set_id)
    with closing(_database()) as db:
        return current(db)''',
    "query_site_history": '''def query_site_history():
    from .history import query
    from .authoring_chat import history_projection
    return history_projection(query())''',
}


def annotation_schema(annotation):
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left, right = annotation_schema(annotation.left), annotation_schema(annotation.right)
        return {"anyOf": [left, right]}
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return {"type": "null"}
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        return {"type": "array", "items": {"type": "object"}} if annotation.value.id == "list" else {"type": "object"}
    name = annotation.id if isinstance(annotation, ast.Name) else "Any"
    return {"type": {"str": "string", "int": "integer", "bool": "boolean", "dict": "object", "float": "number"}.get(name, "object")}


def input_schema(function):
    properties = {}
    required = []
    arguments = function.args.args
    defaults = [None] * (len(arguments) - len(function.args.defaults)) + list(function.args.defaults)
    for argument, default in zip(arguments, defaults):
        properties[argument.arg] = annotation_schema(argument.annotation)
        if default is None:
            required.append(argument.arg)
    properties.update(project_id={"type": "string"}, selection_revision={"type": "integer", "minimum": 1})
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-skill", type=Path, required=True)
    parser.add_argument("--baseline-webui", type=Path, required=True)
    parser.add_argument("--subnet", default="sn_6acf0c01")
    args = parser.parse_args()
    baseline = args.baseline_skill.resolve(strict=True)
    if baseline.name != SKILL or not baseline.is_relative_to(ROOT / ".adaos/trials"):
        parser.error("An exact retained Trial skill baseline is required")
    target = ROOT / ".adaos/dev" / args.subnet / "skills" / SKILL
    scenario = ROOT / ".adaos/dev" / args.subnet / "scenarios" / APP
    if not target.is_dir() or not scenario.is_dir():
        parser.error("The existing development-owned checkouts must be present")
    backup = ROOT / ".adaos/state/private_recovery/site-studio-beta" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup.mkdir(parents=True)
    shutil.copytree(target, backup / "skill", ignore=shutil.ignore_patterns("__pycache__", ".git"))
    shutil.copy2(scenario / "webui.json", backup / "webui.json")
    for path in baseline.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            relative = path.relative_to(baseline)
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output)
    templates = ROOT / "src/adaos/skills_templates/site_studio/handlers"
    for name in ("authoring_chat.py", "source_files.py", "component_properties.py"):
        shutil.copy2(templates / name, target / "handlers" / name)
    for test in (templates.parent / "tests").glob("test_*.py"):
        shutil.copy2(test, target / "tests" / test.name)
    path = target / "handlers/main.py"
    text = path.read_text("utf-8")
    lines = text.splitlines(keepends=True)
    nodes = {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
    for name in sorted(REPLACEMENTS, key=lambda name: nodes[name].lineno, reverse=True):
        node = nodes[name]
        lines[node.lineno - 1:node.end_lineno] = [REPLACEMENTS[name] + "\n"]
    fragment = (templates / "beta_tools.py").read_text("utf-8")
    text = "".join(lines) + "\n\n" + fragment
    ast.parse(text)
    path.write_text(text, encoding="utf-8")
    manifest_path = target / "skill.yaml"
    manifest = yaml.safe_load(manifest_path.read_text("utf-8"))
    definitions = {item["name"]: item for item in manifest["tools"]}
    new_functions = [node for node in ast.parse(fragment).body if isinstance(node, ast.FunctionDef)]
    for function in new_functions:
        name = function.name
        old = definitions.get(name)
        if old:
            raise ValueError("Beta tool collides with the baseline: " + name)
        write = not (name.startswith("query_") or name.startswith("read_"))
        permissions = ["workspace.write" if write else "workspace.read"]
        if name in {"continue_authoring"}:
            permissions.append("llm.generate")
        if name in {"upload_source_file", "read_source_attachment"}:
            permissions.append("storage.blob")
        item = {"name": name, "description": name.replace("_", " "), "entry": "handlers.main:" + name,
            "side_effects": "local_write" if write else "read_only", "input_schema": input_schema(function),
            "output_schema": {"type": "object"}, "permissions": permissions,
            "application_access": {"permission": "workspace.write" if write else "workspace.read"}}
        manifest["tools"].append(item)
        manifest["exports"]["tools"].append(name)
    main_functions = {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
    for name in ("discuss", "query_preview"):
        definitions[name]["input_schema"] = input_schema(main_functions[name])
    from adaos.services.skill.activation_assessment import inspect_handler_activation, recommended_activation
    manifest["runtime"]["activation"] = recommended_activation(manifest, inspect_handler_activation(path))
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    legacy = json.loads(args.baseline_webui.read_text("utf-8"))
    webui = connected_beta(legacy)
    from adaos.sdk.developer import ui
    validation = ui.validate_contract(webui)
    if not validation["ok"]:
        raise ValueError(str(validation)[:2000])
    (scenario / "webui.json").write_text(json.dumps(webui, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    receipt = {"baseline": str(baseline), "backup": str(backup), "skill": str(target), "scenario": str(scenario),
               "tools": len(manifest["exports"]["tools"]), "webui_digest": hashlib.sha256((scenario / "webui.json").read_bytes()).hexdigest()}
    output = ROOT / ".tmp/site-studio-next/beta-source-staging.json"
    output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"staged": True, "tools": receipt["tools"], "release_prepared": False}))


if __name__ == "__main__":
    main()
