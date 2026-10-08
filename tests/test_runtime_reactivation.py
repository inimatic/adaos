from __future__ import annotations

import asyncio

import pytest

from adaos.services.runtime_reactivation import (
    RuntimeReactivationError,
    list_reactivation_attempts,
    reactivate_exact_admitted_package,
)


def _classification(
    digest: str = "sha256:exact",
    *,
    manifest_digest: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema": "adaos.runtime_compatibility.classification.v1",
        "code": "stale_runtime_memory",
        "evidence_complete": True,
        "automatic_recovery_eligible": True,
        "desired_package_digest": digest,
        "installed_package_digest": digest,
    }
    if manifest_digest:
        result["desired_manifest_digest"] = manifest_digest
        result["installed_manifest_digest"] = manifest_digest
    return result


class _Loader:
    def __init__(self, receipts: list[dict[str, object]]) -> None:
        self.receipts = list(receipts)
        self.calls: list[dict[str, object]] = []

    async def reload_skill_handlers(self, _root, skill, **kwargs):
        self.calls.append({"skill": skill, **kwargs})
        return self.receipts.pop(0)


def test_exact_reactivation_is_durable_bounded_and_idempotent(_autocontext) -> None:
    loader = _Loader(
        [
            {
                "ok": True,
                "postcheck": {"ok": True},
                "source_manifest_digest": "sha256:exact",
            }
        ]
    )
    arguments = {
        "ctx": _autocontext,
        "skill_id": "example_skill",
        "expected_version": "1.2.3",
        "expected_slot": "A",
        "expected_package_digest": "sha256:exact",
        "expected_package_manifest_digest": "sha256:manifest",
        "expected_source_manifest_digest": "sha256:manifest",
        "classification": _classification(manifest_digest="sha256:manifest"),
        "loader": loader,
        "now": 100.0,
    }

    first = asyncio.run(reactivate_exact_admitted_package(**arguments))
    duplicate = asyncio.run(reactivate_exact_admitted_package(**arguments))

    assert first["ok"] is True
    assert first["attempt_count"] == 1
    assert duplicate["duplicate"] is True
    assert len(loader.calls) == 1
    assert loader.calls[0]["expected_source_manifest_digest"] == "sha256:manifest"
    assert loader.calls[0]["expected_package_digest"] == "sha256:exact"
    assert loader.calls[0]["expected_package_manifest_digest"] == "sha256:manifest"
    attempts = list_reactivation_attempts(_autocontext, skill_id="example_skill")
    assert [(item["attempt_number"], item["status"]) for item in attempts] == [(1, "succeeded")]


def test_failed_reactivation_enters_cooldown_without_repeating_effect(_autocontext) -> None:
    loader = _Loader([{"ok": False, "reason": "handler_import_failed", "postcheck": {"ok": False}}])
    arguments = {
        "ctx": _autocontext,
        "skill_id": "broken_skill",
        "expected_version": "1.0.0",
        "expected_slot": "B",
        "expected_package_digest": "sha256:exact",
        "classification": _classification(),
        "loader": loader,
        "cooldown_s": 60.0,
    }

    failed = asyncio.run(reactivate_exact_admitted_package(**arguments, now=200.0))
    blocked = asyncio.run(reactivate_exact_admitted_package(**arguments, now=220.0))

    assert failed["ok"] is False
    assert failed["reason"] == "handler_import_failed"
    assert blocked["admitted"] is False
    assert blocked["reason"] == "reactivation_cooldown"
    assert len(loader.calls) == 1


def test_reactivation_rejects_incomplete_or_mismatched_identity_before_ledger(_autocontext) -> None:
    classification = _classification()
    classification["evidence_complete"] = False
    loader = _Loader([])

    with pytest.raises(RuntimeReactivationError, match="evidence is incomplete"):
        asyncio.run(
            reactivate_exact_admitted_package(
                _autocontext,
                skill_id="unknown_skill",
                expected_version="1.0.0",
                expected_slot="A",
                expected_package_digest="sha256:exact",
                classification=classification,
                loader=loader,
            )
        )

    assert list_reactivation_attempts(_autocontext, skill_id="unknown_skill") == []
    assert loader.calls == []
