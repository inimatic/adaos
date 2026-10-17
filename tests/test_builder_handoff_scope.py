import copy

from adaos.services.skill_factory_worker import _prototype_acceptance_prompt_projection


def test_model_projection_retains_the_canonical_prototype_acceptance_stage_and_raw_pending_clauses():
    source={'acceptance_stage':'prototype','acceptance_id':'acceptance.one','decision':'accepted',
        'automation_requirements':[{'requirement_ref':'job:04',
            'statement':'Synchronized viewing is implemented during Automation',
            'acceptance':'A textual explanation is visible in the Prototype editor',
            'status':'pending_automation','prototype_refs':['view:editor']} ]}
    before=copy.deepcopy(source)
    result=_prototype_acceptance_prompt_projection(source)
    assert result['acceptance_stage']=='prototype'
    assert result['automation_requirements']==source['automation_requirements']
    assert source==before


def test_projection_does_not_invent_a_review_stage_for_legacy_acceptance():
    source={'acceptance_id':'acceptance.legacy','automation_requirements':[]}
    result=_prototype_acceptance_prompt_projection(source)
    assert 'acceptance_stage' not in result
