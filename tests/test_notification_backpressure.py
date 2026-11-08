from dataclasses import replace

import pytest

from adaos.sdk import notifications
from adaos.services.notifications import admission, delivery
from adaos.services import conversation_delivery
from test_notification_runtime import notification_runtime, emit
from test_notification_delivery import configure, Adapter


def test_progress_admission_reserves_completion_capacity_and_keeps_stable_retry(notification_runtime, monkeypatch):
    service,binding,manifest,clock=notification_runtime
    manifest["categories"][0]["event_class"]="progress"
    manifest["categories"][0]["id"]="review.progress"
    completion={**manifest["categories"][0],"id":"review.completed","event_class":"completion"}
    manifest["categories"].append(completion)
    binding=replace(binding,release_digest="sha256:progress-class")
    service.admit_catalog(binding,manifest)
    monkeypatch.setattr(admission,"MAX_PROGRESS_PER_MINUTE",2)
    one=emit(service,binding,category_id="review.progress")
    emit(service,binding,category_id="review.progress",event_id="E2",source_revision=2)
    values={"category_id":"review.progress","event_id":"E3","source_revision":3}
    with pytest.raises(notifications.NotificationBackpressure) as raised:
        emit(service,binding,**values)
    assert 1<=raised.value.retry_after_seconds<=61
    assert emit(service,binding,category_id="review.progress")==one
    assert emit(service,binding,event_id="done")["status"]=="committed"
    clock[0]+=61
    assert emit(service,binding,**values)["status"]=="committed"


def test_progress_replacement_retires_unsent_outputs_before_provider_lane(notification_runtime,monkeypatch):
    service,binding,_,clock=notification_runtime
    binding,_,_=configure(service,binding,transports=("max",))
    monkeypatch.setattr(delivery,"live_application_authority",lambda *args:True)
    emit(service,binding)
    emit(service,binding,event_id="E2",source_revision=2,payload={"title":"Latest"})
    adapter=Adapter()
    assert conversation_delivery.dispatch_once(adapter,now=clock[0])
    assert len(adapter.sent)==1 and "Latest" in adapter.sent[0][0]["payload"]["text"]
    assert not conversation_delivery.dispatch_once(adapter,now=clock[0])


def test_capacity_never_acks_unadmitted_source_or_allocates_an_intent(notification_runtime,monkeypatch):
    service,binding,_,clock=notification_runtime
    monkeypatch.setattr(admission,"MAX_LIVE_RECORDS",1)
    emit(service,binding)
    with pytest.raises(notifications.NotificationBackpressure):
        emit(service,binding,source_id="R2",event_id="E2")
    with service.store.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM notification_intents").fetchone()[0]==1


def test_idle_notification_tick_and_peer_lane_never_acquire_writer(notification_runtime,monkeypatch):
    from contextlib import contextmanager
    service,binding,_,clock=notification_runtime
    binding,_,_=configure(service,binding,transports=("max",))
    emit(service,binding)
    service.recheck_preferences()
    statements=[]
    sql=service.ctx.sql
    connect=sql.connect
    @contextmanager
    def traced(**kwargs):
        with connect(**kwargs) as con:
            con.set_trace_callback(statements.append)
            yield con
    monkeypatch.setattr(sql,"connect",traced)
    assert conversation_delivery.claim_next(transport="telegram",now=clock[0]) is None
    assert delivery.tick() is False
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements),statements
