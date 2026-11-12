from __future__ import annotations
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import time

import pytest

from adaos.services.public_site_delivery import build_delivery_bundle
from adaos.services.public_site_root import PublicSiteRootService, PublicationRootError, CHUNK_BYTES
from adaos.services.public_site_root_client import RootPublicationDelivery
from adaos.services.public_site_root_server import RootPeer, RootRole, canonical
from adaos.services.site_studio.compiler import compile_site_revision
from adaos.services.site_studio.contracts import digest
from test_public_site_delivery import _source

POLICY = {"publication": True, "custom_domains": False, "published_sites_limit": 1, "custom_domains_limit": 0}


class Provider:
    def __init__(self):
        self.prepares, self.publishes = 0, 0
        self.fail = False

    def prepare(self, *, site_dir, workspace, platform_path, environment="prod"):
        self.prepares += 1
        bundle = build_delivery_bundle(site_dir)
        pointer = {"deployment_digest": bundle.deployment_digest, "pointer_digest": digest({"path": platform_path, "generation": self.prepares})}
        plan = {"environment": environment, "host": "p.adaos.io", "platform_path": platform_path,
                "public_url": "https://p.adaos.io/"+platform_path+"/", "pointer": pointer}
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace/"publication-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        return {"plan_sha256": digest(plan), **pointer}

    def publish(self, **args):
        self.publishes += 1
        if self.fail:
            raise TimeoutError("uncertain provider response")
        plan = json.loads((args["workspace"]/"publication-plan.json").read_text("utf-8"))
        assert digest(plan) == args["plan_digest"]
        return {"ok": True, **plan["pointer"], "url": plan["public_url"]}


def site(tmp_path, identity="first"):
    source = _source()
    source["id"] = identity
    compiled = compile_site_revision(source)
    output = tmp_path/identity
    for relative, value in compiled["artifacts"].items():
        path = output/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return output


class LocalHttp:
    base_url = "https://root-test.invalid"
    def __init__(self, service, subnet="owner", policy=None):
        self.service, self.subnet, self.policy = service, subnet, policy or POLICY
    def request(self, method, path, *, json, timeout):
        assert method == "POST" and path == "/v1/public-sites/rpc"
        action, args = json["action"], json["arguments"]
        if action == "health":
            result = {"ready": True}
        elif action in {"chunk", "status", "site_status"}:
            result = getattr(self.service, action)(self.subnet, args)
        else:
            result = getattr(self.service, action)(self.subnet, args, self.policy)
        return {"ok": True, "result": result}


def prepare(service, tmp_path, identity="first", subnet="owner", policy=None):
    directory = site(tmp_path, identity)
    client = RootPublicationDelivery(LocalHttp(service, subnet, policy))
    workspace = tmp_path/(identity+"-workspace-"+subnet)
    result = client.prepare(site_dir=directory, workspace=workspace, platform_path="st_"+identity.ljust(6,"0"))
    return client, {"site_dir": directory, "workspace": workspace, "platform_path": "st_"+identity.ljust(6,"0"), "plan_digest": result["plan_sha256"]}


def test_real_bundle_roundtrip_and_receipt_recovery_without_provider_repeat(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    client, args = prepare(service, tmp_path)
    before = service.usage("owner", POLICY)
    assert before["published"] == before["reserved"] == 0
    receipt = client.publish(**args)
    restarted = PublicSiteRootService(tmp_path/"root", provider)
    recovery = RootPublicationDelivery(LocalHttp(restarted))
    assert recovery.publish(**args) == receipt
    assert provider.publishes == 1
    assert restarted.usage("owner", POLICY) == {"published": 1, "reserved": 0, "limit": 1, "remaining": 0, "domains": 0, "custom_domains_limit": 0}


def test_large_artifact_uses_small_authenticated_forwarding_requests(tmp_path):
    source = _source()
    source['resources'][0]['data']['title'] = 'Large artifact ' + 'content ' * 9000
    compiled = compile_site_revision(source)
    directory = tmp_path/'large'
    for relative,value in compiled['artifacts'].items():
        path=directory/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False),encoding='utf-8')
    provider=Provider();service=PublicSiteRootService(tmp_path/'root',provider)
    class BoundedConnection(LocalHttp):
        chunks=0
        def request(self,method,path,*,json,timeout):
            envelope={'kind':'forward','subnet_id':'owner','policy':POLICY,'request':json}
            assert len(canonical(envelope)) < 32*1024
            if json['action']=='chunk':self.chunks+=1
            return super().request(method,path,json=json,timeout=timeout)
    http=BoundedConnection(service);client=RootPublicationDelivery(http)
    prepared=client.prepare(site_dir=directory,workspace=tmp_path/'workspace',platform_path='st_large0')
    assert http.chunks>8
    receipt=client.publish(site_dir=directory,workspace=tmp_path/'workspace',platform_path='st_large0',plan_digest=prepared['plan_sha256'])
    assert receipt['deployment_digest']==build_delivery_bundle(directory).deployment_digest


