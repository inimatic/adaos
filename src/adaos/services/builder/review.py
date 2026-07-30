"""Typed Review acceptance constraints for Builder UI revisions."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .semantic_ui import _read_json, _resolve_target
from .workflow import BuilderWorkflowError, BuilderWorkflowService, _LOCK, _now


BUILDER_ACCEPTANCE_CONSTRAINT_SCHEMA = "adaos.builder.acceptance_constraint.v1"
BUILDER_REVIEW_ANCHOR_SCHEMA = "adaos.builder.review_anchor.v1"
_SUPPORTED_KINDS = {
    "present",
    "label_equals",
    "property_equals",
    "visible",
    "order_before",
    "data_mode",
}


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return _read_json(path, label=name)


def _constraint_id(review_id: str, kind: str, target_ref: str) -> str:
    raw = f"{review_id}\0{kind}\0{target_ref}".encode("utf-8")
    return f"constraint.{hashlib.sha256(raw).hexdigest()[:24]}"


def _normalize_expected(kind: str, expected: Any) -> Any:
    if kind in {"present", "visible"}:
        if not isinstance(expected, bool):
            raise BuilderWorkflowError(f"Review constraint {kind} expected value must be boolean")
        return expected
    if kind in {"label_equals", "data_mode"}:
        token = " ".join(str(expected or "").split())
        if not token or len(token) > 300:
            raise BuilderWorkflowError(f"Review constraint {kind} expected value is required")
        return token
    if kind == "property_equals":
        if not isinstance(expected, Mapping):
            raise BuilderWorkflowError("property_equals expected value must be an object")
        property_name = str(expected.get("property") or "").strip()
        if not property_name or len(property_name) > 120:
            raise BuilderWorkflowError("property_equals requires a bounded property name")
        return {"property": property_name, "value": copy.deepcopy(expected.get("value"))}
    if kind == "order_before":
        other = str(
            expected.get("target_ref") if isinstance(expected, Mapping) else expected
        ).strip()
        if not other.startswith(("widget:", "field:")) or len(other) > 300:
            raise BuilderWorkflowError("order_before requires another widget/field target_ref")
        return {"target_ref": other}
    raise BuilderWorkflowError(f"unsupported Review constraint kind: {kind}")


def compile_constraint(
    review: Mapping[str, Any],
    *,
    kind: str,
    expected: Any,
    source_revision: str,
) -> dict[str, Any]:
    """Compile structured Review intent without reinterpreting narrative text."""

    anchor = copy.deepcopy(dict(review))
    anchor.setdefault("schema", BUILDER_REVIEW_ANCHOR_SCHEMA)
    Draft202012Validator(_schema("builder.review_anchor.v1.schema.json")).validate(anchor)
    kind_token = str(kind or "").strip().lower()
    if kind_token not in _SUPPORTED_KINDS:
        raise BuilderWorkflowError(f"unsupported Review constraint kind: {kind_token}")
    target_ref = str(anchor.get("target_ref") or "").strip()
    if not target_ref.startswith(("widget:", "field:")):
        raise BuilderWorkflowError("typed Review constraints currently require a widget or field target")
    revision = str(source_revision or "").strip()
    if not revision or len(revision) > 80:
        raise BuilderWorkflowError("Review constraint source_revision is required")
    constraint = {
        "schema": BUILDER_ACCEPTANCE_CONSTRAINT_SCHEMA,
        "constraint_id": _constraint_id(str(anchor["review_id"]), kind_token, target_ref),
        "change_id": str(anchor["change_id"]),
        "review_id": str(anchor["review_id"]),
        "project_ref": str(anchor["artifact_ref"]).split("@", 1)[0],
        "artifact_ref": str(anchor["artifact_ref"]),
        "target_ref": target_ref,
        "kind": kind_token,
        "expected": _normalize_expected(kind_token, expected),
        "source_revision": revision,
        "status": "active",
        "last_evaluation": None,
        "created_at": _now(),
        "updated_at": None,
    }
    if not constraint["project_ref"].startswith("scenario:"):
        raise BuilderWorkflowError("typed Review constraints currently support scenarios only")
    Draft202012Validator(_schema("builder.acceptance_constraint.v1.schema.json")).validate(constraint)
    return constraint


def _find_common_order(value: Any, left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[int, int] | None:
    if isinstance(value, list):
        left_index = next((index for index, item in enumerate(value) if item is left), None)
        right_index = next((index for index, item in enumerate(value) if item is right), None)
        if left_index is not None and right_index is not None:
            return left_index, right_index
        for item in value:
            result = _find_common_order(item, left, right)
            if result is not None:
                return result
    elif isinstance(value, Mapping):
        for item in value.values():
            result = _find_common_order(item, left, right)
            if result is not None:
                return result
    return None


def evaluate_constraint(
    constraint: Mapping[str, Any],
    webui: Mapping[str, Any],
    *,
    revision: str,
) -> dict[str, Any]:
    value = copy.deepcopy(dict(constraint))
    Draft202012Validator(_schema("builder.acceptance_constraint.v1.schema.json")).validate(value)
    target_ref = str(value["target_ref"])
    kind = str(value["kind"])
    expected = value.get("expected")
    target: dict[str, Any] | None = None
    target_kind = ""
    resolution_error = ""
    try:
        target, target_kind = _resolve_target(webui, target_ref)
    except BuilderWorkflowError as exc:
        resolution_error = str(exc)

    actual: Any = None
    verifiable = True
    if kind == "present":
        actual = target is not None
    elif target is None:
        verifiable = False
    elif kind == "label_equals":
        property_name = "title" if target_kind == "widget" else "label"
        actual = target.get(property_name)
    elif kind == "property_equals":
        property_name = str(expected.get("property") or "") if isinstance(expected, Mapping) else ""
        actual = target.get(property_name)
        expected = expected.get("value") if isinstance(expected, Mapping) else None
    elif kind == "visible":
        if "hidden" in target:
            actual = not bool(target.get("hidden"))
        elif str(target.get("visibleIf") or "").strip().lower() in {"false", "0"}:
            actual = False
        else:
            actual = True
    elif kind == "data_mode":
        source = target.get("dataSource") if isinstance(target.get("dataSource"), Mapping) else {}
        actual = str(source.get("kind") or "").strip()
    elif kind == "order_before":
        other_ref = str(expected.get("target_ref") or "") if isinstance(expected, Mapping) else ""
        try:
            other, _ = _resolve_target(webui, other_ref)
        except BuilderWorkflowError as exc:
            other = None
            resolution_error = str(exc)
        positions = _find_common_order(webui, target, other) if other is not None else None
        if positions is None:
            verifiable = False
        else:
            actual = positions[0] < positions[1]
            expected = True
    else:
        verifiable = False

    passed = bool(verifiable and actual == expected)
    status = "satisfied" if passed else ("violated" if verifiable else "unverifiable")
    return {
        "constraint_id": str(value["constraint_id"]),
        "review_id": str(value["review_id"]),
        "revision": str(revision),
        "status": status,
        "passed": passed,
        "expected": copy.deepcopy(expected),
        "actual": copy.deepcopy(actual),
        "detail": resolution_error or None,
        "evaluated_at": _now(),
    }


@dataclass(slots=True)
class BuilderReviewService:
    workflow: BuilderWorkflowService

    @classmethod
    def from_context(cls) -> "BuilderReviewService":
        return cls(workflow=BuilderWorkflowService.from_context())

    @staticmethod
    def _normalized_review(value: Mapping[str, Any], *, status: str | None = None) -> dict[str, Any]:
        review = copy.deepcopy(dict(value))
        review.setdefault("schema", BUILDER_REVIEW_ANCHOR_SCHEMA)
        if status:
            review["status"] = status
        review.setdefault("issue_id", None)
        review.setdefault("constraint_ref", None)
        review.setdefault("resolution_ref", None)
        review.setdefault("disposition_reason", None)
        review.setdefault("superseded_by_ref", None)
        review.setdefault("tombstone", False)
        review.setdefault("updated_at", None)
        Draft202012Validator(_schema("builder.review_anchor.v1.schema.json")).validate(review)
        return review

    def _persist_review(
        self,
        object_type: str,
        object_id: str,
        review: Mapping[str, Any],
        *,
        expected_generation: int | None = None,
        create_only: bool = False,
    ) -> dict[str, Any]:
        kind = str(object_type or "").strip().lower()
        project_id = str(object_id or "").strip()
        normalized = self._normalized_review(review)
        with _LOCK:
            state = self.workflow._read_state(kind, project_id)
            workflow = self.workflow._normalized_workflow(
                state, object_type=kind, object_id=project_id
            )
            if expected_generation is not None and int(workflow.get("generation") or 0) != int(
                expected_generation
            ):
                raise BuilderWorkflowError("stale Builder action generation")
            change = workflow.get("change") if isinstance(workflow.get("change"), Mapping) else {}
            if str(change.get("change_id") or "") != str(normalized["change_id"]):
                raise BuilderWorkflowError("Review does not match the focused Change")
            reviews = [
                dict(item) for item in workflow.get("reviews") or [] if isinstance(item, Mapping)
            ]
            existing = next(
                (item for item in reviews if item.get("review_id") == normalized["review_id"]),
                None,
            )
            if create_only and existing is not None:
                raise BuilderWorkflowError(f"Review already exists: {normalized['review_id']}")
            if existing is None:
                reviews.append(normalized)
            else:
                existing.clear()
                existing.update(normalized)
            workflow["reviews"] = reviews[-200:]
            workflow["generation"] = int(workflow.get("generation") or 0) + 1
            workflow["updated_at"] = _now()
            state["workflow"] = workflow
            state["updated_at"] = workflow["updated_at"]
            self.workflow._write_state(kind, project_id, state)
        return self.workflow.describe(kind, project_id)

    def submit(
        self,
        review: Mapping[str, Any],
        *,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        submitted = self._normalized_review(
            {
                **dict(review),
                "status": "submitted",
                "tombstone": False,
                "updated_at": _now(),
            }
        )
        artifact_ref = str(submitted["artifact_ref"])
        project_ref = artifact_ref.split("@", 1)[0]
        if ":" not in project_ref:
            raise BuilderWorkflowError("Review artifact_ref must include a project ref")
        object_type, object_id = project_ref.split(":", 1)
        workflow = self._persist_review(
            object_type,
            object_id,
            submitted,
            expected_generation=expected_generation,
            create_only=True,
        )
        return {"ok": True, "review": submitted, "workflow": workflow}

    def _find(self, object_type: str, object_id: str, review_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        workflow = self.workflow.describe(object_type, object_id)
        review = next(
            (
                dict(item)
                for item in workflow.get("reviews") or []
                if item.get("review_id") == review_id
            ),
            None,
        )
        if review is None:
            raise BuilderWorkflowError(f"unknown Review: {review_id}")
        return workflow, review

    def withdraw(
        self,
        object_type: str,
        object_id: str,
        review_id: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        workflow, review = self._find(object_type, object_id, review_id)
        if review.get("status") != "submitted":
            raise BuilderWorkflowError("only a submitted Review can be withdrawn")
        review.update(
            {
                "status": "withdrawn",
                "comment": "[withdrawn]",
                "target_ref": review["target_ref"],
                "disposition_reason": str(reason or "").strip() or None,
                "tombstone": True,
                "updated_at": _now(),
            }
        )
        updated = self._persist_review(object_type, object_id, review)
        return {"ok": True, "review": review, "workflow": updated}

    def dismiss(
        self,
        object_type: str,
        object_id: str,
        review_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        _workflow, review = self._find(object_type, object_id, review_id)
        if review.get("status") != "submitted":
            raise BuilderWorkflowError("only a submitted Review can be dismissed")
        reason_token = " ".join(str(reason or "").split())
        if not reason_token:
            raise BuilderWorkflowError("dismissing a Review requires a reason")
        review.update(
            {"status": "dismissed", "disposition_reason": reason_token, "updated_at": _now()}
        )
        updated = self._persist_review(object_type, object_id, review)
        return {"ok": True, "review": review, "workflow": updated}

    def accept_as_constraint(
        self,
        object_type: str,
        object_id: str,
        review_id: str,
        *,
        kind: str,
        expected: Any,
        source_revision: str,
    ) -> dict[str, Any]:
        workflow, review = self._find(object_type, object_id, review_id)
        if review.get("status") != "submitted":
            raise BuilderWorkflowError("only a submitted Review can become a constraint")
        constraint = compile_constraint(
            review,
            kind=kind,
            expected=expected,
            source_revision=source_revision,
        )
        transition = self.workflow.transition(
            object_type,
            object_id,
            "review_constraint_added",
            actor="builder.review",
            metadata={
                "change_id": review["change_id"],
                "constraint": constraint,
                "input_refs": [f"review:{review_id}"],
                "output_refs": [f"acceptance_constraint:{constraint['constraint_id']}"],
            },
            expected_generation=int(workflow.get("generation") or 0),
        )
        review.update(
            {
                "status": "accepted_as_constraint",
                "constraint_ref": f"acceptance_constraint:{constraint['constraint_id']}",
                "updated_at": _now(),
            }
        )
        updated = self._persist_review(object_type, object_id, review)
        return {"ok": True, "review": review, "constraint": constraint, "workflow": updated}

    def convert_to_issue(
        self,
        object_type: str,
        object_id: str,
        review_id: str,
        *,
        issue: Mapping[str, Any],
    ) -> dict[str, Any]:
        workflow, review = self._find(object_type, object_id, review_id)
        if review.get("status") != "submitted":
            raise BuilderWorkflowError("only a submitted Review can become an Issue")
        issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
        transition = self.workflow.transition(
            object_type,
            object_id,
            "change_issues_added",
            actor="builder.review",
            metadata={
                "change_set_id": review["change_id"],
                "request": review["comment"],
                "issues": [dict(issue)],
                "source_message_ids": [f"review:{review_id}"],
            },
            expected_generation=int(workflow.get("generation") or 0),
        )
        review.update(
            {
                "status": "converted_to_issue",
                "issue_id": issue_id,
                "updated_at": _now(),
            }
        )
        updated = self._persist_review(object_type, object_id, review)
        return {"ok": True, "review": review, "workflow": updated, "issue": dict(issue)}

    def supersede(
        self,
        object_type: str,
        object_id: str,
        review_id: str,
        *,
        reason: str,
        superseded_by_ref: str | None = None,
        waiver: bool = False,
    ) -> dict[str, Any]:
        workflow, review = self._find(object_type, object_id, review_id)
        if review.get("status") not in {"accepted_as_constraint", "converted_to_issue"}:
            raise BuilderWorkflowError("only an adopted Review can be superseded")
        reason_token = " ".join(str(reason or "").split())
        replacement = str(superseded_by_ref or "").strip() or None
        if not reason_token or (replacement is None and not waiver):
            raise BuilderWorkflowError("superseding a Review requires a reason and replacement or waiver")
        if review.get("constraint_ref"):
            constraint_id = str(review["constraint_ref"]).split(":", 1)[-1]
            transition = self.workflow.transition(
                object_type,
                object_id,
                "review_constraint_superseded",
                actor="builder.review",
                metadata={
                    "change_id": review["change_id"],
                    "constraint_id": constraint_id,
                    "reason": reason_token,
                    "superseded_by_ref": replacement,
                },
                expected_generation=int(workflow.get("generation") or 0),
            )
        review.update(
            {
                "status": "superseded",
                "disposition_reason": reason_token,
                "superseded_by_ref": replacement,
                "updated_at": _now(),
            }
        )
        updated = self._persist_review(object_type, object_id, review)
        return {"ok": True, "review": review, "workflow": updated}

    def resolve(
        self,
        object_type: str,
        object_id: str,
        review_id: str,
        *,
        resolution_ref: str,
    ) -> dict[str, Any]:
        _workflow, review = self._find(object_type, object_id, review_id)
        if review.get("status") not in {"accepted_as_constraint", "converted_to_issue"}:
            raise BuilderWorkflowError("only an adopted Review can be resolved")
        ref = str(resolution_ref or "").strip()
        if not ref:
            raise BuilderWorkflowError("resolving a Review requires resolution_ref")
        review.update({"status": "resolved", "resolution_ref": ref, "updated_at": _now()})
        updated = self._persist_review(object_type, object_id, review)
        return {"ok": True, "review": review, "workflow": updated}

    def delete(self, *_args: Any, **_kwargs: Any) -> None:
        raise BuilderWorkflowError(
            "submitted Reviews cannot be deleted; withdraw, dismiss, resolve, or supersede them"
        )

    def register_constraint(
        self,
        review: Mapping[str, Any],
        *,
        kind: str,
        expected: Any,
        source_revision: str,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        constraint = compile_constraint(
            review,
            kind=kind,
            expected=expected,
            source_revision=source_revision,
        )
        object_type, object_id = str(constraint["project_ref"]).split(":", 1)
        current = self.workflow.describe(object_type, object_id)
        change = current.get("change") if isinstance(current.get("change"), Mapping) else {}
        if str(change.get("change_id") or "") != str(constraint["change_id"]):
            raise BuilderWorkflowError("Review constraint does not match the active Change")
        generation = int(current.get("generation") or 0) if expected_generation is None else expected_generation
        transition = self.workflow.transition(
            object_type,
            object_id,
            "review_constraint_added",
            actor="builder.review",
            metadata={
                "change_id": constraint["change_id"],
                "constraint": constraint,
                "input_refs": [f"review:{constraint['review_id']}", constraint["artifact_ref"]],
                "output_refs": [f"acceptance_constraint:{constraint['constraint_id']}"],
            },
            expected_generation=generation,
        )
        return {"ok": True, "constraint": constraint, "workflow": transition["workflow"]}

    def evaluate_current(
        self,
        object_type: str,
        object_id: str,
        *,
        revision: str | None = None,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        current = self.workflow.describe(object_type, object_id)
        change = current.get("change") if isinstance(current.get("change"), Mapping) else {}
        constraints = [
            item
            for item in change.get("acceptance_constraints") or []
            if isinstance(item, Mapping) and str(item.get("status") or "") != "superseded"
        ]
        revision_token = str(revision or self.workflow.current_prototype_revision(object_type, object_id) or "").strip()
        if not revision_token:
            raise BuilderWorkflowError("Review constraint evaluation requires a Prototype revision")
        if not constraints:
            return {"ok": True, "revision": revision_token, "evaluations": [], "workflow": current}
        root = self.workflow.project_root(object_type, object_id)
        webui = _read_json(root / "webui.json", label="webui.json")
        evaluations = [
            evaluate_constraint(item, webui, revision=revision_token)
            for item in constraints
        ]
        generation = int(current.get("generation") or 0) if expected_generation is None else expected_generation
        transition = self.workflow.transition(
            object_type,
            object_id,
            "review_constraints_evaluated",
            actor="builder.review.evaluator",
            metadata={
                "change_id": change.get("change_id"),
                "revision": revision_token,
                "evaluations": evaluations,
                "input_refs": [f"ui_revision:{revision_token}"],
                "evidence_refs": [f"acceptance_evaluation:{item['constraint_id']}:{revision_token}" for item in evaluations],
            },
            expected_generation=generation,
        )
        return {
            "ok": True,
            "revision": revision_token,
            "evaluations": evaluations,
            "workflow": transition["workflow"],
        }


__all__ = [
    "BUILDER_ACCEPTANCE_CONSTRAINT_SCHEMA",
    "BUILDER_REVIEW_ANCHOR_SCHEMA",
    "BuilderReviewService",
    "compile_constraint",
    "evaluate_constraint",
]
