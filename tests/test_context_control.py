from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.context_control import (
    ContextAccessDenied,
    ContextConflict,
    ContextControlService,
)


def _capsule(
    service: ContextControlService,
    *,
    subject_ref: str,
    kind: str = "project",
    summary: str = "context",
    trust_class: str = "accepted",
    tainted: bool = False,
    valid_from: str | None = None,
) -> dict:
    return service.register_capsule(
        {
            "kind": kind,
            "subject_refs": [subject_ref],
            "authority_ref": subject_ref,
            "trust_class": trust_class,
            "tainted": tainted,
            "sensitivity": "workspace",
            "license": "internal",
            "retention_class": "accepted_release_lineage",
            "valid_from": valid_from,
            "summary": summary,
            "content": {"summary": summary},
        }
    )


def _schema(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_capsule_search_filters_before_limit_and_indexes_content(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    target = service.register_capsule(
        {
            "kind": "procedural",
            "subject_refs": ["prompt-rule:manifest-contract"],
            "authority_ref": "docs:llm-skill-development",
            "trust_class": "validated",
            "summary": "Public tool contract",
            "content": {"rules": ["Keep skill.yaml and webui.json consistent."]},
        }
    )
    service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:newer"],
            "authority_ref": "project:newer",
            "summary": "Unrelated newest capsule",
        }
    )

    assert service.list_capsules(search="skill.yaml consistent", limit=1) == [target]
    assert service.list_capsules(
        subject_ref="prompt-rule:manifest-contract",
        limit=1,
    ) == [target]


