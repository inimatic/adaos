from pathlib import Path

from adaos.services.capability_binding_state import (
    inventory_installed_skills,
    render_skill_inventory_markdown,
)


def _manifest(root: Path, name: str, body: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(body, encoding="utf-8")
    return skill


def test_legacy_capabilities_remain_requested_runtime_access(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    _manifest(
        installed,
        "legacy",
        """name: legacy
version: 1.0.0
capabilities: [storage.relational]
tools:
  - name: write
data_routes:
  - route: local
""",
    )

    report = inventory_installed_skills(installed)
    item = report["skills"][0]

    assert item["requested_runtime_capabilities"] == ["storage.relational"]
    assert item["portable_artifacts"] == {}
    assert item["migration_class"] == "stateful_legacy_provider_candidate"
    assert report["summary"]["native_provider_count"] == 0


def test_native_and_builder_aware_skills_are_distinguished(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    native = _manifest(installed, "native", "name: native\nversion: 1.0.0\ntools: [{name: run}]\n")
    contracts = native / "contracts"
    contracts.mkdir()
    (contracts / "capability.contract.json").write_text(
        '{"schema":"adaos.capability.contract.v1"}', encoding="utf-8"
    )
    (contracts / "binding.definition.json").write_text(
        '{"schema":"adaos.binding.definition.v1"}', encoding="utf-8"
    )
    builder = _manifest(
        installed,
        "builder",
        "name: builder\nversion: 2.0.0\ntools: [{name: compile}]\n",
    )
    (builder / "handlers").mkdir()
    (builder / "handlers" / "main.py").write_text(
        "result = {'cbs_compilation_digest': 'sha256:...'}", encoding="utf-8"
    )

    report = inventory_installed_skills(installed)
    by_name = {item["skill"]: item for item in report["skills"]}

    assert by_name["native"]["migration_class"] == "cbs_native_provider"
    assert by_name["builder"]["migration_class"] == "builder_cbs_aware"
    assert "cbs_compilation_digest" in by_name["builder"]["builder_handoff_markers"]


def test_development_overlay_and_render_are_deterministic(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    development = tmp_path / "dev" / "node" / "skills"
    _manifest(installed, "sample", "name: sample\nversion: 1.0.0\ntools: [{name: get}]\n")
    _manifest(development, "sample", "name: sample\nversion: 1.1.0\ntools: [{name: get}]\n")

    first = inventory_installed_skills(installed, dev_root=tmp_path / "dev")
    second = inventory_installed_skills(installed, dev_root=tmp_path / "dev")

    assert first == second
    assert first["skills"][0]["development_relation"] == "upgrade_available"
    rendered = render_skill_inventory_markdown(first)
    assert first["inventory_digest"] in rendered
    assert "requested runtime access" in rendered
