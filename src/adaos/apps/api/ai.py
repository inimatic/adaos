"""Internal service transport: never accepts a user-supplied owner or consumer."""
from __future__ import annotations

import functools
import anyio
from fastapi import APIRouter, HTTPException, Request
from adaos.services.ai.bridge import invoke_service

router = APIRouter()


@router.post('', include_in_schema=False)
async def invoke(request: Request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 1100 * 1024:
            raise HTTPException(413, 'ai_request_too_large')
    try:
        import json
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {'operation', 'arguments'}:
            raise ValueError('ai_envelope_invalid')
        return await anyio.to_thread.run_sync(functools.partial(invoke_service,
            str(request.headers.get('x-adaos-ai-token') or ''),
            str(request.client.host if request.client else ''), body['operation'], body['arguments']))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, 'ai_attempt_not_found') from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else 'ai_request_invalid') from exc
