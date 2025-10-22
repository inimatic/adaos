from __future__ import annotations
from typing import Dict, Any, Optional


async def issue_pair_code(*, bot_id: str, hub_id: Optional[str], ttl_sec: int) -> Dict[str, Any]:
    # TODO: persist in SQLite (pair_codes)
    return {"pair_code": None, "deep_link": None, "qr_path": None, "expires_at": None}


async def confirm_pair_code(*, code: str, platform_user: Dict[str, Any]) -> Dict[str, Any]:
    # TODO: validate+bind in SQLite (chat_bindings)
    return {"ok": True, "hub_id": None, "ada_user_id": None}


async def pair_status(*, code: str) -> Dict[str, Any]:
    # TODO: read pair state from SQLite
    return {"state": "unknown"}


async def revoke_pair_code(*, code: str) -> Dict[str, Any]:
    # TODO: mark revoked
    return {"ok": True}