def test_capsule_search_backfills_existing_registry(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    target = service.register_capsule(
        {
            "kind": "procedural",
            "subject_refs": ["prompt-rule:reload-safety"],
            "authority_ref": "docs:llm-skill-development",
            "content": {"rules": ["Keep reload cleanup idempotent."]},
        }
    )
    with service._connect() as connection:
        connection.execute("DELETE FROM capsule_search")

    restored = ContextControlService(tmp_path)

    assert restored.list_capsules(search="reload cleanup", limit=1) == [target]


def test_capsule_graph_plan_compile_and_receipt(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    platform = _capsule(service, subject_ref="platform:adaos", kind="platform", summary="SDK surface")
    project = _capsule(service, subject_ref="project:demo", summary="Demo Metrics project")
    service.add_relationship(
        {
            "from_capsule_id": project["capsule_id"],
            "to_capsule_id": platform["capsule_id"],
            "relation_type": "uses",
            "required": True,
        }
    )
    binding = service.bind_subject(
        subject_ref="project:demo",
        capsule_id=project["capsule_id"],
        purpose="builder.repair",
        audience="builder",
    )

    resolution = service.resolve(
        {
            "subject_refs": ["project:demo"],
            "purpose": "builder.repair",
            "audience": "builder",
        }
    )
    assert resolution["status"] == "ready"
    assert [item["ref"] for item in resolution["required"]] == [project["capsule_id"], platform["capsule_id"]]

    plan = service.plan({"resolution": resolution, "token_budget": 1000, "model_profile": {"id": "codex"}})
    compiled = service.compile({"plan": plan, "output_format": "toon"})
    receipt = service.record_receipt(
        {
            "run_ref": "builder-run:demo-1",
            "plan_ref": plan["plan_ref"],
            "subject_refs": ["project:demo"],
            "selected_refs": compiled["selected_refs"],
            "usage": {"provider_input_tokens": 100, "cached_input_tokens": 75, "output_tokens": 10},
            "execution_route": "bounded_patch_agent",
            "validation": {"status": "passed"},
        }
    )

    assert binding["revision"] == 1
    assert plan["status"] == "ready"
    assert plan["required_estimated_tokens"] == sum(
        item["estimated_tokens"] for item in resolution["required"]
    )
    assert plan["omitted_required_refs"] == []
    assert compiled["model_text_format"] == "toon"
    assert compiled["stable_prefix_digest"].startswith("sha256:")
    assert receipt["usage"]["fresh_plus_output"] == 35
    assert service.inspect("builder-run:demo-1")["usage"]["fresh_plus_output"] == 35
    Draft202012Validator(_schema("context.capsule.v2")).validate(project)
    Draft202012Validator(_schema("context.subject_binding.v1")).validate(binding)
    Draft202012Validator(_schema("context.plan.v1")).validate(plan)
    Draft202012Validator(_schema("agent.context_receipt.v1")).validate(receipt)


def test_compile_emits_smaller_digest_bound_delta_for_acknowledged_base(
    tmp_path: Path,
) -> None:
    service = ContextControlService(tmp_path)
    platform = _capsule(
        service,
        subject_ref="platform:adaos",
        kind="platform",
        summary="Stable SDK surface with enough descriptive content for the full packet.",
    )
    project = _capsule(
        service,
        subject_ref="project:demo",
        summary="Demo project generation with bounded source and acceptance context.",
    )
    service.add_relationship(
        {
            "from_capsule_id": project["capsule_id"],
            "to_capsule_id": platform["capsule_id"],
            "relation_type": "uses",
            "required": True,
        }
    )
    service.bind_subject(subject_ref="project:demo", capsule_id=project["capsule_id"])
    resolution = service.resolve({"subject_refs": ["project:demo"]})
    plan = service.plan({"resolution": resolution, "token_budget": 2_000})
    full = service.compile({"plan": plan, "output_format": "min_json"})

    followup = service.compile(
        {
            "plan": plan,
            "output_format": "min_json",
            "base_packet_ref": full["packet_ref"],
        }
    )
    delta = json.loads(followup["model_text"])

    assert followup["delta_mode"] == "delta"
    assert followup["packet_ref"] == full["packet_ref"]
    assert followup["model_projection_ref"] != followup["packet_ref"]
    assert followup["token_estimate"] < followup["full_token_estimate"]
    assert followup["delta"]["saved_bytes"] > 0
    assert delta["schema"] == "adaos.context.delta.v1"
    assert delta["changed"] == []
    assert delta["removed"] == []
    assert len(delta["unchanged"]) == 2
    Draft202012Validator(_schema("context.delta.v1")).validate(delta)
    assert service.get_artifact(followup["packet_ref"])["schema"] == (
        "adaos.context.compiled_packet.v1"
    )


def test_compile_rejects_delta_base_from_another_project(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    compiled: dict[str, dict] = {}
    plans: dict[str, dict] = {}
    for project_id in ("alpha", "beta"):
        capsule = _capsule(service, subject_ref=f"project:{project_id}")
        service.bind_subject(
            subject_ref=f"project:{project_id}",
            capsule_id=capsule["capsule_id"],
        )
        resolution = service.resolve({"subject_refs": [f"project:{project_id}"]})
        plan = service.plan({"resolution": resolution, "token_budget": 1_000})
        plans[project_id] = plan
        compiled[project_id] = service.compile(
            {"plan": plan, "output_format": "min_json"}
        )

    with pytest.raises(ContextAccessDenied, match="another project"):
        service.compile(
            {
                "plan": plans["beta"],
                "output_format": "min_json",
                "base_packet_ref": compiled["alpha"]["packet_ref"],
            }
        )


def test_binding_is_optimistic_and_reconstructs_as_of(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    first = _capsule(service, subject_ref="project:demo", summary="first", valid_from="2027-01-01T00:00:00+00:00")
    second = _capsule(service, subject_ref="project:demo", summary="second", valid_from="2028-01-01T00:00:00+00:00")
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=first["capsule_id"],
        valid_from="2027-01-01T00:00:00+00:00",
    )
    current = service.bind_subject(
        subject_ref="project:demo",
        capsule_id=second["capsule_id"],
        expected_revision=1,
        valid_from="2028-01-01T00:00:00+00:00",
    )
    historical = service.get_binding(
        subject_ref="project:demo",
        as_of="2027-06-01T00:00:00+00:00",
    )

    assert current["revision"] == 2
    assert historical["capsule_id"] == first["capsule_id"]
    with pytest.raises(ContextConflict, match="revision conflict"):
        service.bind_subject(
            subject_ref="project:demo",
            capsule_id=first["capsule_id"],
            expected_revision=1,
        )


def test_branch_merge_requires_unchanged_target_base(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    base = _capsule(service, subject_ref="project:demo", summary="base")
    branch_change = _capsule(service, subject_ref="project:demo", summary="branch")
    target_change = _capsule(service, subject_ref="project:demo", summary="target")
    service.bind_subject(subject_ref="project:demo", capsule_id=base["capsule_id"])
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=base["capsule_id"],
        branch="repair-1",
    )
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=branch_change["capsule_id"],
        branch="repair-1",
        expected_revision=1,
    )

    comparison = service.compare_bindings(
        subject_ref="project:demo",
        left_branch="main",
        right_branch="repair-1",
    )
    merged = service.merge_binding(
        subject_ref="project:demo",
        source_branch="repair-1",
        base_capsule_id=base["capsule_id"],
        expected_target_revision=1,
    )

    assert comparison["status"] == "diverged"
    assert merged["target"]["capsule_id"] == branch_change["capsule_id"]
    Draft202012Validator(_schema("context.binding_comparison.v1")).validate(comparison)
    Draft202012Validator(_schema("context.binding_merge.v1")).validate(merged)
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=target_change["capsule_id"],
        expected_revision=2,
    )
    with pytest.raises(ContextConflict, match="declared base"):
        service.merge_binding(
            subject_ref="project:demo",
            source_branch="repair-1",
            base_capsule_id=base["capsule_id"],
        )


def test_bitemporal_comparisons_normalize_timezone_offsets(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    platform = service.register_capsule(
        {
            "kind": "platform",
            "subject_refs": ["platform:adaos"],
            "authority_ref": "core:adaos",
            "trust_class": "accepted",
            "sensitivity": "workspace",
            "license": "internal",
            "retention_class": "accepted_release_lineage",
            "valid_from": "2026-09-01T17:00:00+03:00",
            "recorded_at": "2026-09-01T17:00:00+03:00",
            "summary": "same instant in Moscow time",
        }
    )
    project = service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "sensitivity": "workspace",
            "license": "internal",
            "retention_class": "project_generation",
            "valid_from": "2026-09-01T14:00:00+00:00",
            "recorded_at": "2026-09-01T14:00:00+00:00",
            "summary": "same instant in UTC",
        }
    )
    service.add_relationship(
        {
            "from_capsule_id": project["capsule_id"],
            "to_capsule_id": platform["capsule_id"],
            "relation_type": "uses",
            "valid_from": "2026-09-01T17:00:00+03:00",
            "recorded_at": "2026-09-01T17:00:00+03:00",
        }
    )
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=project["capsule_id"],
        valid_from="2026-09-01T17:00:00+03:00",
    )

    historical = service.get_binding(
        subject_ref="project:demo",
        as_of="2030-01-01T00:00:00+00:00",
    )
    resolution = service.resolve(
        {
            "subject_refs": [project["capsule_id"]],
            "as_of": "2026-09-01T14:00:01+00:00",
        }
    )

    assert historical["capsule_id"] == project["capsule_id"]
    assert resolution["status"] == "ready"
    assert [item["kind"] for item in resolution["required"]] == ["project", "platform"]


