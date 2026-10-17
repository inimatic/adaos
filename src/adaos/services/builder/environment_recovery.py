"""Classify structured environment causes without interpreting provider text."""
from __future__ import annotations

import errno


def environment_interruption(error: BaseException) -> dict | None:
    pending = [error]
    seen: set[int] = set()
    while pending and len(seen) < 64:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        number, windows = getattr(current, "errno", None), getattr(current, "winerror", None)
        reason = instruction = None
        if number in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)} or windows in {39, 112}:
            reason, instruction = "storage_capacity", "Недостаточно места для завершения. Освободите место на диске."
        elif number == errno.EROFS:
            reason, instruction = "storage_read_only", "Хранилище недоступно для записи. Восстановите доступ к нему."
        elif number in {errno.ECONNREFUSED, errno.ENETUNREACH, errno.ETIMEDOUT} or windows == 10061:
            reason, instruction = "runtime_unavailable", "Не удалось связаться с runtime. Восстановите его доступность."
        if reason:
            return {"reason": reason, "instruction": instruction, "errno": number, "winerror": windows}
        # Requests -> urllib3 -> native socket error can span all four links.
        for candidate in (current.__cause__, current.__context__, getattr(current, "reason", None), *current.args):
            if isinstance(candidate, BaseException) and id(candidate) not in seen:
                pending.append(candidate)
    return None


def connection_was_refused(error: BaseException) -> bool:
    details = environment_interruption(error)
    return bool(details and (details["errno"] == errno.ECONNREFUSED or details["winerror"] == 10061))
