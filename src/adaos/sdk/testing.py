"""Provider-free notification stories for isolated SDK-only authoring workers.

Fixtures use the real notification compiler, state and policy with an in-memory
Core store. They never bootstrap a node, load credentials or run delivery workers.
"""
from __future__ import annotations

from contextlib import ExitStack
import math
import secrets
import sqlite3
from types import SimpleNamespace
from typing import Any, Mapping

from adaos.sdk.core.contracts import public_contract


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class _MemorySQL:
    def __init__(self):
        self.uri = "file:notification-story-" + secrets.token_hex(16) + "?mode=memory&cache=shared"
        self.anchor = sqlite3.connect(self.uri, uri=True, check_same_thread=False)
        self.anchor.execute("CREATE TABLE kv(ns TEXT,k TEXT,v TEXT,PRIMARY KEY(ns,k))")
        self.anchor.commit()

    def connect(self, *, read_only=False):
        con=sqlite3.connect(self.uri, uri=True, check_same_thread=False, factory=_Connection)
        if read_only:
            con.execute("PRAGMA query_only=ON")
        return con


class NotificationStory:
    """A bounded, disposable synthetic universe; all refs remain fixture-owned."""
    test_story = True

    def __init__(self, declaration, *, now, seed, management):
        from adaos.domain.notifications import ProducerBinding, digest
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("story time must be a finite UTC timestamp")
        if not isinstance(seed, str) or not 1 <= len(seed) <= 128:
            raise ValueError("story seed must contain 1..128 characters")
        self.now = float(now)
        self._declaration = declaration
        self._manage = management
        self._publishers = {}
        self._closed = False
        identity = digest(seed)
        self.binding = ProducerBinding("preview:" + identity, "preview-instance:" + identity,
            "preview:" + identity, "development:fixture", "user:preview." + identity,
            "subnet:preview", "skill:preview", "Preview", test_only=True)
        self.ctx = SimpleNamespace(sql=_MemorySQL(), kv=SimpleNamespace(ns="kv"),
                                   config=SimpleNamespace(subnet_id="preview"))

    def __enter__(self):
        from adaos.sdk.notifications import _test_runtime
        from adaos.services.agent_context import use_ctx
        from adaos.services.notifications.runtime import NotificationService
        self._stack = ExitStack()
        self._stack.enter_context(use_ctx(self.ctx))
        self.service = NotificationService(self.ctx, clock=lambda: self.now)
        self.service.admit_catalog(self.binding, self._declaration)
        self._marker = _test_runtime.set(self)
        return self

    def __exit__(self, *args):
        from adaos.sdk.notifications import _test_runtime
        _test_runtime.reset(self._marker)
        self._stack.close()
        self.ctx.sql.anchor.close()
        self._closed = True

    def producer(self):
        if self._closed:
            raise PermissionError("notification story is closed")
        return self.binding

    def management(self, *, write=False):
        self.producer()
        if not self._manage:
            raise PermissionError("this story admits a producer, not a preference administrator")
        return self.binding.principal_ref, self.binding.subnet_ref

    def capture(self, *, lifetime_seconds=86400):
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 86400:
            raise ValueError("publisher lifetime must be 1..86400 seconds")
        ref = "notification-publisher.preview." + secrets.token_hex(24)
        self._publishers[ref] = self.now + lifetime_seconds
        return {"publisher_ref": ref, "expires_at": self._publishers[ref], "test_only": True}

    def captured(self, ref):
        if self._publishers.get(ref, 0) <= self.now:
            raise PermissionError("publisher is expired or belongs to another story")
        return self.producer()

    def advance(self, seconds: float):
        """Advance only virtual fixture time; no production timer or worker runs."""
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("advance must be a finite nonnegative duration")
        self.now += seconds
        self.service.expire_due()
        return self.now

    def validate_destinations(self, settings):
        from adaos.services.notifications.endpoints import EndpointService
        owned = {row["endpoint_id"] for row in EndpointService(self.ctx).list(
            self.binding.principal_ref, self.binding.subnet_ref)}
        choices = [settings.get("defaults", {}), *(rule.get("choice", {}) for rule in settings.get("rules", []))]
        if any(set(choice.get("endpoint_ids", [])) - owned for choice in choices):
            raise PermissionError("fake destination belongs to another story or production scope")

    def endpoint(self, transport: str = "max") -> dict:
        """Admit a no-provider destination in this memory store, without consent."""
        from adaos.services import durable_delivery
        from adaos.services.notifications.endpoints import EndpointService
        if transport not in {"max", "telegram", "web"}:
            raise ValueError("unsupported fake endpoint transport")
        route = durable_delivery.create_reply_route("preview:notifications",
            transport=transport, principal_scope=[self.binding.principal_ref],
            destination_ref={"hub_id": "preview", "session_ref": "preview:session", "chat_id": "101"},
            channel_context={"notification_device": "browser"} if transport == "web" else {
                "chat_type": "private" if transport == "telegram" else "dialog"})
        return EndpointService(self.ctx, clock=lambda: self.now).admit_route(
            self.binding.principal_ref, self.binding.subnet_ref, route["route_id"], label="Fake " + transport,
            capabilities={"permission": "granted"} if transport == "web" else {})

    def evaluate(self, notification_id: str) -> dict:
        """Simulate attention using the real policy; receipt is never provider proof."""
        from adaos.services.notifications.policy import evaluate
        notice = self.service.get(self.binding.principal_ref, notification_id)
        if not notice:
            raise PermissionError("notification belongs to another fixture")
        prefs = self.service.preferences.read(self.binding.principal_ref, self.binding.subnet_ref)
        with self.service.store.connect() as con:
            endpoints = self.service._endpoints(con, self.binding.principal_ref, self.binding.subnet_ref)
        plan = evaluate({**notice, "test_only": False}, prefs, endpoints, now=self.now)
        return {**plan, "test_only": True, "provider_calls": 0}


@public_contract(capabilities=("notifications.authoring",), effects=("isolated_test_state",),
    stability="beta", boundedness={"kind": "in_memory_story", "provider_calls": 0},
    runtime_support={"owner": "notifications.testing", "production_delivery": False})
def notification_story(declaration: Mapping[str, Any], *, now: float = 1794168000,
                       seed: str = "notification-story", management: bool = False) -> NotificationStory:
    """Use with: exercise public notifications APIs against isolated synthetic state.

    Management is an explicit fixture role. Real recipients, credentials, routes
    and publisher handles cannot enter or leave this universe. Leaving the
    context destroys its in-memory state and restores the prior SDK invocation.
    """
    return NotificationStory(declaration, now=now, seed=seed, management=management)


__all__ = ["notification_story"]
