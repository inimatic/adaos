"""Small MCP meta-plane over declarative capabilities; executors own receipts."""
from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from jsonschema import validate

from .catalog import runtime
from .ledger import Ledger, digest, new_id
from .policy import enabled, require_enabled

_PREVIEWS: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


def obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
TOOLS = {
    "companion.context.read": ("Read current scenario/view/modal and a reusable context handle.", obj(), "companion.read"),
    "capabilities.search": ("Search ALL installed declared capabilities. Empty query pages the complete catalog. A miss is not absence; try aliases and inspect catalog pages.", obj(query=TEXT, kind={"type": ["string", "null"]}, offset={"type": "integer", "minimum": 0}, limit={"type": "integer", "minimum": 1, "maximum": 50}), "companion.read"),
    "capabilities.describe": ("Read exact arguments, admission, bindings and effects for a canonical capability ref.", obj(capability_ref=TEXT), "companion.read"),
    "operations.preview": ("Validate the selected capability and JSON arguments against current context. Returns preview_id for execution; does not execute.", obj(capability_ref=TEXT, params_json=TEXT, context_handle=TEXT), "companion.read"),
    "operations.execute": ("Execute exactly one previously validated preview and return its executor receipt.", obj(preview_id=TEXT), "companion.execute"),
    "operations.status": ("Read the latest authoritative receipt. Dispatched is not completed.", obj(action_id=TEXT), "companion.read"),
    "operations.cancel": ("Cancel only a genuinely cancellable pending operation.", obj(action_id=TEXT, context_handle=TEXT), "companion.execute"),
    "activity.list": ("Read recent action receipts in this webspace.", obj(limit={"type": "integer", "minimum": 1, "maximum": 100}), "companion.read"),
    "experience.append": ("Propose a scoped hypothesis from observed evidence. This cannot provide human feedback or accept a hypothesis.", obj(proposal_json=TEXT), "companion.request"),
    "capability_request.capture": ("Record a user-requested wish for later development. Does not initiate Builder.", obj(summary=TEXT, desired_outcome=TEXT), "companion.request"),
}


def model_tools() -> list[dict[str, Any]]:
    return [{"type": "function", "name": name.replace(".", "_"), "description": desc,
             "parameters": deepcopy(schema), "strict": True} for name, (desc, schema, _) in TOOLS.items()]


def contracts():
    if not enabled():
        return []
    from adaos.services.root_mcp.model import RootMcpToolContract, RootMcpSurface, ROOT_MCP_RESPONSE_SCHEMA

    rows = []
    for name, (desc, schema, cap) in TOOLS.items():
        schema = deepcopy(schema)
        # Runtime scope/trace are injected by the adapter, never chosen by the model.
        schema["properties"].update({key: TEXT for key in ("webspace_id", "session_id", "turn_id")})
        rows.append(RootMcpToolContract(id=name, title=name, summary=desc, surface=RootMcpSurface.OPERATIONS,
            input_schema=schema, output_schema=deepcopy(ROOT_MCP_RESPONSE_SCHEMA), required_capability=cap,
            side_effects="write" if cap != "companion.read" else "none",
            metadata={"published_by": "plane:companion_control", "handler": name}))
    return rows


def _parameters(descriptor: dict, params: dict) -> dict:
    fixed = descriptor.get("bound_params") or {}
    if any(key in params and params[key] != value for key, value in fixed.items()):
        raise ValueError("capability_binding_override")
    result = {**params, **fixed}
    schema = descriptor.get("input_schema") or {}
    if descriptor.get("skill"):
        validate(result, schema)
    for key in descriptor.get("required_parameters") or []:
        if key not in result:
            raise ValueError("parameter_required:"+key)
    return result


def execution_frame(index, context) -> dict:
    # Build from the already compiled index, never re-read manifests.
    rows = list(index.rows.values())
    frame = {**context["state"], "context_digest": context["context_digest"],
             "available_modal_ids": [], "catalog_apps": [], "catalog_widgets": [], "published_voice_affordances": []}
    for row in rows:
        bound = row.get("bound_params") or {}
        if bound.get("modal_id"):
            frame["available_modal_ids"].append(bound["modal_id"])
        if bound.get("scenario_id"):
            frame["catalog_apps"].append({"id": row["ref"], "scenario_id": bound["scenario_id"]})
        if bound.get("widget_id"):
            frame["catalog_widgets"].append({"id": bound["widget_id"]})
        if row.get("voice_affordance"):
            frame["published_voice_affordances"].append(row["voice_affordance"])
    return frame


