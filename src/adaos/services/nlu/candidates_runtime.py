from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu.teacher_artifacts import accepted_artifact_metadata
from adaos.services.nlu.teacher_events import append_event, make_event, rebuild_events_by_candidate
from adaos.services.nlu.teacher_validation import validate_candidate_apply_async
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.teacher.candidates")

_LOOKUP_SLOT_NAMES = {"modal_id", "scenario_id", "app_id", "node_ref", "skill_id", "webspace_id"}


def _nlu_candidates_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.candidates_runtime",
        owner="core:nlu.candidates",
        channel="core.nlu.candidates.async",
    )


def _payload(evt: Any) -> Dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload")
        return data if isinstance(data, dict) else {}
    return {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str:
    meta = coerce_dict(payload.get("_meta"))
    token = payload.get("webspace_id") or payload.get("workspace_id") or meta.get("webspace_id") or meta.get("workspace_id")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _candidate_static_rule_slots(candidate: Mapping[str, Any]) -> dict[str, str]:
    """Return canonical slot constants that should override regex captures."""
    out: dict[str, str] = {}
    normalization = coerce_dict(candidate.get("normalization"))
    repair = coerce_dict(normalization.get("llm_proposal_repair"))
    for key in _LOOKUP_SLOT_NAMES:
        value = repair.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()

    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    action_slots = action.get("slots") if isinstance(action.get("slots"), Mapping) else {}
    for key in _LOOKUP_SLOT_NAMES:
        if key in out:
            continue
        value = action_slots.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _teacher_obj(data_map: Any) -> dict[str, Any]:
    return coerce_dict(getattr(data_map, "get", lambda _k: None)("nlu_teacher"))


def _find_candidate(teacher: Mapping[str, Any], candidate_id: str) -> Optional[dict[str, Any]]:
    candidates = teacher.get("candidates")
    for item in iter_mappings(candidates):
        if item.get("id") == candidate_id:
            return dict(item)
    return None


def _read_current_scenario_id(ydoc: Any) -> str | None:
    try:
        ui_map = ydoc.get_map("ui")
        token = ui_map.get("current_scenario")
    except Exception:
        return None
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def _extract_callskill_targets_for_intent(*, scenario_id: str, intent: str) -> list[str]:
    try:
        from adaos.services.scenarios import loader as scenarios_loader  # local import to avoid cycles

        content = scenarios_loader.read_content(scenario_id)
    except Exception:
        return []
    if not isinstance(content, dict):
        return []
    nlu = content.get("nlu")
    if not isinstance(nlu, dict):
        return []
    intents = nlu.get("intents")
    if not isinstance(intents, dict):
        return []
    spec = intents.get(intent)
    if not isinstance(spec, dict):
        return []
    actions = spec.get("actions")
    if not isinstance(actions, list):
        return []
    out: list[str] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        if a.get("type") != "callSkill":
            continue
        target = a.get("target")
        if isinstance(target, str) and target.strip():
            out.append(target.strip())
    return out


def _find_skill_subscribing_to(topic: str) -> str | None:
    if not isinstance(topic, str) or not topic.strip():
        return None
    ctx = get_ctx()
    skills_dir = Path(ctx.paths.skills_dir())
    try:
        skill_yamls = list(skills_dir.glob("*/skill.yaml"))
    except Exception:
        return None
    for path in skill_yamls:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        events = payload.get("events")
        if not isinstance(events, dict):
            continue
        subs = events.get("subscribe")
        if not isinstance(subs, list):
            continue
        if any(isinstance(x, str) and x.strip() == topic for x in subs):
            return path.parent.name
    return None


async def _emit_apply_rejected(
    *,
    ctx: Any,
    webspace_id: str,
    candidate_id: str,
    reason: str,
    meta: Mapping[str, Any],
    request_id: str | None = None,
    request_text: str = "",
    validation: Mapping[str, Any] | None = None,
    preview: Mapping[str, Any] | None = None,
    candidate_patch: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "webspace_id": webspace_id,
        "candidate_id": candidate_id,
        "reason": reason,
        "_meta": dict(meta),
    }
    if validation:
        payload["validation"] = dict(validation)
    if preview:
        payload["preview"] = dict(preview)
    bus_emit(ctx.bus, "nlp.teacher.candidate.apply.rejected", payload, source="nlu.teacher.candidates")
    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="candidate.apply_rejected",
                title="Candidate apply rejected",
                subtitle=reason,
                raw=payload,
                meta=meta,
            ),
        )
    except Exception:
        _log.debug("failed to append teacher event (candidate.apply_rejected) webspace=%s", webspace_id, exc_info=True)
    if candidate_patch:
        next_teacher: dict[str, Any] | None = None
        try:
            async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
                data_map = ydoc.get_map("data")
                teacher = coerce_dict(data_map.get("nlu_teacher"))
                next_candidates: list[dict[str, Any]] = []
                changed = False
                for item in iter_mappings(teacher.get("candidates")):
                    d = dict(item)
                    if d.get("id") == candidate_id:
                        d.update(dict(candidate_patch))
                        changed = True
                    next_candidates.append(d)
                if changed:
                    teacher["candidates"] = next_candidates
                    rebuild_events_by_candidate(teacher)
                    with ydoc.begin_transaction() as txn:
                        data_map.set(txn, "nlu_teacher", teacher)
                    next_teacher = dict(teacher)
        except Exception:
            _log.debug("failed to patch candidate after apply rejection webspace=%s candidate=%s", webspace_id, candidate_id, exc_info=True)
        if next_teacher is not None:
            try:
                from adaos.services.nlu.teacher_store import save_teacher_state

                save_teacher_state(webspace_id=webspace_id, teacher=next_teacher)
            except Exception:
                pass
    try:
        bus_emit(
            ctx.bus,
            "ui.notify",
            {
                "text": f"NLU Teacher: candidate was not applied ({reason}).",
                "webspace_id": webspace_id,
                "_meta": dict(meta),
            },
            source="nlu.teacher.candidates",
        )
    except Exception:
        pass
    try:
        if str(meta.get("route_id") or meta.get("route") or "").strip() == "voice_chat":
            bus_emit(
                ctx.bus,
                "io.out.chat.append",
                {
                    "id": "",
                    "from": "hub",
                    "text": f"Не смог применить правило NLU: {reason}. Детали записаны в NLU Teacher.",
                    "ts": time.time(),
                    "_meta": {"webspace_id": webspace_id, **dict(meta), "route_id": "voice_chat"},
                },
                source="nlu.teacher.candidates",
            )
    except Exception:
        pass


