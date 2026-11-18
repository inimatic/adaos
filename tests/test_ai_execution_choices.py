import pytest
from adaos.services.ai.choices import execution_choices,validate_choice


def test_advertised_models_keep_distinct_funding_routes_and_filter_speech():
    choices=execution_choices({'data':[{'id':'gpt-6.2-sol'},{'id':'gpt-image-2'},{'id':'tts-1'}]},
        [{'id':'gpt-6.2-sol','label':'Sol','capabilities':{'efforts':['low','high']}}],binding='owner-binding')
    assert {r['id'] for r in choices}=={'root::gpt-6.2-sol','personal::gpt-6.2-sol'}
    with pytest.raises(ValueError):validate_choice(choices,'personal::gpt-6.2-sol','high')
    selected=validate_choice(choices,'personal::gpt-6.2-sol','high',subscription_output_accepted=True)
    assert selected['binding_ref']=='owner-binding' and selected['reasoning']=={'effort':'high'}
    with pytest.raises(ValueError):validate_choice(choices,'personal::gpt-6.2-sol','invented',subscription_output_accepted=True)
    with pytest.raises(ValueError):validate_choice(choices,'root::missing')


def test_unknown_capabilities_do_not_invent_subscription_controls():
    choices=execution_choices({},[{'id':'new-model'}],binding='b')
    assert validate_choice(choices,'personal::new-model',subscription_output_accepted=True)['reasoning'] is None
    with pytest.raises(ValueError):validate_choice(choices,'personal::new-model','high',subscription_output_accepted=True)
    assert execution_choices({},[{'id':'new-model'}])==[]
