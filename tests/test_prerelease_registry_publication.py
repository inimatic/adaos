from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from test_application_service import _application, _release
from adaos.services.root.service import RootDeveloperService, RootServiceError
from test_application_lifecycle_operations import operations
from test_native_trial_runtime import activation_record, native_paths


@pytest.mark.parametrize('admitted', [True, False])
def test_beta_publishes_only_catalog_metadata_after_exact_source_and_cbs_checks(tmp_path, monkeypatch, admitted):
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.applications.cbs_admission import NativeApplicationCBSAdmissionService
    from adaos.services.artifact_pipeline import registry_checkout
    from adaos.services.capability_binding_state import default_distribution
    release = _release(version='1.1.0')
    plan = SimpleNamespace(release=release.project_release)
    calls=[]
    class Git:
        def current_commit(self,*args):return 'a'*40
        def changed_files(self,*args,**kwargs):return ['semantic/catalog/index.json']
        def commit_subpath(self,*args,**kwargs):calls.append(('commit',kwargs['subpath']))
        def push(self,*args,**kwargs):calls.append(('push',kwargs))
    repo=tmp_path/'metadata';repo.mkdir()
    (repo/'registry.json').write_text('{"version":2,"skills":[],"scenarios":[],"projects":[]}', encoding='utf-8')
    @contextmanager
    def checkout(*args,**kwargs):
        calls.append(('checkout',kwargs))
        yield repo
    monkeypatch.setattr(registry_checkout,'registry_metadata_checkout',checkout)
    monkeypatch.setattr(NativeTrialRuntime,'_resolve_immutable',lambda *args,**kwargs:SimpleNamespace(
        packages=('exact-package',),verified_source=lambda package:calls.append(('verified_source',package))))
    monkeypatch.setattr(NativeApplicationCBSAdmissionService,'find_by_project_release',lambda *args,**kwargs:
        {'status':'admitted','environment_profile':{}} if admitted else None)
    monkeypatch.setattr(default_distribution,'validate_default_distribution_query',lambda **kwargs:calls.append(('query',kwargs)))
    projection=SimpleNamespace(registry_root=None,
        prepare_release=lambda *args,**kwargs:{'paths':['semantic/applications']},
        prepare_public_application=lambda *args,**kwargs:calls.append(('catalog',kwargs)) or {'paths':['semantic/catalog']})
    root=RootDeveloperService.__new__(RootDeveloperService)
    root.ctx=SimpleNamespace(git=Git(),paths=SimpleNamespace(state_dir=lambda:tmp_path/'state'),
        config=SimpleNamespace(role='hub', subnet_id='sn_test'),
        settings=SimpleNamespace(git_author_name='Test',git_author_email='test@example.com'))
    root._load_config=lambda:None
    root._artifact_publication_service=lambda _:SimpleNamespace(get_candidate_release=lambda _:plan,
        candidate_store=SimpleNamespace(load=lambda _:SimpleNamespace(candidate_id=release.accepted_candidate_id)),
        package_store=object(),promote=lambda *args,**kwargs:pytest.fail('Beta promoted Workspace'))
    root._public_application_for_candidate=lambda *args,**kwargs:(_application(),release)
    root._semantic_registry_projection=lambda:projection
    root._publish_default_resolved_distribution=lambda **kwargs:{'bundle_digest':'sha256:'+'b'*64}
    from test_workspace_catalog_validation import proof_for
    def export(*args):
        calls.append(('tests',release.accepted_candidate_id))
        return proof_for(project=release.project_release.project_id,version=release.project_release.version,
            digest=release.release_digest,package=release.project_release.components[0].digest,
            revision=release.project_release.source_ref.revision)
    monkeypatch.setattr('adaos.services.applications.test_acceptance.export_candidate_test_proof',export)
    import subprocess
    subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'add','registry.json'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=test@example.com',
        'commit','-m','Metadata baseline'],check=True,capture_output=True)
    if not admitted:
        with pytest.raises(RootServiceError,match='EnvironmentProfile'):
            root.publish_project_candidate_source(release.accepted_candidate_id,channel='prerelease')
        assert [name for name,_ in calls]==['verified_source']
        return
    result=root.publish_project_candidate_source(release.accepted_candidate_id,channel='prerelease')
    assert result['release_digest']==release.release_digest
    assert result['publication']['branch']=='main'
    assert ('catalog',{'channel':'prerelease'}) in calls
    assert [name for name,_ in calls].index('query') < [name for name,_ in calls].index('push')
    assert [name for name,_ in calls].index('tests') < [name for name,_ in calls].index('checkout')
    committed = next(value for name,value in calls if name=='commit')
    assert committed[0].startswith('semantic/test-acceptance/')
    assert committed[1:] == ('semantic/applications','semantic/catalog')
    assert (repo/'registry.json').read_text(encoding='utf-8') == '{"version":2,"skills":[],"scenarios":[],"projects":[]}'


def test_recovery_cannot_finish_after_only_reconciling_the_root_pointer(operations, monkeypatch):
    from adaos.sdk.builder import applications
    ops, subnet, _, candidate = operations
    calls=[]
    app=ops.store.get_application('example')
    monkeypatch.setattr(applications,'_application_service',lambda:ops.service)
    monkeypatch.setattr(applications,'_application',lambda *_:app)
    monkeypatch.setattr(applications,'_state_dir',lambda:ops.state)
    distribution=SimpleNamespace(reconcile=lambda _:calls.append('reconcile'),
        publish_trial=lambda *args,**kwargs:calls.append('pointer') or {'release':{'release_digest':candidate.release_digest}})
    monkeypatch.setattr(applications,'_distribution_service',lambda:distribution)
    monkeypatch.setattr(applications,'get_stable_source_publisher',lambda:object())
    def unavailable(*args,**kwargs):
        calls.append('catalog')
        assert kwargs['channel']=='prerelease' and kwargs['require_application_catalog'] is True
        assert kwargs['release_notes']=='Retained Beta notes'
        raise TimeoutError('catalog not sealed yet')
    monkeypatch.setattr(applications,'StableSourceProjectionService',lambda *args,**kwargs:SimpleNamespace(publish=unavailable))
    operation={'action':'publish_prerelease','application_id':'example','actor_ref':'user:owner','subnet_ref':subnet,
        'expected_revision':app.revision,'idempotency_key':'retained-publication',
        'intent':{'candidate_id':candidate.candidate_id,'mode':'prerelease','release_notes':'Retained Beta notes'}}
    with pytest.raises(TimeoutError,match='not sealed'):
        applications._replay_development_operation(operation)
    assert calls==['reconcile','pointer','catalog']
