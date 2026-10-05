"""Bounded Responses tool loop with evidence capture and executor-owned narration."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

from jsonschema import validate

from .ledger import Ledger, digest, new_id
from .plane import TOOLS, model_tools
from .policy import require_enabled

SYSTEM_PROMPT = """Ты Мудрец, исследовательский помощник AdaOS для доверенного отладчика.
Помогай в introduction, post-install, утренних делах и управлении установленными навыками.
Узнавай факты через инструменты. Сначала ищи capability, затем прочитай её точный descriptor.
Если поиск пустой, меняй термин (например слайд-шоу -> slideshow) и смотри каталог пустым запросом
с kind и offset. Поиск без результата не доказывает отсутствия функции. Учитывай admitted и reason.
Перед действием получи context_handle, сделай preview с capability_ref и params_json, затем execute.
Не вызывай execute для другого намерения или неуточнённого неоднозначного объекта.
dispatched означает отправлено, а completed — подтверждено. Статус действия сообщает платформа.
После получения receipt остановись: платформа сама точно сообщит результат без ещё одного вызова LLM.
Не выдумывай факты, capabilities, receipt id, удачное выполнение или согласие пользователя.
Сохраняй полезное открытие через experience.append, с evidence_refs из ответов инструментов.
Разделяй lexical_alias и capability_binding (второй обязательно привязан к catalog_digest).
Не меняй NLU, модель, свой prompt или policy. Не запускай Builder. По явной просьбе запиши хотелку.
Не прерывай диалог анкетой: обратная связь сохраняется отдельно. Уточнения задавай по необходимости.
Отвечай кратко по-русски. Если информации недостаточно — скажи это и поясни, что удалось проверить.
"""

_RECEIPT_CLAIM = re.compile(r"companion-action:|(?:receipt|action)[ _-]?id|\b(?:executed|completed|opened|closed|cancelled)\b|\b(?:открыл[аи]?|закрыл[аи]?|выполнил[аи]?|установил[аи]?|отменил[аи]?)\b", re.I)


def unwrap(value: dict[str, Any]) -> dict[str, Any]:
    current = value
    if isinstance(current.get("response"), dict):
        current = current["response"]
    if current.get("ok") is False:
        error = current.get("error") or {}
        return {"ok": False, "error": error}
    return dict(current.get("result")) if isinstance(current.get("result"), dict) else dict(current)


def render_receipt(receipt: dict[str, Any]) -> str:
    status, operation = receipt.get("status"), receipt.get("operation")
    if status in {"failed", "rejected"}:
        error = receipt.get("error") or {}
        return "Не удалось выполнить: " + str(error.get("message") or error.get("code") or status) + "."
    if status == "dispatched":
        return "Команда отправлена. Подтверждение результата ещё не получено."
    if status == "cancelled":
        return "Действие отменено."
    result = receipt.get("result") or {}
    if status != "completed":
        return "Состояние действия: " + str(status) + "."
    if isinstance(result, dict) and result.get("available") is False:
        return "Источник сейчас недоступен: " + str(result.get("reason") or "нет подтверждённых данных") + "."
    if operation == "status.node_cpu.read":
        cpu = result.get("cpu") or {}
        return f"Загрузка процессора: {cpu.get('percent')}%. Источник: {result.get('source')}; измерено: {result.get('observed_at')}."
    if operation == "status.codex_tokens.read":
        usage = result.get("usage") or result
        return "Расход токенов Codex по данным учёта: " + json.dumps(usage, ensure_ascii=False) + "."
    if operation == "media.catalog.search":
        return "Результат поиска в Media Center: " + json.dumps(result, ensure_ascii=False) + "."
    if operation and operation.startswith("ui."):
        return "Результат действия подтверждён интерфейсом."
    return "По данным системы: " + json.dumps(result, ensure_ascii=False, default=str) + "."


def _provider_response(value: dict[str, Any]) -> dict[str, Any]:
    for key in ("response", "result"):
        nested = value.get(key)
        if isinstance(nested, dict) and ("output" in nested or "usage" in nested):
            return nested
    return value


def run_turn(text: str, *, webspace: str, history: list[dict] | None = None,
             meta: dict | None = None, call_tool: Callable | None = None,
             model_call: Callable | None = None, ledger: Ledger | None = None,
             max_calls: int = 12, max_seconds: float = 90) -> dict[str, Any]:
    require_enabled()
    from adaos.sdk.llm.llm_client import send_response
    from adaos.sdk.data.root_mcp import call_local_root_mcp_tool

    started = time.perf_counter()
    meta = meta or {}
    turn_id = new_id("turn")
    model = os.getenv("ADAOS_COMPANION_MODEL", "gpt-4o-mini")
    ledger = ledger or Ledger()
    if call_tool is None:
        def call_tool(name, args):
            return call_local_root_mcp_tool(name, arguments=args, capability_profile="CompanionOperator",
                                           actor="skill:conversation_companions", request_id=new_id("mcp"), trace_id=turn_id)
    model_call = model_call or send_response
    probe_started = time.perf_counter()
    try:
        base = unwrap(call_tool("capabilities.search", {"query": "", "kind": None, "offset": 0, "limit": 1, "webspace_id": webspace}))
    except Exception as exc:
        base = {"error": {"code": type(exc).__name__, "message": str(exc)[:600]}}
    probe_ms = (time.perf_counter()-probe_started)*1000
    identities = {"model": model, "prompt_digest": digest(SYSTEM_PROMPT), "tool_contract_digest": digest(model_tools()),
                  "catalog_digest": base.get("catalog_digest"), "nlu_baseline": "regexp+rasa",
                  "nlu_trace": meta.get("nlu_pipeline") or {}, "runtime_version": _runtime_version()}
    # A turn-specific NLU trace is evidence, not a session identity.
    trace = identities.pop("nlu_trace")
    from adaos.services.personalization_runtime import current_user_id

    actor = str(current_user_id())
    if not actor.startswith("user:"):
        actor = "user:"+actor
    manifest = ledger.start(webspace+":"+actor, identities, actor=actor)
    session_id = manifest["session_id"]
    ledger.append(session_id, "turn", {"text": text, "nlu": trace, "asr": meta.get("asr"), "catalog_probe": base, "catalog_probe_ms": probe_ms}, turn=turn_id, actor=actor)
    if base.get("error"):
        result = {"message": "Каталог возможностей недоступен. Попытка сохранена для разбора.", "session_id": session_id,
                  "turn_id": turn_id, "used_mcp": True, "used_llm": False, "error": "catalog_unavailable",
                  "duration_ms": (time.perf_counter()-started)*1000}
        ledger.append(session_id, "response", result, turn=turn_id)
        return result
    previous = [dict(v) for v in (history or [])[-8:]]
    if previous and previous[-1].get("role") == "user" and (previous[-1].get("text") or previous[-1].get("content")) == text:
        previous.pop()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": item.get("role", "user"), "content": item.get("text") or item.get("content") or ""} for item in previous)
    messages.append({"role": "user", "content": text})
    calls = 0
    used_llm = False
    receipt = None
    reply = ""
    failure = None
    tool_name_map = {name.replace(".", "_"): name for name in TOOLS}
    while calls < max_calls and time.perf_counter()-started < max_seconds:
        require_enabled()
        model_started = time.perf_counter()
        request_id = new_id("model")
        try:
            raw = model_call(messages, model=model, tools=model_tools(), parallel_tool_calls=False,
                             max_tokens=1200, request_id=request_id,
                             prefer_global=os.getenv("ADAOS_COMPANION_LLM_PREFER_GLOBAL", "true").strip().lower() in {"true", "1", "yes"},
                             timeout=max(1, max_seconds-(time.perf_counter()-started)))
            response = _provider_response(raw)
            used_llm = True
        except Exception as exc:
            failure = "provider_error"
            ledger.append(session_id, "model_call", {"request_id": request_id, "model": model, "error": str(exc)[:600], "duration_ms": (time.perf_counter()-model_started)*1000}, turn=turn_id)
            break
        output = response.get("output") or []
        usage = dict(response.get("usage") or raw.get("usage") or {})
        usage["reasoning_tokens"] = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        ledger.append(session_id, "model_call", {"request_id": request_id, "provider_request_id": response.get("id"),
                      "model": response.get("model") or model, "usage": usage, "usage_status": "reported" if response.get("usage") or raw.get("usage") else "unavailable",
                      "input": list(messages), "output": output, "proxy_protocol": raw.get("_protocol"),
                      "provider": raw.get("provider"), "retry": raw.get("retry"),
                      "duration_ms": (time.perf_counter()-model_started)*1000, "ttft_ms": response.get("ttft_ms"),
                      "ttft_status": "not_reported" if response.get("ttft_ms") is None else "measured"}, turn=turn_id)
        messages.extend(output)
        function_calls = [v for v in output if v.get("type") == "function_call"]
        if not function_calls:
            reply = str(response.get("output_text") or raw.get("output_text") or "").strip()
            if not reply:
                reply = "".join(p.get("text", "") for v in output if v.get("type") == "message" for p in v.get("content", []) if p.get("type") == "output_text")
            if _RECEIPT_CLAIM.search(reply):
                ledger.append(session_id, "anomaly", {"classification": "model_planning_error", "reason": "ungrounded_action_claim", "original_response": reply}, turn=turn_id)
                reply = "У меня нет подтверждения выполнения этого действия. Попробуйте уточнить запрос."
            break
        for call in function_calls:
            if calls >= max_calls or time.perf_counter()-started >= max_seconds:
                failure = "loop_exhausted"
                break
            calls += 1
            tool_started = time.perf_counter()
            tool_name = tool_name_map.get(call.get("name"))
            args = {}
            try:
                if not tool_name:
                    raise ValueError("unknown_tool")
                args = json.loads(call["arguments"])
                validate(args, TOOLS[tool_name][1])
                # Scope and attribution come from the runtime, not the model.
                args.update(webspace_id=webspace, session_id=session_id, turn_id=turn_id)
                result = unwrap(call_tool(tool_name, args))
            except Exception as exc:
                result = {"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)[:600]}}
            evidence = ledger.append(session_id, "tool_result", {"tool": tool_name or call.get("name"), "call_id": call.get("call_id"),
                       "arguments": args, "result": result, "duration_ms": (time.perf_counter()-tool_started)*1000}, turn=turn_id)
            result["evidence_ref"] = evidence["id"]
            messages.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result, ensure_ascii=False, default=str)})
            if result.get("receipt"):
                receipt = result["receipt"]
                ledger.append(session_id, "receipt", receipt, turn=turn_id)
                reply = render_receipt(receipt)
                break
            if result.get("capability_request"):
                reply = "Хотелка записана для дальнейшего разбора."
                break
        if reply or failure:
            break
    if not reply:
        failure = failure or "loop_exhausted"
        reply = "Не удалось завершить запрос: " + failure + ". Попытки сохранены для разбора."
    result = {"message": reply, "turn_id": turn_id, "session_id": session_id, "used_llm": used_llm,
              "used_mcp": True, "tool_calls": calls, "action_receipt": receipt, "error": failure,
              "duration_ms": (time.perf_counter()-started)*1000}
    ledger.append(session_id, "response", result, turn=turn_id)
    return result


def _runtime_version() -> str:
    from adaos.build_info import BUILD_INFO

    return str(getattr(BUILD_INFO, "version", "unknown"))
