import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from adaos.services.root.service import RootDeveloperService, RootServiceError, _prepared_bootstrap_token


def test_legacy_enrollment_has_no_prepared_capability(monkeypatch):
    monkeypatch.delenv('ADAOS_BOOTSTRAP_TOKEN_FILE',raising=False)
    assert _prepared_bootstrap_token() is None


def test_prepared_capability_is_private_bounded_and_never_uses_admin_bootstrap(tmp_path,monkeypatch):
    capability=tmp_path/'bootstrap.token';capability.write_text('1'*48);capability.chmod(0o600)
    monkeypatch.setenv('ADAOS_BOOTSTRAP_TOKEN_FILE',str(capability))
    monkeypatch.setenv('ADAOS_ZONE_ID','ru')
    calls=[]
    class Client:
        def request_bootstrap_token(self,*args,**kwargs):raise AssertionError('administrator credential was used')
        def register_subnet(self,csr,**kwargs):
            calls.append(kwargs);return {'subnet_id':'sn_fixture'}
    service=RootDeveloperService.__new__(RootDeveloperService)
    result=service._register_hub(Client(),'must-not-be-sent',verify=True,private_key=rsa.generate_private_key(public_exponent=65537,key_size=2048))
    assert result['subnet_id']=='sn_fixture'
    assert calls[0]['bootstrap_token']=='1'*48


@pytest.mark.parametrize('value',['', 'dev-root-token','z'*48,'1'*129])
def test_invalid_prepared_capability_refused(tmp_path,monkeypatch,value):
    capability=tmp_path/'token';capability.write_text(value);capability.chmod(0o600)
    monkeypatch.setenv('ADAOS_BOOTSTRAP_TOKEN_FILE',str(capability))
    with pytest.raises(RootServiceError):_prepared_bootstrap_token()


@pytest.mark.skipif(os.name=='nt',reason='POSIX ownership and mode boundary')
def test_other_user_readable_capability_refused(tmp_path,monkeypatch):
    capability=tmp_path/'token';capability.write_text('1'*48);capability.chmod(0o644)
    monkeypatch.setenv('ADAOS_BOOTSTRAP_TOKEN_FILE',str(capability))
    with pytest.raises(RootServiceError):_prepared_bootstrap_token()
