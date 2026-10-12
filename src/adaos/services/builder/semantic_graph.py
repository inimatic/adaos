"""Core-owned, bounded semantic label edits and retained transaction recovery.

This first slice supports existing view/field labels only. It does not execute
Application commands, alter resource schemas, or provide distributed authorship.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock
from .semantic_prototype import compile_semantic_prototype, validate_semantic_prototype
from .semantic_ui import _operation_root
from .workflow import BuilderWorkflowError, BuilderWorkflowService, _LOCK

SCHEMA = "adaos.builder.semantic_graph_change.v1"
JOURNAL_SCHEMA = "adaos.builder.semantic_graph_transaction.v1"
REDUCER = "semantic_graph.labels.v1"


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _contract() -> dict[str, Any]:
    return json.loads((Path(__file__).resolve().parents[2] / "abi/builder.semantic_graph_change.v1.schema.json").read_text(encoding="utf-8"))


def _read(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise BuilderWorkflowError("semantic artifact exceeds the bounded slice")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise BuilderWorkflowError("semantic artifact must be an object")
    return value


def _label(document: Mapping[str, Any], ref: str) -> dict[str, Any]:
    kind, _, identity = ref.partition(":")
    if kind == "view":
        candidates = [item["title"] for item in document["views"] if item["id"] == identity]
    elif kind == "field":
        candidates = [field["label"] for resource in document["resources"] for field in resource["fields"]
                      if (field['id'] if str(field['id']).startswith(f"{resource['id']}.")
                          else f"{resource['id']}.{field['id']}") == identity]
    else:
        candidates = []
    if len(candidates) != 1:
        raise BuilderWorkflowError(f"semantic label ref must resolve exactly once: {ref}")
    return candidates[0]


def _text_nodes(value: Any):
    if isinstance(value, Mapping):
        if "key" in value and any(locale in value for locale in ("en", "ru")):
            yield value
        for child in value.values():
            yield from _text_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_nodes(child)


def reduce_graph(document: Mapping[str, Any], ops: Sequence[Mapping[str, Any]], *, write_refs: Sequence[str]) -> dict[str, Any]:
    """Pure ordered reduction. Only admitted locale text slots may change."""
    if document.get("schema") != "adaos.webui.semantic.v2":
        raise BuilderWorkflowError("graph label reduction requires semantic v2")
    result = copy.deepcopy(dict(document))
    schema = _contract()
    validator = Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/op"})
    if not ops or len(ops) > 16 or not write_refs or len(write_refs) > 16:
        raise BuilderWorkflowError("bounded graph label operation count required")
    inverse = []
    for seq, operation in enumerate(ops, 1):
        try:
            validator.validate(dict(operation))
        except ValidationError as exc:
            raise BuilderWorkflowError(f"invalid semantic operation: {exc.message}") from exc
        if operation["seq"] != seq:
            raise BuilderWorkflowError("semantic operation sequence must be contiguous")
        if operation["ref"] not in write_refs:
            raise BuilderWorkflowError("semantic operation is outside the admitted write scope")
        label = _label(result, operation["ref"])
        if any(node is not label and node.get("key") == label["key"] for node in _text_nodes(result)):
            raise BuilderWorkflowError("shared locale key prevents an isolated semantic label edit")
        locale = operation["locale"]
        if locale not in label or label[locale] != operation["expected"]:
            raise BuilderWorkflowError("semantic label precondition failed")
        if not operation["text"].strip():
            raise BuilderWorkflowError("semantic label cannot be blank")
        inverse.append({**operation, "expected": operation["text"], "text": label[locale]})
        label[locale] = operation["text"]
    validate_semantic_prototype(result)
    inverse = [{**operation, "seq": seq} for seq, operation in enumerate(reversed(inverse), 1)]
    return {"semantic_document": result, "semantic_digest": digest(result), "inverse_ops": inverse, "reducer": REDUCER}


def _runtime_projection(webui: Mapping[str, Any]) -> dict[str, Any]:
    """Exclude only declared bookkeeping and validated Core i18n descriptors."""
    value = copy.deepcopy(dict(webui))
    ui = value["ui"]
    ui.pop("version", None)
    app = ui["application"]
    page = app["desktop"]["pageSchema"]
    metadata = page["meta"]["builder"]
    for key in ("ui_revision", "proto", "scenario_id", "process_constraints", "automation_requirements"):
        metadata.pop(key, None)
    resources = app.get("resources", {})
    for identity, resource in list(resources.items()):
        locale = resource.get("locale")
        if locale in {"en", "ru"} and resource == {
            "kind": "data", "role": "i18n", "locale": locale,
            "path": f"assets/i18n/{locale}.json", "mime": "application/json", "delivery": "core",
        } and identity == f"{page['id']}.i18n.{locale}":
            resources.pop(identity)
    if not resources:
        app.pop("resources", None)
    return value


def verify_baseline(document: Mapping[str, Any], webui: Mapping[str, Any], *, project_ref: str) -> dict[str, Any]:
    """Compare the effective UI with recompilation; a digest marker is insufficient."""
    if document.get("schema") != "adaos.webui.semantic.v2":
        raise BuilderWorkflowError("semantic baseline requires v2 source")
    actual = _runtime_projection(webui)
    for locale in ("en", "ru"):
        compiled = compile_semantic_prototype(document, project_ref=project_ref, primary_locale=locale)
        if compiled["semantic_digest"] != digest(document):
            raise BuilderWorkflowError("semantic baseline source is not canonical; explicit normalization is required")
        if _runtime_projection(compiled["webui"]) == actual:
            return {"compiled": compiled, "primary_locale": locale, "semantic_digest": digest(document)}
    raise BuilderWorkflowError("semantic baseline drift: effective UI differs from compiled source")


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


@dataclass(slots=True)
class SemanticGraphService:
    workflow: BuilderWorkflowService

    @classmethod
    def from_context(cls):
        return cls(BuilderWorkflowService.from_context())

    @staticmethod
    def _file(root: Path, name: str) -> Path:
        if not (name in {"semantic.webui.json", "webui.json", "scenario.json", "ui_revisions/current.txt"}
                or re.fullmatch(r"assets/i18n/(en|ru)\.json|ui_revisions/[0-9]{3,6}\.json", name)):
            raise BuilderWorkflowError("unsupported semantic transaction artifact path")
        target = (root / name).resolve()
        if not target.is_relative_to(root.resolve()):
            raise BuilderWorkflowError("semantic artifact escapes project ownership")
        return target

    def _restore(self, root: Path, files: Mapping[str, Any]) -> None:
        for name, encoded in files.items():
            path = self._file(root, name)
            if encoded is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, base64.b64decode(encoded, validate=True))

    def _recover(self, root: Path, object_id: str) -> None:
        for path in sorted(_operation_root(self.workflow, f"scenario:{object_id}").glob("*.json")):
            journal = _read(path)
            if journal.get("schema") != JOURNAL_SCHEMA or journal.get("status") != "prepared":
                continue
            current = self.workflow.describe("scenario", object_id)
            request = journal["request"]
            if request["project_ref"] != f"scenario:{object_id}" or digest(request) != journal["request_digest"]:
                raise BuilderWorkflowError("semantic transaction recovery identity mismatch")
            if (current.get("change") or {}).get("change_id") != request["change_id"]:
                raise BuilderWorkflowError("pending semantic transaction has a conflicting Change")
            canonical = self.workflow._read_state("scenario", object_id).get("workflow", {})
            head = (canonical.get("prototype") or {}).get("head_revision")
            generation = canonical.get("generation", 0)
            for name, before in journal["before_files"].items():
                target = self._file(root, name)
                present = base64.b64encode(target.read_bytes()).decode("ascii") if target.is_file() else None
                if present not in (before, journal["after_files"][name]):
                    raise BuilderWorkflowError("semantic transaction recovery found conflicting artifact bytes")
            if head == journal["result"]["revision"] and generation == request["expected_generation"] + 1:
                self._restore(root, journal["after_files"])
                journal["status"] = "committed"
                atomic_write_json(path, journal)
            elif generation == request["expected_generation"]:
                self._restore(root, journal["before_files"])
                journal["status"] = "aborted"
                atomic_write_json(path, journal)
            else:
                raise BuilderWorkflowError("pending semantic transaction has a conflicting revision")

    def prepare_label_request(self, *, project_ref: str, instruction: str,
                              source_message_id: str, expected_revision: str | None = None) -> dict[str, Any]:
        """Read-only admission packet, or the exact retained request for a retry.

        Caller intake may update the workflow generation but must not change the
        admitted source/revision/write set. apply still verifies the entire base.
        """
        from .semantic_label_requests import parse_request, plan
        parsed = parse_request(instruction)
        if parsed is None:
            return {"status": "not_applicable"}
        if not re.fullmatch(r"scenario:[A-Za-z0-9_.-]{1,160}", project_ref):
            raise BuilderWorkflowError("semantic label request requires a scenario ref")
        origin = {"instruction": instruction.strip(), "source_message_id": source_message_id}
        if not source_message_id.strip() or len(source_message_id) > 240 or len(origin["instruction"]) > 1000:
            raise BuilderWorkflowError("semantic label request requires bounded source message identity")
        object_id = project_ref.partition(":")[2]
        root = self.workflow.project_root("scenario", object_id)
        operation_root = _operation_root(self.workflow, project_ref)
        with _LOCK, mutation_lock(operation_root / ".mutation.lock"):
            state = self.workflow.describe("scenario", object_id)
            change_id = (state.get("change") or {}).get("change_id")
            if not change_id or not state["capabilities"].get("can_edit_prototype"):
                raise BuilderWorkflowError("semantic label request requires an active editable Change")
            self._recover(root, object_id)
            state = self.workflow.describe("scenario", object_id)
            transaction_id = "label-request-" + digest([project_ref, source_message_id])[7:39]
            path = operation_root / (digest([change_id, transaction_id])[7:] + ".json")
            if path.is_file():
                journal = _read(path)
                if journal["request"].get("source_request") != origin:
                    raise BuilderWorkflowError("source message identity conflicts with retained label request")
                if journal["status"] != "committed":
                    raise BuilderWorkflowError("uncommitted label request requires explicit recovery")
                if expected_revision and expected_revision != journal["request"]["source_revision"]:
                    raise BuilderWorkflowError("label retry revision conflicts with retained request")
                return {"status": "planned", "request": journal["request"], "replayed": True}
            revision = self.workflow.current_prototype_revision("scenario", object_id)
            if expected_revision and expected_revision != revision:
                raise BuilderWorkflowError("stale label request revision")
            document = _read(self._file(root, "semantic.webui.json"))
            verify_baseline(document, _read(self._file(root, "webui.json")), project_ref=f"project:{object_id}")
            if parsed["kind"] == "undo":
                retained = _read(self._file(root, f"ui_revisions/{revision}.json"))
                previous = retained.get("semantic_transaction") or {}
                if previous.get("semantic_digest") != digest(document) or not previous.get("inverse_ops"):
                    raise BuilderWorkflowError("current revision has no exact semantic label inverse")
                patch = retained.get("patch") or {}
                previous_path = operation_root / (digest([patch.get("change_id"), previous.get("transaction_id")])[7:] + ".json")
                journal = _read(previous_path)
                if journal["status"] != "committed" or patch.get("change_id") != change_id:
                    raise BuilderWorkflowError("undo requires a committed label operation in the active Change")
                if journal["result"] != previous or previous.get("revision") != revision:
                    raise BuilderWorkflowError("undo revision differs from its exact retained transaction")
                reduced = reduce_graph(document, previous["inverse_ops"], write_refs=previous["write_refs"])
                planned = {"status": "planned", "ops": previous["inverse_ops"], "write_refs": previous["write_refs"],
                           "semantic_digest": reduced["semantic_digest"]}
            else:
                planned = plan(document, parsed)
            if planned["status"] != "planned":
                return planned
            request = {"schema": SCHEMA, "project_ref": project_ref, "change_id": change_id,
                       "transaction_id": transaction_id, "source_revision": revision, "base_hash": digest(document),
                       "expected_generation": state["generation"], "write_refs": planned["write_refs"],
                       "ops": planned["ops"], "source_request": origin}
            Draft202012Validator(_contract()).validate(request)
            return {"status": "planned", "request": request, "replayed": False}

    def apply(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(request))
        try:
            Draft202012Validator(_contract()).validate(value)
        except ValidationError as exc:
            raise BuilderWorkflowError(f"invalid semantic transaction: {exc.message}") from exc
        object_id = value["project_ref"].partition(":")[2]
        root = self.workflow.project_root("scenario", object_id)
        operation_root = _operation_root(self.workflow, value["project_ref"])
        with _LOCK, mutation_lock(operation_root / ".mutation.lock"):
            projection = self.workflow.describe("scenario", object_id)
            if (projection.get("change") or {}).get("change_id") != value["change_id"] or not projection["capabilities"].get("can_edit_prototype"):
                raise BuilderWorkflowError("semantic transaction requires its active editable Change")
            self._recover(root, object_id)
            projection = self.workflow.describe("scenario", object_id)
            path = operation_root / (digest([value["change_id"], value["transaction_id"]])[7:] + ".json")
            if path.is_file():
                journal = _read(path)
                if journal.get("request_digest") != digest(value):
                    raise BuilderWorkflowError("semantic transaction identity conflicts with retained payload")
                if journal["status"] == "committed":
                    return {**journal["result"], "replayed": True}
                raise BuilderWorkflowError("aborted semantic transaction requires a new explicit identity")
            if projection["generation"] != value["expected_generation"] or self.workflow.current_prototype_revision("scenario", object_id) != value["source_revision"]:
                raise BuilderWorkflowError("stale semantic transaction base or workflow generation")
            document = _read(self._file(root, "semantic.webui.json"))
            if digest(document) != value["base_hash"]:
                raise BuilderWorkflowError("semantic transaction base hash mismatch")
            before_ui = _read(self._file(root, "webui.json"))
            # The first slice admits only the selected scenario's own namespace.
            compilation_ref = f"project:{object_id}"
            baseline = verify_baseline(document, before_ui, project_ref=compilation_ref)
            reduced = reduce_graph(document, value["ops"], write_refs=value["write_refs"])
            compiled = compile_semantic_prototype(reduced["semantic_document"], project_ref=compilation_ref,
                                                  primary_locale=baseline["primary_locale"])
            after_ui = copy.deepcopy(compiled["webui"])
            before_page = before_ui["ui"]["application"]["desktop"]["pageSchema"]
            after_page = after_ui["ui"]["application"]["desktop"]["pageSchema"]
            for key in ("process_constraints", "automation_requirements", "scenario_id"):
                if key in before_page["meta"]["builder"]:
                    after_page["meta"]["builder"][key] = copy.deepcopy(before_page["meta"]["builder"][key])
            revision_dir = root / "ui_revisions"
            known = [int(p.stem) for p in revision_dir.glob("*.json") if p.stem.isdigit()]
            revision = f"{max([int(value['source_revision']), *known]) + 1:03d}"
            if len(revision) > 6:
                raise BuilderWorkflowError("semantic revision capacity exceeded")
            after_page["meta"]["builder"].update(ui_revision=revision, proto=revision)
            if "version" in before_ui["ui"]:
                after_ui["ui"]["version"] = before_ui["ui"]["version"]
            scenario = _read(self._file(root, "scenario.json"))
            scenario["ui"] = {"manifest": "webui.json"}
            after_files = {"semantic.webui.json": _bytes(reduced["semantic_document"]),
                           "webui.json": _bytes(after_ui), "scenario.json": _bytes(scenario),
                           "ui_revisions/current.txt": (revision + "\n").encode("utf-8")}
            app_resources = copy.deepcopy(before_ui["ui"]["application"].get("resources", {}))
            for locale, messages in compiled["locale_dictionaries"].items():
                name = f"assets/i18n/{locale}.json"
                existing = _read(self._file(root, name)) if self._file(root, name).exists() else {}
                # Compiler-owned keys must match the source before a label edit.
                original = baseline["compiled"]["locale_dictionaries"][locale]
                if self._file(root, name).exists() and any(existing.get(key) != text for key, text in original.items()):
                    raise BuilderWorkflowError("semantic baseline drift: locale dictionary differs")
                after_files[name] = _bytes({**existing, **messages})
                app_resources[f"{after_page['id']}.i18n.{locale}"] = {
                    "kind": "data", "role": "i18n", "locale": locale, "path": name,
                    "mime": "application/json", "delivery": "core"}
            after_ui["ui"]["application"]["resources"] = app_resources
            after_files["webui.json"] = _bytes(after_ui)
            result = {"ok": True, "revision": revision, "revision_ref": f"ui_revision:{revision}",
                      "source_revision": value["source_revision"], "base_hash": value["base_hash"],
                      "semantic_digest": reduced["semantic_digest"], "artifact_digest": digest(after_ui),
                      "inverse_ops": reduced["inverse_ops"], "write_refs": value["write_refs"],
                      "transaction_id": value["transaction_id"], "reducer": REDUCER, "model_calls": 0}
            if "source_request" in value:
                result["source_request"] = copy.deepcopy(value["source_request"])
            after_files[f"ui_revisions/{revision}.json"] = _bytes({
                "schema": "adaos.builder.ui_revision.v1", "revision": revision, "scenario_id": object_id,
                "patch": {"operation": "semantic_graph", "id": value["transaction_id"], "change_id": value["change_id"]},
                "before_webui": before_ui, "after_webui": after_ui, "semantic_transaction": result,
                "source_map": compiled["source_map"], "requirement_runtime_map": compiled["requirement_runtime_map"]})
            before_files = {name: base64.b64encode(self._file(root, name).read_bytes()).decode("ascii")
                            if self._file(root, name).is_file() else None for name in after_files}
            journal = {"schema": JOURNAL_SCHEMA, "status": "prepared", "request": value,
                       "request_digest": digest(value), "result": result, "before_files": before_files,
                       "after_files": {name: base64.b64encode(raw).decode("ascii") for name, raw in after_files.items()}}
            if len(_bytes(journal)) > 2 * 1024 * 1024:
                raise BuilderWorkflowError("semantic transaction journal exceeds bounded capacity")
            atomic_write_json(path, journal)
            try:
                self._restore(root, journal["after_files"])
                self.workflow.transition("scenario", object_id, "prototype_revision_recorded", actor="builder.semantic_graph",
                    expected_generation=value["expected_generation"], metadata={
                        "revision": revision, "change_id": value["change_id"], "run_id": value["transaction_id"],
                        "input_refs": [f"ui_revision:{value['source_revision']}"], "output_refs": [f"ui_revision:{revision}"],
                        "evidence_refs": [f"semantic_graph_transaction:{value['transaction_id']}"],
                        "execution_metrics": {"source": "core.semantic_graph", "llm_calls": 0, "repair_attempts": 0,
                            "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                            "cost_usd": 0, "cost_basis": "zero_model",
                            "source_refs": [f"semantic_graph_transaction:{value['transaction_id']}"]}})
                journal["status"] = "committed"
                atomic_write_json(path, journal)
            except Exception:
                # Recovery uses the workflow commit point, never reruns inference.
                self._recover(root, object_id)
                raise
            return result
