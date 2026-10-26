import json
from types import SimpleNamespace

import pytest

from adaos.sdk import conversation, access
from adaos.services import conversation_store, conversation_interactions, durable_delivery
from adaos.services.agent_context import get_ctx


def setup_owned(monkeypatch):
    ctx = get_ctx()
    ctx.skill_ctx.set_loaded("test", ctx.paths.base_dir())
    monkeypatch.setattr(access, "require", lambda _: {"decision": "allow"})
    monkeypatch.setattr(access, "current_user", lambda: {"kind": "user", "id": "tester"})
    conversation_store.upsert_conversation(conversation_id="owned", owner="skill:test", webspace_id="test")
    durable_delivery.create_reply_route("owned", transport="telegram", route_id="owned:telegram",
        principal_scope=["user:tester"], destination_ref={"hub_id": "test"})


def test_sdk_chat_commits_selected_outputs_and_ui_actions_atomically(monkeypatch):
    from adaos.sdk import chat
    setup_owned(monkeypatch)
    conversation_store.upsert_conversation(conversation_id='owned',owner='skill:test',webspace_id='test',
        policy={'output_route_ids':['owned:telegram']})
    content={'content':[{'type':'text','text':'Ready to inspect'}],
             'actions':[{'id':'inspect','label':'Inspect'}]}
    args=dict(conversation_id='owned',webspace_id='test',owner='skill:test',request_id='ordinary:one')
    first=chat.send(content,**args);again=chat.send(content,**args)
    assert first['publication_receipt']==again['publication_receipt']
    assert len(first['publication_receipt']['intent_ids'])==1
    messages=conversation_store.list_messages('owned')
    assert len(messages)==1 and messages[0]['actions']==content['actions']
    assert messages[0]['_meta']['output_delivery_owned'] is True


def test_sdk_chat_default_off_does_not_republish_history_when_enabled(monkeypatch):
    from adaos.sdk import chat
    setup_owned(monkeypatch)
    args=dict(conversation_id='owned',webspace_id='test',owner='skill:test',request_id='ordinary:off')
    first=chat.send('Original output',**args)
    assert first['publication_receipt']['intent_ids']==[]
    conversation_store.upsert_conversation(conversation_id='owned',owner='skill:test',webspace_id='test',
        policy={'output_route_ids':['owned:telegram']})
    assert chat.send('Original output',**args)['publication_receipt']==first['publication_receipt']
    new=chat.send('New output',**{**args,'request_id':'ordinary:new'})
    assert len(new['publication_receipt']['intent_ids'])==1


def test_sdk_chat_cannot_send_another_principal_selected_route(monkeypatch):
    from adaos.sdk import chat
    setup_owned(monkeypatch)
    conversation_store.upsert_conversation(conversation_id='owned',owner='skill:test',webspace_id='test',
        policy={'output_route_ids':['owned:telegram']})
    monkeypatch.setattr(access,'current_user',lambda:{'kind':'user','id':'another'})
    result=chat.send('Visible locally',conversation_id='owned',webspace_id='test',owner='skill:test',request_id='ordinary:other')
    assert result['publication_receipt']['intent_ids']==[]


def test_sdk_chat_publication_failure_never_falls_back_to_second_append(monkeypatch):
    from adaos.sdk import chat
    setup_owned(monkeypatch)
    conversation_store.upsert_conversation(conversation_id='owned',owner='skill:test',webspace_id='test',
        policy={'output_route_ids':['owned:telegram']})
    def fail(*args,**kwargs):raise RuntimeError('synthetic outbox failure')
    monkeypatch.setattr(durable_delivery,'enqueue_response',fail)
    with pytest.raises(RuntimeError,match='outbox failure'):
        chat.send('Never partially committed',conversation_id='owned',webspace_id='test',owner='skill:test',request_id='ordinary:atomic')
    assert conversation_store.list_messages('owned')==[]


def test_sdk_chat_rejects_foreign_conversation_before_owner_takeover(monkeypatch):
    from adaos.sdk import chat
    setup_owned(monkeypatch)
    conversation_store.upsert_conversation(conversation_id='foreign',owner='core:other',webspace_id='test')
    with pytest.raises(PermissionError,match='owning skill'):
        chat.send('Wrong identifier',conversation_id='foreign',webspace_id='test',owner='skill:test',request_id='ordinary:foreign')
    assert conversation_store.get_conversation('foreign')['owner']=='core:other'
    assert conversation_store.list_messages('foreign')==[]


