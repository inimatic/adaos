from __future__ import annotations

import json
from pathlib import Path

from adaos.sdk.builder import conversation
from adaos.services.conversational_compiler import resolve_message
from adaos.services.governed_workflow import compile_definition, new_instance, workflow_ref


EXAMPLE = Path(__file__).parents[1] / "examples" / "conversational-workflow-skill"


def _instance_ref() -> dict:
    definition = compile_definition(json.loads((EXAMPLE / "workflow.json").read_text(encoding="utf-8")))
    instance = new_instance(definition, "release:demo")
    return workflow_ref(
        "workflow",
        instance["instance_id"],
        version=instance["definition_version"],
        generation=instance["generation"],
        digest=instance["definition_digest"],
    )


def test_web_telegram_voice_and_text_share_one_package_bound_proposal() -> None:
    proposals = {
        channel: conversation.emit_intent_proposal(
            EXAMPLE,
            manifest_name="skill.yaml",
            intent_id="submit_release",
            source_text="Submit the release",
            source_message_id=f"message:{channel}",
            conversation_id="conversation:demo",
            locale="en",
            channel=channel,
            modality="voice" if channel == "voice" else "text",
            workflow_instance_ref=_instance_ref(),
        )
        for channel in ("web", "telegram", "voice", "text")
    }

    acts = [proposal["semantic_acts"][0] for proposal in proposals.values()]
    assert {act["kind"] for act in acts} == {"workflow_command"}
    assert {act["command"] for act in acts} == {"submit"}
    assert {act["arguments"]["risk"] for act in acts} == {"isolated_write"}
    assert len({proposal["provenance"]["package_digest"] for proposal in proposals.values()}) == 1
    assert {proposal["input_context"]["channel"] for proposal in proposals.values()} == {
        "web",
        "telegram",
        "voice",
        "text",
    }


def test_semantic_output_is_localized_before_channel_materialization() -> None:
    output = conversation.semantic_output(
        EXAMPLE,
        manifest_name="skill.yaml",
        output_ref="release_submitted",
        conversation_id="conversation:demo",
        locale="en-GB",
    )

    assert output["summary"] == "Release submitted for review."
    assert output["metadata"]["requested_locale"] == "en-GB"
    assert output["metadata"]["locale"] == "en"
    assert output["metadata"]["locale_fallback"] is True
    assert output["provenance"]["package_digest"].startswith("sha256:")


def test_locale_fallback_is_deterministic_and_catalog_bound() -> None:
    bundle = {
        "default_locale": "en",
        "supported_locales": ["en", "ru"],
        "catalog_digest": "sha256:" + "1" * 64,
        "catalogs": {
            "en": {"status.ready": "Ready"},
            "ru": {"status.ready": "Готово"},
        },
    }
    assert resolve_message(bundle, "status.ready", locale="ru-RU")["text"] == "Готово"
    assert resolve_message(bundle, "status.ready", locale="de", fallback="Ready")["text"] == "Ready"