def test_upload_uses_one_bounded_connection_and_preserves_authoring_identity(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/'root', provider)
    class PooledHttp(LocalHttp):
        opened = 0
        closed = 0
        @contextmanager
        def session(self, *, timeout):
            assert timeout == 550
            self.opened += 1
            try:
                yield LocalHttp(self.service, self.subnet, self.policy)
            finally:
                self.closed += 1
        def request(self, *args, **kwargs):
            raise AssertionError('Upload must use the bounded pool')
    http = PooledHttp(service)
    client = RootPublicationDelivery(http)
    client.bind_authoring({'site_id':'first','project_id':'project','revision_id':'draft-1','source_digest':'sha256:'+'a'*64})
    workspace = tmp_path/'workspace'
    client.prepare(site_dir=site(tmp_path), workspace=workspace, platform_path='st_first00')
    assert http.opened == http.closed == provider.prepares == 1
    operation_id = json.loads((workspace/'root-operation.json').read_text())['operation_id']
    with service._db() as db:
        retained = service._operation(db, 'owner', operation_id)
    assert retained['authoring'] == client.authoring


def test_pooled_root_requests_keep_default_authority_headers(monkeypatch):
    import httpx
    from adaos.services.root.client import RootHttpClient
    original = httpx.Client
    opened, observed = [], []
    def handler(request):
        observed.append(request.headers.get('X-Test-Authority'))
        return httpx.Response(200, json={'ok':True,'result':{'ready':True}})
    def factory(*args, **kwargs):
        opened.append(True)
        return original(*args, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, 'Client', factory)
    http = RootHttpClient(base_url='https://root-test.invalid', verify=False, default_headers={'X-Test-Authority':'owner'})
    with http.session(timeout=2) as connection:
        transport = RootPublicationDelivery(connection)
        assert transport.availability()['status'] == 'available'
        transport._call('health', {})
    assert len(opened) == 1 and observed == ['owner','owner']


def test_shared_subnet_last_slot_is_atomic_across_publishers(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    first = prepare(service, tmp_path, "first")
    second = prepare(service, tmp_path, "second")
    def publish(pair):
        try:
            pair[0].publish(**pair[1])
            return "published"
        except PublicationRootError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, [first, second]))
    assert sorted(results) == ["published", "site_studio_site_limit_exhausted"]
    assert provider.publishes == 1