def test_selection_requires_exact_accepted_human_response_and_cannot_replay_old_choice(monkeypatch):
    setup_owned(monkeypatch)
    with pytest.raises(PermissionError, match="human interaction"):
        conversation.select_output_routes("owned", ["owned:telegram"], response_id="made-up")
    def response(identity, routes, revision):
        handle = conversation_interactions.create_interaction(conversation_id="owned", owner="skill:test",
            interaction_id=identity, prompt="Select outputs", input_spec={"kind": "form",
                "required_fields": ["output_mode"], "choices": [], "sensitive": False},
            metadata={"purpose": "conversation.output_routes", "output_policy_revision": revision})
        return conversation_interactions.submit_response(handle["interaction_id"], actor_id="user:tester",
            expected_generation=handle["generation"], values={"route_ids": routes,
                "output_mode": "selected" if routes else "disabled"}, idempotency_key=identity)["response"]["response_id"]
    first_id = response("select:first", ["owned:telegram"], 0)
    selected = conversation.select_output_routes("owned", ["owned:telegram"], response_id=first_id)
    assert selected["policy_revision"] == 1
    assert conversation_store.get_interaction('select:first')['status']=='completed'
    disable_id = response("select:disable", [], 1)
    conversation.select_output_routes("owned", [], response_id=disable_id)
    assert conversation.select_output_routes("owned", ["owned:telegram"], response_id=first_id) == selected
    assert conversation_store.get_conversation("owned")["policy"]["output_route_ids"] == []


def test_selected_route_closes_dispatch_and_supersedes_old_policy_questions(monkeypatch):
    setup_owned(monkeypatch)
    created=[]
    for label in ('old','selected'):
        i=conversation_interactions.create_interaction(conversation_id='owned',owner='skill:test',
            prompt='Select outputs',input_spec={'kind':'choice','required_fields':['choice'],
                'choices':[{'value':'enabled','label':'Enable'}],'sensitive':False},
            actions=[{'action_id':'enabled','label':'Enable','command':'conversation.output_routes.select',
                'value':'enabled','risk':'write','confirmation_required':True,'principal_scope':['user:tester']}],
            metadata={'purpose':'conversation.output_routes','output_policy_revision':0,
                'output_route_choices':{'enabled':['owned:telegram']}})
        p=conversation_interactions.negotiate_presentation(i,conversation_interactions.standard_capability_profile('web'))
        if label=='old':
            old_response=conversation_interactions.submit_action_token(p['actions'][0]['token'],actor_id='user:tester',idempotency_key='old-selection')['response']
        created.append((i,p))
    i,p=created[-1]
    response=conversation_interactions.submit_action_token(p['actions'][0]['token'],actor_id='user:tester',idempotency_key='real-selection')['response']
    receipt=conversation.select_output_routes('owned',['owned:telegram'],response_id=response['response_id'])
    assert conversation_store.get_interaction_dispatch(response_id=response['response_id'])['status']=='succeeded'
    assert conversation_store.get_interaction(i['interaction_id'])['status']=='completed'
    assert conversation_store.get_interaction(created[0][0]['interaction_id'])['status']=='superseded'
    assert conversation_store.get_interaction_dispatch(response_id=old_response['response_id'])['status']=='rejected'
    assert conversation.select_output_routes('owned',['owned:telegram'],response_id=response['response_id'])==receipt


def test_latest_human_receipt_repairs_erased_policy_without_rewinding_disable(monkeypatch):
    setup_owned(monkeypatch)
    def choose(routes,revision):
        i=conversation_interactions.create_interaction(conversation_id='owned',owner='skill:test',
            prompt='Choose outputs',input_spec={'kind':'choice','required_fields':['choice'],
                'choices':[{'value':'selected','label':'Select'}],'sensitive':False},
            actions=[{'action_id':'selected','label':'Select','command':'conversation.output_routes.select',
                'value':'selected','risk':'write','confirmation_required':True,'principal_scope':['user:tester']}],
            metadata={'purpose':'conversation.output_routes','output_policy_revision':revision,
                'output_route_choices':{'selected':routes}})
        p=conversation_interactions.negotiate_presentation(i,conversation_interactions.standard_capability_profile('web'))
        response=conversation_interactions.submit_action_token(p['actions'][0]['token'],actor_id='user:tester',
            idempotency_key='select-'+str(revision))['response']
        receipt=conversation.select_output_routes('owned',routes,response_id=response['response_id'])
        return response['response_id'],receipt
    enabled,receipt=choose(['owned:telegram'],0)
    def erase():
        with get_ctx().sql.connect() as con:
            con.execute("UPDATE conversation_conversations SET policy_json='{}' WHERE conversation_id='owned'")
            con.commit()
    erase()
    assert conversation.select_output_routes('owned',['owned:telegram'],response_id=enabled)==receipt
    assert conversation_store.get_conversation('owned')['policy']['output_route_ids']==['owned:telegram']
    disabled,disabled_receipt=choose([],1)
    erase()
    conversation.select_output_routes('owned',['owned:telegram'],response_id=enabled)
    assert conversation_store.get_conversation('owned')['policy']=={}
    assert conversation.select_output_routes('owned',[],response_id=disabled)==disabled_receipt
    conversation.select_output_routes('owned',['owned:telegram'],response_id=enabled)
    policy=conversation_store.get_conversation('owned')['policy']
    assert policy['output_route_ids']==[] and policy['output_policy_revision']==2


