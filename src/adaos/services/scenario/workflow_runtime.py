from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.scenarios import loader as scenarios_loader
from adaos.apps.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.scenario.workflow")


def _payload(evt: Dict[str, Any]) -> Dict[str, Any]:
  """
  Event bus adapter passes the payload dict directly into handlers, so
  ``evt`` is already the payload. Keep a small helper for future changes.
  """
  return evt if isinstance(evt, dict) else {}


def _resolve_webspace_id(payload: Dict[str, Any]) -> str:
  value = payload.get("webspace_id") or payload.get("workspace_id")
  if isinstance(value, str) and value.strip():
    return value.strip()
  return default_webspace_id()


@dataclass(slots=True)
class ScenarioWorkflowRuntime:
  """
  Lightweight workflow projection for scenarios.

  Responsibilities:
    - read ``workflow`` section from scenario.json,
    - maintain current workflow state and next_actions in Yjs,
    - execute state transitions in response to actions.

  For v0.1 this runtime only updates Yjs and does not call skill tools;
  tool execution remains the responsibility of UI/skills. This keeps the
  core service simple while we validate the Prompt IDE workflow.
  """

  ctx: AgentContext

  async def sync_workflow_for_webspace(self, scenario_id: str, webspace_id: str) -> None:
    """
    Initialise or refresh workflow state for the given scenario+webspace
    based on the scenario.json ``workflow`` section.
    """
    content = scenarios_loader.read_content(scenario_id)
    wf = (content.get("workflow") or {}) if isinstance(content, dict) else {}
    if not wf:
      return

    states = wf.get("states") or {}
    if not isinstance(states, dict) or not states:
      return
    initial = wf.get("initial_state")
    if not isinstance(initial, str) or not initial:
      # fallback: first key in states
      initial = next(iter(states.keys()))

    # Compute next_actions for the current state.
    next_actions = self._actions_for_state(states, initial)

    async with async_get_ydoc(webspace_id) as ydoc:
      data_map = ydoc.get_map("data")
      with ydoc.begin_transaction() as txn:
        prompt_section = data_map.get("prompt")
        if not isinstance(prompt_section, dict):
          prompt_section = {}
        wf_obj = dict(prompt_section.get("workflow") or {})
        wf_obj["state"] = initial
        wf_obj["next_actions"] = json.loads(json.dumps(next_actions))
        prompt_section["workflow"] = wf_obj
        payload = json.loads(json.dumps(prompt_section))
        data_map.set(txn, "prompt", payload)

  def _actions_for_state(self, states: Dict[str, Any], state_id: str) -> List[Dict[str, Any]]:
    state = states.get(state_id) or {}
    if not isinstance(state, dict):
      return []
    actions = state.get("actions") or []
    if not isinstance(actions, list):
      return []
    out: List[Dict[str, Any]] = []
    for entry in actions:
      if not isinstance(entry, dict):
        continue
      action_id = entry.get("id")
      if not isinstance(action_id, str) or not action_id:
        continue
      label = entry.get("label") or action_id
      next_state = entry.get("next_state") or state_id
      out.append(
        {
          "id": action_id,
          "label": label,
          "state": state_id,
          "next_state": next_state,
        }
      )
    return out

  async def apply_action(self, scenario_id: str, webspace_id: str, action_id: str) -> None:
    """
    Apply a workflow action by updating current state and next_actions
    in Yjs. For v0.1 tool execution is intentionally left out.
    """
    action_id = (action_id or "").strip()
    if not action_id:
      return
    content = scenarios_loader.read_content(scenario_id)
    wf = (content.get("workflow") or {}) if isinstance(content, dict) else {}
    states = wf.get("states") or {}
    if not isinstance(states, dict) or not states:
      return

    # Determine current state from Yjs or fallback to initial_state.
    initial = wf.get("initial_state")
    if not isinstance(initial, str) or not initial:
      initial = next(iter(states.keys()))

    async with async_get_ydoc(webspace_id) as ydoc:
      data_map = ydoc.get_map("data")
      with ydoc.begin_transaction() as txn:
        prompt_section = data_map.get("prompt")
        if not isinstance(prompt_section, dict):
          prompt_section = {}
        wf_obj = dict(prompt_section.get("workflow") or {})
        current_state = wf_obj.get("state") or self._read_state(ydoc) or initial

        next_state = self._resolve_next_state(states, current_state, action_id)
        if not next_state:
          return

        wf_obj["state"] = next_state
        wf_obj["next_actions"] = json.loads(
          json.dumps(self._actions_for_state(states, next_state))
        )
        prompt_section["workflow"] = wf_obj
        payload = json.loads(json.dumps(prompt_section))
        data_map.set(txn, "prompt", payload)

  def _read_state(self, ydoc: Any) -> Optional[str]:
    data_map = ydoc.get_map("data")
    raw = data_map.get("prompt")
    if not isinstance(raw, dict):
      return None
    wf = raw.get("workflow") or {}
    if isinstance(wf, dict):
      value = wf.get("state")
      if isinstance(value, str) and value:
        return value
    return None

  async def set_state(self, scenario_id: str, webspace_id: str, state_id: str) -> None:
    """
    Force workflow into a specific state without executing any action.

    Used when switching projects in Prompt IDE so that the global
    workflow projection matches per-project saved state.
    """
    state_id = (state_id or "").strip()
    if not state_id:
      return
    content = scenarios_loader.read_content(scenario_id)
    wf = (content.get("workflow") or {}) if isinstance(content, dict) else {}
    states = wf.get("states") or {}
    if not isinstance(states, dict) or not states:
      return
    if state_id not in states:
      # Fallback: ignore unknown state ids.
      return

    async with async_get_ydoc(webspace_id) as ydoc:
      data_map = ydoc.get_map("data")
      with ydoc.begin_transaction() as txn:
        prompt_section = data_map.get("prompt")
        if not isinstance(prompt_section, dict):
          prompt_section = {}
        wf_obj = dict(prompt_section.get("workflow") or {})
        wf_obj["state"] = state_id
        wf_obj["next_actions"] = json.loads(
          json.dumps(self._actions_for_state(states, state_id))
        )
        prompt_section["workflow"] = wf_obj
        payload = json.loads(json.dumps(prompt_section))
        data_map.set(txn, "prompt", payload)

  def _resolve_next_state(self, states: Dict[str, Any], current_state: str, action_id: str) -> Optional[str]:
    state = states.get(current_state) or {}
    if not isinstance(state, dict):
      return None
    actions = state.get("actions") or []
    if not isinstance(actions, list):
      return None
    for entry in actions:
      if not isinstance(entry, dict):
        continue
      if entry.get("id") != action_id:
        continue
      next_state = entry.get("next_state")
      if isinstance(next_state, str) and next_state:
        return next_state
    return None


