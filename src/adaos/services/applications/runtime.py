from __future__ import annotations

from pathlib import Path
from threading import RLock

from .service import ApplicationExecutor, ApplicationService
from .store import ApplicationStore


_LOCK = RLock()
_EXECUTOR: ApplicationExecutor | None = None
_SERVICES: dict[str, ApplicationService] = {}


def register_application_executor(executor: ApplicationExecutor | None) -> None:
    global _EXECUTOR
    with _LOCK:
        _EXECUTOR = executor
        _SERVICES.clear()


def get_application_service(state_dir: Path) -> ApplicationService:
    key = str(Path(state_dir).expanduser().resolve())
    with _LOCK:
        service = _SERVICES.get(key)
        if service is None:
            service = ApplicationService(ApplicationStore(Path(key)), executor=_EXECUTOR)
            _SERVICES[key] = service
        return service


__all__ = ["get_application_service", "register_application_executor"]
