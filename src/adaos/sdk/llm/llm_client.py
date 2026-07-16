from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
import json
import logging
import os
import time
from urllib.parse import quote

import requests
from adaos.services.agent_context import get_ctx
from adaos.services.root.client import RootHttpClient, RootHttpError


_LOG = logging.getLogger("adaos.sdk.llm")
_ROOT_LLM_HEALTH_CACHE: dict[str, tuple[float, bool, str]] = {}


def _env_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    items: list[str] = []
    for raw in str(value).replace(";", ",").split(","):
        item = raw.strip()
        if item:
            items.append(item)
    return tuple(dict.fromkeys(items))


def _normalize_root_base_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _json_size_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8", errors="replace"))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    try:
        value = float(raw) if raw else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), min(float(value), float(maximum)))


def _env_enabled(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _current_ctx() -> Any | None:
    try:
        return get_ctx()
    except Exception:
        return None


def _current_node_config(ctx: Any | None) -> Any | None:
    if ctx is None:
        return None
    cfg = getattr(ctx, "config", None)
    if cfg is not None:
        return cfg
    try:
        from adaos.services.node_config import load_config

        return load_config(ctx=ctx)
    except Exception:
        return None


def _root_base_url_for_ctx(ctx: Any | None, cfg: Any | None = None) -> str:
    root_settings = getattr(cfg, "root_settings", None) if cfg is not None else None
    settings = getattr(ctx, "settings", None) if ctx is not None else None
    base = str(
        os.getenv("ADAOS_ROOT_LLM_BASE_URL")
        or os.getenv("ADAOS_LLM_ROOT_BASE_URL")
        or getattr(root_settings, "base_url", None)
        or getattr(settings, "api_base", None)
        or ""
    ).strip()
    return base.rstrip("/") or "https://api.inimatic.com"


def _root_llm_base_urls(primary: RootHttpClient, *, prefer_global: bool = False) -> list[str]:
    urls: list[str] = []

    def add(value: str | None) -> None:
        url = _normalize_root_base_url(value)
        if url and url not in urls:
            urls.append(url)

    explicit_primary = os.getenv("ADAOS_ROOT_LLM_BASE_URL") or os.getenv("ADAOS_LLM_ROOT_BASE_URL")
    primary_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    global_url = "https://api.inimatic.com"
    if explicit_primary:
        add(explicit_primary)
        add(primary_url)
    elif (
        prefer_global
        and primary_url
        and primary_url != global_url
        and primary_url.endswith(".api.inimatic.com")
    ):
        add(global_url)
        add(primary_url)
    else:
        add(primary_url)
    for item in _env_csv(os.getenv("ADAOS_ROOT_LLM_FALLBACK_BASE_URLS")):
        add(item)

    if (
        primary_url
        and primary_url != global_url
        and primary_url.endswith(".api.inimatic.com")
    ):
        add(global_url)
    return urls or [global_url]


def _root_llm_health_probe(
    http: RootHttpClient,
    base_url: str,
    *,
    timeout: float | None = None,
) -> tuple[bool, str]:
    """
    Probe a root endpoint before a job submit when another root is available.

    A dead regional root can otherwise hold the Builder chat for the full
    /v1/llm/jobs submit timeout while the TLS handshake stalls. The cache keeps
    repeated Builder turns from paying even the short probe timeout.
    """
    if not _env_enabled("ADAOS_ROOT_LLM_HEALTHCHECK", True):
        return True, "disabled"
    now = time.monotonic()
    cached = _ROOT_LLM_HEALTH_CACHE.get(base_url)
    if cached is not None:
        expires_at, ok, detail = cached
        if expires_at > now:
            return ok, detail
    probe_timeout = timeout if timeout is not None else _env_float(
        "ADAOS_ROOT_LLM_HEALTHCHECK_TIMEOUT_S",
        1.0,
        minimum=0.25,
        maximum=10.0,
    )
    try:
        raw = http.request("GET", "/v1/health", timeout=probe_timeout)
        ok = not (isinstance(raw, Mapping) and raw.get("ok") is False)
        detail = "ok" if ok else "health_not_ok"
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    ttl = _env_float(
        "ADAOS_ROOT_LLM_HEALTHCHECK_TTL_S" if ok else "ADAOS_ROOT_LLM_UNHEALTHY_TTL_S",
        30.0 if ok else 60.0,
        minimum=1.0,
        maximum=3600.0,
    )
    _ROOT_LLM_HEALTH_CACHE[base_url] = (now + ttl, ok, detail)
    return ok, detail


def _mark_root_llm_submit_unhealthy(base_url: str, detail: str) -> None:
    ttl = _env_float(
        "ADAOS_ROOT_LLM_UNHEALTHY_TTL_S",
        60.0,
        minimum=1.0,
        maximum=3600.0,
    )
    _ROOT_LLM_HEALTH_CACHE[_normalize_root_base_url(base_url)] = (
        time.monotonic() + ttl,
        False,
        str(detail or "submit_failed"),
    )


def _root_llm_poll_error_label(exc: Exception) -> str:
    if isinstance(exc, RootHttpError):
        status_code = int(getattr(exc, "status_code", 0) or 0)
        return _root_http_error_code(exc) or (f"http_{status_code}" if status_code else type(exc).__name__)
    return type(exc).__name__


def _retryable_llm_job_poll_error(exc: Exception) -> bool:
    if not isinstance(exc, RootHttpError):
        return False
    status_code = int(getattr(exc, "status_code", 0) or 0)
    if status_code in {0, 408, 429, 502, 503, 504}:
        return True
    code = _root_http_error_code(exc)
    if code in {"llm_proxy_upstream_failed"}:
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "bad gateway" in text or "temporarily unavailable" in text


def _root_http_client(ctx: Any | None = None) -> tuple[RootHttpClient, Any | None]:
    ctx = ctx if ctx is not None else _current_ctx()
    cfg = _current_node_config(ctx)
    base_url = _root_base_url_for_ctx(ctx, cfg)
    verify: str | bool = True
    cert_tuple: tuple[str, str] | None = None
    try:
        ca_path = cfg.ca_cert_path() if cfg is not None and hasattr(cfg, "ca_cert_path") else None
        verify_ca = str(os.getenv("ADAOS_ROOT_VERIFY_CA") or os.getenv("ADAOS_LLM_ROOT_VERIFY_CA") or "0").lower()
        if verify_ca in {"1", "true", "yes", "on"} and ca_path is not None and Path(ca_path).exists():
            verify = str(ca_path)
    except Exception:
        verify = True
    try:
        cert_path = cfg.hub_cert_path() if cfg is not None and hasattr(cfg, "hub_cert_path") else None
        key_path = cfg.hub_key_path() if cfg is not None and hasattr(cfg, "hub_key_path") else None
        if cert_path is not None and key_path is not None and Path(cert_path).exists() and Path(key_path).exists():
            cert_tuple = (str(cert_path), str(key_path))
    except Exception:
        cert_tuple = None
    return RootHttpClient(base_url=base_url, verify=verify, cert=cert_tuple), cfg


def _llm_endpoint() -> str:
    override = os.getenv("ADAOS_LLM_ENDPOINT")
    if override:
        return override
    return f"{get_ctx().settings.api_base}/v1/llm/response"


def _llm_models_endpoint() -> str:
    """
    Use the same base as _llm_endpoint (Root LLM proxy), but with /models.

    This ensures we go through the same mtls / token path as /v1/llm/response
    instead of calling upstream APIs directly from the hub.
    """
    override = os.getenv("ADAOS_LLM_MODELS_ENDPOINT")
    if override:
        return override
    base_response = _llm_endpoint()
    # Strip trailing '/response' if present.
    if base_response.endswith("/response"):
        base = base_response.rsplit("/", 1)[0]
    else:
        base = base_response.rstrip("/")
    return f"{base}/models"


def _llm_jobs_endpoint() -> str:
    override = os.getenv("ADAOS_LLM_JOBS_ENDPOINT")
    if override:
        return override.rstrip("/")
    base_response = _llm_endpoint()
    if base_response.endswith("/response"):
        base = base_response.rsplit("/", 1)[0]
    else:
        base = base_response.rstrip("/")
    return f"{base}/jobs"


def _llm_job_endpoint(job_id: str) -> str:
    return f"{_llm_jobs_endpoint().rstrip('/')}/{job_id.strip()}"


def _auth_headers() -> Dict[str, str]:
    token = os.getenv("ADAOS_LLM_TOKEN") or os.getenv("ADAOS_ROOT_TOKEN") or os.getenv("ADAOS_TOKEN") or "dev-local-token"
    headers = {
        "X-AdaOS-Token": token,
        "Content-Type": "application/json",
    }
    try:
        ctx = get_ctx()
    except Exception:
        ctx = None
    settings = getattr(ctx, "settings", None) if ctx is not None else None
    config = getattr(ctx, "config", None) if ctx is not None else None
    subnet_id = str(
        os.getenv("ADAOS_SUBNET_ID")
        or getattr(settings, "subnet_id", None)
        or getattr(config, "subnet_id", None)
        or ""
    ).strip()
    node_id = str(getattr(config, "node_id", None) or "").strip()
    if subnet_id:
        headers["X-AdaOS-Subnet-Id"] = subnet_id
    if node_id:
        headers["X-AdaOS-Node-Id"] = node_id
    return headers


def _identity_headers(ctx: Any | None = None, cfg: Any | None = None) -> Dict[str, str]:
    ctx = ctx if ctx is not None else _current_ctx()
    cfg = cfg if cfg is not None else _current_node_config(ctx)
    settings = getattr(ctx, "settings", None) if ctx is not None else None
    config = getattr(ctx, "config", None) if ctx is not None else None
    subnet_id = str(
        os.getenv("ADAOS_SUBNET_ID")
        or getattr(config, "subnet_id", None)
        or getattr(config, "subnet_id_value", None)
        or getattr(cfg, "subnet_id", None)
        or getattr(cfg, "subnet_id_value", None)
        or getattr(settings, "subnet_id", None)
        or ""
    ).strip()
    node_id = str(
        os.getenv("ADAOS_NODE_ID")
        or getattr(config, "node_id", None)
        or getattr(cfg, "node_id", None)
        or ""
    ).strip()
    headers: Dict[str, str] = {}
    if subnet_id:
        headers["X-AdaOS-Subnet-Id"] = subnet_id
    if node_id:
        headers["X-AdaOS-Node-Id"] = node_id
    return headers


def _root_http_error_code(exc: RootHttpError) -> str:
    if exc.error_code:
        return str(exc.error_code)
    payload = exc.payload
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("code"), str):
            return str(error.get("code") or "")
        detail = payload.get("detail")
        if isinstance(detail, Mapping) and isinstance(detail.get("code"), str):
            return str(detail.get("code") or "")
        for key in ("code", "error"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return ""


def _should_retry_llm_proxy(exc: Exception) -> bool:
    if not isinstance(exc, RootHttpError):
        return False
    if int(getattr(exc, "status_code", 0) or 0) in {0, 502, 503, 504}:
        return True
    code = _root_http_error_code(exc)
    if code in {"llm_proxy_upstream_failed", "openai_api_key_missing", "unsupported_country_region_territory"}:
        return True
    text = str(exc)
    return (
        "llm_proxy_upstream_failed" in text
        or "unsupported_country_region_territory" in text
        or "openai_api_key_missing" in text
    )


def _legacy_http_enabled() -> bool:
    return bool(os.getenv("ADAOS_LLM_ENDPOINT"))


def list_llm_models(*, timeout: float | None = None, scope: str | None = None) -> Dict[str, Any]:
    """
    Fetch available LLM models from the Root LLM proxy.
    """
    params = {"scope": scope} if isinstance(scope, str) and scope.strip() else None
    if _legacy_http_enabled() or os.getenv("ADAOS_LLM_MODELS_ENDPOINT"):
        resp = requests.get(_llm_models_endpoint(), headers=_auth_headers(), params=params, timeout=timeout or 30)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json() if resp.text else {}
        return data

    ctx = _current_ctx()
    primary, cfg = _root_http_client(ctx)
    headers = _identity_headers(ctx, cfg)
    base_urls = _root_llm_base_urls(primary)
    last_exc: Exception | None = None
    primary_base_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    for index, base_url in enumerate(base_urls):
        http = (
            primary
            if (primary_base_url and primary_base_url == base_url) or (not primary_base_url and index == 0)
            else RootHttpClient(
                base_url=base_url,
                verify=getattr(primary, "verify", True),
                cert=getattr(primary, "cert", None),
            )
        )
        try:
            path = "/v1/llm/models"
            if params:
                path = f"{path}?scope={quote(str(params['scope']))}"
            raw = http.request("GET", path, headers=headers or None, timeout=timeout or 30)
            if isinstance(raw, Mapping):
                return dict(raw)
            if isinstance(raw, list):
                return {"data": list(raw)}
            return {"data": raw}
        except Exception as exc:
            last_exc = exc
            if index + 1 < len(base_urls) and _should_retry_llm_proxy(exc):
                _LOG.warning("root LLM models attempt failed base_url=%s; trying fallback error=%s", base_url, exc)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return {}


def _extract_output_text(payload: Mapping[str, Any]) -> Optional[str]:
    if not payload:
        return None
    meta = payload.get("metadata") or {}
    if isinstance(meta, Mapping):
        direct = meta.get("output_text")
        if isinstance(direct, str):
            return direct
    direct_root = payload.get("output_text")
    if isinstance(direct_root, str):
        return direct_root
    output = payload.get("output")
    if isinstance(output, Iterable):
        chunks: list[str] = []
        for block in output:
            if not isinstance(block, Mapping):
                continue
            for part in block.get("content") or []:
                if not isinstance(part, Mapping):
                    continue
                text_val = part.get("text") or part.get("output_text") or part.get("content")
                if isinstance(text_val, str):
                    chunks.append(text_val)
        if chunks:
            return "".join(chunks)
    return None


def _extract_job_output_text(payload: Mapping[str, Any]) -> Optional[str]:
    direct = _extract_output_text(payload)
    if direct:
        return direct
    response = payload.get("response")
    if isinstance(response, Mapping):
        return _extract_output_text(response)
    return None


def _message_list(messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    return [{"role": str(msg.get("role", "user") or "user"), "content": str(msg.get("content", "") or "")} for msg in messages]


def _responses_payload(base_payload: Mapping[str, Any], messages: list[Mapping[str, str]]) -> Dict[str, Any]:
    payload = dict(base_payload)
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user").strip().lower()
        content = str(msg.get("content") or "")
        if not content.strip():
            continue
        if role in {"system", "developer"}:
            instructions.append(content.strip())
            continue
        normalized_role = "assistant" if role == "assistant" else "user"
        input_items.append(
            {
                "role": normalized_role,
                "content": [
                    {
                        "type": "output_text" if normalized_role == "assistant" else "input_text",
                        "text": content,
                    }
                ],
            }
        )
    if instructions and not payload.get("instructions"):
        payload["instructions"] = "\n\n".join(instructions)
    payload["input"] = input_items if input_items else ""
    if "max_tokens" in payload and "max_output_tokens" not in payload:
        payload["max_output_tokens"] = payload.pop("max_tokens")
    return payload


def _llm_root_payload(
    messages: Iterable[Mapping[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    reasoning: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    stream: bool | None = None,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
    stream_protocol: str | None = None,
) -> tuple[Dict[str, Any], list[Mapping[str, str]]]:
    normalized_messages = _message_list(messages)
    payload: Dict[str, Any] = {
        "model": model or os.getenv("ADAOS_LLM_MODEL") or "gpt-4o-mini",
        "messages": normalized_messages,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if isinstance(reasoning, Mapping) and reasoning:
        payload["reasoning"] = dict(reasoning)
    req_id = str(request_id or "").strip()
    if req_id:
        payload["request_id"] = req_id
    if stream is not None:
        payload["stream"] = bool(stream)
    cache_key = str(prompt_cache_key or "").strip()
    if cache_key:
        payload["prompt_cache_key"] = cache_key
    cache_retention = str(prompt_cache_retention or "").strip()
    if cache_retention:
        payload["prompt_cache_retention"] = cache_retention
    protocol = str(stream_protocol or "").strip().lower()
    if protocol:
        payload["stream_protocol"] = protocol
    return _responses_payload(payload, normalized_messages), normalized_messages


def _root_http_for_base(primary: RootHttpClient, primary_base_url: str, base_url: str, index: int) -> RootHttpClient:
    if (primary_base_url and primary_base_url == base_url) or (not primary_base_url and index == 0):
        return primary
    return RootHttpClient(
        base_url=base_url,
        verify=getattr(primary, "verify", True),
        cert=getattr(primary, "cert", None),
    )


def _llm_proxy_protocol(data: Dict[str, Any], *, base_url: str, fallback: bool, attempts: list[dict[str, Any]]) -> None:
    protocol = data.setdefault("_protocol", {})
    if isinstance(protocol, dict):
        protocol["llm_proxy"] = {
            "base_url": base_url,
            "fallback": fallback,
            "attempts": list(attempts),
        }


def send_response(
    messages: Iterable[Mapping[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    reasoning: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    stream: bool | None = None,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
    stream_protocol: str | None = None,
    timeout: float | None = None,
) -> Dict[str, Any]:
    """
    Send a message batch to the Root LLM proxy (Responses API wrapper).

    Returns dict with raw response plus convenience "output_text" field.
    """
    root_payload, normalized_messages = _llm_root_payload(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        reasoning=reasoning,
        request_id=request_id,
        stream=stream,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
        stream_protocol=stream_protocol,
    )

    if _legacy_http_enabled():
        resp = requests.post(_llm_endpoint(), json=root_payload, headers=_auth_headers(), timeout=timeout or 45)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json() if resp.text else {}
        data["output_text"] = _extract_output_text(data)
        return data

    ctx = _current_ctx()
    primary, cfg = _root_http_client(ctx)
    headers = _identity_headers(ctx, cfg)
    base_urls = _root_llm_base_urls(primary)
    last_exc: Exception | None = None
    primary_base_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    attempts: list[dict[str, Any]] = []
    for index, base_url in enumerate(base_urls):
        http = _root_http_for_base(primary, primary_base_url, base_url, index)
        try:
            raw = http.request(
                "POST",
                "/v1/llm/response",
                json=root_payload,
                headers=headers or None,
                timeout=timeout or 45,
            )
            data = dict(raw) if isinstance(raw, Mapping) else {"result": raw}
            _llm_proxy_protocol(data, base_url=base_url, fallback=index > 0, attempts=attempts)
            data["output_text"] = _extract_output_text(data)
            return data
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, RootHttpError):
                status_code = int(getattr(exc, "status_code", 0) or 0)
                error_label = _root_http_error_code(exc) or (
                    f"http_{status_code}" if status_code else type(exc).__name__
                )
            else:
                error_label = type(exc).__name__
            attempts.append({"base_url": base_url, "error": error_label})
            if index + 1 < len(base_urls) and _should_retry_llm_proxy(exc):
                _LOG.warning("root LLM attempt failed base_url=%s; trying fallback error=%s", base_url, exc)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    data: Dict[str, Any] = {}
    data["output_text"] = _extract_output_text(data)
    return data


def submit_response_job(
    messages: Iterable[Mapping[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    reasoning: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    stream: bool | None = None,
    prompt_cache_key: str | None = None,
    prompt_cache_retention: str | None = None,
    stream_protocol: str | None = None,
    timeout: float | None = None,
) -> Dict[str, Any]:
    """
    Submit a Responses API request as an asynchronous Root LLM job.

    Returns the job envelope. If Root already has a cached response for
    request_id, the returned job can be immediately "succeeded".
    """
    submit_started = time.perf_counter()
    root_payload, _ = _llm_root_payload(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        reasoning=reasoning,
        request_id=request_id,
        stream=stream,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
        stream_protocol=stream_protocol,
    )
    payload_bytes = _json_size_bytes(root_payload)
    if _legacy_http_enabled():
        legacy_started = time.perf_counter()
        resp = requests.post(_llm_jobs_endpoint(), json=root_payload, headers=_auth_headers(), timeout=timeout or 15)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json() if resp.text else {}
        data["output_text"] = _extract_job_output_text(data)
        data.setdefault("_client", {})["base_url"] = _llm_jobs_endpoint().rsplit("/v1/llm/jobs", 1)[0]
        _LOG.debug(
            "root LLM job submit legacy completed request_id=%s endpoint=%s payload_bytes=%d duration_ms=%.1f total_ms=%.1f status=%s job_id=%s",
            str(request_id or ""),
            _llm_jobs_endpoint(),
            payload_bytes,
            (time.perf_counter() - legacy_started) * 1000.0,
            (time.perf_counter() - submit_started) * 1000.0,
            str(data.get("status") or ""),
            str(data.get("job_id") or ""),
        )
        return data

    ctx = _current_ctx()
    primary, cfg = _root_http_client(ctx)
    headers = _identity_headers(ctx, cfg)
    base_urls = _root_llm_base_urls(
        primary,
        prefer_global=_env_enabled("ADAOS_ROOT_LLM_PREFER_GLOBAL", True),
    )
    last_exc: Exception | None = None
    primary_base_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    attempts: list[dict[str, Any]] = []
    trace_attempts: list[dict[str, Any]] = []
    _LOG.debug(
        "root LLM job submit start request_id=%s base_urls=%s primary=%s timeout_s=%s payload_bytes=%d",
        str(request_id or ""),
        base_urls,
        primary_base_url,
        str(timeout or 15),
        payload_bytes,
    )
    for index, base_url in enumerate(base_urls):
        attempt_started: float | None = None
        http = _root_http_for_base(primary, primary_base_url, base_url, index)
        if index + 1 < len(base_urls):
            cached = _ROOT_LLM_HEALTH_CACHE.get(base_url)
            if cached is not None:
                expires_at, cached_ok, cached_detail = cached
                if expires_at > time.monotonic() and not cached_ok:
                    attempts.append(
                        {
                            "base_url": base_url,
                            "error": "recent_submit_unhealthy",
                            "detail": cached_detail,
                        }
                    )
                    trace_attempts.append(
                        {
                            "base_url": base_url,
                            "phase": "cached_skip",
                            "ok": False,
                            "detail": cached_detail,
                        }
                    )
                    continue
        # Only preflight the configured primary root before falling back away
        # from it. When ADAOS_ROOT_LLM_PREFER_GLOBAL puts the global root first,
        # the submit itself is the cheapest and most accurate health probe; a
        # transient /v1/health timeout must not force a large LLM payload onto a
        # slower regional relay.
        should_probe_health = (
            index + 1 < len(base_urls)
            and primary_base_url
            and base_url == primary_base_url
            and _env_enabled("ADAOS_ROOT_LLM_HEALTHCHECK_PRIMARY", True)
        )
        if should_probe_health:
            health_started = time.perf_counter()
            healthy, health_detail = _root_llm_health_probe(http, base_url)
            health_ms = (time.perf_counter() - health_started) * 1000.0
            trace_attempts.append(
                {
                    "base_url": base_url,
                    "phase": "health",
                    "ok": bool(healthy),
                    "duration_ms": round(health_ms, 1),
                    "detail": health_detail,
                }
            )
            _LOG.debug(
                "root LLM job health request_id=%s base_url=%s ok=%s duration_ms=%.1f detail=%s",
                str(request_id or ""),
                base_url,
                bool(healthy),
                health_ms,
                health_detail,
            )
            if not healthy:
                attempts.append(
                    {
                        "base_url": base_url,
                        "error": "root_health_unavailable",
                        "detail": health_detail,
                    }
                )
                _LOG.warning(
                    "root LLM job submit skipped unhealthy base_url=%s; trying fallback detail=%s",
                    base_url,
                    health_detail,
                )
                continue
        try:
            attempt_started = time.perf_counter()
            raw = http.request(
                "POST",
                "/v1/llm/jobs",
                json=root_payload,
                headers=headers or None,
                timeout=timeout or 15,
            )
            attempt_ms = (time.perf_counter() - attempt_started) * 1000.0
            data = dict(raw) if isinstance(raw, Mapping) else {"result": raw}
            _llm_proxy_protocol(data, base_url=base_url, fallback=index > 0, attempts=attempts)
            client_meta = data.setdefault("_client", {})
            client_meta["base_url"] = base_url
            client_meta["fallback"] = index > 0
            client_meta["payload_bytes"] = payload_bytes
            client_meta["attempt_ms"] = round(attempt_ms, 1)
            client_meta["total_ms"] = round((time.perf_counter() - submit_started) * 1000.0, 1)
            client_meta["attempts"] = list(attempts)
            client_meta["trace_attempts"] = list(trace_attempts) + [
                {
                    "base_url": base_url,
                    "phase": "submit",
                    "ok": True,
                    "duration_ms": round(attempt_ms, 1),
                    "status": str(data.get("status") or ""),
                }
            ]
            data["output_text"] = _extract_job_output_text(data)
            _LOG.debug(
                "root LLM job submit completed request_id=%s base_url=%s fallback=%s attempt_ms=%.1f total_ms=%.1f status=%s job_id=%s attempts=%s",
                str(request_id or ""),
                base_url,
                index > 0,
                attempt_ms,
                (time.perf_counter() - submit_started) * 1000.0,
                str(data.get("status") or ""),
                str(data.get("job_id") or ""),
                attempts,
            )
            return data
        except Exception as exc:
            attempt_ms = (time.perf_counter() - attempt_started) * 1000.0 if attempt_started is not None else -1.0
            last_exc = exc
            if isinstance(exc, RootHttpError):
                status_code = int(getattr(exc, "status_code", 0) or 0)
                error_label = _root_http_error_code(exc) or (
                    f"http_{status_code}" if status_code else type(exc).__name__
                )
            else:
                error_label = type(exc).__name__
            attempts.append({"base_url": base_url, "error": error_label})
            if index + 1 < len(base_urls) and _should_retry_llm_proxy(exc):
                _mark_root_llm_submit_unhealthy(base_url, str(exc))
            trace_attempts.append(
                {
                    "base_url": base_url,
                    "phase": "submit",
                    "ok": False,
                    "duration_ms": round(attempt_ms, 1),
                    "error": error_label,
                    "detail": str(exc),
                }
            )
            _LOG.warning(
                "root LLM job submit attempt failed request_id=%s base_url=%s fallback=%s attempt_ms=%.1f total_ms=%.1f error=%s detail=%s",
                str(request_id or ""),
                base_url,
                index > 0,
                attempt_ms,
                (time.perf_counter() - submit_started) * 1000.0,
                error_label,
                exc,
            )
            if index + 1 < len(base_urls) and _should_retry_llm_proxy(exc):
                if request_id:
                    recovery_started = time.perf_counter()
                    recovery_timeout = max(1.0, min(float(timeout or 15), 10.0))
                    try:
                        raw_recovered = http.request(
                            "POST",
                            "/v1/llm/jobs/lookup",
                            json=root_payload,
                            headers=headers or None,
                            timeout=recovery_timeout,
                        )
                        recovery_ms = (time.perf_counter() - recovery_started) * 1000.0
                        data = dict(raw_recovered) if isinstance(raw_recovered, Mapping) else {"result": raw_recovered}
                        _llm_proxy_protocol(data, base_url=base_url, fallback=index > 0, attempts=attempts)
                        client_meta = data.setdefault("_client", {})
                        client_meta["base_url"] = base_url
                        client_meta["fallback"] = index > 0
                        client_meta["payload_bytes"] = payload_bytes
                        client_meta["attempt_ms"] = round(attempt_ms, 1)
                        client_meta["recovery_ms"] = round(recovery_ms, 1)
                        client_meta["total_ms"] = round((time.perf_counter() - submit_started) * 1000.0, 1)
                        client_meta["attempts"] = list(attempts)
                        client_meta["recovered_after_submit_error"] = True
                        client_meta["trace_attempts"] = list(trace_attempts) + [
                            {
                                "base_url": base_url,
                                "phase": "submit_recovery",
                                "ok": True,
                                "duration_ms": round(recovery_ms, 1),
                                "status": str(data.get("status") or ""),
                                "job_id": str(data.get("job_id") or ""),
                            }
                        ]
                        data["output_text"] = _extract_job_output_text(data)
                        _LOG.warning(
                            "root LLM job submit recovered request_id=%s base_url=%s fallback=%s recovery_ms=%.1f total_ms=%.1f status=%s job_id=%s",
                            str(request_id or ""),
                            base_url,
                            index > 0,
                            recovery_ms,
                            (time.perf_counter() - submit_started) * 1000.0,
                            str(data.get("status") or ""),
                            str(data.get("job_id") or ""),
                        )
                        return data
                    except Exception as recovery_exc:
                        recovery_ms = (time.perf_counter() - recovery_started) * 1000.0
                        if isinstance(recovery_exc, RootHttpError):
                            recovery_status = int(getattr(recovery_exc, "status_code", 0) or 0)
                            recovery_label = _root_http_error_code(recovery_exc) or (
                                f"http_{recovery_status}" if recovery_status else type(recovery_exc).__name__
                            )
                        else:
                            recovery_label = type(recovery_exc).__name__
                        trace_attempts.append(
                            {
                                "base_url": base_url,
                                "phase": "submit_recovery",
                                "ok": False,
                                "duration_ms": round(recovery_ms, 1),
                                "error": recovery_label,
                                "detail": str(recovery_exc),
                            }
                        )
                        _LOG.warning(
                            "root LLM job submit recovery failed request_id=%s base_url=%s recovery_ms=%.1f error=%s detail=%s",
                            str(request_id or ""),
                            base_url,
                            recovery_ms,
                            recovery_label,
                            recovery_exc,
                        )
                _LOG.warning("root LLM job submit failed base_url=%s; trying fallback error=%s", base_url, exc)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return {}


def get_response_job(job_id: str, *, base_url: str | None = None, timeout: float | None = None) -> Dict[str, Any]:
    """
    Read a Root LLM job status. Use base_url from submit_response_job["_client"]
    when available so polling stays on the root that owns the job.
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    if _legacy_http_enabled() and not base_url:
        resp = requests.get(_llm_job_endpoint(job_id), headers=_auth_headers(), timeout=timeout or 15)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json() if resp.text else {}
        data["output_text"] = _extract_job_output_text(data)
        return data

    ctx = _current_ctx()
    primary, cfg = _root_http_client(ctx)
    headers = _identity_headers(ctx, cfg)
    primary_base_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    base_urls = [_normalize_root_base_url(base_url)] if base_url else _root_llm_base_urls(primary)
    base_urls = [item for item in base_urls if item]
    last_exc: Exception | None = None
    attempts: list[dict[str, Any]] = []
    for index, candidate_base_url in enumerate(base_urls):
        http = _root_http_for_base(primary, primary_base_url, candidate_base_url, index)
        try:
            raw = http.request(
                "GET",
                f"/v1/llm/jobs/{job_id}",
                headers=headers or None,
                timeout=timeout or 15,
            )
            data = dict(raw) if isinstance(raw, Mapping) else {"result": raw}
            _llm_proxy_protocol(data, base_url=candidate_base_url, fallback=index > 0, attempts=attempts)
            data.setdefault("_client", {})["base_url"] = candidate_base_url
            data["output_text"] = _extract_job_output_text(data)
            return data
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, RootHttpError):
                status_code = int(getattr(exc, "status_code", 0) or 0)
                error_label = _root_http_error_code(exc) or (
                    f"http_{status_code}" if status_code else type(exc).__name__
                )
            else:
                error_label = type(exc).__name__
            attempts.append({"base_url": candidate_base_url, "error": error_label})
            if index + 1 < len(base_urls) and _should_retry_llm_proxy(exc):
                _LOG.warning("root LLM job poll failed base_url=%s; trying fallback error=%s", candidate_base_url, exc)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return {}


def wait_response_job(
    job_id: str,
    *,
    base_url: str | None = None,
    timeout_s: float = 240,
    poll_interval_s: float = 2,
    request_timeout: float | None = None,
    log_interval_s: float = 30,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> Dict[str, Any]:
    started = time.monotonic()
    deadline = started + max(0.1, float(timeout_s))
    interval = max(0.25, float(poll_interval_s))
    log_interval = max(5.0, float(log_interval_s))
    last: Dict[str, Any] = {}
    last_status = ""
    last_poll_error = ""
    next_log_at = started
    poll_count = 0
    last_progress_seq = -1
    _LOG.debug(
        "root LLM job wait start job_id=%s base_url=%s timeout_s=%.1f poll_interval_s=%.1f request_timeout_s=%.1f",
        job_id,
        str(base_url or ""),
        float(timeout_s),
        interval,
        float(request_timeout or min(15, interval + 5)),
    )
    while True:
        poll_count += 1
        try:
            last = get_response_job(job_id, base_url=base_url, timeout=request_timeout or min(15, interval + 5))
        except Exception as exc:
            if not _retryable_llm_job_poll_error(exc):
                _LOG.warning(
                    "root LLM job wait failed job_id=%s base_url=%s polls=%d elapsed_ms=%.1f error=%s detail=%s",
                    job_id,
                    str(base_url or ""),
                    poll_count,
                    (time.monotonic() - started) * 1000.0,
                    _root_llm_poll_error_label(exc),
                    exc,
                )
                raise
            now = time.monotonic()
            last_poll_error = f"{_root_llm_poll_error_label(exc)}: {exc}"
            if now >= deadline:
                raise TimeoutError(
                    f"Root LLM job timed out: {job_id} status={last_status or 'unknown'} "
                    f"last_poll_error={last_poll_error}"
                ) from exc
            _LOG.warning(
                "root LLM job poll transient failure job_id=%s base_url=%s polls=%d elapsed_ms=%.1f remaining_ms=%.1f error=%s detail=%s",
                job_id,
                str(base_url or ""),
                poll_count,
                (now - started) * 1000.0,
                max(0.0, (deadline - now) * 1000.0),
                _root_llm_poll_error_label(exc),
                exc,
            )
            time.sleep(min(interval, max(0.0, deadline - now)))
            continue
        now = time.monotonic()
        status = str(last.get("status") or "").lower()
        progress = last.get("progress") if isinstance(last.get("progress"), Mapping) else {}
        try:
            progress_seq = int(progress.get("seq") or 0)
        except (TypeError, ValueError):
            progress_seq = 0
        if progress_callback is not None and progress_seq != last_progress_seq:
            try:
                progress_callback(dict(progress))
            except Exception:
                _LOG.warning(
                    "root LLM job progress callback failed job_id=%s seq=%s",
                    job_id,
                    progress_seq,
                    exc_info=True,
                )
            last_progress_seq = progress_seq
        if status in {"succeeded", "failed"}:
            _LOG.debug(
                "root LLM job wait completed job_id=%s base_url=%s status=%s polls=%d elapsed_ms=%.1f",
                job_id,
                str(base_url or ""),
                status,
                poll_count,
                (now - started) * 1000.0,
            )
            return last
        if status != last_status or now >= next_log_at:
            _LOG.debug(
                "root LLM job wait pending job_id=%s base_url=%s status=%s polls=%d elapsed_ms=%.1f last_poll_error=%s",
                job_id,
                str(base_url or ""),
                status or "unknown",
                poll_count,
                (now - started) * 1000.0,
                last_poll_error,
            )
            next_log_at = now + log_interval
            last_status = status
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Root LLM job timed out: {job_id} status={status or 'unknown'}"
                + (f" last_poll_error={last_poll_error}" if last_poll_error else "")
            )
        time.sleep(interval)


def _load_prompt(template_path: Path, substitutions: Mapping[str, str]) -> str:
    text = Path(template_path).read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace(key, value)
    return text


def request_ts_draft(
    technical_spec: str,
    *,
    model: Optional[str] = None,
    code_map_path: str | Path = "artifacts/code_map.yaml",
    output_path: str | Path | None = None,
    timeout: float | None = None,
) -> Dict[str, Any]:
    """
    Build a TS-focused LLM request and send it via the Root LLM proxy.

    - injects the Technical Specification into the ts_detailed_request template
    - inlines artifacts/code_map.yaml content
    - when output_path is provided, writes the LLM output to that file
      (callers may omit it to keep artifacts in Yjs / memory only)
    """
    code_map_text = Path(code_map_path).read_text(encoding="utf-8") if code_map_path else ""
    prompt_path = Path(__file__).parent / "prompts" / "ts_detailed_request.md"
    prompt = _load_prompt(
        prompt_path,
        {
            "<<<USER_REQUEST>>>": technical_spec.strip(),
            "<<<artifacts\\code_map.yaml>>>": code_map_text.strip(),
        },
    )
    response = send_response(
        [{"role": "user", "content": prompt}],
        model=model,
        timeout=timeout,
    )
    output_text = response.get("output_text") or ""
    final_output_path: str | Path | None = output_path
    if final_output_path:
        out_path = Path(final_output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
    return {
        "request_prompt": prompt,
        "response": response,
        "output_text": output_text,
        "output_path": str(final_output_path) if final_output_path else None,
    }