def test_active_context_never_guesses_shared_conversation(monkeypatch):
    setup_owned(monkeypatch)
    monkeypatch.setattr(access, "invocation", lambda: {"request_id": "request:one"})
    monkeypatch.setattr(conversation, "current", lambda **_: None)
    context = conversation.communication_context(webspace_id="test", channel_id="builder")
    assert context["status"] == "unavailable" and context["conversation_id"] is None
    monkeypatch.setattr(conversation, "current", lambda **_: {"conversation_id": "owned"})
    assert conversation.communication_context(webspace_id="test")["conversation_id"] == "owned"


def test_owning_skill_cannot_publish_into_another_skill_chat(monkeypatch):
    setup_owned(monkeypatch)
    conversation_store.upsert_conversation(conversation_id="foreign", owner="skill:other", webspace_id="test")
    with pytest.raises(PermissionError, match="owning skill"):
        conversation.publish(conversation_id="foreign", publication_key="one", text="private")


def test_shared_skill_chat_policy_does_not_grant_another_users_private_route(monkeypatch):
    setup_owned(monkeypatch)
    with get_ctx().sql.connect() as con:
        con.execute("UPDATE conversation_conversations SET policy_json=? WHERE conversation_id='owned'",
                    (json.dumps({"output_route_ids": ["owned:telegram"]}),))
        con.commit()
    monkeypatch.setattr(access, "current_user", lambda: {"kind": "user", "id": "another"})
    receipt = conversation.publish(conversation_id="owned", publication_key="private:another", text="Do not mirror")
    assert receipt["status"] == "committed" and receipt["intent_ids"] == []
    assert len(conversation_store.list_messages("owned")) == 1


def test_stored_turn_delivery_keeps_sdk_principal_route_boundary(monkeypatch):
    setup_owned(monkeypatch)
    with get_ctx().sql.connect() as con:
        con.execute("UPDATE conversation_conversations SET policy_json=? WHERE conversation_id='owned'",
                    (json.dumps({'output_route_ids':['owned:telegram']}),))
        con.commit()
    stored=conversation_store.append_message(conversation_id='owned', owner='skill:test',
        webspace_id='test', channel_id='general', text='Exact user turn', role='user')
    monkeypatch.setattr(access,'current_user',lambda:{'kind':'user','id':'another'})
    receipt=conversation.publish(conversation_id='owned',publication_key='stored:private',
        text='Exact user turn',existing_message_id=stored['id'])
    assert receipt['intent_ids']==[] and receipt['message_id']==stored['id']
    assert len(conversation_store.list_messages('owned'))==1


@pytest.mark.parametrize("transport", ["web", "telegram"])
def test_route_choice_uses_real_canonical_channel_action_and_can_disable(monkeypatch, transport):
    setup_owned(monkeypatch)
    for revision, choice, selected in [(0, "enabled", ["owned:telegram"]), (1, "disabled", [])]:
        interaction = conversation_interactions.create_interaction(conversation_id="owned", owner="skill:test",
            prompt="Choose future external output delivery", input_spec={"kind": "choice", "required_fields": ["choice"],
                "choices": [{"value": "enabled", "label": "Enable Telegram"}, {"value": "disabled", "label": "Disable"}], "sensitive": False},
            actions=[{"action_id": item, "label": label, "command": "conversation.output_routes.select",
                      "value": item, "risk": "write", "confirmation_required": True, "principal_scope": ["user:tester"]}
                     for item, label in [("enabled", "Enable Telegram"), ("disabled", "Disable external outputs")]],
            metadata={"purpose": "conversation.output_routes", "output_policy_revision": revision,
                      "output_route_choices": {"enabled": ["owned:telegram"], "disabled": []}})
        presentation = conversation_interactions.negotiate_presentation(interaction,
            conversation_interactions.standard_capability_profile(transport))
        action = next(item for item in presentation["actions"] if item["action_id"] == choice)
        response = conversation_interactions.submit_response(interaction["interaction_id"], actor_id="user:tester",
            expected_generation=interaction["generation"], action_token=action["token"],
            presentation_id=presentation["presentation_id"], idempotency_key=choice)["response"]
        assert response["status"] == "answered"
        assert response["values"]["choice"] == choice
        receipt = conversation.select_output_routes("owned", selected, response_id=response["response_id"])
        assert receipt["policy_revision"] == revision + 1
    assert conversation_store.get_conversation("owned")["policy"]["output_route_ids"] == []


def test_route_discovery_is_bounded_and_principal_scoped(monkeypatch):
    setup_owned(monkeypatch)
    for index in range(22):
        durable_delivery.create_reply_route("owned", transport="telegram", route_id=f"extra:{index}",
            principal_scope=["user:tester"], destination_ref={"hub_id": "test"})
    durable_delivery.create_reply_route("owned", transport="telegram", route_id="foreign-principal",
        principal_scope=["user:other"], destination_ref={"hub_id": "test"})
    result = conversation.output_routes("owned")
    assert len(result["items"]) == 20 and result["truncated"]
    assert all(item["route_id"] != "foreign-principal" for item in result["items"])
