"""Authenticated same-origin gateway for optional service-skill browser UIs."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from adaos.apps.api.auth import ensure_token, resolve_presented_token
from adaos.services.skill.service_supervisor import ServiceSpec, get_service_supervisor


router = APIRouter()

_COOKIE_NAME = "adaos_service_ui"
_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_REQUEST_HEADERS = {"accept", "accept-language", "content-type", "if-none-match", "if-modified-since", "range", "user-agent"}
_RESPONSE_HEADERS = {"cache-control", "content-disposition", "content-language", "content-range", "content-type", "etag", "last-modified"}


def _presented_token(request: Request) -> str | None:
    authorization = str(request.headers.get("authorization") or "").strip() or None
    header_token = str(request.headers.get("x-adaos-token") or "").strip() or None
    query_token = str(request.query_params.get("token") or "").strip() or None
    cookie_token = str(request.cookies.get(_COOKIE_NAME) or "").strip() or None
    return resolve_presented_token(
        x_adaos_token=header_token,
        authorization=authorization,
        query_token=query_token or cookie_token,
    )


def _authorize(request: Request) -> None:
    ensure_token(_presented_token(request))
    fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site == "cross-site":
        raise HTTPException(status_code=403, detail="cross-site service UI access is forbidden")
    origin = str(request.headers.get("origin") or "").strip()
    if origin:
        supplied = urlsplit(origin)
        expected = urlsplit(str(request.base_url))
        if (supplied.scheme.lower(), supplied.netloc.lower()) != (
            expected.scheme.lower(),
            expected.netloc.lower(),
        ):
            raise HTTPException(status_code=403, detail="service UI origin is forbidden")


def _service_spec(name: str) -> ServiceSpec:
    supervisor = get_service_supervisor()
    spec = supervisor._specs.get(name)  # Core gateway and supervisor share this trust boundary.
    if spec is None or not spec.ui_enabled:
        raise HTTPException(status_code=404, detail="service UI surface not found")
    if spec.ui_access != "authenticated" or spec.ui_origin_policy != "same-origin":
        raise HTTPException(status_code=403, detail="service UI policy is not admissible")
    return spec


def _upstream_url(spec: ServiceSpec, path: str, request: Request) -> str:
    segments = [segment for segment in str(path or "").replace("\\", "/").split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        raise HTTPException(status_code=400, detail="invalid service UI path")
    prefix = spec.ui_path.rstrip("/")
    suffix = "/".join(segments)
    if suffix:
        url = f"{spec.base_url}{prefix}/{suffix}"
    else:
        # The gateway's public surface is rooted at ``.../ui/``. Preserve that
        # directory semantic upstream as well; applications mounted under a
        # static prefix commonly redirect ``/prefix`` to ``/prefix/``.
        url = f"{spec.base_url}{prefix}/" if prefix else f"{spec.base_url}/"
    query = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
    return f"{url}?{urlencode(query, doseq=True)}" if query else url


def _proxy_location(name: str, location: str, spec: ServiceSpec) -> str:
    value = str(location or "").strip()
    if not value:
        return value
    base = spec.base_url.rstrip("/")
    if value.startswith(base):
        value = value[len(base):]
    if value.startswith("/"):
        prefix = spec.ui_path.rstrip("/")
        if prefix and value.startswith(prefix):
            value = value[len(prefix):] or "/"
        return f"/api/services/{name}/ui/{value.lstrip('/')}"
    return value


@router.get("/services/{name}/ui-surface")
async def service_ui_surface(name: str, request: Request) -> dict[str, Any]:
    _authorize(request)
    supervisor = get_service_supervisor()
    await supervisor.refresh_discovered()
    surface = supervisor.ui_surface(name, check_health=True)
    if surface is None:
        raise HTTPException(status_code=404, detail="service UI surface not found")
    return {"ok": True, "surface": surface}


@router.get("/services/{name}/ui-bootstrap")
async def service_ui_bootstrap(
    name: str,
    request: Request,
    fragment: str | None = None,
) -> Response:
    _authorize(request)
    await get_service_supervisor().refresh_discovered()
    spec = _service_spec(name)
    if spec.ui_embedding not in {"external", "same-origin"}:
        raise HTTPException(status_code=403, detail="service UI embedding is disabled")
    ui_fragment = str(fragment or "")
    if ui_fragment:
        if (
            len(ui_fragment) > 2048
            or not ui_fragment.startswith("#/")
            or any(ord(char) < 32 or ord(char) == 127 for char in ui_fragment)
        ):
            raise HTTPException(status_code=400, detail="invalid service UI fragment")
    response = RedirectResponse(
        f"/api/services/{name}/ui/{ui_fragment}",
        status_code=303,
    )
    response.set_cookie(
        _COOKIE_NAME,
        _presented_token(request) or "",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path=f"/api/services/{name}/ui",
        max_age=300,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.api_route(
    "/services/{name}/ui/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def service_ui_proxy(name: str, path: str, request: Request) -> Response:
    _authorize(request)
    await get_service_supervisor().refresh_discovered()
    spec = _service_spec(name)
    status = get_service_supervisor().status(name, check_health=True) or {}
    if not bool(status.get("health_ok")):
        raise HTTPException(status_code=503, detail="service UI is unavailable")
    body = await request.body()
    if len(body) > spec.ui_max_request_bytes:
        raise HTTPException(status_code=413, detail="service UI request exceeds the admitted limit")
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _REQUEST_HEADERS and key.lower() not in _HOP_HEADERS
    }
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
            upstream = await client.request(
                request.method,
                _upstream_url(spec, path, request),
                content=body or None,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"service UI upstream unavailable: {type(exc).__name__}") from exc
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() in _RESPONSE_HEADERS and key.lower() not in _HOP_HEADERS
    }
    if upstream.headers.get("location"):
        response_headers["Location"] = _proxy_location(name, upstream.headers["location"], spec)
    response_headers.update(
        {
            "Content-Security-Policy": spec.ui_content_security_policy,
            "X-Frame-Options": "SAMEORIGIN",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        }
    )
    redirect_body = bool(upstream.headers.get("location")) and 300 <= upstream.status_code < 400
    return Response(
        # Redirect bodies are provider-generated HTML and can contain an
        # absolute loopback URL even after Location has been rewritten.
        content=b"" if request.method == "HEAD" or redirect_body else upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


__all__ = ["router"]
