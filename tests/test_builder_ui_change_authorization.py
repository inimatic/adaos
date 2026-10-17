from copy import deepcopy
import pytest
from adaos.services.builder.ui_change_authorization import admit_ui_change, authorized_ui_change


def test_ui_authorization_is_explicit_and_bound_to_the_exact_project_revision():
    value = admit_ui_change({'reason':'Organize the existing workspace', 'source_message_ids':['message:1']},
        project_id='retouch', prototype_revision='008')
    constraints={'ui_change_authorization':value}
    assert authorized_ui_change(constraints, project_id='retouch', prototype_revision='008') == value
    assert authorized_ui_change(constraints, project_id='other', prototype_revision='008') is None
    assert authorized_ui_change(constraints, project_id='retouch', prototype_revision='009') is None
    tampered=deepcopy(constraints);tampered['ui_change_authorization']['allowed_paths'].append('skills/other/handlers/main.py')
    assert authorized_ui_change(tampered, project_id='retouch', prototype_revision='008') is None
    assert authorized_ui_change({'allow_semantic_ui_delta':True}, project_id='retouch', prototype_revision='008') is None


@pytest.mark.parametrize('value',[{}, {'reason':'change','source_message_ids':[]},
    {'reason':'change','source_message_ids':['message:1'],'authority':'forged'},
    {'reason':'change','source_message_ids':'not-a-list'}])
def test_incomplete_or_forged_ui_authorization_is_not_admitted(value):
    with pytest.raises(ValueError):
        admit_ui_change(value, project_id='retouch', prototype_revision='008')
