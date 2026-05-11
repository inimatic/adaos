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


def test_ensure_rasa_service_skill_installed_refreshes_managed_files():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.rasa_skill_installer import ensure_rasa_service_skill_installed

    ctx = get_ctx()
    target = Path(ctx.paths.skills_dir()) / "rasa_nlu_service_skill"
    (target / "handlers").mkdir(parents=True)
    (target / "skill.yaml").write_text("name: stale\n", encoding="utf-8")
    (target / "handlers" / "main.py").write_text("stale handler\n", encoding="utf-8")
    (target / "custom.txt").write_text("keep me\n", encoding="utf-8")

    installed = ensure_rasa_service_skill_installed()

    assert installed == target
    assert "Local Rasa NLU-only service" in (target / "skill.yaml").read_text(encoding="utf-8")
    assert "AdaOSRasaNLU" in (target / "handlers" / "main.py").read_text(encoding="utf-8")
    assert (target / "requirements.in").exists()
    assert (target / "custom.txt").read_text(encoding="utf-8").strip() == "keep me"


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


def test_interpreter_cli_rasa_service_url_reuses_healthy_service(monkeypatch):
    import importlib
    import sys
    import types

    from adaos.services.agent_context import get_ctx

    bootstrap_stub = types.ModuleType("adaos.apps.bootstrap")
    bootstrap_stub.get_ctx = get_ctx
    monkeypatch.setitem(sys.modules, "adaos.apps.bootstrap", bootstrap_stub)
    interpreter_cli = importlib.import_module("adaos.apps.cli.commands.interpreter")

    monkeypatch.setattr(
        interpreter_cli,
        "_http_get_json",
        lambda url, *, timeout_ms=1000: {"ok": True} if url.endswith("/health") else None,
    )

    class Supervisor:
        async def refresh_discovered(self, force=False):
            raise AssertionError("healthy Rasa service should be reused")

        async def start(self, name):
            raise AssertionError("healthy Rasa service should not be restarted")

        def resolve_base_url(self, name):
            return None

    monkeypatch.setattr(interpreter_cli, "get_service_supervisor", lambda: Supervisor())

    assert interpreter_cli._rasa_service_url(start=True) == "http://127.0.0.1:18092"


def test_interpreter_cli_rasa_train_records_successful_service_training(monkeypatch, tmp_path):
    import importlib
    import sys
    import types

    from adaos.services.agent_context import get_ctx

    bootstrap_stub = types.ModuleType("adaos.apps.bootstrap")
    bootstrap_stub.get_ctx = get_ctx
    monkeypatch.setitem(sys.modules, "adaos.apps.bootstrap", bootstrap_stub)
    interpreter_cli = importlib.import_module("adaos.apps.cli.commands.interpreter")

    records = []

    class Workspace:
        def build_rasa_project(self):
            return tmp_path / "rasa_project"

        def record_training(self, *, note=None, extra=None):
            records.append({"note": note, "extra": extra})
            return {"trained_at": "now"}

    monkeypatch.setattr(interpreter_cli, "_workspace", lambda: Workspace())
    monkeypatch.setattr(interpreter_cli, "sync_from_scenarios_and_skills", lambda ctx: None)
    monkeypatch.setattr(interpreter_cli, "_rasa_service_url", lambda: "http://127.0.0.1:18092")
    monkeypatch.setattr(
        interpreter_cli,
        "_http_post_json",
        lambda url, payload, *, timeout_ms: {"ok": True, "model_path": str(tmp_path / "interpreter_latest.tar.gz")},
    )

    interpreter_cli.train(note="smoke", dry_run=False, engine="rasa")

    assert records == [
        {
            "note": "smoke",
            "extra": {
                "engine": "rasa_service",
                "model_path": str(tmp_path / "interpreter_latest.tar.gz"),
            },
        }
    ]
