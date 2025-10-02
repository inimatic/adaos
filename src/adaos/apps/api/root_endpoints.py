from __future__ import annotations
from fastapi import APIRouter

router = APIRouter(prefix="/v1/root", tags=["root"])


@router.post("/register")
def root_register() -> dict:
    """Legacy bootstrap endpoint has been replaced by owner-based flow."""
    raise RuntimeError("legacy endpoint removed; use owner login flow")


@router.post("/llm/chat")
def llm_chat() -> dict:
    raise RuntimeError("legacy endpoint removed")
