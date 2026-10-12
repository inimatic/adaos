"""Performance contracts assert work avoided, not machine-specific timings."""
from adaos.services.scenario.webspace_components.cache import WebspaceCacheState
from pathlib import Path


def test_scenario_invalidation_preserves_unrelated_catalog_inputs():
    cache = WebspaceCacheState()
    cache.put_skill_declarations("workspace", 0, "fp", [{"skill": "builder"}],
                                 dependency_refs=frozenset({"skill:builder"}))
    cache.put_skill_source_fingerprint("workspace", 0, "sources")
    cache.put_desktop_scenarios("host", 0, (), [("changed", "Changed", "")])
    cache.put_desktop_scenarios("other", 0, (), [("other", "Other", "")])
    result = cache.invalidate_catalog_dependencies(("scenario:changed",))
    assert result["skill_declarations"] == 0
    assert result["desktop_scenarios"] == 1
    assert cache.get_skill_declarations("workspace") is not None
    assert cache.get_skill_source_fingerprint("workspace") is not None
    assert cache.get_desktop_scenarios("other") is not None


def test_skill_invalidation_only_discards_dependent_declarations():
    cache = WebspaceCacheState()
    for skill in ("changed", "other"):
        cache.put_skill_declarations(skill, 0, skill, [], dependency_refs=frozenset({f"skill:{skill}"}))
    cache.put_skill_declarations("legacy-unknown", 0, "legacy", [])
    assert cache.invalidate_catalog_dependencies(("skill:changed",))["skill_declarations"] == 2
    assert cache.get_skill_declarations("changed") is None
    assert cache.get_skill_declarations("other") is not None


def test_unknown_mutation_conservatively_invalidates_catalogs():
    cache = WebspaceCacheState()
    cache.put_skill_declarations("workspace", 0, "fp", [])
    cache.put_skill_source_fingerprint("workspace", 0, "fp")
    cache.put_desktop_scenarios("host", 0, (), [])
    assert cache.invalidate_catalog_dependencies(None)["scope"] == "unknown_dependencies"
    assert cache.get_skill_declarations("workspace") is None
    assert cache.get_skill_source_fingerprint("workspace") is None
    assert cache.get_desktop_scenarios("host") is None


def test_unrecognized_artifact_ref_conservatively_invalidates_catalogs():
    cache = WebspaceCacheState()
    cache.put_skill_declarations("workspace", 0, "fp", [])
    cache.put_skill_source_fingerprint("workspace", 0, "fp")
    cache.put_desktop_scenarios("host", 0, (), [])

    result = cache.invalidate_catalog_dependencies(("application:changed",))

    assert result["scope"] == "unknown_dependencies"
    assert cache.get_skill_declarations("workspace") is None
    assert cache.get_skill_source_fingerprint("workspace") is None
    assert cache.get_desktop_scenarios("host") is None


def test_retained_qualification_cannot_allocate_builder_hosts():
    source = (Path(__file__).resolve().parents[1] / "e2e/stand/qualify-context-composition-isolated.py").read_text(encoding="utf-8")
    assert "require_builder_host(args.host)" in source
    assert "test_ctxqual_" not in source
    assert "owner.post(" not in source
