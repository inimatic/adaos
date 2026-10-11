from datetime import datetime, timedelta, timezone
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from adaos.services.root.service import RootDeveloperService
from types import SimpleNamespace


@pytest.mark.parametrize('start,end,accepted', [(-2,-1,False),(1,2,False),(-1,1,True)])
def test_certificate_validity_is_required_even_for_a_matching_legacy_subnet(start,end,accepted):
    now=datetime.now(timezone.utc)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'subnet:sn_test')])
    certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now+timedelta(days=start)).not_valid_after(now+timedelta(days=end))
        .sign(key,hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    assert RootDeveloperService._hub_certificate_is_acceptable(certificate,subnet_id='sn_test') is accepted
    assert not RootDeveloperService._hub_certificate_is_acceptable(certificate,subnet_id='sn_other')


@pytest.mark.parametrize('preferred,rotate', [('sn_existing',False),(None,False),('sn_new',True)])
def test_renewing_the_same_explicit_identity_preserves_the_private_key(tmp_path,monkeypatch,preferred,rotate):
    key_path=tmp_path/'hub.key';key_path.write_text('retained test key',encoding='utf-8')
    config=SimpleNamespace(node_settings=SimpleNamespace(id='node-test'),node_id='node-test',
        subnet_settings=SimpleNamespace(id='sn_existing'),subnet_id='sn_existing',
        hub_key_path=lambda:key_path,hub_cert_path=lambda:tmp_path/'missing.cert',ca_cert_path=lambda:tmp_path/'ca')
    service=object.__new__(RootDeveloperService)
    service.ctx=SimpleNamespace(bus=SimpleNamespace(publish=lambda *_args,**_kwargs:None))
    service._load_config=lambda:config
    flags=[]
    def stop_after_key_policy(_cfg,*,force_new=False):
        flags.append(force_new)
        raise RuntimeError('stop before registration')
    service._ensure_hub_keypair=stop_after_key_policy
    monkeypatch.setattr('adaos.services.root.service.emit',lambda *_args,**_kwargs:None)
    with pytest.raises(RuntimeError,match='stop before registration'):
        service.init(root_token='test-token',preferred_subnet_id=preferred)
    assert flags==[rotate]
    assert key_path.read_text(encoding='utf-8')=='retained test key'