def test_resolution_denies_cross_project_and_tainted_dependencies(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    project = _capsule(service, subject_ref="project:alpha")
    foreign = _capsule(service, subject_ref="project:beta", kind="component")
    tainted = _capsule(
        service,
        subject_ref="artifact:telegram-note",
        kind="episodic_memory",
        trust_class="untrusted",
        tainted=True,
    )
    for target, relation_type in ((foreign, "uses"), (tainted, "observed_from")):
        service.add_relationship(
            {
                "from_capsule_id": project["capsule_id"],
                "to_capsule_id": target["capsule_id"],
                "relation_type": relation_type,
            }
        )
    service.bind_subject(subject_ref="project:alpha", capsule_id=project["capsule_id"])

    resolution = service.resolve({"subject_refs": ["project:alpha"]})

    reasons = {item["reason"] for item in resolution["denied"]}
    assert "cross_project_dependency_denied" in reasons
    assert "tainted" in reasons
    assert resolution["status"] == "insufficient"


def test_invalidation_denies_stale_capsule_but_admits_matching_generation(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    old = service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo", "skill:demo_skill"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "source_digests": {"release": "sha256:old"},
            "summary": "old generation",
        }
    )
    service.bind_subject(subject_ref="project:demo", capsule_id=old["capsule_id"])
    invalidation = service.invalidate(
        subject_ref="skill:demo_skill",
        source_digest="sha256:new",
        reason="skills.updated",
        event_ref="event:skill-update-1",
    )

    stale = service.resolve({"subject_refs": ["project:demo"]})

    assert stale["status"] == "insufficient"
    assert stale["denied"][0]["reason"] == "source_invalidated"
    assert stale["denied"][0]["invalidation_refs"] == [invalidation["invalidation_id"]]
    assert service.list_invalidations(subject_ref="skill:demo_skill") == [invalidation]
    Draft202012Validator(_schema("context.invalidation.v1")).validate(invalidation)

    current = service.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo", "skill:demo_skill"],
            "authority_ref": "project:demo",
            "trust_class": "accepted",
            "source_digests": {"release": "sha256:new"},
            "summary": "current generation",
        }
    )
    service.bind_subject(
        subject_ref="project:demo",
        capsule_id=current["capsule_id"],
        expected_revision=1,
    )
    fresh = service.resolve({"subject_refs": ["project:demo"]})
    assert fresh["status"] == "ready"
    assert fresh["required"][0]["ref"] == current["capsule_id"]