def _execute(preview: dict, *, webspace: str) -> dict:
    from adaos.services.root_mcp import companion_plane as legacy

    index, handles = runtime(webspace)
    context = handles.resolve(preview["context_handle"])
    descriptor = index.describe(preview["capability_ref"])
    if descriptor["descriptor_digest"] != preview["descriptor_digest"]:
        raise ValueError("descriptor_changed")
    operation = descriptor.get("operation")
    request = {"request_id": preview["preview_id"], "operation": operation, "params": preview["params"],
               "webspace_id": webspace, "context_digest": context["context_digest"], "trace_id": preview.get("turn_id"),
               "session_id": preview.get("session_id"), "turn_id": preview.get("turn_id")}
    if operation in legacy._OPERATION_BY_ID:
        return legacy.execute_action_request(request, frame=execution_frame(index, context))
    receipt = {"schema": legacy.ACTION_RECEIPT_SCHEMA, "action_id": new_id("action"),
               "operation": operation or descriptor["ref"], "request_id": preview["preview_id"],
               "webspace_id": webspace, "status": "completed", "cancellable": False,
               "validation": {"ok": True}, "accepted_at": legacy._iso_now(), "completed_at": legacy._iso_now()}
    try:
        if operation == "runtime.version.read":
            from adaos.build_info import BUILD_INFO
            receipt["result"] = asdict(BUILD_INFO)
        elif operation == "profile.self.read":
            from adaos.services.personalization_runtime import current_user_header_settings
            receipt["result"] = current_user_header_settings()
        elif descriptor.get("skill") and descriptor["effect"] in {"none", "read_only"}:
            receipt["result"] = legacy._run_skill_tool(descriptor["skill"], descriptor["tool"], {**preview["params"], "webspace_id": webspace, "_meta": {"companion_session_id": preview.get("session_id")}})
        else:
            raise ValueError("capability_not_admitted")
    except Exception as exc:
        receipt.update(status="failed", error={"code": type(exc).__name__, "message": str(exc)[:500]})
    legacy._write_receipt(receipt)
    return receipt


def dispatch(name: str, arguments: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    require_enabled()
    from adaos.services.root_mcp import companion_plane as legacy

    webspace = str(arguments.get("webspace_id") or "desktop")
    index, handles = runtime(webspace)
    if name == "companion.context.read":
        return handles.read()
    if name == "capabilities.search":
        return index.search(arguments.get("query") or "", kind=arguments.get("kind"), offset=arguments.get("offset", 0), limit=arguments.get("limit", 12))
    if name == "capabilities.describe":
        return index.describe(arguments["capability_ref"])
    if name == "operations.preview":
        descriptor = index.describe(arguments["capability_ref"])
        if not descriptor["admitted"]:
            return {"ok": False, "error": "capability_not_admitted", "reason": descriptor.get("reason")}
        context = handles.resolve(arguments["context_handle"])
        if context["state"].get("ui_live") is False and descriptor.get("effect") not in {"read_only", "none"}:
            return {"ok": False, "error": "live_ui_context_unavailable", "hint": "Open the target Webspace before a UI action."}
        params = json.loads(arguments["params_json"])
        if not isinstance(params, dict):
            raise ValueError("params_must_be_object")
        params = _parameters(descriptor, params)
        operation = descriptor.get("operation")
        if operation in legacy._OPERATION_BY_ID:
            validation = legacy.validate_action_request({"operation": operation, "params": params, "context_digest": context["context_digest"]}, frame=execution_frame(index, context))
            if not validation["ok"]:
                return validation
        preview = {"preview_id": new_id("preview"), "capability_ref": descriptor["ref"],
                   "descriptor_digest": descriptor["descriptor_digest"], "context_handle": context["context_handle"],
                   "params": params, "webspace_id": webspace, "expires_at": context["expires_at"],
                   "turn_id": arguments.get("turn_id"), "session_id": arguments.get("session_id"),
                   "effect": descriptor["effect"], "ok": True}
        with _LOCK:
            for expired in [key for key,v in _PREVIEWS.items() if v["expires_at"] < time.time()]:
                _PREVIEWS.pop(expired)
            _PREVIEWS[preview["preview_id"]] = preview
        return deepcopy(preview)
    if name == "operations.execute":
        with _LOCK:
            preview = _PREVIEWS.get(arguments["preview_id"])
            if not preview or preview["webspace_id"] != webspace or any(
                preview.get(key) != arguments.get(key) for key in ("session_id", "turn_id")
            ):
                raise ValueError("preview_not_found")
            if preview.get("receipt"):
                return {"receipt": deepcopy(preview["receipt"]), "replayed": True}
            if dry_run:
                return {"preview": deepcopy(preview)}
            receipt = _execute(preview, webspace=webspace)
            preview["receipt"] = receipt
            return {"receipt": receipt}
    if name == "operations.status":
        for receipt in legacy._recent_receipts(webspace_id=webspace, limit=100):
            if receipt["action_id"] == arguments["action_id"]:
                return {"receipt": receipt}
        return {"error": "action_not_found"}
    if name == "operations.cancel":
        context = handles.resolve(arguments["context_handle"])
        if dry_run:
            return {"would_cancel": arguments["action_id"]}
        return {"receipt": legacy.execute_action_request({"operation": "action.cancel", "params": {"target_action_id": arguments["action_id"]}, "context_digest": context["context_digest"], "webspace_id": webspace}, frame=execution_frame(index, context))}
    if name == "activity.list":
        return {"receipts": legacy._recent_receipts(webspace_id=webspace, limit=arguments.get("limit", 20))}
    if name == "experience.append":
        if dry_run:
            return {"would_append": True}
        return {"hypothesis": Ledger().hypothesis(arguments["session_id"], turn=arguments["turn_id"], proposal=json.loads(arguments["proposal_json"]))}
    if name == "capability_request.capture":
        return {"would_capture": True} if dry_run else {"capability_request": legacy._capture_capability_request(arguments)}
    raise KeyError("unknown_companion_tool")


def handlers():
    return {name: (lambda args, dry_run=False, tool=name: dispatch(tool, args, dry_run=dry_run)) for name in TOOLS}