@subscribe("nlp.teacher.candidate.apply")
async def _on_candidate_apply(evt: Any) -> None:
    """
    Apply a teacher candidate.

    For now this supports:
    - kind=regex_rule -> delegates to nlp.teacher.regex_rule.apply
    - kind=skill|scenario -> marks as applied and adds into data.nlu_teacher.plan

    Payload:
      - candidate_id
      - webspace_id (optional; falls back to meta/default)
      - _meta (optional; preserved for downstream responses)
    """
    ctx = get_ctx()
    try:
        allow = bool(getattr(getattr(ctx.config, "root_settings", None), "llm", None).allow_nlu_teacher)  # type: ignore[attr-defined]
    except Exception:
        allow = True
    payload = _payload(evt)
    webspace_id = _resolve_webspace_id(payload)
    if not allow:
        try:
            bus_emit(
                ctx.bus,
                "ui.notify",
                {"text": "NLU Teacher отключён политикой (root.llm.allow_nlu_teacher=false).", "webspace_id": webspace_id},
                source="nlu.teacher.candidates",
            )
        except Exception:
            pass
        return
    meta = coerce_dict(payload.get("_meta"))
    payload_target = payload.get("target") if isinstance(payload.get("target"), Mapping) else None

    candidate_id = payload.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        return
    candidate_id = candidate_id.strip()

    candidate: Optional[dict[str, Any]] = None
    request_id: Optional[str] = None
    request_text: str = ""

    try:
        async with _nlu_candidates_write_meta():
            async with async_get_ydoc(webspace_id) as ydoc:
                data_map = ydoc.get_map("data")
                teacher = _teacher_obj(data_map)

                candidate = _find_candidate(teacher, candidate_id)
                if not candidate:
                    return

                request_id = candidate.get("request_id") if isinstance(candidate.get("request_id"), str) else None
                request_text = candidate.get("text") if isinstance(candidate.get("text"), str) else ""
                if candidate.get("status") == "quarantined":
                    await _emit_apply_rejected(
                        ctx=ctx,
                        webspace_id=webspace_id,
                        candidate_id=candidate_id,
                        reason="candidate_quarantined",
                        preview=dict(candidate.get("preview") or {})
                        if isinstance(candidate.get("preview"), Mapping)
                        else {},
                        meta=meta,
                        request_id=request_id,
                        request_text=request_text,
                    )
                    return

                validation = await validate_candidate_apply_async(
                    webspace_id=webspace_id,
                    candidate=candidate,
                    payload_target=payload_target,
                )
                kind = candidate.get("kind")
                if validation.get("ok") and kind == "regex_rule":
                    candidate = dict(candidate)
                    candidate["validation"] = dict(validation)
                    candidate["validated_at"] = time.time()
                else:
                    next_candidates: list[dict[str, Any]] = []
                    validated_candidate: dict[str, Any] | None = None
                    for item in iter_mappings(teacher.get("candidates")):
                        d = dict(item)
                        if d.get("id") == candidate_id:
                            d["validation"] = dict(validation)
                            d["validated_at"] = time.time()
                            if not validation.get("ok"):
                                d["status"] = "validation_failed"
                                d["validation_failed_at"] = time.time()
                            validated_candidate = d
                        next_candidates.append(d)
                    teacher["candidates"] = next_candidates
                    with ydoc.begin_transaction() as txn:
                        data_map.set(txn, "nlu_teacher", teacher)
                    if validated_candidate is not None:
                        candidate = validated_candidate
                if not validation.get("ok"):
                    await _emit_apply_rejected(
                        ctx=ctx,
                        webspace_id=webspace_id,
                        candidate_id=candidate_id,
                        reason="m4_validation_failed",
                        validation=validation,
                        meta=meta,
                        request_id=request_id,
                        request_text=request_text,
                        candidate_patch={
                            "validation": dict(validation),
                            "validated_at": candidate.get("validated_at") or time.time(),
                            "status": "validation_failed",
                            "validation_failed_at": candidate.get("validation_failed_at") or time.time(),
                        },
                    )
                    return

                if kind == "regex_rule":
                    rr = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
                    intent = rr.get("intent")
                    pattern = rr.get("pattern")
                    if isinstance(intent, str) and intent.strip() and isinstance(pattern, str) and pattern.strip():
                        target: dict[str, Any] | None = None
                        # UI override has the highest priority.
                        if isinstance(payload_target, Mapping):
                            t_type = payload_target.get("type")
                            t_id = payload_target.get("id")
                            if isinstance(t_type, str) and isinstance(t_id, str) and t_type.strip() and t_id.strip():
                                target = {"type": t_type.strip(), "id": t_id.strip()}

                        # If the candidate already carries a preferred target, keep it.
                        if target is None:
                            cand_target = candidate.get("target") if isinstance(candidate.get("target"), Mapping) else None
                            if isinstance(cand_target, Mapping):
                                t_type = cand_target.get("type")
                                t_id = cand_target.get("id")
                                if isinstance(t_type, str) and isinstance(t_id, str) and t_type.strip() and t_id.strip():
                                    target = {"type": t_type.strip(), "id": t_id.strip()}

                        scenario_id = _read_current_scenario_id(ydoc)
                        if target is None and scenario_id:
                            # Prefer attaching regex rules to the skill that actually handles the intent,
                            # so they survive scenario tweaks and remain reusable.
                            for call_target in _extract_callskill_targets_for_intent(scenario_id=scenario_id, intent=intent.strip()):
                                skill = _find_skill_subscribing_to(call_target)
                                if skill:
                                    target = {"type": "skill", "id": skill}
                                    break

                            # Fallback: scenario itself owns the intent mapping.
                            if target is None:
                                try:
                                    from adaos.services.scenarios import loader as scenarios_loader  # local import to avoid cycles

                                    content = scenarios_loader.read_content(scenario_id)
                                    intents = (content.get("nlu") or {}).get("intents") if isinstance(content, dict) else None
                                    if isinstance(intents, dict) and intent.strip() in intents:
                                        target = {"type": "scenario", "id": scenario_id}
                                except Exception:
                                    target = None
                        from adaos.services.nlu.regex_rules_runtime import apply_regex_rule  # local import to avoid cycles

                        static_slots = _candidate_static_rule_slots(candidate)
                        await apply_regex_rule(
                            {
                                "webspace_id": webspace_id,
                                "candidate_id": candidate_id,
                                "intent": intent.strip(),
                                "pattern": pattern,
                                **({"target": target} if target else {}),
                                **({"slots": static_slots} if static_slots else {}),
                                "candidate_patch": {
                                    "validation": dict(validation),
                                    "validated_at": candidate.get("validated_at") or time.time(),
                                },
                                "_meta": dict(meta),
                            }
                        )
                    return

                if kind == "training_example":
                    strategy_candidate = (
                        candidate.get("strategy_candidate") if isinstance(candidate.get("strategy_candidate"), Mapping) else {}
                    )
                    intent = candidate.get("intent") or strategy_candidate.get("intent")
                    if not isinstance(intent, str) or not intent.strip():
                        bus_emit(
                            ctx.bus,
                            "nlp.teacher.candidate.apply.rejected",
                            {
                                "webspace_id": webspace_id,
                                "candidate_id": candidate_id,
                                "reason": "missing_intent",
                                "_meta": dict(meta),
                            },
                            source="nlu.teacher.candidates",
                        )
                        return
                    target = None
                    if isinstance(payload_target, Mapping):
                        t_type = payload_target.get("type")
                        t_id = payload_target.get("id")
                        if isinstance(t_type, str) and isinstance(t_id, str) and t_type.strip() and t_id.strip():
                            target = {"type": t_type.strip(), "id": t_id.strip()}
                    if target is None and isinstance(candidate.get("target"), Mapping):
                        cand_target = candidate.get("target")
                        t_type = cand_target.get("type")
                        t_id = cand_target.get("id")
                        if isinstance(t_type, str) and isinstance(t_id, str) and t_type.strip() and t_id.strip():
                            target = {"type": t_type.strip(), "id": t_id.strip()}

                    raw_examples = candidate.get("examples")
                    if not isinstance(raw_examples, list):
                        raw_examples = strategy_candidate.get("examples") if isinstance(strategy_candidate.get("examples"), list) else []
                    examples = [str(item).strip() for item in raw_examples if str(item).strip()]
                    if not examples and request_text:
                        examples = [request_text]
                    if not examples:
                        bus_emit(
                            ctx.bus,
                            "nlp.teacher.candidate.apply.rejected",
                            {
                                "webspace_id": webspace_id,
                                "candidate_id": candidate_id,
                                "reason": "missing_examples",
                                "_meta": dict(meta),
                            },
                            source="nlu.teacher.candidates",
                        )
                        return
                    slots = candidate.get("slots") if isinstance(candidate.get("slots"), Mapping) else {}
                    next_candidates: list[dict[str, Any]] = []
                    applied_candidate: dict[str, Any] | None = None
                    for item in iter_mappings(teacher.get("candidates")):
                        d = dict(item)
                        if d.get("id") == candidate_id:
                            artifact_meta = accepted_artifact_metadata(
                                target=target,
                                source="nlu_teacher",
                                webspace_id=webspace_id,
                                request_id=request_id,
                                thread_id=d.get("thread_id") if isinstance(d.get("thread_id"), str) else None,
                                candidate_id=candidate_id,
                                operator_action="save_example_request",
                                meta=meta,
                            )
                            d["status"] = "apply_requested"
                            d["applied_at"] = time.time()
                            d["applied"] = {
                                "type": "example_save_request",
                                "examples_count": len(examples),
                                "target": dict(target or {}),
                            }
                            d["promotion"] = dict(artifact_meta["promotion"])
                            d["provenance"] = dict(artifact_meta["provenance"])
                            d["privacy"] = dict(artifact_meta["privacy"])
                            applied_candidate = d
                        next_candidates.append(d)
                    teacher["candidates"] = next_candidates
                    with ydoc.begin_transaction() as txn:
                        data_map.set(txn, "nlu_teacher", teacher)
                    if applied_candidate is not None:
                        candidate = applied_candidate
                    for example in examples:
                        bus_emit(
                            ctx.bus,
                            "nlp.teacher.example.save",
                            {
                                "webspace_id": webspace_id,
                                "candidate_id": candidate_id,
                                "text": example,
                                "intent": intent.strip(),
                                **({"target": dict(target)} if isinstance(target, Mapping) else {}),
                                "slots": dict(slots),
                                "request_id": request_id,
                                "source": "nlu_teacher_m3_training_example",
                                "note": candidate.get("notes") if isinstance(candidate.get("notes"), str) else None,
                                "_meta": dict(meta),
                            },
                            source="nlu.teacher.candidates",
                        )
                    return

                if kind not in {"skill", "scenario", "entity_alias", "descriptor_fix", "development_task", "nlu_strategy"}:
                    return

                # mark applied
                next_candidates: list[dict[str, Any]] = []
                for item in iter_mappings(teacher.get("candidates")):
                    d = dict(item)
                    if d.get("id") == candidate_id:
                        target = d.get("target") if isinstance(d.get("target"), Mapping) else payload_target
                        artifact_meta = accepted_artifact_metadata(
                            target=target,
                            source="nlu_teacher",
                            webspace_id=webspace_id,
                            request_id=request_id,
                            thread_id=d.get("thread_id") if isinstance(d.get("thread_id"), str) else None,
                            candidate_id=candidate_id,
                            operator_action="apply_plan",
                            meta=meta,
                        )
                        d["status"] = "applied"
                        d["applied_at"] = time.time()
                        d["applied"] = {"type": "plan"}
                        promotion = coerce_dict(artifact_meta["promotion"])
                        promotion["plan_status"] = "pending_developer_or_operator_handoff"
                        d["promotion"] = promotion
                        provenance = coerce_dict(artifact_meta["provenance"])
                        provenance["accepted_artifact_source"] = "teacher_plan"
                        d["provenance"] = provenance
                        d["privacy"] = dict(artifact_meta["privacy"])
                    next_candidates.append(d)
                teacher["candidates"] = next_candidates

                # add to plan
                plan = teacher.get("plan")
                plan = [dict(x) for x in iter_mappings(plan)]
                plan_item = {
                    "id": f"plan.{int(time.time() * 1000)}",
                    "ts": time.time(),
                    "status": "pending",
                    "candidate_id": candidate_id,
                    "kind": kind,
                    "request_id": request_id,
                    "text": request_text,
                    "candidate": coerce_dict(candidate.get("candidate")),
                    "strategy_candidate": coerce_dict(candidate.get("strategy_candidate")),
                    "training_strategy": coerce_dict(candidate.get("training_strategy")),
                    "promotion": coerce_dict(candidate.get("promotion")),
                    "provenance": coerce_dict(candidate.get("provenance")),
                    "privacy": coerce_dict(candidate.get("privacy")),
                    "notes": candidate.get("notes"),
                }
                plan.append(plan_item)
                teacher["plan"] = plan[-200:]

                with ydoc.begin_transaction() as txn:
                    data_map.set(txn, "nlu_teacher", teacher)
    except Exception:
        _log.warning("failed to apply candidate webspace=%s candidate_id=%s", webspace_id, candidate_id, exc_info=True)
        return

    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="candidate.applied",
                title="Candidate applied",
                subtitle=str((candidate or {}).get("kind") or ""),
                raw={"candidate_id": candidate_id, "candidate": candidate},
                meta=meta,
            ),
        )
    except Exception:
        _log.debug("failed to append teacher event (candidate.applied) webspace=%s", webspace_id, exc_info=True)

    bus_emit(
        ctx.bus,
        "nlp.teacher.candidate.applied",
        {"webspace_id": webspace_id, "candidate": candidate, "_meta": dict(meta)},
        source="nlu.teacher.candidates",
    )
