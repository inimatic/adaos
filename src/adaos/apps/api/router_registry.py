from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True, slots=True)
class RuntimeRouter:
    router: APIRouter
    prefix: str = ""


def runtime_routers() -> tuple[RuntimeRouter, ...]:
    """Resolve runtime routers lazily after the AgentContext is available."""
    from adaos.apps.api import (
        builder,
        io_webhooks,
        join_api,
        nlu_teacher_api,
        node_api,
        observe_api,
        operations,
        personalization,
        project_deployment,
        redevice_api,
        release_validation,
        root_endpoints,
        scenarios,
        service_ui,
        skills,
        stt_api,
        subnet_api,
        tool_bridge,
    )
    from adaos.services.subnet.link_ws import router as subnet_link_router
    from adaos.services.yjs.gateway import router as yjs_router

    return (
        RuntimeRouter(tool_bridge.router, "/api"),
        RuntimeRouter(subnet_api.router, "/api"),
        RuntimeRouter(nlu_teacher_api.router, "/api"),
        RuntimeRouter(builder.router, "/api/builder"),
        RuntimeRouter(node_api.router, "/api/node"),
        RuntimeRouter(join_api.router, "/api"),
        RuntimeRouter(personalization.router, "/api"),
        RuntimeRouter(project_deployment.router, "/api/node/project-deployment"),
        RuntimeRouter(observe_api.router, "/api/observe"),
        RuntimeRouter(operations.router, "/api/operations"),
        RuntimeRouter(release_validation.router, "/api/release-validation"),
        RuntimeRouter(scenarios.router, "/api/scenarios"),
        RuntimeRouter(skills.router, "/api/skills"),
        RuntimeRouter(service_ui.router, "/api"),
        RuntimeRouter(stt_api.router, "/api"),
        RuntimeRouter(redevice_api.router),
        RuntimeRouter(root_endpoints.router),
        RuntimeRouter(io_webhooks.router),
        RuntimeRouter(yjs_router),
        RuntimeRouter(subnet_link_router),
    )


def mount_runtime_routers(app: FastAPI) -> None:
    if bool(getattr(app.state, "runtime_routers_mounted", False)):
        return
    for registration in runtime_routers():
        app.include_router(registration.router, prefix=registration.prefix)
    app.state.runtime_routers_mounted = True
