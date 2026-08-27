from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from adaos.adapters.sdk.inproc_skill_context import InprocSkillContext
from adaos.sdk.data import secrets
from adaos.services.agent_context import use_ctx
from adaos.services.crypto.secrets_service import SecretsService
from adaos.services.skill.secrets_backend import SkillSecretsBackend


class _AllowAllCaps:
    def allows(self, *_args) -> bool:
        return True

    def require(self, *_args) -> None:
        return None


def _skill_source(tmp_path: Path, skill_name: str) -> Path:
    source = (
        tmp_path
        / "workspace"
        / "skills"
        / ".runtime"
        / skill_name
        / "v1.0"
        / "slots"
        / "A"
        / "src"
        / "skills"
        / skill_name
    )
    source.mkdir(parents=True)
    return source


def test_sdk_secrets_follow_context_local_skill_runtime(tmp_path: Path) -> None:
    caps = _AllowAllCaps()
    skill_ctx = InprocSkillContext()
    previous = skill_ctx.get()
    shared_store = tmp_path / "shared" / "secrets.json"
    ctx = SimpleNamespace(
        caps=caps,
        skill_ctx=skill_ctx,
        secrets=SecretsService(SkillSecretsBackend(shared_store), caps),
    )
    alpha_source = _skill_source(tmp_path, "alpha")
    beta_source = _skill_source(tmp_path, "beta")

    try:
        with use_ctx(ctx):
            skill_ctx.set_loaded("alpha", alpha_source)
            secrets.set("TOKEN", "alpha-token")

            skill_ctx.set_loaded("beta", beta_source)
            assert secrets.get("TOKEN") is None
            secrets.set("TOKEN", "beta-token")

            skill_ctx.set_loaded("alpha", alpha_source)
            assert secrets.get("TOKEN") == "alpha-token"
            skill_ctx.set_loaded("beta", beta_source)
            assert secrets.get("TOKEN") == "beta-token"
    finally:
        if previous is None:
            skill_ctx.clear()
        else:
            skill_ctx.set_loaded(
                previous.name,
                previous.path,
                logs_dir=previous.logs_dir,
                service_log_path=previous.service_log_path,
                runtime_log_path=previous.runtime_log_path,
                ui_diagnostics_log_path=previous.ui_diagnostics_log_path,
            )

    assert not shared_store.exists()
    assert (
        tmp_path
        / "workspace"
        / "skills"
        / ".runtime"
        / "alpha"
        / "v1.0"
        / "data"
        / "files"
        / "secrets.json"
    ).exists()
    assert (
        tmp_path
        / "workspace"
        / "skills"
        / ".runtime"
        / "beta"
        / "v1.0"
        / "data"
        / "files"
        / "secrets.json"
    ).exists()
