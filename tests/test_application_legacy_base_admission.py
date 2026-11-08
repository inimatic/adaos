from types import SimpleNamespace as NS

import pytest

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.applications.publication_attempts import reviewed_publication, reviewed_genesis_base_admission


@pytest.mark.parametrize('defect', [None, 'confirmation', 'reviewed_base', 'current_installation',
    'candidate_identity', 'publisher', 'unaccepted', 'unconfirmed_status', 'tampered_plan', 'real_base', 'no_review'])
def test_only_durable_exact_review_can_supply_a_legacy_genesis_baseline(tmp_path, monkeypatch, defect):
    from adaos.services.applications import store as module
    digest = 'sha256:' + 'b' * 64
    candidate = NS(project_id='example', candidate_id='candidate-example', digest='sha256:' + 'c' * 64,
        release_digest='sha256:' + 'd' * 64, base_release_digest='sha256:' + '0' * 64,
        source_ref=NS(forge='content-addressed-dev'), verification_source_ref=None,
        status='accepted', trials=[NS(status='accepted', candidate_digest='sha256:' + 'c' * 64)])
    stable = NS(release_digest=digest, source_ref=NS(revision='stable-source'))
    plan = dict(action='promote_stable', candidate_id=candidate.candidate_id, candidate_digest=candidate.digest,
        release_digest=candidate.release_digest, stable_release_digest=digest)
    op = NS(operation_id='core-finalize', application_id='example', actor_ref='user:owner',
        subnet_ref='subnet:home', status='reconciling', plan=plan, plan_digest=canonical_payload_digest(plan))
    op.result = {'confirmation':dict(confirmed=True, plan_digest=op.plan_digest, actor_ref=op.actor_ref, subnet_ref=op.subnet_ref)}
    app = NS(publisher_ref=op.subnet_ref, legacy_project_id='example')
    installation = NS(status='active', installed_release_digest=digest)
    if defect == 'confirmation': op.result = {}
    if defect == 'reviewed_base':
        plan['stable_release_digest'] = 'sha256:' + 'e' * 64
        op.plan_digest = canonical_payload_digest(plan)
        op.result['confirmation']['plan_digest'] = op.plan_digest
    if defect == 'current_installation': installation.installed_release_digest = 'sha256:' + 'e' * 64
    if defect == 'candidate_identity': candidate.digest = 'sha256:' + 'e' * 64
    if defect == 'publisher': app.publisher_ref = 'subnet:another'
    if defect == 'unaccepted': candidate.trials = []
    if defect == 'unconfirmed_status': op.status = 'planned'
    if defect == 'tampered_plan': plan['injected'] = True
    if defect == 'real_base': candidate.base_release_digest = 'sha256:' + 'f' * 64
    fake = NS(get_operation=lambda _:op, get_application=lambda _:app, get_installation=lambda _:installation)
    monkeypatch.setattr(module, 'ApplicationStore', lambda _:fake)
    if defect == 'no_review': result = reviewed_genesis_base_admission(tmp_path, candidate, stable)
    else:
        with reviewed_publication({**vars(op), 'action':'promote_stable'}):
            result = reviewed_genesis_base_admission(tmp_path, candidate, stable)
    if defect is None:
        assert result['candidate_digest'] == candidate.digest
        assert result['reviewed_base_digest'] == stable.release_digest
        assert result['original_base_digest'] == candidate.base_release_digest
    else:
        assert result is None
