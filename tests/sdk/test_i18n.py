from __future__ import annotations

import json

from adaos.sdk.data.context import clear_current_skill, set_current_skill
from adaos.sdk.data.i18n import _
from adaos.services.agent_context import clear_ctx, get_ctx


def test_i18n_preboot():
    clear_ctx()
    assert _("cli.help") == "AdaOS CLI \u2013 managing skills, tests and Runtime"


def test_i18n_runtime_keys_use_current_skill_scope():
    ctx = get_ctx()
    skill_dir = ctx.paths.skills_workspace_dir() / "weather_skill"
    (skill_dir / "i18n").mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        "name: weather_skill\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (skill_dir / "i18n" / "en.json").write_text(
        json.dumps(
            {
                "runtime.weather.errors.status": "Weather API returned status {status}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        assert set_current_skill("weather_skill") is True
        assert _("runtime.weather.errors.status", status=503) == "Weather API returned status 503"
    finally:
        clear_current_skill()
