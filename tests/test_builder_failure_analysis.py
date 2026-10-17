import copy
import pytest
from adaos.sdk.builder.observability import analyze_failure as analyze_constraints, qualify_failure_replay as qualify_replay


def episode():
    return {'task_id':'task.one','execution_ref':'execution.one','source':'src:1','verifier':'v:1',
            'state':'state:1','environment':'env:1','outcome':'failed','blocked_by':['result.history'],
            'terminal_evidence_ref':'receipt:failed'}


def event(n, constraint, satisfied, applies=True):
    return {**{key:episode()[key] for key in ('task_id','execution_ref','source','verifier','state','environment')},
            'observation_id':f'e{n}','sequence':n,'constraint_id':constraint,'satisfied':satisfied,
            'applies':applies,'evidence_ref':f'log:{n}'}


def analyze(rows, ep=None):
    return analyze_constraints(episode=ep or episode(),constraints=[
        {'constraint_id':'transport.read','owner':'environment'},
        {'constraint_id':'result.history','owner':'application'}],observations=rows,
        hypotheses=[{'claim':'Mutable settings overwrite a historical projection'}])


def test_earlier_recovered_exception_is_not_the_critical_task_failure():
    rows=[event(1,'transport.read',False),event(2,'transport.read',True),event(3,'result.history',False)]
    result=analyze(rows)
    assert result['critical_violation']['observation_id']=='e3'
    assert result['violations'][0]['recovered'] is True
    assert result['classification']=='application'
    assert result['hypotheses'][0]['status']=='unverified'
    assert not result['source_rewrite_authorized']


def test_recovered_then_rebroken_uses_the_later_unrecovered_violation():
    result=analyze([event(1,'result.history',False),event(2,'result.history',True),event(3,'result.history',False)])
    assert result['critical_violation']['sequence']==3


@pytest.mark.parametrize('guard', [False,None])
def test_false_or_unknown_guard_cannot_prove_failure(guard):
    result=analyze([event(1,'result.history',False,guard)])
    assert result['critical_violation'] is None
    assert result['classification']=='insufficient_evidence'


def test_terminal_link_is_required_before_attribution():
    ep=episode();ep['blocked_by']=[]
    result=analyze([event(1,'result.history',False)],ep)
    assert result['classification']=='insufficient_evidence'


@pytest.mark.parametrize('field', ['task_id','source','verifier','state','environment','execution_ref'])
def test_foreign_dependency_observations_are_rejected(field):
    row=event(1,'result.history',False);row[field]='foreign'
    with pytest.raises(ValueError,match='Foreign task'): analyze([row])


def test_controlled_replay_freezes_every_other_dependency_and_preserves_sources():
    before=episode();after=copy.deepcopy(before)
    after.update(execution_ref='execution.replay',verifier='v:2',outcome='passed',terminal_evidence_ref='receipt:passed')
    frozen=copy.deepcopy((before,after))
    result=qualify_replay(before,after,changed_factor='verifier',hypothesis='Failed job read is valid')
    assert result['supported'] and (before,after)==frozen
    after['source']='src:2'
    with pytest.raises(ValueError,match='every other dependency'):
        qualify_replay(before,after,changed_factor='verifier',hypothesis='Failed job read is valid')
