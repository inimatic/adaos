from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / ".adaos" / "workspace" / "skills" / "new_face_vision_skill"


def _load_engine_class():
    if str(SKILL_ROOT) not in sys.path:
        sys.path.insert(0, str(SKILL_ROOT))
    from service.engine import NewFaceVisionEngine

    return NewFaceVisionEngine


def _load_engine_module():
    if str(SKILL_ROOT) not in sys.path:
        sys.path.insert(0, str(SKILL_ROOT))
    from service import engine as engine_module

    return engine_module


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png_bytes(size: tuple[int, int] = (512, 512)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    width, height = size
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend(((x * 17 + y * 31) % 256, (x * 47 + y * 11) % 256, (x * 7 + y * 59) % 256))
    buf = io.BytesIO()
    Image.frombytes("RGB", size, bytes(pixels)).save(buf, format="PNG")
    return buf.getvalue()


def _frames_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("frame_000.png", _png_bytes((240, 240, 240)))
        zf.writestr("frame_001.png", _png_bytes((40, 40, 40)))


def _large_frames_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("frame_000.png", _noisy_png_bytes())


def test_new_face_vision_engine_snapshot_imports_without_image_dependencies(tmp_path: Path) -> None:
    engine_cls = _load_engine_class()
    engine = engine_cls(tmp_path / "state")

    snapshot = engine.snapshot()

    assert snapshot["ok"] is True
    assert snapshot["status"] == "init"
    assert snapshot["history"] == []


def test_new_face_vision_retries_late_image_dependency_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PIL.Image")
    pytest.importorskip("numpy")
    engine_module = _load_engine_module()
    archive = tmp_path / "frames.zip"
    _frames_zip(archive)

    monkeypatch.setattr(engine_module, "Image", None, raising=False)
    monkeypatch.setattr(engine_module, "np", None, raising=False)
    monkeypatch.setattr(engine_module, "_pillow_import_error", ImportError("stale pillow import"), raising=False)
    monkeypatch.setattr(engine_module, "_numpy_import_error", ImportError("stale numpy import"), raising=False)

    engine = engine_module.NewFaceVisionEngine(tmp_path / "state")
    load_result = engine.load_frames(str(archive))

    assert load_result["ok"] is True
    assert load_result["total_frames"] == 2
    assert engine_module.Image is not None
    assert engine_module.np is not None


def test_new_face_vision_snapshot_stays_compact_and_stream_payloads_hold_preview(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    engine = engine_cls(tmp_path / "state")
    archive = tmp_path / "frames.zip"
    _frames_zip(archive)

    load_result = engine.load_frames(str(archive), source_ref={"uri": "skill://upload/frames.zip"})
    assert load_result["ok"] is True

    first = engine.process_frame()
    second = engine.process_frame()
    assert first["frame_idx"] == 0
    assert second["frame_idx"] == 1

    snapshot = engine.snapshot()
    assert "preview_base64" not in snapshot
    assert "preview_base64" not in snapshot["latest"]
    assert snapshot["latest"]["frame_idx"] == 1
    assert snapshot["latest"]["seq"] == 2
    assert snapshot["playback"]["run_id"] == snapshot["latest"]["run_id"]
    assert snapshot["stats"]["processed_frames"] == 2
    assert snapshot["stats"]["next_frame"] == 0
    assert snapshot["files"]["frames"]["source"]["uri"] == "skill://upload/frames.zip"
    assert snapshot["files"]["frames"]["updated_at"] is not None
    assert snapshot["file_items"][0]["id"] == "frames"
    assert snapshot["file_items"][0]["updated_at"] == snapshot["files"]["frames"]["updated_at"]

    frame_payload = engine.frame_stream_payload(second)
    metrics_payload = engine.metrics_stream_payload(second)
    assert frame_payload["id"] == second["id"]
    assert frame_payload["seq"] == second["seq"]
    assert frame_payload["run_id"] == second["run_id"]
    assert frame_payload["image"]["encoding"] == "base64"
    assert frame_payload["image"]["data"] == ""
    assert frame_payload["image"]["src"].startswith("data:image/jpeg;base64,")
    assert metrics_payload["id"] == second["id"]
    assert metrics_payload["seq"] == second["seq"]
    assert metrics_payload["series"]["pred_ratio"] == second["pred_ratio"]
    assert metrics_payload["series"]["iou"] == second["metrics"]["iou"]


def test_new_face_vision_frame_preview_is_compact_jpeg_stream_payload(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    engine = engine_cls(tmp_path / "state")
    archive = tmp_path / "frames.zip"
    _large_frames_zip(archive)

    load_result = engine.load_frames(str(archive))
    assert load_result["ok"] is True

    result = engine.process_frame()
    frame_payload = engine.frame_stream_payload(result)
    payload_size = len(json.dumps(frame_payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    encoded = frame_payload["image"]["src"].split(",", 1)[1]

    assert frame_payload["image"]["mime"] == "image/jpeg"
    assert frame_payload["image"]["data"] == ""
    assert base64.b64decode(encoded).startswith(b"\xff\xd8")
    assert payload_size < 21_000


def test_new_face_vision_removes_stale_uploads_after_successful_load(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    engine = engine_cls(tmp_path / "state")
    upload_dir = tmp_path / ".runtime" / "new_face_vision_skill" / "v0.2" / "data" / "files" / "uploads" / "frames"
    upload_dir.mkdir(parents=True)
    stale = upload_dir / "frames.zip"
    current = upload_dir / "frames-20260521-1.zip"
    _frames_zip(stale)
    _frames_zip(current)

    result = engine.load_frames(str(current), source_ref={"purpose": "frames", "path": str(current)})

    assert result["ok"] is True
    assert current.exists()
    assert not stale.exists()
    cleanup = engine.snapshot()["files"]["frames"]["cleanup"]
    assert cleanup["deleted_count"] == 1
    assert cleanup["deleted_names"] == ["frames.zip"]


def test_new_face_vision_errors_are_normalized_and_projectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine_module = _load_engine_module()
    monkeypatch.setattr(engine_module, "Image", object(), raising=False)
    monkeypatch.setattr(engine_module, "np", object(), raising=False)
    engine = engine_module.NewFaceVisionEngine(tmp_path / "state")

    result = engine.process_frame()

    assert result["ok"] is False
    assert result["error"]["code"] == "frames_missing"
    assert result["error"]["message"] == "No frames loaded"
    assert result["error"]["retryable"] is False

    snapshot = engine.snapshot()
    assert snapshot["status"] == "error"
    assert snapshot["error"]["code"] == "frames_missing"
    assert snapshot["operation"]["id"] == "process_frame"
    assert snapshot["operation"]["error"]["code"] == "frames_missing"
    assert "preview_base64" not in snapshot["latest"]


SUPPORTED_CLIENT_WIDGET_TYPES = {
    "collection.grid",
    "collection.tree",
    "visual.metricTile",
    "visual.qrCode",
    "feedback.log",
    "feedback.statusBar",
    "ui.chat",
    "ui.voiceInput",
    "ui.list",
    "ui.table",
    "ui.form",
    "ui.actions",
    "ui.jsonViewer",
    "item.textEditor",
    "item.codeViewer",
    "item.details",
    "input.commandBar",
    "input.fileUpload",
    "input.text",
    "input.selector",
    "desktop.widgets",
    "visual.frameViewer",
    "visual.image",
    "visual.timeseriesChart",
    "media.videoBrowser",
    "host.webspaceControls",
}


def _collect_widget_types(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        raw_type = value.get("type")
        if isinstance(raw_type, str) and "." in raw_type and ("id" in value or "area" in value):
            out.append(raw_type)
        for nested in value.values():
            _collect_widget_types(nested, out)
    elif isinstance(value, list):
        for item in value:
            _collect_widget_types(item, out)


def test_new_face_vision_uses_only_client_supported_widget_types() -> None:
    files = [
        ROOT / ".adaos" / "workspace" / "scenarios" / "new_face_vision" / "scenario.json",
        SKILL_ROOT / "webui.json",
    ]
    unknown: dict[str, list[str]] = {}

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        found: list[str] = []
        _collect_widget_types(payload, found)
        missing = sorted({item for item in found if item not in SUPPORTED_CLIENT_WIDGET_TYPES})
        if missing:
            unknown[str(path.relative_to(ROOT))] = missing

    assert unknown == {}


def test_new_face_vision_declares_yjs_stream_route_balance() -> None:
    skill = yaml.safe_load((SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8"))
    dependencies = {
        str(dep).lower().split(">", 1)[0].split("<", 1)[0].split("=", 1)[0].strip()
        for dep in skill.get("dependencies") or []
    }
    assert {"pillow", "numpy", "torch", "torchvision"} <= dependencies

    routes = skill.get("data_routes") or []

    assert any(
        route.get("route") == "yjs"
        and route.get("projection_slot") == "new_face_vision.current"
        and route.get("budget", {}).get("max_payload_bytes", 0) <= 12288
        for route in routes
    )

    stream_routes = {route.get("receiver"): route for route in routes if route.get("route") == "stream"}
    assert {"newface_vision_frame", "newface_vision_metrics", "newface_vision_progress"} <= set(stream_routes)
    assert stream_routes["newface_vision_frame"]["budget"]["max_payload_bytes"] <= 65536
    assert stream_routes["newface_vision_frame"]["budget"]["snapshot_policy"] == "on_subscribe"
    assert stream_routes["newface_vision_metrics"]["budget"]["max_items"] <= 120

    webui = json.loads((SKILL_ROOT / "webui.json").read_text(encoding="utf-8"))
    receivers = webui["webio"]["receivers"]
    assert receivers["newface_vision_frame"]["mode"] == "replace"
    assert receivers["newface_vision_frame"]["budget"]["maxPayloadBytes"] <= 65536
    assert receivers["newface_vision_frame"]["snapshotPolicy"] == "on_subscribe"
    assert receivers["newface_vision_metrics"]["mode"] == "append"
    assert receivers["newface_vision_metrics"]["collectionKey"] == "points"
    assert receivers["newface_vision_metrics"]["dedupeBy"] == "id"
    assert receivers["newface_vision_metrics"]["maxItems"] <= 120


def test_new_face_vision_compacts_uploads_into_modal() -> None:
    scenario = json.loads(
        (ROOT / ".adaos" / "workspace" / "scenarios" / "new_face_vision" / "scenario.json").read_text(encoding="utf-8")
    )
    application = scenario["ui"]["application"]
    page_widgets = application["desktop"]["pageSchema"]["widgets"]

    assert not any(widget.get("type") == "input.fileUpload" for widget in page_widgets)
    assert not any(widget.get("type") == "ui.jsonViewer" for widget in page_widgets)

    controls = next(widget for widget in page_widgets if widget.get("id") == "controls")
    upload_action = next(action for action in controls["actions"] if action.get("on") == "click:upload")
    assert upload_action["type"] == "openModal"
    assert upload_action["params"]["modalId"] == "newface_upload_modal"

    upload_widgets = application["modals"]["newface_upload_modal"]["schema"]["widgets"]
    assert [widget["type"] for widget in upload_widgets].count("input.fileUpload") == 4
    file_list = next(widget for widget in upload_widgets if widget.get("id") == "loaded-files")
    assert file_list["type"] == "ui.list"
    assert file_list["dataSource"]["path"] == "data/new_face_vision/current/file_items"
    assert file_list["inputs"]["refreshOnStateEmit"] is True


def test_new_face_vision_places_charts_under_preview_area() -> None:
    scenario = json.loads(
        (ROOT / ".adaos" / "workspace" / "scenarios" / "new_face_vision" / "scenario.json").read_text(encoding="utf-8")
    )
    page_widgets = scenario["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    page_by_id = {widget["id"]: widget for widget in page_widgets}
    assert page_by_id["bad-ratio-stream"]["area"] == "main"
    assert page_by_id["metrics-stream"]["area"] == "main"

    webui = json.loads((SKILL_ROOT / "webui.json").read_text(encoding="utf-8"))
    modal_widgets = webui["registry"]["modals"]["newface_vision_modal"]["schema"]["widgets"]
    modal_by_id = {widget["id"]: widget for widget in modal_widgets}
    assert modal_by_id["newface_modal_ratio_chart"]["area"] == "main"
    assert modal_by_id["newface_modal_metrics_chart"]["area"] == "main"
