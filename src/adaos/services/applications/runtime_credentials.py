"""Owner-only, declared credential slots resolved through the node's vault.

Bindings travel with Application configuration; values never enter that store.
Delegated/background actor grants and legacy plaintext import require separate
adapters. No failure here may fall back to a process-wide or another skill vault.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
import uuid

import yaml

from adaos.services.personalization_runtime import personalization_access_service
from adaos.services.policy.caller import current_caller
from adaos.services.policy.skill_capabilities import _manifest_path, SkillCapabilityAdmissionError
from .configuration import _digest
from .runtime_configuration import ApplicationRuntimeConfiguration


_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
_REFERENCE = re.compile(r"^credential:([a-f0-9]{32})$")


class ApplicationRuntimeCredentials:
    def __init__(self, ctx):
        self.ctx = ctx
        self.configuration = ApplicationRuntimeConfiguration(ctx)

    @staticmethod
    def declared(ctx) -> bool:
        port = getattr(ctx, "skill_ctx", None)
        current = port.get() if port is not None else None
        if current is None or not getattr(current, "path", None):
            return False
        # Legacy skills without a manifest retain their existing isolated adapter.
        try:
            path = _manifest_path(current)
        except SkillCapabilityAdmissionError:
            return False
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return "credentials" in (manifest.get("configuration") or {})

    def _binding(self, name, capability):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Credential slot name is invalid")
        caller = current_caller()
        owner = personalization_access_service(self.ctx).owner
        if caller is None or caller != owner:
            raise PermissionError("Application credentials currently require the verified local owner")
        selected, schema, defaults, store, lease = self.configuration._binding(capability)
        manifest = yaml.safe_load(_manifest_path(self.ctx.skill_ctx.get()).read_text(encoding="utf-8"))
        slots = (manifest.get("configuration") or {}).get("credentials")
        declaration = slots.get(name) if isinstance(slots, dict) else None
        if (not isinstance(declaration, dict) or set(declaration) != {"purpose"}
                or not isinstance(declaration.get("purpose"), str)
                or not 1 <= len(declaration["purpose"].strip()) <= 1000):
            raise PermissionError("Credential slot and its purpose must be declared by the active skill")
        vault = getattr(self.ctx, "credential_vault", None)
        if vault is None:
            raise PermissionError("Node credential vault is unavailable; no legacy fallback is allowed")
        identity = {**store.identity, "owner_ref": owner.ref(),
                    "slot": name, "purpose": declaration["purpose"]}
        return selected, schema, defaults, store, lease, vault, identity

    @staticmethod
    def _key(identity, reference):
        match = _REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
        if not match:
            raise PermissionError("Invalid admitted credential reference")
        return "application-credential:" + _digest(identity).split(":")[1] + ":" + match[1]

    @staticmethod
    def _vault_call(vault, action, *args):
        try:
            return getattr(vault, action)(*args)
        except Exception:
            raise PermissionError("Credential vault operation failed") from None

    def get(self, name, *, default=None):
        selected, schema, defaults, store, lease, vault, identity = self._binding(name, "secrets.read")
        with lease:
            config, _candidate = self.configuration._current(selected, schema, defaults, store.read())
            reference = config["credentials"].get(name)
            if reference is None:
                return default
            raw = self._vault_call(vault, "get", self._key(identity, reference))
            if raw is None:
                return default
            try:
                record = json.loads(raw)
                if record["identity"] != identity or not isinstance(record["value"], str):
                    raise ValueError
                return record["value"]
            except (TypeError, ValueError, KeyError):
                raise PermissionError("Credential ownership record is invalid") from None

    def _change(self, name, value):
        selected, schema, defaults, store, lease, vault, identity = self._binding(name, "secrets.write")
        with lease:
            record = store.read()
            config, candidate = self.configuration._current(selected, schema, defaults, record)
            credentials = deepcopy(config["credentials"])
            if value is None:
                reference = credentials.pop(name, None)
                if reference is None:
                    return
                self._vault_call(vault, "delete", self._key(identity, reference))
            else:
                reference = "credential:" + uuid.uuid4().hex
                payload = json.dumps({"identity": identity, "value": value}, ensure_ascii=False)
                self._vault_call(vault, "put", self._key(identity, reference), payload)
                credentials[name] = reference
            # A CAS failure leaves an unreferenced vault item, never a plaintext
            # file or permission bypass. Retained old bindings remain recoverable.
            if candidate:
                store.update_beta(candidate_id=candidate, schema=schema, values=config["values"],
                                  credentials=credentials, expected_revision=record["revision"])
            else:
                store.set_stable(release_digest=selected.release_digest if selected else "development:" + _digest(schema),
                                 schema=schema, values=config["values"], credentials=credentials,
                                 expected_revision=record["revision"])

    def put(self, name, value):
        if not isinstance(value, str) or len(value.encode("utf-8")) > 65536:
            raise ValueError("Credential value must be a string of at most 64 KiB")
        self._change(name, value)

    def delete(self, name):
        self._change(name, None)
