from __future__ import annotations
from typing import Optional, Dict, Any, Iterable
import hashlib
import json
import keyring
from time import time
from adaos.ports.secrets import Secrets, SecretScope
from adaos.ports import KV


class KeyringVault(Secrets):
    """
    Секреты храним в OS keyring под service='adaos:<scope>:<profile>'.
    Индекс ключей (для list/export) держим в KV ('secrets:index:<scope>').
    Поддерживает KV с API get/set ИЛИ get_json/set_json.
    """

    def __init__(self, *, profile: str, kv: KV):
        self.profile = profile
        self.kv = kv

    _CHUNK_PREFIX = "adaos-keyring-chunks-v1:"
    # Windows Credential Manager stores a generic credential as a bounded
    # UTF-16 blob.  Keep chunks comfortably below its platform limit while
    # retaining OS-keyring protection for OAuth correlation and credentials.
    _CHUNK_CHARS = 1200

    def _service(self, scope: SecretScope) -> str:
        return f"adaos:{scope}:{self.profile}"

    def _index_key(self, scope: SecretScope) -> str:
        return f"secrets:index:{scope}"

    # ---------- совместимость с разными KV ----------
    def _kv_get_json(self, key: str):
        # сначала пробуем "родной" JSON-метод
        try:
            return self.kv.get_json(key)
        except AttributeError:
            pass
        # затем строковый get + json.loads
        raw = None
        for meth in ("get", "read", "fetch"):
            if hasattr(self.kv, meth):
                raw = getattr(self.kv, meth)(key)
                break
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _kv_set_json(self, key: str, obj) -> None:
        # сначала пробуем "родной" JSON-метод
        try:
            self.kv.set_json(key, obj)
            return
        except AttributeError:
            pass
        data = json.dumps(obj, ensure_ascii=False)
        for meth in ("set", "write", "put"):
            if hasattr(self.kv, meth):
                getattr(self.kv, meth)(key, data)
                return
        raise AttributeError("KV has no suitable set/set_json method")

    def _load_index(self, scope: SecretScope) -> dict:
        return self._kv_get_json(self._index_key(scope)) or {}

    def _save_index(self, scope: SecretScope, idx: dict) -> None:
        self._kv_set_json(self._index_key(scope), idx)

    @classmethod
    def _chunk_manifest(cls, raw: str | None) -> dict[str, Any] | None:
        if not isinstance(raw, str) or not raw.startswith(cls._CHUNK_PREFIX):
            return None
        try:
            value = json.loads(raw[len(cls._CHUNK_PREFIX) :])
        except (TypeError, ValueError):
            raise PermissionError("keyring chunk manifest is invalid") from None
        if (
            not isinstance(value, dict)
            or int(value.get("count") or 0) <= 0
            or not str(value.get("digest") or "").startswith("sha256:")
            or not str(value.get("key_hash") or "")
        ):
            raise PermissionError("keyring chunk manifest is invalid")
        return value

    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:24]

    @classmethod
    def _chunk_key(cls, key_hash: str, digest: str, index: int) -> str:
        return f"adaos-chunk:{key_hash}:{digest.removeprefix('sha256:')[:20]}:{index:04d}"

    def _delete_chunks(self, service: str, manifest: dict[str, Any] | None) -> None:
        if manifest is None:
            return
        count = int(manifest.get("count") or 0)
        key_hash = str(manifest.get("key_hash") or "")
        digest = str(manifest.get("digest") or "")
        for index in range(count):
            try:
                keyring.delete_password(
                    service,
                    self._chunk_key(key_hash, digest, index),
                )
            except keyring.errors.PasswordDeleteError:
                pass

    # -------------------------------------------------

    def put(self, key: str, value: str, *, scope: SecretScope = "profile", meta: Optional[Dict[str, Any]] = None) -> None:
        service = self._service(scope)
        text = str(value)
        previous = self._chunk_manifest(keyring.get_password(service, key))
        next_manifest: dict[str, Any] | None = None
        if len(text) > self._CHUNK_CHARS:
            digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
            key_hash = self._key_hash(key)
            chunks = [
                text[offset : offset + self._CHUNK_CHARS]
                for offset in range(0, len(text), self._CHUNK_CHARS)
            ]
            next_manifest = {
                "schema": "adaos.keyring.chunk_manifest.v1",
                "digest": digest,
                "key_hash": key_hash,
                "count": len(chunks),
            }
            written: list[str] = []
            try:
                for index, chunk in enumerate(chunks):
                    chunk_key = self._chunk_key(key_hash, digest, index)
                    keyring.set_password(service, chunk_key, chunk)
                    written.append(chunk_key)
                keyring.set_password(
                    service,
                    key,
                    self._CHUNK_PREFIX
                    + json.dumps(next_manifest, ensure_ascii=True, separators=(",", ":")),
                )
            except Exception:
                for chunk_key in written:
                    try:
                        keyring.delete_password(service, chunk_key)
                    except Exception:
                        pass
                raise
        else:
            keyring.set_password(service, key, text)
        if previous != next_manifest:
            self._delete_chunks(service, previous)
        idx = self._load_index(scope)
        idx[key] = {
            "meta": meta or {},
            "updated_at": time(),
            "storage": "chunked" if next_manifest is not None else "direct",
        }
        self._save_index(scope, idx)

    def get(self, key: str, *, default: Optional[str] = None, scope: SecretScope = "profile") -> Optional[str]:
        service = self._service(scope)
        value = keyring.get_password(service, key)
        if value is None:
            return default
        manifest = self._chunk_manifest(value)
        if manifest is None:
            return value
        parts: list[str] = []
        for index in range(int(manifest["count"])):
            chunk = keyring.get_password(
                service,
                self._chunk_key(
                    str(manifest["key_hash"]),
                    str(manifest["digest"]),
                    index,
                ),
            )
            if chunk is None:
                raise PermissionError("keyring chunk is missing")
            parts.append(chunk)
        restored = "".join(parts)
        observed = "sha256:" + hashlib.sha256(restored.encode("utf-8")).hexdigest()
        if observed != manifest["digest"]:
            raise PermissionError("keyring chunk digest mismatch")
        return restored

    def delete(self, key: str, *, scope: SecretScope = "profile") -> None:
        service = self._service(scope)
        manifest = self._chunk_manifest(keyring.get_password(service, key))
        try:
            keyring.delete_password(service, key)
        except keyring.errors.PasswordDeleteError:
            pass
        self._delete_chunks(service, manifest)
        idx = self._load_index(scope)
        if key in idx:
            idx.pop(key)
            self._save_index(scope, idx)

    def list(self, *, scope: SecretScope = "profile") -> list[Dict[str, Any]]:
        idx = self._load_index(scope)
        return [{"key": k, "meta": v.get("meta", {})} for k, v in sorted(idx.items())]

    def import_items(self, items: Iterable[Dict[str, Any]], *, scope: SecretScope = "profile") -> int:
        cnt = 0
        for it in items:
            k = it.get("key")
            v = it.get("value")
            if not k or v is None:
                continue
            self.put(
                str(k),
                str(v),
                scope=scope,
                meta=it.get("meta") or {},
            )
            cnt += 1
        return cnt

    def export_items(self, *, scope: SecretScope = "profile") -> list[Dict[str, Any]]:
        out = []
        idx = self._load_index(scope)
        for k in sorted(idx.keys()):
            v = self.get(k, scope=scope)
            out.append({"key": k, "value": v, "meta": idx[k].get("meta") or {}})
        return out
