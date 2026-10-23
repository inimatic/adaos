"""Core provider regression tests extracted with the Context Chat transport."""
from __future__ import annotations

import base64
import json
import threading
import time
import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from adaos.services.ai.chatgpt import API, AUTH, PLAN_SCOPE, AccountError, ChatGPTAccount, KeyringVault, validate_id_token
from adaos.services.ai.store import AIStore as Store


class Vault:
    def __init__(self, data=None): self.data = data
    def read(self): return dict(self.data) if self.data else None
    def write(self, data): self.data = dict(data)
    def clear(self): self.data = None


def test_web_search_is_opt_in_and_persists_safe_citations(tmp_path):
    seen, citations = [], []
    output = 'Fresh fact [1]'
    def handler(request):
        seen.append(json.loads(request.content))
        annotations = [{'type':'url_citation', 'start_index':11,'end_index':14,'url':'https://example.org/source','title':'Source'}, {'type':'url_citation','start_index':0,'end_index':5,'url':'javascript:alert(1)','title':'Unsafe'}]
        events = [{'type':'response.output_text.delta','delta':output}, {'type':'response.completed','response':{'output':[{'type':'message','content':[{'type':'output_text','text':output,'annotations':annotations}]}]}}]
        return httpx.Response(200, text='\n\n'.join('data: '+json.dumps(e) for e in events)+'\n\n')
    store = Store(tmp_path)
    account = ChatGPTAccount(store, Vault({'access_token':'fixture','expires_at':time.time()+3600,'scope':PLAN_SCOPE}), httpx.Client(transport=httpx.MockTransport(handler)))
    assert ''.join(account.stream('fixture', [], '', threading.Event())) == output
    assert 'tools' not in seen[0]
    assert ''.join(account.stream('fixture', [], '', threading.Event(), web_search=True, on_citations=citations.extend)) == output
    assert seen[1]['tools'] == [{'type':'web_search'}] and seen[1]['store'] is False
    assert len(citations) == 1 and citations[0]['url'] == 'https://example.org/source'


def test_web_search_rejection_has_actionable_error_and_does_not_retry(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={'error':{'message':'unsupported tool'}})
    account = ChatGPTAccount(Store(tmp_path), Vault({'access_token':'fixture','expires_at':time.time()+3600,'scope':PLAN_SCOPE}), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AccountError, match='выключите поиск'):
        list(account.stream('fixture', [], '', threading.Event(), web_search=True))
    assert len(requests) == 1


def test_oauth_pkce_and_invalid_state(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    account=ChatGPTAccount(Store(tmp_path),Vault())
    result=account.start(); values=parse_qs(urlsplit(result['url']).query)
    assert values['client_id']==['dynamic_agent_client']
    assert values['code_challenge_method']==['S256']
    assert PLAN_SCOPE in values['scope'][0]
    response=httpx.get(values['redirect_uri'][0]+'?state=invalid&code=ignored',trust_env=False)
    assert response.status_code==400 and account.pending is not None
    account.pending=None


def test_refresh_rotation_and_redacted_status(tmp_path):
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'access_token':'rotated-access','refresh_token':'rotated-refresh','expires_in':3600,'scope':PLAN_SCOPE})
    vault=Vault({'access_token':'old-access','refresh_token':'old-refresh','client_id':'issued','scope':PLAN_SCOPE,'expires_at':0,'email':'test@example.invalid'})
    account=ChatGPTAccount(Store(tmp_path),vault,httpx.Client(transport=httpx.MockTransport(handler)))
    assert account.access_token()=='rotated-access'
    assert vault.read()['refresh_token']=='rotated-refresh'
    body=requests[0].content.decode()
    assert 'grant_type=refresh_token' in body and 'scope=' not in body
    assert 'access_token' not in json.dumps(account.status())


