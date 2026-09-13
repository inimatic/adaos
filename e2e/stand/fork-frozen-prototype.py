"""Create a new TEST application from an accepted Prototype, never from Automation code.

Only identifiers are remapped. Review inheritance is conditional on reversible
WebUI equivalence and retained resource/locale digests. This is an evaluation
fixture import, not a new generation or an independent visual acceptance.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _load_document, _write_json
from adaos.sdk.builder import workflow
from adaos.sdk.developer import compositions, projects
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.resources.prototype import PrototypeResourceService, prototype_webui_digest


def remap(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [remap(item, old, new) for item in value]
    if isinstance(value, dict):
        return {remap(key, old, new): remap(item, old, new) for key, item in value.items()}
    return value


def confined(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError("Fixture path escapes its evidence root")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("source_git", type=Path, help="Retained pre-Automation workspace Git tree")
    parser.add_argument("plan", type=Path)
    parser.add_argument("identifier")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    load_dotenv()
    evidence_root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or not re.fullmatch(r"test_[a-z0-9_-]{8,72}", args.identifier):
        parser.error("Requires DEV and an explicit unique test_ identifier")
    if not output.is_relative_to(evidence_root) or output == evidence_root or output.exists():
        parser.error("Requires new evidence inside e2e/artifacts/builder")
    baseline, review = _load_document(args.baseline / "pin.json"), _load_document(args.review)
    old, new = baseline["scenario_id"], args.identifier
    ui = _load_document(args.baseline / "prototype.webui.json")
    if old == new or not old.startswith("test_") or "[TEST]" not in json.dumps(ui, ensure_ascii=False):
        raise ValueError("Source and target must be distinct TEST applications")
    if (review["project_ref"] != f"scenario:{old}" or prototype_webui_digest(ui) != review["webui_digest"]
            or baseline["webui_digest"] != review["webui_digest"]):
        raise ValueError("The exact reviewed Prototype is required")
    revision = baseline["revision"]
    if not revision.isdigit():
        raise ValueError("Requires an immutable numeric Prototype revision")
    source_commit = subprocess.check_output(["git", "-C", str(args.source_git), "rev-parse", "HEAD"], text=True).strip()

    def git_json(relative):
        raw = subprocess.check_output(["git", "-C", str(args.source_git), "show", f"{source_commit}:scenarios/{old}/{relative}"])
        return json.loads(raw.decode("utf-8-sig"))

    if prototype_webui_digest(git_json("webui.json")) != baseline["webui_digest"]:
        raise ValueError("Git tree is not the pre-Automation Prototype")
    mapped_ui = remap(ui, old, new)
    if remap(mapped_ui, new, old) != ui:
        raise ValueError("Identifier remapping must be reversible without design changes")
    mapped_digest = prototype_webui_digest(mapped_ui)
    init_ctx(Settings.from_sources())
    for kind in ("scenario", "skill"):
        if projects.resolve_root(kind, new if kind == "scenario" else new + "_skill", required=False).exists():
            raise ValueError("Target source already exists")
    original_source = projects.resolve_root("scenario", old) / "webui.json"
    original_digest = hashlib.sha256(original_source.read_bytes()).hexdigest()
    state = workflow.get_state("scenario", old)
    accepted = state["prototype"]["acceptance"]
    if accepted["webui_digest"] != baseline["webui_digest"] or accepted["revision"] != revision:
        raise ValueError("Source acceptance is no longer the selected Prototype")
    expected_resources = {item["resource_type"]: item for item in accepted["prototype_resources"]}
    service = BuilderWorkflowService.from_context()
    provider = PrototypeResourceService()
    bundles = []
    for resource_type in service._prototype_resource_types(ui):
        resource = provider._require_state(resource_type)
        snapshots = provider.acceptance_snapshots(project_ref=resource["project_ref"], change_id=accepted["change_id"],
            revision=revision, webui_digest=baseline["webui_digest"], resource_types=[resource_type])
        expected = expected_resources[resource_type]
        if any(snapshots[0][key] != expected[key] for key in ("records_digest", "definition_digest", "bundle_digest")):
            raise ValueError("Source resource changed after acceptance")
        data = copy.deepcopy(resource["data_definition"])
        data["seed"] = resource["records"]
        bundles.append({"schema": "adaos.builder.prototype_resource.v1", "project_ref": f"project:{new}",
            "revision": revision, "webui_digest": mapped_digest,
            "resource_definition": remap(resource["definition"], old, new),
            "data_definition": remap(data, old, new)})
    locales = {}
    for item in ui["ui"]["application"].get("resources", {}).values():
        if item.get("role") == "i18n":
            confined(Path("."), item["path"])
            locales[item["path"]] = git_json(item["path"])
    output.mkdir(parents=True)
    receipt = {"scope": "Inherited accepted Prototype, not new generation; Automation candidate excluded",
        "source": old, "target": new, "revision": revision, "source_git_commit": source_commit,
        "source_webui_digest": baseline["webui_digest"], "webui_digest": mapped_digest,
        "identifier_only_equivalence": True, "protected_automation_webui_sha256": original_digest,
        "source_acceptance_id": accepted["acceptance_id"], "source_acceptance_digest": accepted["digest"], "ok": False}
    _write_json(output / "fork.json", receipt)
    title = f"Automation repeat [TEST] {new}"
    compositions.create_with_primary_component(new, kind="scenario", template="scenario_default", title=title,
        description="Isolated Automation evaluation from an accepted Prototype", tags=["test", "automation-e2e"])

    def write(relative, value):
        confined(projects.resolve_root("scenario", new), relative)
        projects.write_file("scenario", new, relative, json.dumps(value, ensure_ascii=False, indent=2) + "\n", max_bytes=1_048_576)

    write("webui.json", mapped_ui)
    write("semantic.webui.json", remap(git_json("semantic.webui.json"), old, new))
    # Trusted fixture import creates append-only revision files exclusively. The
    # public editing SDK intentionally does not allow rewriting these files.
    revision_path = confined(projects.resolve_root("scenario", new), f"ui_revisions/{revision}.json")
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    with revision_path.open("x", encoding="utf-8") as stream:
        json.dump({"schema": "adaos.builder.ui_revision.v1", "scenario_id": new,
            "revision": revision, "after_webui": mapped_ui,
            "request": {"text": "Retained accepted Prototype for isolated Automation"}}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    with revision_path.with_name("current.txt").open("x", encoding="utf-8") as stream:
        stream.write(revision + "\n")
    for relative, value in locales.items():
        write(relative, value)
    _, locale_evidence = service._prototype_locale_snapshot("scenario", new, mapped_ui, revision)
    if locale_evidence != expected_resources.get("prototype.locale_dictionaries"):
        raise ValueError("Retained locale assets do not match the accepted Prototype")
    changed = workflow.transition("scenario", new, "plan_change_set", actor="agent:builder-e2e", metadata={
        "change_set_id": f"e2e-frozen-{new}", "request": "Automate the inherited accepted Prototype without regenerating it.",
        "prototype_acceptance_required": True, "issues": [{"issue_id": "frozen-prototype", "title": "Retain accepted Prototype",
            "lane": "prototype", "acceptance_criteria": ["The reviewed Prototype is preserved."]}]})["workflow"]
    for bundle in bundles:
        bundle["change_id"] = changed["change"]["change_id"]
        provider.materialize(bundle)
    inherited = copy.deepcopy(review)
    inherited.update(project_ref=f"scenario:{new}", webui_digest=mapped_digest,
        review_scope="Inherited review through reversible identifier-only equivalence; not independently rendered yet")
    for check in inherited["behavior_checks"]:
        for ref in check["evidence_refs"]:
            source = confined(args.review.parent.parent, ref)
            dest = confined(output, ref)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    for check in inherited["visual_checks"]:
        ref = check["evidence_ref"]
        dest = confined(output, ref)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(args.review.parent.parent, ref), dest)
    _write_json(output / "reviews/accepted-prototype.json", inherited)
    _write_json(output / "plan.json", _load_document(args.plan))
    checkpoint = {"context": {"bundle_dir": str(output), "run_id": output.name, "case_id": "frozen-repeat",
        "owned_artifacts": [{"project_id": new, "primary_ref": f"scenario:{new}"}]}, "cleanup": {"test": True}}
    _write_json(output / "checkpoint.json", checkpoint)
    if hashlib.sha256(original_source.read_bytes()).hexdigest() != original_digest:
        raise ValueError("Protected successful Automation changed during fixture import")
    receipt["ok"] = True
    _write_json(output / "fork.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
