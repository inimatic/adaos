from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

from adaos.adapters.db.relational import PostgreSQLRelationalStorageProvider, SQLiteRelationalStorageProvider
from adaos.services.storage.relational import RelationalStorageBroker


SKILL_ROOT = Path(__file__).resolve().parents[1] / ".adaos" / "workspace" / "skills" / "research_manager_skill"
if not (SKILL_ROOT / "research").is_dir():
    pytest.skip(
        "research_manager_skill is an optional workspace skill and is not installed",
        allow_module_level=True,
    )
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from research.contracts import ResearchRecord, identity  # noqa: E402
from research.repository import ResearchRepository  # noqa: E402
from research.tracker import LocalTracker  # noqa: E402


def _activate(ctx) -> None:
    assert ctx.skill_ctx.set("research_manager_skill", SKILL_ROOT)


def _exercise_repository(ctx) -> tuple[ResearchRepository, str]:
    _activate(ctx)
    repository = ResearchRepository()
    suffix = uuid.uuid4().hex
    study_id = identity("study", {"storage": suffix})
    trial_id = identity("trial", {"storage": suffix})
    run_id = identity("run", {"storage": suffix})
    repository.put(ResearchRecord("study", study_id, study_id, 0, {"title": "storage conformance", "mode": "exploratory"}))
    tracker = LocalTracker(repository)
    tracker.register_run(
        run_id=run_id,
        study_id=study_id,
        trial_id=trial_id,
        parameters={"seed": 1},
        tags={"provider-neutral": "true"},
    )
    tracker.observe(run_id=run_id, name="accuracy", value=0.5, split_role="validation")
    exported = tracker.finalize(run_id, "succeeded")
    observations = [
        event["payload"]
        for event in exported["events"]
        if event["event_kind"] == "observation"
    ]
    assert observations[0]["value"] == 0.5
    assert exported["session"]["status"] == "succeeded"
    assert exported["export_digest"].startswith("sha256:")
    assert repository.get("study", study_id).payload["title"] == "storage conformance"
    return repository, study_id


def test_research_manager_and_tracker_use_isolated_sqlite_binding(_autocontext) -> None:
    repository, _ = _exercise_repository(_autocontext)
    assert repository._db.binding.provider_id == "sqlite"
    assert repository._db.binding.owner_ref == "skill:research_manager_skill"


@pytest.mark.integration
def test_research_manager_and_tracker_use_isolated_postgresql_binding(_autocontext) -> None:
    admin_url = str(os.getenv("ADAOS_TEST_POSTGRES_URL") or "").strip()
    if not admin_url:
        pytest.skip("ADAOS_TEST_POSTGRES_URL is not configured")
    provider = PostgreSQLRelationalStorageProvider(admin_url, secret_ref="test:storage/postgresql")
    object.__setattr__(_autocontext, "relational_storage", RelationalStorageBroker((provider,)))
    repository, _ = _exercise_repository(_autocontext)
    try:
        assert repository._db.binding.provider_id == "postgresql"
        health = repository._db.health()
        assert health["ok"] is True
        assert health["owner_role"] is True
        assert health["pool_size"] == 5
    finally:
        provider.destroy_for_testing(
            repository._db.binding,
            owner_ref="skill:research_manager_skill",
        )