@subscribe("scenario.workflow.action")
async def _on_workflow_action(evt: Dict[str, Any]) -> None:
  """
  Handle workflow action requests coming from IO layers (web, chat, voice).

  Payload:
    - scenario_id: scenario identifier (required)
    - action_id: workflow action identifier (required)
    - webspace_id / workspace_id: optional, defaults to default webspace.
  """
  payload = _payload(evt)
  scenario_id = str(payload.get("scenario_id") or "").strip()
  action_id = str(payload.get("action_id") or "").strip()
  if not scenario_id or not action_id:
    return
  webspace_id = _resolve_webspace_id(payload)
  ctx = get_ctx()
  runtime = ScenarioWorkflowRuntime(ctx)
  _log.info("workflow.action scenario=%s webspace=%s action=%s", scenario_id, webspace_id, action_id)
  try:
    await runtime.apply_action(scenario_id, webspace_id, action_id)
  except Exception as exc:  # pragma: no cover - defensive
    _log.warning(
      "workflow.action failed scenario=%s webspace=%s action=%s error=%s",
      scenario_id,
      webspace_id,
      action_id,
      exc,
      exc_info=True,
    )


@subscribe("scenario.workflow.set_state")
async def _on_workflow_set_state(evt: Dict[str, Any]) -> None:
  """
  Force workflow state (without executing tools).

  Payload:
    - scenario_id: scenario identifier (required)
    - state: workflow state id (required)
    - webspace_id / workspace_id: optional, defaults to default webspace.
  """
  payload = _payload(evt)
  scenario_id = str(payload.get("scenario_id") or "").strip()
  state_id = str(payload.get("state") or "").strip()
  if not scenario_id or not state_id:
    return
  webspace_id = _resolve_webspace_id(payload)
  ctx = get_ctx()
  runtime = ScenarioWorkflowRuntime(ctx)
  _log.info("workflow.set_state scenario=%s webspace=%s state=%s", scenario_id, webspace_id, state_id)
  try:
    await runtime.set_state(scenario_id, webspace_id, state_id)
  except Exception as exc:  # pragma: no cover - defensive
    _log.warning(
      "workflow.set_state failed scenario=%s webspace=%s state=%s error=%s",
      scenario_id,
      webspace_id,
      state_id,
      exc,
      exc_info=True,
    )
