from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from adaos.services.context_control import ContextConflict, ContextControlService


def _path(service: ContextControlService, artifact: dict) -> Path:
    return service.artifact_root / (artifact['digest'].removeprefix('sha256:') + '.json')


@pytest.mark.parametrize('replacement', [b'{"approved":true}', b'{broken json', b'\xff'])
def test_context_artifact_rejects_changed_bytes_before_consuming(
    tmp_path: Path, replacement: bytes,
) -> None:
    service = ContextControlService(tmp_path)
    artifact = service.put_artifact({'approved': False})
    path = _path(service, artifact)
    path.write_bytes(replacement)

    with pytest.raises(ContextConflict, match='digest mismatch'):
        service.get_artifact(artifact['ref'])

    assert path.read_bytes() == replacement


def test_context_artifact_put_cannot_acknowledge_corrupt_existing_object(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    value = {'run': 'retained', 'result': 'checked'}
    artifact = service.put_artifact(value)
    path = _path(service, artifact)
    path.write_bytes(b'{"run":"other"}')

    with pytest.raises(ContextConflict, match='digest mismatch'):
        service.put_artifact(value)

    assert path.read_bytes() == b'{"run":"other"}'


def test_context_artifact_unicode_roundtrip_and_idempotency_survive_restart(tmp_path: Path) -> None:
    value = {'message': '\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e \U0001f4f7', 'lines': 'one\ntwo', 'facts': [False, None, 0]}
    service = ContextControlService(tmp_path)
    artifact = service.put_artifact(value)
    original = _path(service, artifact).read_bytes()
    restarted = ContextControlService(tmp_path)

    assert restarted.get_artifact(artifact['ref']) == value
    assert restarted.put_artifact(value) == artifact
    assert artifact['bytes'] == len(original)
    assert original.decode('utf-8') == json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def test_context_artifact_whitespace_change_requires_new_content_identity(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    artifact = service.put_artifact({'approved': False})
    _path(service, artifact).write_bytes(b'{ "approved": false }')

    with pytest.raises(ContextConflict, match='digest mismatch'):
        service.get_artifact(artifact['ref'])


def test_context_artifact_missing_is_unavailable_not_empty(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    artifact = service.put_artifact({'input': 'retained'})
    _path(service, artifact).unlink()

    with pytest.raises(KeyError, match='unavailable'):
        service.get_artifact(artifact['ref'])


def test_context_artifact_write_failure_is_not_acknowledged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services.artifact_pipeline import storage

    service = ContextControlService(tmp_path)

    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(storage, 'replace_with_retry', disk_full)
    with pytest.raises(OSError, match='No space left'):
        service.put_artifact({'output': 'not committed'})

    assert list(service.artifact_root.iterdir()) == []


def test_context_compile_refuses_modified_pinned_input(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    capsule = service.register_capsule({
        'kind': 'project', 'subject_refs': ['project:integrity'],
        'authority_ref': 'project:integrity', 'content': {'instruction': 'preserve data'},
    }, bind=True)
    resolution = service.resolve({'subject_refs': ['project:integrity']})
    plan = service.plan({'resolution': resolution, 'token_budget': 1000})
    path = service.artifact_root / (capsule['artifact_ref'].rsplit('/', 1)[-1] + '.json')
    original = json.loads(path.read_text(encoding='utf-8'))
    original['content']['instruction'] = 'replace data'
    path.write_text(json.dumps(original), encoding='utf-8')

    with pytest.raises(ContextConflict, match='digest mismatch'):
        service.compile({'plan_id': plan['plan_id'], 'output_format': 'min_json'})


def test_context_api_reports_artifact_conflict_without_exposing_content(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from adaos.apps.api import agent_context_control as context_api

    service = ContextControlService(tmp_path)
    capsule = service.register_capsule({
        'kind': 'project', 'subject_refs': ['project:integrity'],
        'authority_ref': 'project:integrity', 'content': {'approval': False},
    })
    path = service.artifact_root / (capsule['artifact_ref'].rsplit('/', 1)[-1] + '.json')
    path.write_bytes(b'{"private":"do-not-return-this-content"}')
    app = FastAPI()
    app.include_router(context_api.router, prefix='/api/context')
    app.dependency_overrides[context_api._get_service] = lambda: service
    app.dependency_overrides[context_api.require_token] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(f"/api/context/capsules/{capsule['capsule_id']}?include_content=true")

    assert response.status_code == 409
    assert 'digest mismatch' in response.json()['detail']
    assert 'do-not-return-this-content' not in response.text


@pytest.mark.parametrize('view', ['plan', 'receipts', 'inspection'])
def test_context_api_does_not_hide_corrupt_plan_or_run_receipt(tmp_path: Path, view: str) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from adaos.apps.api import agent_context_control as context_api

    service = ContextControlService(tmp_path)
    service.register_capsule({
        'kind': 'project', 'subject_refs': ['project:integrity'],
        'authority_ref': 'project:integrity', 'content': {'instruction': 'preserve data'},
    }, bind=True)
    resolution = service.resolve({'subject_refs': ['project:integrity']})
    plan = service.plan({'resolution': resolution, 'token_budget': 1000})
    receipt = service.record_receipt({'run_ref': 'run:integrity', 'plan_ref': plan['plan_ref']})
    artifact_ref = plan['plan_ref'] if view == 'plan' else receipt['receipt_ref']
    path = service.artifact_root / (artifact_ref.rsplit('/', 1)[-1] + '.json')
    path.write_bytes(b'{"private":"do-not-return-this-content"}')
    app = FastAPI()
    app.include_router(context_api.router, prefix='/api/context')
    app.dependency_overrides[context_api._get_service] = lambda: service
    app.dependency_overrides[context_api.require_token] = lambda: None
    client = TestClient(app, raise_server_exceptions=False)
    route = {'plan': f"/api/context/plans/{plan['plan_id']}",
             'receipts': '/api/context/receipts?run_ref=run:integrity',
             'inspection': '/api/context/inspect/run:integrity'}[view]

    response = client.get(route)

    assert response.status_code == 409
    assert 'digest mismatch' in response.json()['detail']
    assert 'do-not-return-this-content' not in response.text
