from __future__ import annotations
from fastapi import APIRouter, HTTPException
import asyncio
import os
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI
from openai import OpenAIError

router = APIRouter(prefix="/v1/root", tags=["root"])


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str = Field(default="gpt-4o-mini", description="LLM model identifier")
    messages: list[ChatMessage]
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=None, ge=0, le=1)


def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
    return OpenAI(api_key=api_key)


@router.post("/register")
def root_register() -> dict:
    """Legacy bootstrap endpoint has been replaced by owner-based flow."""
    raise RuntimeError("legacy endpoint removed; use owner login flow")


@router.post("/llm/chat")
async def llm_chat(payload: ChatRequest) -> dict:
    client = _get_openai_client()
    request_payload: dict = {
        "model": payload.model,
        "messages": [msg.model_dump() for msg in payload.messages],
    }
    if payload.temperature is not None:
        request_payload["temperature"] = payload.temperature
    if payload.max_tokens is not None:
        request_payload["max_tokens"] = payload.max_tokens
    if payload.top_p is not None:
        request_payload["top_p"] = payload.top_p

    try:
        completion = await asyncio.to_thread(client.chat.completions.create, **request_payload)
    except OpenAIError as exc:
        status = getattr(exc, "status_code", None) or 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - safety net for unexpected SDK errors
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc}") from exc

    return completion.model_dump()
