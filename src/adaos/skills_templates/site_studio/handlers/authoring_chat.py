"""Site Studio application adapter: durable bounded authoring through the Root broker."""
from contextlib import closing
import json
import time

from adaos.sdk import access
from adaos.sdk.site_studio import (
    CHAT_OUTPUT_SCHEMA, CHAT_PROMPT, MAX_STEPS, build_chat_context, default_component_catalog,
    digest, propose_chat_change, read_chat_source, read_source_file, source_files,
)
from .authoring import _database, _error
from .compiler import current, encode, project_identity, resolve_asset_bytes
from .discussion import authorize, invocation, target, button as legacy_button, FIELDS, validate_target

PENDING = {"submitting", "queued", "in_progress", "running"}


def button(label, tool, params):
    value = legacy_button(label, tool, params)
    if tool in {"commit_site_change", "reject_site_change", "undo_site_change"}:
        value["action"]["invalidates"] += ["site-studio:sections", "site-studio:assets", "site-studio:preview",
                                           "site-studio:history", "site-studio:structure", "site-studio:properties"]
        value["action"]["followups"] = [{"type": "updateState", "params": {"preview_change_set_id": ""}}]
    return value


def _ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS site_studio_chat_jobs(
        id TEXT PRIMARY KEY,actor TEXT NOT NULL,input_digest TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL)""")


def _phase(db, jid, step, phase, text, status="completed"):
    message = {"id": f"{jid}-phase-{step}-{phase}", "from": "assistant", "text": text,
               "ts": int(time.time() * 1000), "progress_group_id": jid, "progress_phase": phase,
               "progress_label": {"inspect": "Контекст", "validate": "Проверка", "review": "Результат"}[phase],
               "progress_status": status, "progress_seq": step}
    db.execute("INSERT INTO messages(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (message["id"], encode(message)))


def reference_inventory(db):
    pid = project_identity(db)[0]
    sources = source_files(db, pid)
    preview = current(db)
    assets = [{"id": item["id"], "label": item.get("alt") or item["id"], "kind": "media",
               "digest": item["digest"], "uri": f"adaos-material://{pid}/asset/{item['id']}?digest={item['digest']}"}
              for item in preview["source"].get("assets", [])]
    return sources + assets


def _references(db, references):
    if not isinstance(references, list) or len(references) > 12:
        raise ValueError("At most twelve selected project materials are supported")
    inventory = {item["uri"]: item for item in reference_inventory(db)}
    result = []
    seen = set()
    for ref in references:
        item = inventory.get(ref.get("uri")) if isinstance(ref, dict) else None
        if not item or item["id"] != ref.get("id") or item.get("digest") != ref.get("digest"):
            raise ValueError("material_reference_conflict: a selected material changed or is unavailable")
        if item["uri"] in seen:
            continue
        seen.add(item["uri"])
        result.append({key: item[key] for key in ("id", "label", "uri", "digest", "kind")})
        if ref.get("mention"):
            result[-1]["mention"] = str(ref["mention"])[:180]
    return result


def discuss(text, subject="site", block_id="", references=None, web_search=False):
    actor = authorize(True, True)
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000 or subject not in {"site", "target", "block"}:
        raise ValueError("A bounded message and supported context are required")
    if web_search:
        return _error("web_search_unavailable", "Поиск в интернете пока недоступен для этого диалога. Исходные материалы можно выбрать через + или @.")
    jid = digest({"actor": actor, "request_id": invocation()})[7:]
    with closing(_database()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _ensure(db)
        refs = _references(db, references or [])
        fingerprint = digest({"text": text, "subject": subject, "block_id": block_id, "references": refs})
        existing = db.execute("SELECT actor,input_digest FROM site_studio_chat_jobs WHERE id=?", (jid,)).fetchone()
        if existing:
            if existing != (encode(actor), fingerprint):
                return _error("idempotency_conflict", "This request already carries different input")
        else:
            preview = current(db)
            source = preview["source"]
            pid = project_identity(db)[0]
            selected = next((f"routes.{route['id']}.sections.{section['id']}" for route in source["routes"]
                             for section in route["sections"] if section["id"] == block_id), "") if block_id else ""
            if subject == "block" and not selected:
                return _error("not_found", "Select a current section before sending")
            recent = [json.loads(row[0]) for row in db.execute("SELECT body FROM messages ORDER BY seq DESC LIMIT 8")][::-1]
            packet = build_chat_context(source, project_id=pid, source_digest=preview["source_digest"],
                target=target(db), user_text=text, selected_anchor=selected, recent=recent, materials=refs)
            packet["brief_editing_enabled"] = subject == "target"
            state = {"packet": packet, "step": 0, "repairs": 0, "project_id": pid,
                     "source_digest": preview["source_digest"], "references": refs, "subject": subject,
                     "block_id": block_id, "anchor": selected}
            db.execute("INSERT INTO site_studio_chat_jobs VALUES(?,?,?,?,?)", (jid, encode(actor), fingerprint, "created", encode(state)))
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (jid, encode(actor), fingerprint, "site-author-" + jid, "created", encode(state), None))
            message = {"id": jid, "from": "user", "text": text, "ts": int(time.time() * 1000),
                       "references": refs, "job_id": jid, "block_id": block_id, "source_anchor": selected}
            db.execute("INSERT INTO messages(id,body) VALUES(?,?)", (jid, encode(message)))
            _phase(db, jid, 0, "inspect", "Читаю выбранный раздел, материалы и контракты компонентов.")
    return resume(jid)


def owns(job_id):
    with closing(_database()) as db:
        _ensure(db)
        return bool(db.execute("SELECT 1 FROM site_studio_chat_jobs WHERE id=?", (job_id,)).fetchone())


def resume(job_id):
    from adaos.sdk.llm import content
    actor = authorize(True, True)
    from .service_admission import authorize as authorize_service
    for _ in range(2):
        with closing(_database()) as db:
            _ensure(db)
            row = db.execute("SELECT actor,status,body FROM site_studio_chat_jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row[0] != encode(actor):
                return _error("not_found", "Owned authoring request not found")
            if row[1] in {"completed", "failed", "cancelled"}:
                return {"ok": row[1] == "completed", "status": row[1], "job_id": job_id}
            state = json.loads(row[2])
            if project_identity(db)[0] != state["project_id"]:
                return _error("project_selection_conflict", "Open the project that owns this request")
            step = state["step"]
        authorize_service("llm.request", resource_id=f"{job_id}-{step}")
        try:
            # The same step always reuses the exact same broker request and data.
            # The broker puts the declared application purpose in its developer
            # message. Keep the trusted controller contract there; project data
            # and the customer's request remain lower-priority, separate inputs.
            result = content.generate(request_id=f"site-author-{job_id}-{step}", purpose=CHAT_PROMPT,
                prompt=state["packet"]["user_text"], schema=CHAT_OUTPUT_SCHEMA, data=state["packet"], max_output_tokens=6000)
            if result.get("status") in PENDING:
                time.sleep(1)
                result = content.get(f"site-author-{job_id}-{step}")
        except content.SubmissionNotAdmitted:
            with closing(_database()) as db, db:
                return _finish(db, job_id, state, "failed", "Запрос не допущен службой моделей. Проверьте доступ к AI в подписке и повторите сообщение.")
        except Exception as exc:
            # Retain a bounded, non-sensitive diagnostic, never transport payloads/tokens.
            with closing(_database()) as db, db:
                latest = db.execute("SELECT status FROM site_studio_chat_jobs WHERE id=?", (job_id,)).fetchone()
                if latest[0] in {"completed", "failed", "cancelled"}:
                    return {"ok": True, "status": latest[0], "job_id": job_id}
                state["transport_failures"] = state.get("transport_failures", 0) + 1
                state["transport_error_type"] = type(exc).__name__
                if state["transport_failures"] >= 3:
                    return _finish(db, job_id, state, "failed", "Служба моделей недоступна после трёх проверок. Запрос сохранён, черновик не изменён. Повторите сообщение позже.")
                db.execute("UPDATE site_studio_chat_jobs SET body=? WHERE id=?", (encode(state), job_id))
            return {"ok": True, "status": "pending", "job_id": job_id,
                    "message": "Запрос сохранён. Ответ Root пока недоступен; повторная проверка продолжит тот же запрос.",
                    "actions": [button("Продолжить", "resume_discussion", {"job_id": job_id})]}
        if result.get("status") in PENDING:
            return {"ok": True, "status": "pending", "job_id": job_id,
                    "actions": [button("Продолжить", "resume_discussion", {"job_id": job_id})]}
        with closing(_database()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT status,body FROM site_studio_chat_jobs WHERE id=?", (job_id,)).fetchone()
            if latest[0] in {"completed", "failed", "cancelled"} or json.loads(latest[1])["step"] != step:
                continue
            value = result.get("data")
            if result.get("status") != "completed" or not isinstance(value, dict):
                return _finish(db, job_id, state, "failed", "Модель не завершила запрос: " + str(result.get("message") or result.get("status"))[:500])
            try:
                preview = current(db)
                if preview["source_digest"] != state["source_digest"]:
                    return _finish(db, job_id, state, "failed", "Сайт изменился во время подготовки ответа. Черновик сохранён; отправьте запрос по текущей редакции.")
                if value.get("proposed_description") is not None and not state["packet"].get("brief_editing_enabled"):
                    raise ValueError("proposed_description must be null. The selected context is page content, not the project brief.")
                if value["action"] == "inspect":
                    if not value["reads"] or value["operations"]:
                        raise ValueError("Inspection needs reads and no operations")
                    observations = []
                    for request in value["reads"]:
                        if request["kind"] == "source":
                            observation = read_chat_source(preview["source"], request["target"], offset=request["offset"])
                        elif request["kind"] == "component":
                            observation = next(c for c in default_component_catalog()["components"] if c["id"] == request["target"])
                        else:
                            selected = next(ref for ref in state["references"] if ref["kind"] == "source" and ref["id"] == request["target"])
                            file = next(f for f in source_files(db, state["project_id"]) if f["id"] == selected["id"] and f["uri"] == selected["uri"])
                            observation = read_source_file(db, project_id=state["project_id"], identity=file["id"],
                                expected_revision=file["revision"], expected_digest=selected["digest"], offset=request["offset"])
                        observations.append({"request": request, "result": observation})
                    state["packet"]["observations"] = (state["packet"]["observations"] + observations)[-8:]
                    _next(db, job_id, state)
                    continue
                actions = []
                if value["action"] == "propose":
                    proposal = propose_chat_change(db, project_id=state["project_id"], expected_source_digest=state["source_digest"],
                        run_id=job_id, actor_id=str(actor), decision=value, asset_resolver=resolve_asset_bytes)
                    cid = proposal["change_set"]["change_set_id"]
                    state["change_set_id"] = cid
                    _phase(db, job_id, step, "validate", "Предложение проверено и скомпилировано. Сохранённые данные пока не изменены.")
                    actions = [button("Применить", "commit_site_change", {"change_set_id": cid, "expected_source_digest": state["source_digest"]}),
                               button("Отклонить", "reject_site_change", {"change_set_id": cid, "expected_source_digest": state["source_digest"]})]
                    actions.insert(0, {"label": "Посмотреть", "action": {"on": "click", "type": "updateState",
                        "params": {"preview_change_set_id": cid, "preview_change_source_digest": state["source_digest"]}}})
                elif value["action"] != "reply" or value["operations"] or value["reads"]:
                    raise ValueError("Use action=propose with nonempty operations and reads=[] to prepare an edit. "
                                     "A reply must have operations=[] and reads=[]. "
                                     "Resource edits target resources.<selection.resource.id>.data.<field>. "
                                     "Set proposed_description=null unless the user asked to edit the project brief.")
                if value.get("proposed_description") is not None:
                    validate_target(value["proposed_description"])
                    brief = target(db)
                    proposal = {"before": {key: brief[key] for key in FIELDS}, "after": value["proposed_description"],
                                "base_revision": brief["revision"], "reason": value["proposal_reason"]}
                    db.execute("INSERT OR IGNORE INTO proposals VALUES(?,?,?)", (job_id, "ready", encode(proposal)))
                    actions += [button("Применить описание", "decide_proposal", {"proposal_id": job_id, "decision": "apply"}),
                                button("Отклонить описание", "decide_proposal", {"proposal_id": job_id, "decision": "reject"})]
                return _finish(db, job_id, state, "completed", value["reply"], actions)
            except (ValueError, KeyError, StopIteration, IndexError, TypeError) as exc:
                if state["repairs"] >= 2:
                    return _finish(db, job_id, state, "failed", "Не удалось получить допустимое предложение. Черновик сохранён. " + str(exc)[:400])
                state["repairs"] += 1
                state["packet"]["validation_feedback"] = str(exc)[:2400]
                _next(db, job_id, state)
    return {"ok": True, "status": "pending", "job_id": job_id,
            "actions": [button("Продолжить", "resume_discussion", {"job_id": job_id})]}


def _next(db, jid, state):
    state["step"] += 1
    state["packet"]["remaining_steps"] = MAX_STEPS - state["step"]
    if state["step"] >= MAX_STEPS or len(encode(state["packet"]).encode()) > 80 * 1024:
        _finish(db, jid, state, "failed", "Достигнут предел обработки запроса. Черновик сохранён; уточните задачу или выберите отдельный раздел.")
    else:
        db.execute("UPDATE site_studio_chat_jobs SET status='created',body=? WHERE id=?", (encode(state), jid))


def _finish(db, jid, state, status, text, actions=None):
    db.execute("UPDATE site_studio_chat_jobs SET status=?,body=? WHERE id=?", (status, encode(state), jid))
    db.execute("UPDATE jobs SET status=?,result=? WHERE id=?", (status, encode({"status": status, "change_set_id": state.get("change_set_id")}), jid))
    message = {"id": jid + "-response", "from": "assistant" if status == "completed" else "system",
               "text": text, "ts": int(time.time() * 1000), "references": state["references"], "actions": actions or [],
               "source_anchor": state["anchor"], "block_id": state["block_id"], "job_id": jid}
    db.execute("INSERT OR IGNORE INTO messages(id,body) VALUES(?,?)", (message["id"], encode(message)))
    _phase(db, jid, state["step"], "review", "Предложение готово к проверке." if status == "completed" else text, status)
    return {"ok": status == "completed", "status": status, "job_id": jid, "change_set_id": state.get("change_set_id")}


def cancel(job_id):
    actor = authorize(True)
    with closing(_database()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _ensure(db)
        row = db.execute("SELECT actor,status,body FROM site_studio_chat_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row[0] != encode(actor):
            return _error("not_found", "Owned authoring request not found")
        if row[1] in {"completed", "failed", "cancelled"}:
            return {"ok": True, "status": row[1]}
        return _finish(db, job_id, json.loads(row[2]), "cancelled", "Запрос остановлен. Черновик не изменён.")


def discussion_projection(value):
    actor = authorize()
    with closing(_database()) as db:
        _ensure(db)
        owned = {row[0]: (row[1], json.loads(row[2])) for row in db.execute(
            "SELECT id,status,body FROM site_studio_chat_jobs WHERE actor=? ORDER BY rowid DESC LIMIT 100", (encode(actor),))}
        for message in value.get("messages", []):
            job = owned.get(message.get("job_id"))
            if not job:
                continue
            if job[0] not in {"completed", "failed", "cancelled"}:
                message["actions"] = [button("Продолжить", "resume_discussion", {"job_id": message["job_id"]}),
                                      button("Остановить", "cancel_authoring", {"job_id": message["job_id"]})]
            elif job[1].get("change_set_id") and message["id"].endswith("-response"):
                row = db.execute("SELECT body FROM site_studio_change_sets WHERE change_set_id=? AND project_id=?", (job[1]["change_set_id"], job[1]["project_id"])).fetchone()
                change = json.loads(row[0]) if row else {}
                if change.get("status") == "committed":
                    receipt = change["commit_receipt"]
                    message["actions"] = []
                    message["text"] += "\n\nПрименено в черновик: " + receipt["revision_id"] + "."
                    if current(db)["source_digest"] == receipt["source_digest"]:
                        message["actions"] = [button("Отменить правку", "undo_site_change", {
                            "change_set_id": change["change_set_id"], "expected_source_digest": receipt["source_digest"]})]
                elif change.get("status") == "rejected":
                    message["actions"] = []
                    message["text"] += "\n\nПредложение отклонено."
    return value


def candidate_preview(change_set_id):
    from adaos.sdk.site_studio import apply_change_set, compile_site_revision
    authorize()
    with closing(_database()) as db:
        pid = project_identity(db)[0]
        preview = current(db)
        row = db.execute("SELECT body FROM site_studio_change_sets WHERE project_id=? AND change_set_id=?", (pid, change_set_id)).fetchone()
        if not row:
            raise ValueError("Owned change proposal not found")
        change = json.loads(row[0])
        if change["status"] != "proposed" or preview["source_digest"] != change["base_source_digest"]:
            raise ValueError("revision_conflict: this proposal no longer targets the current draft")
        source = apply_change_set(preview["source"], change)
        source["revision"] = {"id": "proposal-" + digest(change)[7:23], "created_at": change["created_at"],
                              "base_digest": change["base_source_digest"], "prompt_packet_ref": "change-sets/" + change_set_id + ".json"}
        result = compile_site_revision(source, asset_resolver=resolve_asset_bytes)["preview"]
        from .compiler import _compatibility_aliases
        return _compatibility_aliases(result)


def section_projection(value):
    with closing(_database()) as db:
        source = current(db)["source"]
    anchors = {section["id"]: f"routes.{route['id']}.sections.{section['id']}"
               for route in source["routes"] for section in route["sections"]}
    for item in value.get("items", []):
        item["source_anchor"] = anchors.get(item["id"], "")
    return value


def history_projection(value):
    with closing(_database()) as db:
        source_digest = current(db)["source_digest"]
    for change in value.get("change_sets", []):
        change["latest_undo_available"] = (change.get("commit_receipt") or {}).get("source_digest") == source_digest
        if (change.get("origin") or {}).get("channel") == "chat":
            change["origin_label"] = "Чат"
    return value
