from pathlib import Path


def test_ensure_rasa_service_skill_installed_creates_skill_tree():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.rasa_skill_installer import ensure_rasa_service_skill_installed

    ctx = get_ctx()
    skills_root = Path(ctx.paths.skills_dir())
    target = skills_root / "rasa_nlu_service_skill"
    assert not target.exists()

    installed = ensure_rasa_service_skill_installed()

    assert installed is not None
    assert installed == target
    assert (target / "skill.yaml").exists()
    assert (target / "handlers" / "main.py").exists()

