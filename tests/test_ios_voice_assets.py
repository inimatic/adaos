"""The offline wizard ships complete, bounded audio matching its text catalog."""
import hashlib
import json
from pathlib import Path
import wave


RESOURCES = Path(__file__).resolve().parents[1] / "jailbreak/smoke/Resources"


def test_activation_fixture_is_bounded_known_pcm_with_matching_receipt():
    path = RESOURCES / "Fixtures/arseniy-check.wav"
    receipt = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert receipt["schema"] == "adaos.ios.activation-fixture.v1"
    assert receipt["text"].startswith("Арсений,")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]
    with wave.open(str(path)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        assert 1 < audio.getnframes() / 16000 < 12
        assert path.stat().st_size == 44 + audio.getnframes() * 2
        assert receipt["seconds"] == audio.getnframes() / 16000


def test_offline_prompts_have_matching_localized_audio():
    catalog_bytes = (RESOURCES / "onboarding-prompts.json").read_bytes().replace(b"\r\n", b"\n")
    catalog = json.loads(catalog_bytes)
    receipt = json.loads((RESOURCES / "Voice/assets.json").read_text(encoding="utf-8"))
    assert catalog["schema"] == "adaos.ios.voice-pack.v1"
    assert receipt["catalog_sha256"] == hashlib.sha256(catalog_bytes).hexdigest()
    assert set(catalog["locales"]) == {"ru", "en"}
    assert set(catalog["locales"]["ru"]) == set(catalog["locales"]["en"])
    assert {"welcome", "speaker", "microphone", "connection"} <= set(catalog["locales"]["ru"])
    expected = set()
    for locale, prompts in catalog["locales"].items():
        for prompt in prompts.values():
            assert isinstance(prompt["text"], str) and 1 < len(prompt["text"]) < 1000
            name = prompt["audio"]
            assert Path(name).name == name and name.startswith(locale + "_") and name.endswith(".wav")
            path = RESOURCES / "Voice" / name
            expected.add(name)
            assert path.stat().st_size < 1_500_000
            assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["files"][name]["sha256"]
            with wave.open(str(path)) as audio:
                assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
                assert 0.2 < audio.getnframes() / audio.getframerate() < 45
    assert set(receipt["files"]) == expected
    assert {path.name for path in (RESOURCES / "Voice").glob("*.wav")} == expected
