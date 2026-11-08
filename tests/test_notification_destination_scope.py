import json

import pytest

from adaos.services import durable_delivery, conversation_delivery
from adaos.services.notifications.endpoints import EndpointService
from adaos.services.notifications import delivery
from test_notification_runtime import notification_runtime, emit
from test_notification_delivery import configure, Adapter


@pytest.mark.parametrize("transport,chat_type,chat_id", [("max","chat","-100"),("max","unknown","123"),("telegram","group","-123")])
def test_pairing_group_or_unknown_destination_never_grants_private_notification_export(notification_runtime,monkeypatch,transport,chat_type,chat_id):
    service,binding,_,clock=notification_runtime
    binding,_,_=configure(service,binding,transports=())
    route=durable_delivery.create_reply_route("group-scope",transport=transport,principal_scope=[binding.principal_ref],
        destination_ref={"hub_id":"A","bot_id":"fixture","chat_id":chat_id,"user_id":"owner","binding_id":"fixture"},
        channel_context={"chat_type":chat_type})
    endpoint=EndpointService().admit_route(binding.principal_ref,binding.subnet_ref,route["route_id"],label="Group")
    assert endpoint["capabilities"]["source_audience"]=="unverified"
    prefs=service.preferences.read(binding.principal_ref,binding.subnet_ref)
    prefs["defaults"]={"mode":"immediate","endpoint_ids":[endpoint["endpoint_id"]]}
    service.preferences.write(binding.principal_ref,binding.subnet_ref,prefs,expected_revision=prefs["revision"])
    monkeypatch.setattr(delivery,"live_application_authority",lambda *args:True)
    emit(service,binding,payload={"title":"PRIVATE fixture"})
    adapter=Adapter()
    assert conversation_delivery.dispatch_once(adapter,now=clock[0])
    assert adapter.sent==[]
