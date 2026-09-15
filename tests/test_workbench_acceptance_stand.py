import copy
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "workbench_acceptance", Path(__file__).parents[1] / "e2e/stand/accept-workbench-test-prototype.py")
stand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stand)


def evidence(field):
    webui = {"ui": {"application": {"modals": {"create": {"schema": {"widgets": [{
        "type": "ui.form", "inputs": {"fields": [
            {"id": field, "type": "shortText"}, {"id": "author", "type": "shortText"}]},
        "actions": [{"type": "resourceOperation", "target": "prototype.books", "params": {"operation_id": "create"}}],
    }]}}}}}}
    tasks = [f"search/{field}", "search/author", "search/empty-result", "choice-filter/change",
             "create/required-field-rejection/no-mutation", "create/cancel/no-mutation/clear-draft",
             "update/required-field-rejection/no-mutation",
             "create/optional-fields-empty/select-created-record", "delete/confirmation-cancel/no-mutation",
             "delete/confirmation-accept/collection-refresh"]
    checks = [{"status": "passed", "task": task, "resource": "prototype.books"} for task in tasks]
    checks.append({"status": "passed", "task": "select/edit/save/reopen", "field": field, "resource": "prototype.books"})
    return webui, [{"layout": layout, "checks": copy.deepcopy(checks)} for layout in ("wide", "compact")]


@pytest.mark.parametrize("field", ["title", "books.title"])
def test_literal_field_identity_is_preserved(field):
    webui, samples = evidence(field)
    assert stand.crud_coverage(webui, samples)


@pytest.mark.parametrize("defect", ["other_resource", "other_editor", "wrong_field", "missing_task", "missing_width", "failed_task"])
def test_unrelated_or_incomplete_evidence_cannot_satisfy_acceptance(defect):
    webui, samples = evidence("books.title")
    if defect == "other_resource":
        samples[0]["checks"][0]["resource"] = "prototype.other"
    elif defect == "other_editor":
        samples[0]["checks"][-1]["resource"] = "prototype.settings"
    elif defect == "wrong_field":
        samples[0]["checks"][-1]["field"] = "preferred_view"
    elif defect == "missing_task":
        samples[0]["checks"].pop(0)
    elif defect == "missing_width":
        samples.pop()
    else:
        samples[0]["checks"][0]["status"] = "failed"
    assert not stand.crud_coverage(webui, samples)
