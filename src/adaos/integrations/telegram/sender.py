from __future__ import annotations
from adaos.services.chat_io.interfaces import ChatSender, ChatOutputEvent, ChatOutputMessage
from adaos.services.agent_context import get_ctx
from adaos.services.io_bus.rate_limit import PerChatLimiter
import json
import time
import urllib.request
from typing import Any


class TelegramSender(ChatSender):
    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self._token = get_ctx().settings.tg_bot_token
        self._limiter = PerChatLimiter(rate_per_sec=1.0, capacity=30)

    async def send(self, out: ChatOutputEvent) -> None:
        # TODO: respect rate-limit, idempotency
        for m in out.messages:
            await self._send_one(out, m)

    async def _send_one(self, out: ChatOutputEvent, m: ChatOutputMessage) -> None:
        chat_id = out.target.get("chat_id")
        if not chat_id or not self._token:
            return
        # rate limit per chat
        if not self._limiter.allow(chat_id):
            time.sleep(0.5)
        if m.type == "text" and m.text:
            await self._call("sendMessage", {"chat_id": chat_id, "text": m.text})
        elif m.type == "photo" and m.image_path:
            # simple caption within text if provided
            await self._call_multipart("sendPhoto", {"chat_id": chat_id}, file_field="photo", file_path=m.image_path)
        elif m.type == "voice" and m.audio_path:
            await self._call_multipart("sendVoice", {"chat_id": chat_id}, file_field="voice", file_path=m.audio_path)

    async def _call(self, method: str, payload: dict[str, Any]) -> None:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        await _with_retries(req)

    async def _call_multipart(self, method: str, fields: dict[str, Any], *, file_field: str, file_path: str) -> None:
        # very simple multipart builder
        boundary = "----AdaOSFormBoundary"
        parts: list[bytes] = []
        for k, v in fields.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f"Content-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        with open(file_path, "rb") as f:
            content = f.read()
        filename = file_path.split("/")[-1]
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f"Content-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n".encode())
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(content)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        await _with_retries(req)


async def _with_retries(req: urllib.request.Request, *, attempts: int = 3) -> None:
    backoff = 0.5
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status in (200, 201, 202):
                    return
                if resp.status in (429, 500, 502, 503, 504):
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 5.0)
                    continue
                return
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
