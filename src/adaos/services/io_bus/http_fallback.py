from __future__ import annotations
from typing import Mapping, Any
import json
import urllib.request


class HttpFallbackBus:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    def post(self, path: str, payload: Mapping[str, Any]) -> int:
        url = f"{self._base}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as resp:
            return resp.status

