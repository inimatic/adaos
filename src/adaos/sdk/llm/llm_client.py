from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
import logging
import os

import requests
from adaos.services.agent_context import get_ctx
from adaos.services.root.client import RootHttpClient, RootHttpError


_LOG = logging.getLogger("adaos.sdk.llm")


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


def _root_llm_base_urls(primary: RootHttpClient) -> list[str]:
    urls: list[str] = []

    def add(value: str | None) -> None:
        url = _normalize_root_base_url(value)
        if url and url not in urls:
            urls.append(url)

    add(os.getenv("ADAOS_ROOT_LLM_BASE_URL") or os.getenv("ADAOS_LLM_ROOT_BASE_URL"))
    add(getattr(primary, "base_url", None))
    for item in _env_csv(os.getenv("ADAOS_ROOT_LLM_FALLBACK_BASE_URLS")):
        add(item)

    primary_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    if (
        primary_url
        and primary_url != "https://api.inimatic.com"
        and primary_url.endswith(".api.inimatic.com")
    ):
        add("https://api.inimatic.com")
    return urls or ["https://api.inimatic.com"]


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


def list_llm_models(*, timeout: float | None = None) -> Dict[str, Any]:
    """
    Fetch available LLM models from the Root LLM proxy.
    """
    if _legacy_http_enabled() or os.getenv("ADAOS_LLM_MODELS_ENDPOINT"):
        resp = requests.get(_llm_models_endpoint(), headers=_auth_headers(), timeout=timeout or 30)
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
            raw = http.request("GET", "/v1/llm/models", headers=headers or None, timeout=timeout or 30)
            if isinstance(raw, Mapping):
                return dict(raw)
            if isinstance(raw, list):
                return {"data": list(raw)}
            return {"data": raw}
        except Exception as exc:
            last_exc = exc
            if index + 1 < len(base_urls) and _should_retry_llm_proxy(exc):
                _LOG.warning("root LLM models attempt failed; trying fallback base_url=%s error=%s", base_url, exc)
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


def send_response(
    messages: Iterable[Mapping[str, str]],
    *,
    model: Optional[str] = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    request_id: str | None = None,
    timeout: float | None = None,
) -> Dict[str, Any]:
    """
    Send a message batch to the Root LLM proxy (Responses API wrapper).

    Returns dict with raw response plus convenience "output_text" field.
    """
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
    req_id = str(request_id or "").strip()
    if req_id:
        payload["request_id"] = req_id

    if _legacy_http_enabled():
        resp = requests.post(_llm_endpoint(), json=payload, headers=_auth_headers(), timeout=timeout or 45)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json() if resp.text else {}
        data["output_text"] = _extract_output_text(data)
        return data

    ctx = _current_ctx()
    primary, cfg = _root_http_client(ctx)
    headers = _identity_headers(ctx, cfg)
    base_urls = _root_llm_base_urls(primary)
    root_payload = _responses_payload(payload, normalized_messages)
    last_exc: Exception | None = None
    primary_base_url = _normalize_root_base_url(getattr(primary, "base_url", None))
    attempts: list[dict[str, Any]] = []
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
            raw = http.request(
                "POST",
                "/v1/llm/response",
                json=root_payload,
                headers=headers or None,
                timeout=timeout or 45,
            )
            data = dict(raw) if isinstance(raw, Mapping) else {"result": raw}
            protocol = data.setdefault("_protocol", {})
            if isinstance(protocol, dict):
                protocol["llm_proxy"] = {
                    "base_url": base_url,
                    "fallback": index > 0,
                    "attempts": list(attempts),
                }
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
                _LOG.warning("root LLM attempt failed; trying fallback base_url=%s error=%s", base_url, exc)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    data: Dict[str, Any] = {}
    data["output_text"] = _extract_output_text(data)
    return data


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
