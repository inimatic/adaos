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
    assert (target / "requirements.in").exists()
    assert (target / "handlers" / "main.py").exists()


def test_interpreter_cli_rasa_service_url_bootstraps_template_without_starting(monkeypatch):
    import importlib
    import sys
    import types

    from adaos.services.agent_context import get_ctx

    ctx = get_ctx()
    target = Path(ctx.paths.skills_dir()) / "rasa_nlu_service_skill"
    assert not target.exists()

    bootstrap_stub = types.ModuleType("adaos.apps.bootstrap")
    bootstrap_stub.get_ctx = get_ctx
    monkeypatch.setitem(sys.modules, "adaos.apps.bootstrap", bootstrap_stub)
    interpreter_cli = importlib.import_module("adaos.apps.cli.commands.interpreter")

    assert interpreter_cli._rasa_service_url(start=False) == "http://127.0.0.1:18092"
    assert (target / "skill.yaml").exists()
