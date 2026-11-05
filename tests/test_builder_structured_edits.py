import pytest
from adaos.services.builder.structured_edits import validate_structured_edit_set, json_pointer_tokens


@pytest.mark.parametrize("change,error", [
    ({"pointer": ""}, "non-root"), ({"pointer": "/bad~2escape"}, "pointer escape"),
    ({"path": "../outside.json"}, "relative source path"), ({"path": "D:/outside.json"}, "relative source path"),
    ({"path": "scenarios/other/webui.json"}, "outside exact_changed_paths"),
    ({"op": "replace_file"}, "unsupported"),
])
def test_edit_admission_rejects_bad_shape_before_any_source_access(change, error):
    edit = {"op": "json_replace", "path": "scenarios/demo/webui.json", "pointer": "/ui", "expected": None, "value": {}}
    with pytest.raises(ValueError, match=error):
        validate_structured_edit_set({"schema": "adaos.builder.structured_edit_set.v1", "operations": [{**edit, **change}]},
                                     allowed_paths=["scenarios/demo/webui.json"])


def test_null_precondition_is_explicit_and_rfc_escapes_are_preserved():
    edit = {"op": "json_replace", "path": "webui.json", "pointer": "/a~1b/~0key", "expected": None, "value": {}}
    value = {"schema": "adaos.builder.structured_edit_set.v1", "operations": [edit]}
    assert validate_structured_edit_set(value, allowed_paths=["webui.json"]) == value
    assert json_pointer_tokens(edit["pointer"]) == ["a/b", "~key"]
    del edit["expected"]
    with pytest.raises(ValueError, match="requires"):
        validate_structured_edit_set(value, allowed_paths=["webui.json"])
