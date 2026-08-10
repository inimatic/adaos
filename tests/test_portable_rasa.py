from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from adaos.services.nlu.portable_rasa import load_portable_rasa


_BUNDLE_ROOT = (
    Path(__file__).parents[1]
    / "src"
    / "adaos"
    / "integrations"
    / "android-node"
    / "app"
    / "src"
    / "main"
    / "python"
    / "adaos"
    / "android"
    / "bundle"
)
_MODEL = _BUNDLE_ROOT / "rasa_mobile_bundle.json.gz"
_RUNTIME = load_portable_rasa(_MODEL)


def test_portable_rasa_bundle_is_pinned_to_the_promoted_model() -> None:
    descriptor = json.loads(
        (_BUNDLE_ROOT / "android_poc_v1.install.json").read_text(encoding="utf-8")
    )
    model = descriptor["models"][0]
    assert model["sha256"] == hashlib.sha256(_MODEL.read_bytes()).hexdigest()
    assert _RUNTIME.describe() == {
        "schema": "adaos.rasa.mobile.v1",
        "model_id": model["model_id"],
        "source_model_sha256": model["source_model_sha256"],
        "rasa_version": "3.6.21",
        "trained_at": "2026-08-10T04:46:18.995211",
        "intent_count": 27,
        "entity_labels": [
            "O",
            "U-modal_id",
            "U-node_ref",
            "U-scenario_id",
            "U-app_id",
            "U-capability_id",
            "B-duration",
            "L-duration",
        ],
        "runtime": "portable_rasa",
    }


@pytest.mark.parametrize(
    ("text", "intent", "confidence"),
    [
        ("привет", "greet", 0.6901980733468518),
        ("какая погода в Москве", "weather.current", 0.8829258239014717),
        ("позови Миру", "conversation.switch_character", 0.4345343843500729),
        ("дай совет по ситуации", "conversation.talk", 0.8023249480019865),
        ("говори короче", "conversation.update_profile", 0.8576209478397628),
    ],
)
def test_portable_rasa_preserves_canonical_intent_predictions(
    text: str,
    intent: str,
    confidence: float,
) -> None:
    result = _RUNTIME.parse(text)

    assert result["intent"]["name"] == intent
    # Export stores portable numeric weights. Predictions must remain within
    # sub-ppm distance of the canonical Rasa/Sklearn pipeline.
    assert result["intent"]["confidence"] == pytest.approx(confidence, abs=2e-7)


def test_portable_rasa_preserves_canonical_crf_entities() -> None:
    result = _RUNTIME.parse("open scenario web_desktop")

    assert result["intent"]["name"] == "desktop.switch_scenario"
    assert result["entities"] == [
        {
            "entity": "scenario_id",
            "start": 14,
            "end": 25,
            "confidence_entity": pytest.approx(0.653014055189146, abs=1e-9),
            "value": "web_desktop",
            "extractor": "CRFEntityExtractor",
            "processors": ["EntitySynonymMapper"],
        }
    ]
