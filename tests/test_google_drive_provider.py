"""Contract tests for real Drive OAuth boundaries and bounded REST operations."""
import json
from urllib.parse import parse_qs, urlparse
import pytest
from test_google_gmail_provider import FakeVault, FakeResponse, FakeTransport, _application, _release
from adaos.services.applications import ApplicationService, ApplicationStore
from adaos.services.providers.google_drive import GoogleDriveProvider, DRIVE_READONLY_SCOPE
from adaos.services.providers.google_gmail import GoogleGmailProvider, GoogleGmailProviderError, _credential_admission_cache_key


def connect(tmp_path, *, dispatch=False):
    applications=ApplicationService(ApplicationStore(tmp_path/'applications'))
    applications.register(_application('drive_photos'))
    release=_release(applications,'drive_photos',provider_id='google.drive')
    vault=FakeVault()
    transport=FakeTransport([FakeResponse(200,{'access_token':'drive-access-secret','refresh_token':'drive-refresh-secret',
        'scope':DRIVE_READONLY_SCOPE,'expires_in':3600}),FakeResponse(200,{'user':{'emailAddress':'owner@example.test'}})])
    options=dict(vault=vault,applications=applications,transport=transport,client_id='client',client_secret='secret',
        redirect_uri='http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback',clock=lambda:2_000_000_000.)
    drive=GoogleDriveProvider(**options)
    context=dict(application_id='drive_photos',release_digest=release.release_digest,subject_ref='user:owner',account_id='google.drive')
    start=drive.begin_authorization(**context)
    query=parse_qs(urlparse(start['authorization_url']).query)
    assert query['scope']==[DRIVE_READONLY_SCOPE] and query['code_challenge_method']==['S256']
    finisher=GoogleGmailProvider(**options) if dispatch else drive
    result=finisher.complete_authorization(state=query['state'][0],code='authorization-code')
    return drive,transport,vault,result,context,query


@pytest.mark.parametrize('dispatch',[False,True])
def test_google_callback_dispatch_is_bound_to_consumed_state_not_browser_provider(tmp_path,dispatch):
    drive,transport,vault,result,context,query=connect(tmp_path,dispatch=dispatch)
    assert result['provider_id']=='google.drive' and result['status']=='connected'
    assert transport.calls[-1][1]=='https://www.googleapis.com/drive/v3/about'
    accounts=drive.access.connected_accounts('drive_photos')
    assert accounts[0]['provider_id']=='google.drive'
    assert 'drive-access-secret' not in json.dumps(accounts)
    assert 'drive-refresh-secret' not in json.dumps(result)
    assert drive._account_key('user:owner','google.drive')!=GoogleGmailProvider._account_key('user:owner','google.drive')
    with pytest.raises(GoogleGmailProviderError):
        drive.complete_authorization(state=query['state'][0],code='authorization-code')


def test_file_tree_is_one_bounded_folder_query_and_hides_signed_thumbnail_links(tmp_path):
    drive,transport,_,_,context,_=connect(tmp_path)
    transport.responses.append(FakeResponse(200,{'files':[{'id':'p','name':'Portrait.jpg','mimeType':'image/jpeg',
        'thumbnailLink':'https://lh3.googleusercontent.com/temporary-signed','size':'7000'}],'nextPageToken':'page2'}))
    page=drive.execute('list_files',**context,arguments={'parent_id':'folder1','page_size':25})
    assert page['next_page_token']=='page2' and page['items'][0]['has_thumbnail']
    assert 'temporary-signed' not in json.dumps(page)
    method,url,args=transport.calls[-1]
    assert method=='GET' and url=='https://www.googleapis.com/drive/v3/files'
    assert "'folder1' in parents" in args['params']['q'] and args['params']['pageSize']==25
    assert args['allow_redirects'] is False
    count=len(transport.calls)
    for arguments in ({'parent_id':"x' or true"},{'page_size':101},{'destination':'https://example.com'}):
        with pytest.raises(GoogleGmailProviderError): drive.execute('list_files',**context,arguments=arguments)
    assert len(transport.calls)==count


def test_thumbnail_never_sends_credential_to_an_arbitrary_metadata_url(tmp_path):
    drive,transport,_,_,context,_=connect(tmp_path)
    transport.responses.append(FakeResponse(200,{'id':'p','mimeType':'image/jpeg','thumbnailLink':'https://evil.example/thumb'}))
    before=len(transport.calls)
    with pytest.raises(GoogleGmailProviderError,match='thumbnail_unavailable'):
        drive.execute('read_thumbnail',**context,arguments={'file_id':'p'})
    assert len(transport.calls)==before+1


def test_stream_limit_closes_response_and_does_not_return_partial_image(tmp_path):
    drive,transport,_,_,context,_=connect(tmp_path)
    class Stream:
        status_code=200;headers={'Content-Type':'image/jpeg'};closed=False
        def iter_content(self,_): yield b'x'*(64*1024*1024+1)
        def close(self): self.closed=True
    stream=Stream()
    transport.responses.extend([FakeResponse(200,{'id':'p','mimeType':'image/jpeg','size':'1'}),stream])
    with pytest.raises(GoogleGmailProviderError,match='too_large'):
        drive.execute('read_file',**context,arguments={'file_id':'p'})
    assert stream.closed


def test_credential_admission_cache_namespaces_providers(tmp_path):
    drive,_,_,_,context,_=connect(tmp_path)
    arguments={**context,'candidate_permission_profile':None}
    gmail=GoogleGmailProvider(vault=drive.vault,applications=drive.applications)
    assert _credential_admission_cache_key(drive,**arguments)!=_credential_admission_cache_key(gmail,**arguments)


def test_no_mail_or_write_operations_are_exposed_by_drive(tmp_path):
    drive,transport,_,_,context,_=connect(tmp_path)
    count=len(transport.calls)
    for operation in ('send_message','delete_file','list_messages'):
        with pytest.raises(GoogleGmailProviderError,match='not_supported'): drive.execute(operation,**context)
    assert len(transport.calls)==count