@pytest.mark.parametrize('granted', [True,False])
def test_oauth_callback_exchanges_code_validates_identity_and_checks_scope(tmp_path,granted):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    def encode(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')
    public=key.public_key().public_numbers()
    jwks={'keys':[{'kid':'callback-key','kty':'RSA','n':encode(public.n.to_bytes(256,'big')),'e':encode(public.e.to_bytes(3,'big'))}]}
    seen=[]; vault=Vault()
    def handler(request):
        seen.append(request)
        if request.url.path.endswith('/jwks.json'):
            return httpx.Response(200,json=jwks)
        header=encode(json.dumps({'alg':'RS256','kid':'callback-key'}).encode())
        payload=encode(json.dumps({'iss':AUTH,'aud':'issued-client','sub':'verified-subject','nonce':account.pending['nonce'],'exp':time.time()+300,'email':'test@example.invalid'}).encode())
        body=header+'.'+payload
        token=body+'.'+encode(key.sign(body.encode(),padding.PKCS1v15(),hashes.SHA256()))
        return httpx.Response(200,json={'id_token':token,'access_token':'private-access','refresh_token':'private-refresh','scope':PLAN_SCOPE if granted else 'openid email','expires_in':3600})
    account=ChatGPTAccount(Store(tmp_path),vault,httpx.Client(transport=httpx.MockTransport(handler)))
    account.start()
    callback=account.pending['redirect_uri']+'?state='+account.pending['state']+'&code=valid-code&client_id=issued-client'
    assert httpx.get(callback,trust_env=False).status_code==200
    assert account.status()['plan_enabled'] is granted
    assert account.pending is None
    assert account.store.setting('client_id')=='issued-client'
    assert b'code_verifier=' in seen[0].content
    if granted:
        assert vault.read()['subject']=='verified-subject'
        assert 'private-access' not in json.dumps(account.status())
    else:
        assert vault.read() is None
        assert 'разрешение' in account.status()['error']


@pytest.mark.parametrize('terminal,valid', [('response.completed',True),('response.failed',False),(None,False)])
def test_stream_requires_completed_and_uses_supported_contract(tmp_path,terminal,valid):
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content))
        lines=['data: '+json.dumps({'type':'response.output_text.delta','delta':'partial'})]
        if terminal: lines.append('data: '+json.dumps({'type':terminal,'response':{'error':{'code':'subscription_sharing_usage_limit_exceeded'}}}))
        return httpx.Response(200,text='\n\n'.join(lines)+'\n\n')
    vault=Vault({'access_token':'test-secret','expires_at':time.time()+3600,'scope':PLAN_SCOPE})
    account=ChatGPTAccount(Store(tmp_path),vault,httpx.Client(transport=httpx.MockTransport(handler)))
    stream=account.stream('test-model',[{'role':'user','content':'Hi'}],'instructions',threading.Event())
    assert next(stream)=='partial'
    if valid: assert list(stream)==[]
    else:
        with pytest.raises(AccountError): list(stream)
    assert seen[0]['store'] is False and seen[0]['stream'] is True
    assert not {'conversation','previous_response_id','background','max_output_tokens'}.intersection(seen[0])


