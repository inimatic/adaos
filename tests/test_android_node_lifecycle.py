from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[1] / "src" / "adaos" / "integrations" / "android-node"
APP = ROOT / "app"
MAIN = APP / "src" / "main"
JAVA = MAIN / "java" / "dev" / "adaos" / "androidnode"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def test_android_special_use_manifest_keeps_api_26_floor() -> None:
    gradle = (APP / "build.gradle").read_text(encoding="utf-8")
    manifest = ET.parse(MAIN / "AndroidManifest.xml").getroot()

    assert "minSdk = 26" in gradle
    assert "targetSdk = 36" in gradle
    permissions = {
        element.attrib[f"{ANDROID}name"]
        for element in manifest.findall("uses-permission")
    }
    assert "android.permission.FOREGROUND_SERVICE_SPECIAL_USE" in permissions
    assert "android.permission.FOREGROUND_SERVICE_MICROPHONE" in permissions
    assert "android.permission.RECORD_AUDIO" in permissions
    assert "android.permission.FOREGROUND_SERVICE_DATA_SYNC" not in permissions
    assert "android.permission.RECEIVE_BOOT_COMPLETED" in permissions

    service = manifest.find("application/service")
    assert service is not None
    assert service.attrib[f"{ANDROID}name"] == ".NodeService"
    assert set(service.attrib[f"{ANDROID}foregroundServiceType"].split("|")) == {"specialUse", "microphone"}
    subtype = service.find("property")
    assert subtype is not None
    assert subtype.attrib[f"{ANDROID}name"] == (
        "android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
    )
    assert "AdaOS node" in subtype.attrib[f"{ANDROID}value"]


def test_android_autostart_is_explicit_sticky_and_boot_restored() -> None:
    manifest = ET.parse(MAIN / "AndroidManifest.xml").getroot()
    receiver = manifest.find("application/receiver")
    assert receiver is not None
    assert receiver.attrib[f"{ANDROID}name"] == ".NodeBootReceiver"
    actions = {
        action.attrib[f"{ANDROID}name"]
        for action in receiver.findall("intent-filter/action")
    }
    assert actions == {
        "android.intent.action.BOOT_COMPLETED",
        "android.intent.action.MY_PACKAGE_REPLACED",
    }

    receiver_source = (JAVA / "NodeBootReceiver.kt").read_text(encoding="utf-8")
    service_source = (JAVA / "NodeService.kt").read_text(encoding="utf-8")
    activity_source = (JAVA / "MainActivity.kt").read_text(encoding="utf-8")
    assert "NodeLifecycleStore.desiredRunning(context)" in receiver_source
    assert "context.startForegroundService" in receiver_source
    assert service_source.count("return START_STICKY") >= 2
    assert "START_REASON_STICKY_RESTART" in service_source
    assert "NodeLifecycleStore.setDesiredRunning(this, true)" in activity_source
    assert "NodeLifecycleStore.setDesiredRunning(this, false)" in activity_source


def test_android_skill_verifier_skips_unrelated_stream_events() -> None:
    verifier_path = ROOT / "verify_android_skills.py"
    spec = importlib.util.spec_from_file_location("_android_skill_verifier", verifier_path)
    assert spec is not None and spec.loader is not None
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)

    class FakeWebSocket:
        def __init__(self) -> None:
            self.messages = iter(
                [
                    {"kind": "browser.session.updated"},
                    {"kind": "webio.stream.desktop.demo_metrics.events", "data": []},
                ]
            )

        def recv(self, *, timeout: float) -> str:
            assert timeout > 0
            return json.dumps(next(self.messages))

    event = verifier._recv_event(
        FakeWebSocket(),
        "webio.stream.desktop.demo_metrics.events",
    )
    assert event["kind"] == "webio.stream.desktop.demo_metrics.events"
