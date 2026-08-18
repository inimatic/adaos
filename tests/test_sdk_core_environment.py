from pathlib import Path

from adaos.sdk.core.environment import runtime_identity


def test_runtime_identity_is_path_free_and_identifies_current_skill(_autocontext) -> None:
    ctx = _autocontext
    root = Path(ctx.paths.skills_dir()) / "identity_skill"
    root.mkdir(parents=True, exist_ok=True)
    (root / "skill.yaml").write_text(
        "name: identity_skill\nversion: 1.2.3\ncapabilities: []\n",
        encoding="utf-8",
    )
    assert ctx.skill_ctx.set("identity_skill", root)

    identity = runtime_identity()

    assert identity["schema"] == "adaos.runtime.identity.v1"
    assert identity["python_version"]
    assert identity["platform"]
    assert identity["core"]["source_tree"]["kind"] in {"git", "installed"}
    assert identity["core"]["source_tree"]["clean"] in {True, False, None}
    assert identity["current_skill"]["name"] == "identity_skill"
    assert identity["current_skill"]["version"] == "1.2.3"
    assert identity["current_skill"]["manifest_digest"].startswith("sha256:")
    assert str(root) not in str(identity)