def test_update_at_capacity_and_distinct_subnet_isolation(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    client, args = prepare(service, tmp_path)
    client.publish(**args)
    # A different retained operation may publish another revision of the same site.
    args["workspace"] = tmp_path/"another-operation"
    prepared = client.prepare(**{k:v for k,v in args.items() if k!="plan_digest"})
    args["plan_digest"] = prepared["plan_sha256"]
    client.publish(**args)
    assert service.usage("owner", POLICY)["published"] == 1
    identity = json.loads((args["workspace"]/"root-operation.json").read_text("utf-8"))
    with pytest.raises(PublicationRootError, match="not found"):
        service.status("other", {"operation_id": identity["operation_id"]})
    assert service.usage("other", POLICY)["published"] == 0


def test_canonical_site_identity_and_verified_legacy_migration_survive_node_loss(tmp_path):
    service = PublicSiteRootService(tmp_path/"root", Provider())
    pointer = {"site_id":"existing", "pointer_digest":"sha256:"+"a"*64,
               "deployment_digest":"sha256:"+"b"*64, "generation":3}
    kwargs = dict(subnet="owner", site_id="existing", platform_path="st_legacy00", environment="prod",
        operation_id="legacy-receipt", expected_pointer_digest=pointer["pointer_digest"],
        authoring={"revision_id":"draft-17", "source_digest":"sha256:"+"c"*64})
    with pytest.raises(ValueError, match="does not match"):
        service.adopt(**kwargs, verify=lambda:{**pointer,"site_id":"other"})
    assert service.usage("owner", POLICY)["published"] == 0
    service.adopt(**kwargs, verify=lambda:pointer)
    restarted = PublicSiteRootService(tmp_path/"root", Provider())
    remote = RootPublicationDelivery(LocalHttp(restarted)).site_status("existing")
    assert remote["platform_path"] == "st_legacy00"
    assert remote["publications"]["prod"]["revision_id"] == "draft-17"
    assert restarted.usage("owner", POLICY)["published"] == 1
    with pytest.raises(PublicationRootError):
        restarted.site_status("another-owner", {"site_id":"existing"})


def test_domain_dns_readiness_does_not_skip_binding_or_replay_uncertain_activation(tmp_path):
    class Domains(Provider):
        activations = 0
        def request_domain(self, hostname):
            return {"hostname":hostname,"status":"active","ssl_status":"active"}
        status_domain = request_domain
        def activate_domain_for_plan(self, *args):
            self.activations += 1
            raise TimeoutError("binding acknowledgement lost")
    provider = Domains()
    service = PublicSiteRootService(tmp_path/"root", provider)
    client, args = prepare(service, tmp_path)
    client.publish(**args)
    policy = {**POLICY,"custom_domains":True,"custom_domains_limit":1}
    request = {"site_id":"first","hostname":"site.example","command":"request"}
    service.domain("owner", request, policy)
    activate = {**request,"command":"activate","confirmed":True}
    with pytest.raises(TimeoutError):
        service.domain("owner", activate, policy)
    assert provider.activations == 1
    observed = service.domain("owner", {**request,"command":"status"}, {})
    assert observed["status"] == "reconciliation_required"
    with pytest.raises(PublicationRootError, match="reconciliation"):
        service.domain("owner", activate, policy)
    assert provider.activations == 1


def test_uncertain_result_retains_reservation_and_never_retries_after_restart(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    client, args = prepare(service, tmp_path)
    provider.fail = True
    with pytest.raises(TimeoutError):
        client.publish(**args)
    restarted = PublicSiteRootService(tmp_path/"root", provider)
    assert restarted.usage("owner", POLICY)["reserved"] == 1
    client.http.service = restarted
    with pytest.raises(RuntimeError, match="reconciliation"):
        client.publish(**args)
    assert provider.publishes == 1


def test_node_bytes_and_reviewed_plan_cannot_change_after_prepare(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    client, args = prepare(service, tmp_path)
    bad = {**args, "plan_digest": "sha256:"+"0"*64}
    with pytest.raises(ValueError, match="candidate changed"):
        client.publish(**bad)
    (args["site_dir"]/"robots.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes changed"):
        client.publish(**args)
    assert provider.publishes == 0


def test_authorization_precedes_upload_and_custom_domains_remain_separate(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    with pytest.raises(PublicationRootError) as error:
        prepare(service, tmp_path, policy={"publication": False})
    assert error.value.status == 403
    assert provider.prepares == 0
    with pytest.raises(PublicationRootError) as domain:
        service.domain("owner", {"site_id": "first", "hostname": "site.example", "command": "request"}, POLICY)
    assert domain.value.code == "site_studio_feature_not_entitled"


def test_bundle_path_traversal_and_manifest_digest_rejected_before_provider(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    bundle = build_delivery_bundle(site(tmp_path))
    manifest = dict(bundle.manifest)
    manifest["artifacts"] = [{**a, "path": "../outside"} if i==0 else a for i,a in enumerate(manifest["artifacts"])]
    manifest["bundle_digest"] = digest({k:v for k,v in manifest.items() if k!="bundle_digest"})
    with pytest.raises((PublicationRootError, ValueError)):
        service.begin("owner", {"operation_id": "test", "manifest": manifest, "platform_path": "st_first0", "environment": "prod"}, POLICY)
    assert provider.prepares == provider.publishes == 0


def test_peer_signatures_bind_body_and_time():
    peer = RootPeer("https://api.inimatic.com", b"a"*32)
    import hmac, hashlib
    raw = canonical({"kind": "mirror", "action": "publish"})
    stamp = str(int(time.time()))
    signature = hmac.new(peer.key, stamp.encode()+b"\n"+raw, hashlib.sha256).hexdigest()
    assert peer.verify(raw, stamp, signature)
    assert not peer.verify(raw+b" ", stamp, signature)
    assert not peer.verify(raw, "1000000000", signature)
    with pytest.raises(ValueError):
        RootPeer("https://user-controlled.example", b"a"*32)


def test_peer_tls_timeout_leaves_retry_budget_without_shortening_prepare_read(monkeypatch):
    import http.client
    from unittest.mock import Mock
    from adaos.services.public_site_root_server import PeerHTTPSConnection
    observed = []
    def connect(connection):
        observed.append(connection.timeout)
        connection.sock = Mock()
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', connect)
    connection = PeerHTTPSConnection('api.inimatic.com', timeout=550)
    connection.connect()
    assert observed == [10]
    assert connection.timeout == 550
    connection.sock.settimeout.assert_called_once_with(550)


@pytest.mark.parametrize('kind', ['mirror','forward'])
def test_peer_retries_identical_upload_after_tls_reset_but_never_activation(monkeypatch, kind):
    import urllib.error
    import ssl
    peer = RootPeer('https://api.inimatic.com', b'a'*32)
    calls=[]
    def once(envelope, **kwargs):
        calls.append(canonical(envelope))
        if len(calls)==1:
            raise urllib.error.URLError(ssl.SSLZeroReturnError('connection closed before acknowledgement'))
        return {'accepted':True}
    monkeypatch.setattr(peer,'_call_once',once)
    monkeypatch.setattr(time,'sleep',lambda _:None)
    def envelope(action):
        request={'action':action,'arguments':{'operation_id':'same-operation','index':3,'content':'same-bytes'}}
        return {'kind':'forward','request':request} if kind=='forward' else {'kind':'mirror',**request}
    assert peer.call(envelope('chunk'))=={'accepted':True}
    assert len(calls)==2 and calls[0]==calls[1]
    for action in ['prepare','publish','domain']:
        calls.clear()
        with pytest.raises(urllib.error.URLError):peer.call(envelope(action))
        assert len(calls)==1


def test_http_role_rejects_missing_or_node_certificate_before_policy():
    role = object.__new__(RootRole)
    for headers in [{}, {"X-SSL-Client-Verify": "SUCCESS", "X-Client-Subject": "CN=node:worker,O=subnet:owner"}]:
        with pytest.raises(PublicationRootError) as error:
            role.rpc({"action":"begin"}, headers)
        assert error.value.code == "hub_certificate_required"


def test_verified_hub_identity_overrides_body_claims(tmp_path):
    role = object.__new__(RootRole)
    role.zone = "global"
    role.engine = PublicSiteRootService(tmp_path/"root", Provider())
    role.policy_cache = {}
    role.policy = lambda subnet, headers: POLICY
    role.ownership_admitted = lambda: True
    bundle = build_delivery_bundle(site(tmp_path))
    request = {"action": "begin", "subnet_id": "victim", "arguments": {
        "operation_id": "claimed", "subnet_id": "victim", "manifest": dict(bundle.manifest),
        "platform_path": "st_first0", "environment": "prod"}}
    role.rpc(request, {"X-SSL-Client-Verify": "SUCCESS", "X-Client-Subject": "CN=subnet:owner,O=subnet:owner"})
    assert role.engine.status("owner", {"operation_id": "claimed"})["status"] == "uploading"
    with pytest.raises(PublicationRootError):
        role.engine.status("victim", {"operation_id": "claimed"})


def test_global_cannot_admit_an_empty_or_replaced_ownership_ledger(tmp_path):
    role = object.__new__(RootRole)
    role.zone = "global"
    role.engine = PublicSiteRootService(tmp_path/"authority", Provider())
    role.ownership_admission = tmp_path/"admission.json"
    with pytest.raises(PublicationRootError) as initial:
        role.dispatch("owner", POLICY, {"action":"begin", "arguments":{}})
    assert initial.value.code == "publication_bootstrap_pending"
    role.ownership_admission.write_text(json.dumps({"schema":"adaos.public_site_ownership_admission.v1",
        "authority_id":role.engine.authority_id}),encoding="utf-8")
    assert role.ownership_admitted()
    reopened = PublicSiteRootService(tmp_path/"authority", Provider())
    assert reopened.authority_id == role.engine.authority_id
    role.engine = PublicSiteRootService(tmp_path/"replacement", Provider())
    assert not role.ownership_admitted()
    with pytest.raises(PublicationRootError) as replaced:
        role.dispatch("owner", POLICY, {"action":"publish", "arguments":{}})
    assert replaced.value.code == "publication_bootstrap_pending"


def test_root_rejects_corrupt_chunk_before_provider_plan(tmp_path):
    provider = Provider()
    service = PublicSiteRootService(tmp_path/"root", provider)
    source = site(tmp_path)
    client = RootPublicationDelivery(LocalHttp(service))
    original = client.http.request
    corrupted = False
    def request(method, path, *, json, timeout):
        nonlocal corrupted
        if json["action"] == "chunk" and not corrupted:
            corrupted = True
            raw = base64.b64decode(json["arguments"]["content"])
            json["arguments"]["content"] = base64.b64encode(bytes([raw[0]^1])+raw[1:]).decode()
        return original(method, path, json=json, timeout=timeout)
    client.http.request = request
    with pytest.raises(PublicationRootError) as error:
        client.prepare(site_dir=source, workspace=tmp_path/"workspace", platform_path="st_first0")
    assert error.value.code == "artifact_digest_mismatch"
    assert provider.prepares == 0
