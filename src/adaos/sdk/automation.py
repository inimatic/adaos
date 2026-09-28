"""Governed read access to the remote AdaOS automation fleet.

The caller selects no URL, identity, certificate, or token.  Core resolves its
pre-provisioned Builder identity and returns only the bounded, secret-free
inventory projection admitted by Global Root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import ssl
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

from adaos.sdk import access
from adaos.services.runtime_dotenv import merged_runtime_dotenv_env


_BUILDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AutomationInventoryUnavailable(RuntimeError):
    """Stable fail-closed error whose message never contains secret material."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = str(environment.get(name) or "").strip()
    if not value:
        raise AutomationInventoryUnavailable("automation_identity_not_configured")
    return value


def _read_token(path_value: str) -> str:
    try:
        token_path = Path(path_value).expanduser().resolve(strict=True)
        if not token_path.is_file() or token_path.stat().st_size > 8192:
            raise OSError("invalid token file")
        token = token_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise AutomationInventoryUnavailable("automation_credential_unavailable") from exc
    if not token or len(token) > 8192:
        raise AutomationInventoryUnavailable("automation_credential_unavailable")
    return token


def inventory(*, task_limit: int = 100) -> dict[str, Any]:
    """Read a bounded fleet snapshot through the configured standard Root route."""

    access.require("external_provider.use")
    environment = merged_runtime_dotenv_env(os.environ)
    base_url = _required(environment, "ADAOS_AUTOMATION_ROOT_URL").rstrip("/")
    builder_id = _required(environment, "ADAOS_AUTOMATION_BUILDER_ID")
    if not _BUILDER_ID.fullmatch(builder_id):
        raise AutomationInventoryUnavailable("automation_identity_not_configured")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise AutomationInventoryUnavailable("automation_route_invalid")

    token = _read_token(_required(environment, "ADAOS_AUTOMATION_BUILDER_TOKEN_FILE"))
    cert_file = _required(environment, "ADAOS_AUTOMATION_CLIENT_CERT")
    key_file = _required(environment, "ADAOS_AUTOMATION_CLIENT_KEY")
    ca_file = str(environment.get("ADAOS_AUTOMATION_CA_FILE") or "").strip()
    try:
        context = ssl.create_default_context(cafile=ca_file or None)
        context.load_cert_chain(cert_file, key_file)
    except (OSError, ssl.SSLError) as exc:
        raise AutomationInventoryUnavailable("automation_credential_unavailable") from exc

    limit = max(1, min(int(task_limit), 100))
    path = (
        f"/v1/automation/builders/{urllib.parse.quote(builder_id, safe='._:-')}/inventory"
        f"?{urllib.parse.urlencode({'task_limit': limit})}"
    )
    request = urllib.request.Request(
        base_url + path,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "adaos-core-automation-inventory/0.1",
        },
    )
    timeout = max(
        2.0,
        min(float(environment.get("ADAOS_AUTOMATION_REQUEST_TIMEOUT_S") or 15), 60.0),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise AutomationInventoryUnavailable("automation_inventory_unavailable") from exc
    if len(body) > _MAX_RESPONSE_BYTES:
        raise AutomationInventoryUnavailable("automation_inventory_too_large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AutomationInventoryUnavailable("automation_inventory_invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "adaos.automation.builder_inventory.v1"
        or not isinstance(payload.get("nodes"), list)
        or not isinstance(payload.get("tasks"), list)
    ):
        raise AutomationInventoryUnavailable("automation_inventory_invalid")
    return payload


__all__ = ["AutomationInventoryUnavailable", "inventory"]
