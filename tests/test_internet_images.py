import io
import json
import socket
import time
from types import SimpleNamespace
import pytest
from PIL import Image
from adaos.services import internet_images as m


def picture(fmt='PNG'):
    stream=io.BytesIO();Image.new('RGB',(32,24),'red').save(stream,format=fmt)
    return stream.getvalue()


def resolver(*ips):
    return lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in ips]


@pytest.mark.parametrize('url',['http://example.com/a.png','file:///a.png','https://user:pass@example.com/a','https://example.com:8443/a','https://localhost/a','https://foo.local/a','https://example.com/white space','https://example.com/\\a'])
def test_only_public_https_urls(url):
    with pytest.raises(ValueError):m.public_target(url,resolver('8.8.8.8'))


@pytest.mark.parametrize('ips',[['127.0.0.1'],['10.0.0.1'],['169.254.169.254'],['::1'],['::ffff:127.0.0.1'],['8.8.8.8','192.168.1.1']])
def test_all_dns_answers_must_be_public(ips):
    with pytest.raises(ValueError,match='non_public'):m.public_target('https://example.com/a',resolver(*ips))


def test_public_target_returns_pinned_ip_and_preserves_query():
    assert m.public_target('https://EXAMPLE.com/a?x=1#frag',resolver('8.8.8.8')) == ('https://example.com/a?x=1','example.com','8.8.8.8')


def test_multicast_is_not_a_public_image_destination():
    with pytest.raises(ValueError,match='non_public'):
        m.public_target('https://example.com/a',resolver('224.0.0.1'))


def test_interrupted_expired_fetches_release_node_concurrency(tmp_path):
    service=m.InternetImages(tmp_path,downloader=lambda url:(picture(),{'final_url':url}))
    grant=service.grant('scope',confirmed=True)['grant_id']
    with service.db() as db:
        for index in range(4):
            db.execute('INSERT INTO images VALUES(?,?,?,?,?,?)',
                (str(index),grant,str(index),'https://example.com/a','fetching','{}'))
        db.execute('UPDATE grants SET expires=0')
    fresh=service.grant('scope',confirmed=True)['grant_id']
    assert service.fetch('scope',fresh,'one','https://example.com/a')['width']==32
    with service.db() as db:
        assert db.execute("SELECT count(*) FROM images WHERE status='expired'").fetchone()[0]==4


@pytest.mark.parametrize('fmt',['PNG','JPEG','WEBP'])
def test_image_decoder_verifies_supported_content(fmt):
    result=m.verify_image(picture(fmt));assert result['width']==32 and result['height']==24
    assert result['media_type'].startswith('image/')


def test_wrong_file_and_oversized_image_rejected():
    with pytest.raises(Exception):m.verify_image(b'<html>not an image</html>')
    with pytest.raises(ValueError):m.verify_image(b'x'*(m.MAX_BYTES+1))
    stream=io.BytesIO();Image.new('1',(8193,1)).save(stream,format='PNG')
    with pytest.raises(ValueError,match='dimensions'):m.verify_image(stream.getvalue())
    with pytest.raises(Exception):m.verify_image(b'<svg xmlns="http://www.w3.org/2000/svg"/>')


def test_owned_grants_replay_revocation_and_integrity(tmp_path):
    calls=[]
    def download(url):calls.append(url);return picture(),{'final_url':url}
    service=m.InternetImages(tmp_path,downloader=download)
    with pytest.raises(PermissionError):service.grant('scope',confirmed=False)
    grant=service.grant('scope',confirmed=True)['grant_id']
    with pytest.raises(PermissionError):service.fetch('foreign',grant,'one','https://example.com/a')
    item=service.fetch('scope',grant,'one','https://example.com/a')
    assert service.fetch('scope',grant,'one','https://example.com/a')==item and len(calls)==1
    with pytest.raises(ValueError,match='conflict'):service.fetch('scope',grant,'one','https://example.com/b')
    with pytest.raises(PermissionError):service.read('foreign',grant,item['fetch_id'])
    assert service.read('scope',grant,item['fetch_id'])[0]==picture()
    service.set_policy('disabled')
    with pytest.raises(PermissionError):service.read('scope',grant,item['fetch_id'])
    service.set_policy('ask')
    assert service.task_status('scope',grant)=={'enabled':False}


def test_expired_grants_and_task_file_byte_budget(tmp_path,monkeypatch):
    service=m.InternetImages(tmp_path,downloader=lambda url:(picture(),{'final_url':url}))
    grant=service.grant('scope',confirmed=True)['grant_id']
    monkeypatch.setattr(m,'TASK_FILES',1)
    service.fetch('scope',grant,'one','https://example.com/a')
    with pytest.raises(ValueError,match='quota'):service.fetch('scope',grant,'two','https://example.com/b')
    with service.db() as db:db.execute('UPDATE grants SET expires=0')
    with pytest.raises(PermissionError):service.fetch('scope',grant,'one','https://example.com/a')


def test_expired_staging_bytes_are_reclaimed_without_removing_audit(tmp_path):
    service=m.InternetImages(tmp_path,downloader=lambda url:(picture(),{'final_url':url}))
    grant=service.grant('scope',confirmed=True)['grant_id']
    result=service.fetch('scope',grant,'one','https://example.com/a')
    path=tmp_path/'objects'/result['digest'][7:]
    assert path.exists()
    service.revoke('scope',grant)
    service.grant('scope',confirmed=True)
    assert not path.exists()
    with service.db() as db:
        assert db.execute('SELECT status FROM images').fetchone()[0]=='expired'


def test_redirects_are_revalidated_and_never_carry_credentials(monkeypatch):
    targets=[];headers=[]
    class Connection:
        sock=None
        def __init__(self,host,ip,timeout):targets.append((host,ip))
        def request(self,*args,**kwargs):headers.append(kwargs['headers'])
        def getresponse(self):return SimpleNamespace(status=302,getheader=lambda k,*a:'http://127.0.0.1/admin')
        def close(self):pass
    original=m.public_target
    monkeypatch.setattr(m,'public_target',lambda url:original(url,resolver('8.8.8.8')))
    monkeypatch.setattr(m,'_PinnedHTTPS',Connection)
    with pytest.raises(ValueError):m.download_image('https://example.com/a')
    assert len(targets)==1 and set(headers[0])=={'Accept','Accept-Encoding','User-Agent'}


def test_node_transport_verifies_actual_mime_and_bounded_body(monkeypatch):
    class Connection:
        sock=None
        def __init__(self,*a):self.stream=io.BytesIO(picture())
        def request(self,*a,**k):pass
        def getresponse(self):return SimpleNamespace(status=200,getheader=lambda name,default=None:{'Content-Type':'text/html'}.get(name,default),read1=self.stream.read)
        def close(self):pass
    monkeypatch.setattr(m,'public_target',lambda url:(url,'example.com','8.8.8.8'))
    monkeypatch.setattr(m,'_PinnedHTTPS',Connection)
    with pytest.raises(ValueError,match='content_type'):m.download_image('https://example.com/a')