def test_memory_requires_independent_evidence_and_supports_rollback(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    candidate = service.propose_memory(
        {
            "kind": "procedural",
            "source_refs": ["run:1"],
            "summary": "Use exact widget refs",
            "content": {"rule": "resolve widget before editing"},
            "proposed_by": "agent:builder",
            "proposed_by_kind": "llm",
            "authority_ref": "project:demo",
        }
    )
    with pytest.raises(ContextAccessDenied, match="cannot qualify"):
        service.qualify_memory(
            candidate["candidate_id"],
            validation_refs=["test:one"],
            qualified_by="agent:builder",
        )
    qualified = service.qualify_memory(
        candidate["candidate_id"],
        validation_refs=["test:one", "evidence:review"],
        qualified_by="evaluator:local",
        expected_revision=1,
    )
    with pytest.raises(ContextAccessDenied, match="cannot promote"):
        service.promote_memory(
            candidate["candidate_id"],
            actor_ref="agent:builder",
            subject_refs=["project:demo"],
        )
    promoted = service.promote_memory(
        candidate["candidate_id"],
        actor_ref="user:owner",
        subject_refs=["project:demo"],
        expected_revision=qualified["revision"],
    )
    rolled_back = service.rollback_memory(
        candidate["candidate_id"],
        actor_ref="user:owner",
        reason="validation regression",
    )

    assert promoted["candidate"]["status"] == "promoted"
    assert rolled_back["candidate"]["status"] == "rolled_back"
    assert rolled_back["revoked_capsule"]["revocation_reason"] == "validation regression"


def test_tainted_memory_requires_sanitization_evidence(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    candidate = service.propose_memory(
        {
            "kind": "procedural",
            "source_refs": ["telegram:message-1"],
            "summary": "Untrusted instruction",
            "proposed_by": "agent:builder",
            "proposed_by_kind": "llm",
            "authority_ref": "project:demo",
            "tainted": True,
        }
    )
    service.qualify_memory(candidate["candidate_id"], validation_refs=["test:one"], qualified_by="evaluator:local")
    with pytest.raises(ContextAccessDenied, match="sanitization"):
        service.promote_memory(
            candidate["candidate_id"],
            actor_ref="user:owner",
            subject_refs=["project:demo"],
        )


def test_root_mcp_context_plane_is_an_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from adaos.services.root_mcp import service as root_service

    context = ContextControlService(tmp_path)
    capsule = _capsule(context, subject_ref="project:demo")
    context.bind_subject(subject_ref="project:demo", capsule_id=capsule["capsule_id"])
    monkeypatch.setattr(root_service, "_context_service", lambda: context)

    resolution = root_service._handle_context_resolve(
        {"subject_refs": ["project:demo"], "purpose": "builder.repair", "audience": "builder"},
        dry_run=False,
    )["resolution"]
    plan = root_service._handle_context_plan(
        {"resolution": resolution, "token_budget": 1000},
        dry_run=False,
    )["plan"]
    compilation = root_service._handle_context_compile(
        {"plan": plan, "output_format": "min_json"},
        dry_run=False,
    )["compilation"]

    assert resolution["status"] == "ready"
    assert plan["status"] == "ready"
    assert compilation["selected_refs"] == [capsule["capsule_id"]]
