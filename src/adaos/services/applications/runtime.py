from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from .service import ApplicationExecutor, ApplicationService
from .store import ApplicationStore


_LOCK = RLock()
_EXECUTOR: ApplicationExecutor | None = None
_OPERATION_PUBLISHER: Callable[[Mapping[str, Any]], Any] | None = None
_REPORT_SERVICE: Any | None = None
_REPORT_FACTORY: Callable[[], Any] | None = None
_DISTRIBUTION_SERVICE: Any | None = None
_DISTRIBUTION_FACTORY: Callable[[], Any] | None = None
_STABLE_SOURCE_PUBLISHER: Callable[..., Mapping[str, Any]] | None = None
_SERVICES: dict[str, ApplicationService] = {}


def register_application_executor(executor: ApplicationExecutor | None) -> None:
    global _EXECUTOR
    with _LOCK:
        _EXECUTOR = executor
        _SERVICES.clear()


def register_application_operation_publisher(
    publisher: Callable[[Mapping[str, Any]], Any] | None,
) -> None:
    global _OPERATION_PUBLISHER
    with _LOCK:
        _OPERATION_PUBLISHER = publisher
        _SERVICES.clear()


def register_development_report_service(service: Any | None) -> None:
    global _REPORT_FACTORY, _REPORT_SERVICE
    with _LOCK:
        _REPORT_SERVICE = service
        _REPORT_FACTORY = None


def register_development_report_service_factory(factory: Callable[[], Any] | None) -> None:
    global _REPORT_FACTORY, _REPORT_SERVICE
    with _LOCK:
        _REPORT_FACTORY = factory
        _REPORT_SERVICE = None


def register_application_distribution_service(service: Any | None) -> None:
    global _DISTRIBUTION_FACTORY, _DISTRIBUTION_SERVICE
    with _LOCK:
        _DISTRIBUTION_SERVICE = service
        _DISTRIBUTION_FACTORY = None


def register_application_distribution_service_factory(
    factory: Callable[[], Any] | None,
) -> None:
    global _DISTRIBUTION_FACTORY, _DISTRIBUTION_SERVICE
    with _LOCK:
        _DISTRIBUTION_FACTORY = factory
        _DISTRIBUTION_SERVICE = None


def get_application_distribution_service() -> Any:
    global _DISTRIBUTION_SERVICE
    with _LOCK:
        if _DISTRIBUTION_SERVICE is None and _DISTRIBUTION_FACTORY is not None:
            _DISTRIBUTION_SERVICE = _DISTRIBUTION_FACTORY()
        if _DISTRIBUTION_SERVICE is None:
            raise RuntimeError("Application distribution service is not configured")
        return _DISTRIBUTION_SERVICE


def register_stable_source_publisher(
    publisher: Callable[..., Mapping[str, Any]] | None,
) -> None:
    global _STABLE_SOURCE_PUBLISHER
    with _LOCK:
        _STABLE_SOURCE_PUBLISHER = publisher


def get_stable_source_publisher() -> Callable[..., Mapping[str, Any]]:
    with _LOCK:
        if _STABLE_SOURCE_PUBLISHER is None:
            raise RuntimeError("stable source publisher is not configured")
        return _STABLE_SOURCE_PUBLISHER


def get_development_report_service() -> Any:
    global _REPORT_SERVICE
    with _LOCK:
        if _REPORT_SERVICE is None and _REPORT_FACTORY is not None:
            _REPORT_SERVICE = _REPORT_FACTORY()
        if _REPORT_SERVICE is None:
            raise RuntimeError("Development Report service is not configured")
        return _REPORT_SERVICE


def create_local_development_report_service(
    state_dir: Path,
    *,
    subnet_ref: str,
    zone_id: str,
    display_name: str | None = None,
) -> Any:
    from .development_reports import DevelopmentReportService
    from .report_directory import SubnetKeyDirectoryAuthority, SubnetKeyDirectoryClient
    from .report_keys import SubnetPurposeKeyStore
    from .report_relay import DurableDevelopmentReportRelay
    from .report_classifier import OciDevelopmentReportClassifier

    root = Path(state_dir).expanduser().resolve()
    keys = SubnetPurposeKeyStore(root)
    keys.ensure_key(subnet_ref, "message_signing")
    keys.ensure_key(subnet_ref, "message_encryption")
    authority = SubnetKeyDirectoryAuthority(root, zone_id=zone_id)
    projection = authority.publish_subnet(
        subnet_ref,
        home_zone=zone_id,
        keys=keys.list_public(subnet_ref),
        display_name=display_name,
    )
    directory = SubnetKeyDirectoryClient()
    directory.update(projection)
    classifier_image = str(
        os.getenv("ADAOS_DEVELOPMENT_REPORT_CLASSIFIER_IMAGE") or ""
    ).strip()
    classifier = (
        OciDevelopmentReportClassifier(
            state_root=root / "applications" / "development_reports",
            image=classifier_image,
            runtime=str(
                os.getenv("ADAOS_DEVELOPMENT_REPORT_CLASSIFIER_RUNTIME") or "docker"
            ),
        )
        if classifier_image
        else None
    )
    return DevelopmentReportService(
        root,
        subnet_ref=subnet_ref,
        application_store=get_application_service(root).store,
        key_store=keys,
        directory=directory,
        relay=DurableDevelopmentReportRelay(root, zone_id=zone_id, directory=directory),
        classifier=classifier,
    )


def get_application_service(state_dir: Path) -> ApplicationService:
    key = str(Path(state_dir).expanduser().resolve())
    with _LOCK:
        service = _SERVICES.get(key)
        if service is None:
            service = ApplicationService(
                ApplicationStore(Path(key)),
                executor=_EXECUTOR,
                operation_publisher=_OPERATION_PUBLISHER,
            )
            _SERVICES[key] = service
        return service


__all__ = [
    "get_application_service",
    "get_application_distribution_service",
    "get_development_report_service",
    "get_stable_source_publisher",
    "create_local_development_report_service",
    "register_application_executor",
    "register_application_distribution_service",
    "register_application_distribution_service_factory",
    "register_application_operation_publisher",
    "register_development_report_service",
    "register_development_report_service_factory",
    "register_stable_source_publisher",
]