def test_id_token_signature_audience_and_nonce():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    def encode(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')
    public=key.public_key().public_numbers()
    jwks={'keys':[{'kid':'test-key','kty':'RSA','n':encode(public.n.to_bytes(256,'big')),'e':encode(public.e.to_bytes(3,'big'))}]}
    header=encode(json.dumps({'alg':'RS256','kid':'test-key'}).encode())
    payload=encode(json.dumps({'iss':AUTH,'aud':'issued-client','sub':'subject','nonce':'correct','exp':time.time()+60}).encode())
    body=header+'.'+payload
    token=body+'.'+encode(key.sign(body.encode(),padding.PKCS1v15(),hashes.SHA256()))
    assert validate_id_token(token,'issued-client','correct',jwks)['sub']=='subject'
    with pytest.raises(AccountError): validate_id_token(token,'issued-client','wrong',jwks)
    with pytest.raises(AccountError): validate_id_token(token,'another-client','correct',jwks)


def test_account_verification_requires_nonempty_completed_inference(tmp_path):
    def handler(request):
        if request.url.path.endswith('/models'):
            return httpx.Response(200,json={'models':[{'slug':'account-text','display_name':'Account text','visibility':'list'}]})
        return httpx.Response(200,text='data: {"type":"response.output_text.delta","delta":"OK"}\n\ndata: {"type":"response.completed"}\n\n')
    vault=Vault({'access_token':'secret','scope':PLAN_SCOPE,'client_id':'issued','subject':'subject','expires_at':time.time()+3600})
    account=ChatGPTAccount(Store(tmp_path),vault,httpx.Client(transport=httpx.MockTransport(handler)))
    assert not account.status()['inference_verified']
    account.verify()
    for _ in range(200):
        if account.probe['status']!='running': break
        time.sleep(.01)
    assert account.probe['status']=='completed'
    assert account.status()['inference_verified']
    assert account.status()['verified_model']=='account-text'
    assert 'secret' not in json.dumps(account.status())


def test_empty_completed_response_is_not_account_verification(tmp_path):
    transport=httpx.MockTransport(lambda request:httpx.Response(200,text='data: {"type":"response.completed"}\n\n'))
    vault=Vault({'access_token':'secret','scope':PLAN_SCOPE,'expires_at':time.time()+3600})
    account=ChatGPTAccount(Store(tmp_path),vault,httpx.Client(transport=transport))
    with pytest.raises(AccountError): list(account.stream('text',[], '',threading.Event()))
    assert not account.status()['inference_verified']


def test_expired_unrefreshable_session_requires_reauthorization(tmp_path):
    account=ChatGPTAccount(Store(tmp_path),Vault({'access_token':'expired','scope':PLAN_SCOPE,'expires_at':0}))
    assert account.status()['needs_reauth']
    assert not account.status()['plan_enabled']


def test_revoked_refresh_disables_plan_until_reauthorization(tmp_path):
    vault=Vault({'access_token':'old','refresh_token':'revoked','scope':PLAN_SCOPE,'expires_at':0,'client_id':'issued','subject':'user'})
    transport=httpx.MockTransport(lambda request:httpx.Response(400,json={'error':'invalid_grant'}))
    account=ChatGPTAccount(Store(tmp_path),vault,httpx.Client(transport=transport))
    with pytest.raises(AccountError): account.access_token()
    assert account.status()['needs_reauth']
    assert not account.status()['plan_enabled']
    assert vault.read()['refresh_token']=='revoked'
    resumed=ChatGPTAccount(account.store,vault,httpx.Client(transport=transport))
    assert resumed.status()['needs_reauth']


def test_http_usage_limit_is_actionable_and_does_not_expose_provider_body(tmp_path):
    transport=httpx.MockTransport(lambda request:httpx.Response(429,text='secret-provider-body'))
    account=ChatGPTAccount(Store(tmp_path),Vault({'access_token':'token','scope':PLAN_SCOPE,'expires_at':time.time()+3600}),httpx.Client(transport=transport))
    with pytest.raises(AccountError,match='ресурсы плана') as error:
        list(account.stream('text',[], '',threading.Event()))
    assert 'secret-provider-body' not in str(error.value)


def fake_keyring(monkeypatch):
    import keyring
    records={}
    monkeypatch.setattr(keyring,'get_password',lambda service,identity:records.get((service,identity)))
    monkeypatch.setattr(keyring,'set_password',lambda service,identity,value:records.__setitem__((service,identity),value))
    monkeypatch.setattr(keyring,'delete_password',lambda service,identity:records.pop((service,identity),None))
    return records


def test_large_oauth_session_is_encrypted_with_only_small_key_in_os_vault(tmp_path,monkeypatch):
    records=fake_keyring(monkeypatch)
    vault=KeyringVault('stable-host',tmp_path/'files/session.enc')
    session={'access_token':'large-private-token'*1500,'refresh_token':'rotating-refresh','id_token':'identity'*1200}
    vault.write(session)
    assert vault.read()==session
    assert len(next(iter(records.values())))==44
    assert b'large-private-token' not in vault.path.read_bytes()
    assert b'rotating-refresh' not in vault.path.read_bytes()
    assert len(vault.path.read_bytes())>10000
    vault.clear()
    assert vault.read() is None and not records


def test_atomic_credential_write_failure_keeps_previous_session(tmp_path,monkeypatch):
    fake_keyring(monkeypatch)
    vault=KeyringVault('stable-host',tmp_path/'files/session.enc')
    vault.write({'access_token':'previous'})
    import adaos.services.ai.chatgpt as account_module
    def fail_replace(*args): raise OSError('Simulated disk error')
    monkeypatch.setattr(account_module.os,'replace',fail_replace)
    with pytest.raises(AccountError): vault.write({'access_token':'new-secret'})
    assert vault.read()=={'access_token':'previous'}
    assert len(list(vault.path.parent.iterdir()))==1


def test_credential_key_survives_runtime_path_change_when_data_is_migrated(tmp_path,monkeypatch):
    import shutil
    fake_keyring(monkeypatch)
    old=KeyringVault('same-host',tmp_path/'old/session.enc'); old.write({'access_token':'preserved'})
    target=tmp_path/'new/session.enc';target.parent.mkdir();shutil.copyfile(old.path,target)
    upgraded=KeyringVault('same-host',target)
    assert upgraded.read()=={'access_token':'preserved'}
