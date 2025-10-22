from __future__ import annotations
from adaos.services.chat_io.interfaces import ChatSender, ChatOutputEvent, ChatOutputMessage


class TelegramSender(ChatSender):
    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        # TODO: init HTTP client/token/limiter

    async def send(self, out: ChatOutputEvent) -> None:
        # TODO: respect rate-limit, idempotency
        for m in out.messages:
            await self._send_one(out, m)

    async def _send_one(self, out: ChatOutputEvent, m: ChatOutputMessage) -> None:
        # TODO: sendMessage / editMessageText / sendVoice / sendPhoto
        # пока заглушка
        return
