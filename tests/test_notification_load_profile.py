"""L0 port-level workload; no model, provider, source effect or shared UI writes."""
from dataclasses import replace
import json
from pathlib import Path
import statistics
import os
import time
import tracemalloc

from test_notification_runtime import notification_runtime, emit


def distribution(samples):
    ordered=sorted(samples)
    return {"count":len(samples),"p50_ms":round(statistics.median(samples)*1000,3),
            "p95_ms":round(ordered[min(len(ordered)-1,int(len(ordered)*.95))]*1000,3)}


def test_l0_large_private_history_is_indexed_and_bounded(notification_runtime):
    service,binding,_,clock=notification_runtime
    first=emit(service,binding)
    with service.store.connect() as con:
        seed=dict(con.execute("SELECT * FROM notification_records WHERE notification_id=?",(first["notification_id"],)).fetchone())
        keys=list(seed)
        def history():
            for index in range(10000):
                user=binding.principal_ref if index%2 else "user:other"
                row={**seed,"notification_id":f"notification:history:{index}","principal_ref":user,
                    "source_id":f"history:{index}","read_revision":1,"source_state":"expired",
                    "expires_at":clock[0]-100,"updated_at":clock[0]-1000-index}
                body=json.loads(seed["record_json"])
                body.update({key:row[key] for key in keys if key in body})
                row["record_json"]=json.dumps(body)
                yield tuple(row[key] for key in keys)
        con.executemany("INSERT INTO notification_records VALUES("+",".join("?" for _ in keys)+")",history())
        con.commit()
        plan=[row[3] for row in con.execute("""EXPLAIN QUERY PLAN SELECT * FROM notification_records
            WHERE principal_ref=? AND json_extract(record_json,'$.decision.visible')=1
            AND read_revision<content_revision AND archived=0 ORDER BY updated_at DESC,notification_id DESC LIMIT 11""",(binding.principal_ref,))]
    assert any("idx_notification_unread_page" in line for line in plan),plan
    admission=[]
    for index in range(50):
        before=time.perf_counter()
        emit(service,binding,event_id=f"L0:{index}",source_id=f"L0:{index}")
        admission.append(time.perf_counter()-before)
    queries=[]
    for _ in range(50):
        before=time.perf_counter()
        page=service.list(binding.principal_ref,limit=100,view="unread")
        queries.append(time.perf_counter()-before)
        assert all(item["principal_ref"]==binding.principal_ref for item in page["items"])
        assert len(page["items"])==51
    # Allocation tracing materially changes latency: measure it separately.
    tracemalloc.start()
    service.list(binding.principal_ref,limit=100,view="unread")
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    report={"profile":"L0.Core-port","history_records":10000,"users":2,
        "topology":"isolated workstation SQLite; no provider/model/SDK ingress",
        "admission":distribution(admission),"private_query":distribution(queries),
        "query_peak_traced_bytes":peak,"query_plan":plan,"provider_calls":0}
    report["latency_targets_met"]=report["admission"]["p95_ms"]<=250 and report["private_query"]["p95_ms"]<=100
    output=Path(__file__).resolve().parents[1]/".tmp/notification-load-profile.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    # Ordinary correctness runs do not assume a quiescent performance host.
    # Qualification explicitly opts into the unchanged declared latency gate.
    if os.getenv("ADAOS_NOTIFY_ENFORCE_LATENCY")=="1":
        assert report["latency_targets_met"],report
    assert peak<=5*1024*1024,report
