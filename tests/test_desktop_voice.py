import base64
import io
import threading
import time
import uuid
import wave
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from adaos.apps.api import desktop_voice as voice
from adaos.apps.api import stt_api
from adaos.domain.personalization_access import SessionKey, SubjectRef
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore


def wav_bytes(rate=16000, channels=1, width=2, frames=16000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((channels, width, rate, frames, 'NONE', 'not compressed'))
        audio.writeframes(b'\0' * frames * width * channels)
    return output.getvalue()


def payload(**changes):
    return {'request_id': str(uuid.uuid4()), 'created_at': time.time(), 'lang': 'ru-RU',
            'audio_b64': base64.b64encode(wav_bytes()).decode(), **changes}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from adaos.services import personalization_runtime as personalization
    from adaos.services.workspaces import index
    access = PersonalizationAccessService(PersonalizationAccessStore(tmp_path / 'access.json'),
                                         owner=SubjectRef('user', 'owner'))
    monkeypatch.setattr(personalization, 'personalization_access_service', lambda: access)
    monkeypatch.setattr(personalization, 'current_user_id', lambda: 'owner')
    monkeypatch.setattr(personalization, 'current_subnet_id', lambda: 'lab-subnet')
    monkeypatch.setattr(index, 'get_workspace', lambda target: SimpleNamespace(workspace_id=target))
    monkeypatch.setenv('ADAOS_TOKEN', 'unit-test-owner')
    policy = DesktopAccessService(access)
    visitor = SubjectRef('user', 'visitor')
    policy.share(DesktopPrincipal(access.owner), 'allowed', visitor)
    access.put_session(SessionKey(session_id='lab-session', key_id='test', subject=visitor,
                                  expires_at=time.time() + 600))
    token = policy.issue_credential(DesktopPrincipal(SubjectRef('session', 'lab-session'), 'lab-session'))['access_token']
    monkeypatch.setattr(voice, '_available', lambda: True)
    calls = []

    def recognize(value):
        calls.append(value)
        return {'ok': True, 'text': 'test transcript', 'provider': 'vosk'}

    monkeypatch.setattr(stt_api, 'transcribe_audio_payload', recognize)
    with voice._lock:
        assert not voice._active
        voice._recent.clear()
    app = FastAPI()
    app.include_router(voice.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, policy=policy, access=access, calls=calls,
                              headers={'Authorization': 'Bearer ' + token},
                              path='/api/node/yjs/webspaces/allowed/voice')
    assert not voice._active


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer adaos-desktop-v1.invalid'}])
def test_missing_or_invalid_credentials_do_not_recognize(lab, headers):
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=headers)
    assert response.status_code in (401, 403)
    assert lab.calls == []


def test_scoped_capabilities_and_transcription(lab):
    capabilities = lab.client.get(lab.path + '/capabilities', headers=lab.headers)
    assert capabilities.status_code == 200 and capabilities.json()['recognition_only'] is True
    value = payload()
    response = lab.client.post(lab.path + '/transcribe', json=value, headers=lab.headers)
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'schema': 'adaos.desktop_voice.v1', 'webspace_id': 'allowed',
                               'request_id': value['request_id'], 'recognition_only': True,
                               'text': 'test transcript', 'provider': 'vosk'}
    assert response.headers['cache-control'] == 'no-store'
    assert len(lab.calls) == 1 and set(lab.calls[0]) == {'audio_b64', 'lang'}


@pytest.mark.parametrize('suffix', ['/capabilities', '/transcribe'])
def test_other_desktop_is_denied(lab, suffix):
    other = lab.path.replace('/allowed/', '/other/') + suffix
    response = (lab.client.get(other, headers=lab.headers) if suffix == '/capabilities' else
                lab.client.post(other, json=payload(), headers=lab.headers))
    assert response.status_code == 403 and not lab.calls


@pytest.mark.parametrize('changes', [
    {'actor': {'kind': 'user', 'id': 'owner'}}, {'model_id': 'untrusted'}, {'lang': 'en-US'},
    {'request_id': 'invalid'}, {'created_at': True}, {'created_at': time.time() - 120},
    {'created_at': time.time() + 120}, {'audio_b64': 'not-base64'},
    {'audio_b64': base64.b64encode(b'not a wav').decode()},
    {'audio_b64': base64.b64encode(wav_bytes(rate=8000)).decode()},
    {'audio_b64': base64.b64encode(wav_bytes(channels=2)).decode()},
    {'audio_b64': base64.b64encode(wav_bytes(width=1)).decode()},
    {'audio_b64': base64.b64encode(wav_bytes(frames=60000)).decode()},
])
def test_invalid_audio_or_authority_payload_is_rejected(lab, changes):
    response = lab.client.post(lab.path + '/transcribe', json=payload(**changes), headers=lab.headers)
    assert response.status_code in (400, 409)
    assert lab.calls == []


