# src\adaos\api\tool_bridge.py
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from pydantic import BaseModel, Field
from typing import Any, Dict

from adaos.apps.api.auth import require_token
from adaos.services.observe import attach_http_trace_headers
from adaos.services.agent_context import get_ctx, AgentContext
from adaos.services.skill.manager import SkillManager
from adaos.adapters.db import SqliteSkillRegistry


router = APIRouter()


class ToolCall(BaseModel):
    """
    Вызов инструмента навыка:
      tool: "<skill_name>:<public_tool_name>"
      arguments: {...}  # опционально
      context:   {...}  # опционально (резерв на будущее)
    """

    tool: str
    arguments: Dict[str, Any] | None = None
    context: Dict[str, Any] | None = None
    timeout: float | None = Field(default=None)
    dev: bool = Field(default=False, description="Run tool from DEV workspace instead of installed runtime")
    model_config = {"extra": "ignore"}


@router.post("/tools/call", dependencies=[Depends(require_token)])
async def call_tool(body: ToolCall, request: Request, response: Response, ctx: AgentContext = Depends(get_ctx)):
    # Разбираем "<skill_name>:<public_tool_name>"
    if ":" not in body.tool:
        raise HTTPException(status_code=400, detail="tool must be in '<skill_name>:<public_tool_name>' format")

    skill_name, public_tool = body.tool.split(":", 1)
    if not skill_name or not public_tool:
        raise HTTPException(status_code=400, detail="invalid tool spec")

    # Используем общий путь исполнения как в CLI (SkillManager.run_tool)
    mgr = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )

    trace = attach_http_trace_headers(request.headers, response.headers)
    payload: Dict[str, Any] = body.arguments or {}
    try:
        if body.dev:
            result = mgr.run_dev_tool(skill_name, public_tool, payload, timeout=body.timeout)
        else:
            result = mgr.run_tool(skill_name, public_tool, payload, timeout=body.timeout)
    except KeyError as e:
        # неверное имя инструмента или отсутствует дефолтный инструмент
        raise HTTPException(status_code=404, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"run failed: {type(e).__name__}: {e}")

    return {"ok": True, "result": result, "trace_id": trace}
