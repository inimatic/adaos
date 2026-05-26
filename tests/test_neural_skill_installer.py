from pathlib import Path


def test_ensure_neural_service_skill_installed_creates_skill_tree():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.neural_skill_installer import ensure_neural_service_skill_installed
    from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

    ctx = get_ctx()
    skills_root = Path(ctx.paths.skills_dir())
    target = skills_root / "neural_nlu_service_skill"
    assert not target.exists()

    installed = ensure_neural_service_skill_installed(enabled=True)

    assert installed is not None
    assert installed == target
    assert (target / "skill.yaml").exists()
    assert (target / "handlers" / "main.py").exists()
    assert (target / ".adaos-managed.json").exists()
    assert "AdaOSNeuralNLU/0.2" in (target / "handlers" / "main.py").read_text(encoding="utf-8")
    manifest_text = (target / "skill.yaml").read_text(encoding="utf-8")
    assert "mode: venv" in manifest_text
    assert "- torch" in manifest_text
    assert "- numpy" in manifest_text
    assert "- faiss-cpu" in manifest_text

    env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name="neural_nlu_service_skill")
    version = env.resolve_active_version()
    assert version
    slot = env.read_active_slot(version)
    slot_skill = env.build_slot_paths(version, slot).src_dir / "skills" / "neural_nlu_service_skill"
    assert (slot_skill / "skill.yaml").exists()
    assert (slot_skill / ".adaos-managed.json").exists()


def test_ensure_neural_service_skill_installed_respects_disabled_flag(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.neural_skill_installer import ensure_neural_service_skill_installed

    monkeypatch.setenv("ADAOS_NLU_NEURAL", "0")
    ctx = get_ctx()
    target = Path(ctx.paths.skills_dir()) / "neural_nlu_service_skill"

    assert ensure_neural_service_skill_installed() is None
    assert not target.exists()


def test_ensure_neural_service_skill_installed_is_opt_in_by_default(monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.neural_skill_installer import ensure_neural_service_skill_installed

    monkeypatch.delenv("ADAOS_NLU_NEURAL", raising=False)
    monkeypatch.delenv("ADAOS_NLU_NEURAL_ENABLED", raising=False)
    monkeypatch.delenv("ADAOS_INSTALL_NEURAL_NLU", raising=False)
    ctx = get_ctx()
    target = Path(ctx.paths.skills_dir()) / "neural_nlu_service_skill"

    assert ensure_neural_service_skill_installed() is None
    assert not target.exists()


def test_setup_bootstrap_neural_nlu_respects_cli_flag(monkeypatch, tmp_path):
    from adaos.apps.cli.commands import setup

    calls: list[bool | None] = []

    def _ensure(*, enabled=None):
        calls.append(enabled)
        return tmp_path / "neural_nlu_service_skill"

    monkeypatch.delenv("ADAOS_NLU_NEURAL", raising=False)
    monkeypatch.setattr(setup, "ensure_neural_service_skill_installed", _ensure)
    skipped = {"warnings": []}

    setup._bootstrap_neural_nlu_after_install(skipped, enabled=False)

    assert calls == []
    assert skipped["nlu"]["neural"]["reason"] == "disabled_by_cli"
    assert skipped["warnings"] == []

    installed = {"warnings": []}
    setup._bootstrap_neural_nlu_after_install(installed, enabled=True)

    assert calls == [True]
    assert installed["nlu"]["neural"]["ok"] is True
    assert installed["warnings"] == []


def test_neural_installer_does_not_depend_on_interpreter_data():
    from adaos.services.nlu import neural_skill_installer as installer

    assert "interpreter_data" not in Path(installer.__file__).read_text(encoding="utf-8")
