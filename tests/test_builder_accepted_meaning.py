import copy

import pytest

from test_builder_workflow import workflow_project as _workflow_project
from test_builder_generation_authoring import ready_result
from adaos.services.builder.generation_authoring import accepted_generation_meaning, prepare_generation_publication, commit_generation_publication
from adaos.services.builder.generation_journals import GenerationJournals
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder.prototype_publications import PrototypePublications
from adaos.services.builder.prototype_context import compile_semantic_revision_model_context
from adaos.sdk.builder import prototype

workflow_project=_workflow_project


def accepted(service):
    journals=GenerationJournals(service)
    owner=journals.prepare('scenario:recipes')
    directory=journals.resolve(owner['journal_ref'])
    arguments=ready_result(service,directory)
    prepared=prepare_generation_publication(service,**arguments)
    commit_generation_publication(service,**arguments,publication_ref=prepared['publication_ref'])
    publication=PrototypePublications(service.state_dir).load(prepared['publication_ref'])
    baseline=compile_semantic_revision_model_context(publication['document'],publication['brief'],
        source_ref=publication['publication_ref'],revision=publication['seed']['revision'])
    return directory,publication,baseline


def test_a_failed_interpretation_cache_cannot_become_the_next_accepted_history(workflow_project):
    service,_=workflow_project
    directory,publication,baseline=accepted(service)
    before=copy.deepcopy(service.lifecycle_state('scenario','recipes'))
    poison=copy.deepcopy(publication['brief']);poison['digest']='sha256:'+'f'*64
    poison['residual_requirements']=[{'id':'failed-only','statement':'Invented unaccepted requirement'}]
    result=accepted_generation_meaning(service,journal_dir=directory,baseline=baseline,claimed_brief=poison)
    assert result['brief']==publication['brief']
    assert result['baseline']==baseline
    assert result['receipt']['cache_disposition']=='ignored_unaccepted_cache'
    assert result['receipt']['provider_calls']==0
    assert service.lifecycle_state('scenario','recipes')==before
    assert accepted_generation_meaning(service,journal_dir=directory,baseline=baseline,claimed_brief=poison)==result


@pytest.mark.parametrize('fault',['digest','revision','document','foreign_directory'])
def test_stale_or_foreign_baselines_stop_before_inference(workflow_project,tmp_path,fault):
    service,_=workflow_project
    directory,publication,baseline=accepted(service)
    if fault=='digest':baseline['digest']='sha256:'+'f'*64
    if fault=='revision':baseline['revision']='999'
    if fault=='document':baseline['document']['title']={'en':'Changed but unaccepted'}
    if fault=='foreign_directory':
        copied=tmp_path/'foreign';copied.mkdir()
        (copied/'owner.json').write_bytes((directory/'owner.json').read_bytes())
        directory=copied
    with pytest.raises((InterpretationRunRejected,ValueError)):
        accepted_generation_meaning(service,journal_dir=directory,baseline=baseline,claimed_brief=publication['brief'])


def test_sdk_substitutes_only_the_committed_meaning_and_rebuilds_its_compiler_view(workflow_project,monkeypatch):
    service,_=workflow_project
    directory,publication,baseline=accepted(service)
    monkeypatch.setattr(prototype,'_generation_workflow',lambda:service)
    forwarded=[]
    monkeypatch.setattr('adaos.services.builder.task_pipeline.prepare_generation_request',
        lambda request,**options:forwarded.append((request,options)) or {'ok':True})
    poison=copy.deepcopy(publication['brief']);poison['digest']='sha256:'+'f'*64
    request={'output_mode':'semantic_v2','dynamic_request':{'instruction':'Next real change','current_semantic':baseline},
        'base_request':{'current_semantic':baseline}}
    before=copy.deepcopy(request)
    result=prototype.prepare_generation_request(request,metadata={'builder_generation_run_protocol':'v1'},
        journal_dir=directory,previous_brief=poison)
    assert result=={'ok':True} and request==before
    current,options=forwarded[0]
    assert current['dynamic_request']['instruction']=='Next real change'
    assert options['previous_brief']==publication['brief']
    assert current['dynamic_request']['current_semantic']==baseline