def test_streamed_body_limit_and_content_type(lab):
    response = lab.client.post(lab.path + '/transcribe', content=b'x' * (voice.MAX_BODY + 1),
                               headers={**lab.headers, 'Content-Type': 'application/json'})
    assert response.status_code == 413
    response = lab.client.post(lab.path + '/transcribe', content=b'{}', headers=lab.headers)
    assert response.status_code == 415 and not lab.calls


def test_duplicate_request_reuses_result_and_conflict_is_rejected(lab):
    value = payload()
    for _ in range(2):
        assert lab.client.post(lab.path + '/transcribe', json=value, headers=lab.headers).status_code == 200
    changed = {**value, 'created_at': value['created_at'] - 1}
    assert lab.client.post(lab.path + '/transcribe', json=changed, headers=lab.headers).status_code == 409
    assert len(lab.calls) == 1


def test_revocation_during_recognition_withholds_result(lab, monkeypatch):
    def recognize(value):
        lab.access.store.update_session('lab-session', {'revoked_at': time.time(), 'status': 'revoked'})
        return {'ok': True, 'text': 'withheld private result'}
    monkeypatch.setattr(stt_api, 'transcribe_audio_payload', recognize)
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers)
    assert response.status_code == 403 and 'withheld' not in response.text


def test_provider_errors_and_overlong_text_are_bounded(lab, monkeypatch):
    def fail(value):
        raise HTTPException(503, 'private model path or provider detail')
    monkeypatch.setattr(stt_api, 'transcribe_audio_payload', fail)
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers)
    assert response.status_code == 503 and 'private' not in response.text
    monkeypatch.setattr(stt_api, 'transcribe_audio_payload', lambda value: {'ok': True, 'text': 'a' * 4097})
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers)
    assert response.status_code == 502


def test_absent_model_does_not_invoke_provider_or_download(lab, monkeypatch):
    monkeypatch.setattr(voice, '_available', lambda: False)
    assert lab.client.get(lab.path + '/capabilities', headers=lab.headers).json()['available'] is False
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers)
    assert response.status_code == 503 and not lab.calls


def test_recognition_worker_retains_verified_request_context(lab, monkeypatch):
    from adaos.services.policy.caller import current_caller
    def recognize(value):
        actor = current_caller()
        assert actor is not None and actor.id == 'lab-session'
        return {'ok': True, 'text': 'context retained'}
    monkeypatch.setattr(stt_api, 'transcribe_audio_payload', recognize)
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers)
    assert response.status_code == 200
    assert response.json()['text'] == 'context retained'


def test_expired_session_is_denied_before_provider(lab):
    lab.access.store.update_session('lab-session', {'expires_at': time.time() - 1})
    response = lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers)
    assert response.status_code == 403 and not lab.calls


@pytest.mark.parametrize('path,expected', [
    ('/api/node/yjs/webspaces/lab/voice/transcribe', (1.5, 15.0)),
    ('/api/node/yjs/webspaces/lab/voice/transcribe?lang=ru', (1.5, 15.0)),
    ('/api/node/yjs/webspaces/lab/voice/capabilities', (1.5, 2.5)),
    ('/api/node/yjs/webspaces/lab/voice/transcribe/', (1.5, 2.5)),
    ('/api/node/yjs/webspaces/lab/voice/transcribe-other', (1.5, 2.5)),
    ('/api/node/status', (0.5, 1.2)),
])
def test_voice_route_budget_preserves_recognition_deadline_without_post_retry(path, expected):
    from adaos.services.bootstrap_runtime import HubRouteProxyPolicy
    assert HubRouteProxyPolicy.local_http_timeout(path) == expected
    assert HubRouteProxyPolicy.should_retry_http_upstream_error(method='POST', path=path, error_kind='ReadTimeout') is False


def test_timeout_keeps_worker_slot_until_actual_completion(lab, monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    monkeypatch.setattr(voice, 'TIMEOUT', 0.02)
    def slow(value):
        try:
            assert release.wait(5)
            return {'ok': True, 'text': 'late result'}
        finally:
            finished.set()
    monkeypatch.setattr(stt_api, 'transcribe_audio_payload', slow)
    first = payload()
    try:
        response = lab.client.post(lab.path + '/transcribe', json=first, headers=lab.headers)
        assert response.status_code == 504
        assert lab.client.post(lab.path + '/transcribe', json=payload(), headers=lab.headers).status_code == 429
        assert lab.client.post(lab.path + '/transcribe', json=first, headers=lab.headers).status_code == 409
    finally:
        release.set()
        assert finished.wait(5)
        # The provider's finally runs immediately before the wrapper releases its slot.
        deadline = time.monotonic() + 2
        while voice._active and time.monotonic() < deadline:
            time.sleep(0.005)
    assert not voice._active
    assert lab.client.post(lab.path + '/transcribe', json=first, headers=lab.headers).json()['text'] == 'late result'
