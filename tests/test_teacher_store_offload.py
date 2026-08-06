from __future__ import annotations

import threading

import pytest

from adaos.services.nlu import teacher_store_runtime


@pytest.mark.asyncio
async def test_teacher_ready_rehydration_reads_persisted_state_off_event_loop(monkeypatch) -> None:
    owner_thread = threading.get_ident()
    load_threads: list[int] = []

    def _load_teacher_state(*, webspace_id: str):
        load_threads.append(threading.get_ident())
        return {}

    async def _read_teacher(webspace_id: str):
        return {}

    monkeypatch.setattr(teacher_store_runtime, "load_teacher_state", _load_teacher_state)
    monkeypatch.setattr(teacher_store_runtime, "_read_teacher_from_ydoc", _read_teacher)

    await teacher_store_runtime._on_sys_ready({"webspace_id": "desktop"})

    assert load_threads
    assert load_threads[0] != owner_thread
