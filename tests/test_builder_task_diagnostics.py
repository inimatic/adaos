import pytest

from adaos.sdk.builder.prototype import task_admission_failure
from adaos.services.builder.task_diagnostics import interpretation_refusal_text
from adaos.services.builder.task_interpretation import TaskInterpretationRejected


@pytest.mark.parametrize('locale,expected', [('ru', 'Прототип не изменён'), ('en', 'Prototype is unchanged')])
def test_typed_refusal_has_owner_diagnostics_but_never_copies_arbitrary_exception_prose(locale, expected):
    error = TaskInterpretationRejected([{'code': 'task.fact_not_source', 'detail': 'PRIVATE-PROVIDER-DATA'},
        {'code': '<script>PRIVATE</script>', 'detail': 'PRIVATE'}])
    refusal = task_admission_failure(error, locale=locale)
    text = interpretation_refusal_text(refusal, locale=locale)
    assert expected in text and 'task.fact_not_source' in text
    assert 'PRIVATE' not in text and '<script>' not in text
    assert refusal['paid_candidate_generation'] is False and refusal['candidate_created'] is False


def test_non_interpretation_failures_keep_the_existing_router_path():
    assert interpretation_refusal_text({'error': 'provider_failed', 'detail': 'PRIVATE'}) is None
    assert task_admission_failure(ValueError('PRIVATE')) is None
