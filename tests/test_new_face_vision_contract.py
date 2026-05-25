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


def _metadata_file(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps({"frame_idx": 0, "ratio_bad_true": 0.1}),
                json.dumps({"frame_idx": 1, "ratio_bad_true": 0.2}),
            ]
        ),
        encoding="utf-8",
    )


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
    assert snapshot["compute"]["mode"] in {"CPU", "GPU"}
    assert "cuda_available" in snapshot["compute"]


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
    assert snapshot["stats"]["target_fps"] == 5.0
    assert "actual_fps" in snapshot["stats"]
    assert snapshot["quality"]["bad_ratio"] == snapshot["latest"]["pred_ratio"]
    assert snapshot["quality"]["threshold"] == snapshot["thresholds"]["warning"]
    assert snapshot["quality"]["color"] in {"success", "danger"}
    assert snapshot["activity"]["label"] == "ready"
    assert snapshot["timeline"]["total_frames"] == 2
    assert snapshot["timeline"]["current_frame"] == 1
    assert snapshot["timeline"]["calculated_count"] == 2
    assert snapshot["timeline"]["calculated_ranges"] == [{"start": 0, "end": 1}]
    assert snapshot["files"]["frames"]["name"] == "frames.zip"
    assert "source" not in snapshot["files"]["frames"]
    assert "path" not in snapshot["files"]["frames"]
    assert snapshot["files"]["frames"]["updated_at"] is not None
    assert snapshot["file_items"][0]["id"] == "frames"
    assert snapshot["file_items"][0]["updated_at"] == snapshot["files"]["frames"]["updated_at"]
    assert snapshot["history"]
    assert snapshot["history"][0]["frame_label"] == "1/2"
    assert snapshot["cache"]["disk_entries"] >= 2

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


def test_new_face_vision_exposes_calculation_state_only_for_uncached_frames(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    engine = engine_cls(tmp_path / "state")
    archive = tmp_path / "frames.zip"
    _frames_zip(archive)

    load_result = engine.load_frames(str(archive))
    assert load_result["ok"] is True
    assert engine.is_frame_cached(0) is False

    mark = engine.begin_calculation_status(0)
    calculating = engine.snapshot()
    assert mark["frame_idx"] == 0
    assert calculating["activity"]["label"] == "Calculate"
    assert calculating["activity"]["color"] == "warning"

    first = engine.process_frame(0)
    ready = engine.snapshot()
    assert first["ok"] is True
    assert engine.is_frame_cached(0) is True
    assert ready["activity"]["label"] == "ready"


def test_new_face_vision_can_step_back_to_previous_cached_frame(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    engine = engine_cls(tmp_path / "state")
    archive = tmp_path / "frames.zip"
    _frames_zip(archive)

    load_result = engine.load_frames(str(archive))
    assert load_result["ok"] is True

    first = engine.process_frame()
    second = engine.process_frame()
    processed_count = engine.snapshot()["stats"]["processed_frames"]

    back = engine.process_relative_frame(-1)
    snapshot = engine.snapshot()

    assert first["frame_idx"] == 0
    assert second["frame_idx"] == 1
    assert back["ok"] is True
    assert back["frame_idx"] == 0
    assert back["navigation"] is True
    assert snapshot["latest"]["frame_idx"] == 0
    assert snapshot["latest"]["navigation"] is True
    assert snapshot["stats"]["processed_frames"] == processed_count
    assert snapshot["stats"]["next_frame"] == 1

    seek = engine.seek_frame(1)
    seek_snapshot = engine.snapshot()
    assert seek["ok"] is True
    assert seek["frame_idx"] == 1
    assert seek_snapshot["latest"]["navigation"] is True
    assert seek_snapshot["stats"]["processed_frames"] == processed_count


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


def test_new_face_vision_persists_prediction_cache_across_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PIL.Image")
    engine_module = _load_engine_module()
    state_dir = tmp_path / "state"
    archive = tmp_path / "frames.zip"
    _frames_zip(archive)

    engine = engine_module.NewFaceVisionEngine(state_dir)
    load_result = engine.load_frames(str(archive))
    assert load_result["ok"] is True
    first = engine.process_frame(0)
    assert first["ok"] is True
    assert first["cached"] is False
    assert list((state_dir / "prediction_cache").glob("*.json"))

    def fail_if_recomputed(self: Any, frame: Any) -> Any:
        raise AssertionError("prediction should come from persistent cache")

    monkeypatch.setattr(engine_module.NewFaceVisionEngine, "_create_dummy_prediction", fail_if_recomputed)
    restarted = engine_module.NewFaceVisionEngine(state_dir)
    assert restarted.snapshot()["timeline"]["calculated_count"] == 1
    cached = restarted.process_frame(0)

    assert cached["ok"] is True
    assert cached["cached"] is True
    assert cached["preview_base64"] == first["preview_base64"]
    assert restarted.snapshot()["cache"]["hits"] == 1


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


def test_new_face_vision_rehydrates_manifest_after_restart(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    state_dir = tmp_path / "state"
    frames_zip = tmp_path / "frames.zip"
    masks_zip = tmp_path / "masks.zip"
    metadata = tmp_path / "meta.jsonl"
    model = tmp_path / "best_full_finetune_v2.pt"
    _frames_zip(frames_zip)
    _frames_zip(masks_zip)
    _metadata_file(metadata)
    model.write_bytes(b"model-placeholder")

    manifest = {
        "schema": "new_face_vision.state.v1",
        "files": {
            "model": {"path": str(model), "name": model.name},
            "frames": {"path": str(frames_zip), "name": frames_zip.name},
            "masks": {"path": str(masks_zip), "name": masks_zip.name},
            "metadata": {"path": str(metadata), "name": metadata.name},
        },
    }
    state_dir.mkdir()
    (state_dir / "state_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    engine = engine_cls(state_dir)
    snapshot = engine.snapshot()

    assert snapshot["status"] == "ready"
    assert snapshot["stats"]["total_frames"] == 2
    assert snapshot["stats"]["loaded_masks"] == 2
    assert snapshot["stats"]["loaded_metadata"] == 2
    assert snapshot["stats"]["model_loaded"] is True
    assert snapshot["model"]["loaded"] is True
    assert snapshot["model"]["materialized"] is False
    assert {item["id"] for item in snapshot["file_items"]} == {"model", "frames", "masks", "metadata"}


def test_new_face_vision_model_load_retries_pytorch26_weights_only_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_module = _load_engine_module()
    calls: list[dict[str, Any]] = []

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

        @staticmethod
        def load(path: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"path": path, **kwargs})
            if kwargs.get("weights_only") is True:
                raise RuntimeError("Weights only load failed. Unsupported operand 123")
            return {"model_state": {"classifier.weight": object()}, "epoch": 7}

    class FakeLayer:
        pass

    class FakeModel:
        def __init__(self) -> None:
            self.classifier = [FakeLayer()]
            self.loaded_state: dict[str, Any] | None = None

        def load_state_dict(self, state: dict[str, Any], strict: bool = False) -> None:
            self.loaded_state = state

        def to(self, device: str) -> None:
            self.device = device

        def eval(self) -> None:
            self.eval_called = True

    class FakeSegmentation:
        @staticmethod
        def deeplabv3_resnet50(**_: Any) -> FakeModel:
            return FakeModel()

    class FakeModels:
        segmentation = FakeSegmentation()

    class FakeTorchvision:
        models = FakeModels()

    class FakeNN:
        @staticmethod
        def Conv2d(*_: Any, **__: Any) -> FakeLayer:
            return FakeLayer()

    monkeypatch.setattr(engine_module, "torch", FakeTorch, raising=False)
    monkeypatch.setattr(engine_module, "torchvision", FakeTorchvision, raising=False)
    monkeypatch.setattr(engine_module, "nn", FakeNN, raising=False)
    monkeypatch.setattr(engine_module, "TF", object(), raising=False)

    model_path = tmp_path / "best_full_finetune_v2.pt"
    model_path.write_bytes(b"checkpoint" * 200)
    engine = engine_module.NewFaceVisionEngine(tmp_path / "state")

    result = engine.load_model(str(model_path))

    assert result["ok"] is True
    assert [call.get("weights_only") for call in calls] == [True, False]


def test_new_face_vision_model_load_rejects_json_placeholder(tmp_path: Path) -> None:
    engine_cls = _load_engine_class()
    model_path = tmp_path / "best_full_finetune_v2.pt"
    model_path.write_text("{}", encoding="utf-8")
    engine = engine_cls(tmp_path / "state")

    result = engine.load_model(str(model_path))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_model_file"
    assert "too small" in result["error"]["message"]


def test_new_face_vision_discovers_legacy_uploads_without_manifest(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    upload_root = tmp_path / "runtime" / "data" / "files" / "uploads"
    frames_zip = upload_root / "frames" / "frames.zip"
    masks_zip = upload_root / "masks" / "masks.zip"
    metadata = upload_root / "metadata" / "meta.jsonl"
    model = upload_root / "model" / "best_full_finetune_v2.pt"
    frames_zip.parent.mkdir(parents=True)
    masks_zip.parent.mkdir(parents=True)
    metadata.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    _frames_zip(frames_zip)
    _frames_zip(masks_zip)
    _metadata_file(metadata)
    model.write_bytes(b"model-placeholder")

    engine = engine_cls(tmp_path / "state", upload_root=upload_root)
    snapshot = engine.snapshot()

    assert snapshot["stats"]["total_frames"] == 2
    assert snapshot["stats"]["loaded_masks"] == 2
    assert snapshot["stats"]["loaded_metadata"] == 2
    assert snapshot["stats"]["model_loaded"] is True
    assert (tmp_path / "state" / "state_manifest.json").exists()


def test_new_face_vision_merges_missing_manifest_entries_from_uploads(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    upload_root = tmp_path / "runtime" / "data" / "files" / "uploads"
    frames_zip = upload_root / "frames" / "frames.zip"
    masks_zip = upload_root / "masks" / "masks.zip"
    metadata = upload_root / "metadata" / "meta.jsonl"
    for path in (frames_zip, masks_zip, metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
    _frames_zip(frames_zip)
    _frames_zip(masks_zip)
    _metadata_file(metadata)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state_manifest.json").write_text(
        json.dumps(
            {
                "schema": "new_face_vision.state.v1",
                "files": {
                    "model": None,
                    "frames": None,
                    "masks": None,
                    "metadata": None,
                },
                "thresholds": {
                    "prediction": 0.35,
                    "warning": 0.05,
                    "alarm": 0.15,
                },
            }
        ),
        encoding="utf-8",
    )

    engine = engine_cls(state_dir, upload_root=upload_root)
    snapshot = engine.snapshot()

    assert snapshot["stats"]["total_frames"] == 2
    assert snapshot["stats"]["loaded_masks"] == 2
    assert snapshot["stats"]["loaded_metadata"] == 2
    assert {item["id"] for item in snapshot["file_items"]} >= {"frames", "masks", "metadata"}


def test_new_face_vision_clear_tombstone_prevents_upload_resurrection(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    engine_cls = _load_engine_class()
    upload_root = tmp_path / "runtime" / "data" / "files" / "uploads"
    frames_zip = upload_root / "frames" / "frames.zip"
    frames_zip.parent.mkdir(parents=True)
    _frames_zip(frames_zip)

    engine = engine_cls(tmp_path / "state", upload_root=upload_root)
    assert engine.snapshot()["stats"]["total_frames"] == 2

    clear_result = engine.clear()
    restarted = engine_cls(tmp_path / "state", upload_root=upload_root)
    manifest = json.loads((tmp_path / "state" / "state_manifest.json").read_text(encoding="utf-8"))

    assert clear_result["ok"] is True
    assert "cleared_at" in manifest
    assert restarted.snapshot()["stats"]["total_frames"] == 0


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
    "input.frameSlider",
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
    lifecycle = skill.get("lifecycle") or {}
    assert lifecycle["persist_before_switch"] == "new_face_vision_persist_state"
    assert lifecycle["rehydrate"] == "new_face_vision_rehydrate"
    tool_names = {tool.get("name") for tool in skill.get("tools") or []}
    assert {
        "new_face_vision_step_back",
        "new_face_vision_step_forward",
        "new_face_vision_seek_frame",
        "new_face_vision_persist_state",
        "new_face_vision_rehydrate",
    } <= tool_names
    tools = {tool.get("name"): tool for tool in skill.get("tools") or []}
    assert tools["new_face_vision_play"]["timeout_seconds"] >= 90
    assert tools["new_face_vision_stop"]["timeout_seconds"] >= 180
    assert tools["new_face_vision_seek_frame"]["timeout_seconds"] >= 180
    assert tools["new_face_vision_process_frame"]["timeout_seconds"] >= 180
    assert tools["new_face_vision_step_forward"]["timeout_seconds"] >= 180


def test_new_face_vision_compacts_uploads_into_modal() -> None:
    scenario = json.loads(
        (ROOT / ".adaos" / "workspace" / "scenarios" / "new_face_vision" / "scenario.json").read_text(encoding="utf-8")
    )
    application = scenario["ui"]["application"]
    page_widgets = application["desktop"]["pageSchema"]["widgets"]

    assert not any(widget.get("type") == "input.fileUpload" for widget in page_widgets)
    assert not any(widget.get("type") == "ui.jsonViewer" for widget in page_widgets)

    controls = next(widget for widget in page_widgets if widget.get("id") == "controls")
    frame = next(widget for widget in page_widgets if widget.get("id") == "frame-stream")
    frame_position = next(widget for widget in page_widgets if widget.get("id") == "frame-position")
    assert frame["inputs"]["retainLastImageOnEmpty"] is True
    assert frame_position["type"] == "input.frameSlider"
    assert frame_position["dataSource"]["path"] == "data/new_face_vision/current/timeline"
    seek_action = next(action for action in frame_position["actions"] if action.get("on") == "change")
    assert seek_action["target"] == "new_face_vision_skill.new_face_vision_seek_frame"
    button_ids = [button["id"] for button in controls["inputs"]["buttons"]]
    assert "step_back" in button_ids
    assert "step_forward" in button_ids
    upload_action = next(action for action in controls["actions"] if action.get("on") == "click:upload")
    assert upload_action["type"] == "openModal"
    assert upload_action["params"]["modalId"] == "newface_upload_modal"

    upload_widgets = application["modals"]["newface_upload_modal"]["schema"]["widgets"]
    assert [widget["type"] for widget in upload_widgets].count("input.fileUpload") == 4
    file_list = next(widget for widget in upload_widgets if widget.get("id") == "loaded-files")
    assert file_list["type"] == "ui.list"
    assert file_list["dataSource"]["path"] == "data/new_face_vision/current/file_items"
    assert file_list["inputs"]["refreshOnStateEmit"] is True
    assert "newface_metrics_modal" in application["modals"]
    metrics_widgets = application["modals"]["newface_metrics_modal"]["schema"]["widgets"]
    table = next(widget for widget in metrics_widgets if widget.get("type") == "ui.table")
    assert table["dataSource"]["path"] == "data/new_face_vision/history"


def test_new_face_vision_places_charts_side_by_side_under_preview_area() -> None:
    scenario = json.loads(
        (ROOT / ".adaos" / "workspace" / "scenarios" / "new_face_vision" / "scenario.json").read_text(encoding="utf-8")
    )
    page_widgets = scenario["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    page_by_id = {widget["id"]: widget for widget in page_widgets}
    page_ids = [widget["id"] for widget in page_widgets]
    assert page_by_id["bad-ratio-stream"]["area"] == "main"
    assert page_by_id["metrics-stream"]["area"] == "main"
    assert page_by_id["frame-stream"]["inputs"]["retainLastImageOnEmpty"] is True
    assert page_by_id["frame-position"]["type"] == "input.frameSlider"
    assert page_ids.index("frame-stream") < page_ids.index("bad-ratio-stream") < page_ids.index("controls")
    assert page_ids.index("frame-stream") < page_ids.index("frame-position") < page_ids.index("bad-ratio-stream")
    assert page_ids.index("frame-stream") < page_ids.index("metrics-stream") < page_ids.index("controls")
    assert page_by_id["compute-state"]["dataSource"]["path"] == "data/new_face_vision/current/compute"
    assert page_by_id["bad-ratio"]["dataSource"]["path"] == "data/new_face_vision/current/quality"
    assert page_by_id["bad-ratio"]["inputs"]["colorPath"] == "color"
    assert page_by_id["pipeline-status"]["dataSource"]["path"] == "data/new_face_vision/current/activity"
    assert page_by_id["fps-state"]["dataSource"]["path"] == "data/new_face_vision/current/stats"
    assert {"total-frames", "model-state", "masks-state", "threshold-state"}.isdisjoint(page_by_id)
    for chart_id in ("bad-ratio-stream", "metrics-stream"):
        chart = page_by_id[chart_id]
        assert chart["inputs"]["detailsModalId"] == "newface_metrics_modal"
        assert chart["inputs"]["showValues"] is False
        assert chart["inputs"]["layoutGroup"] == "newface-charts"
        assert any(action.get("params", {}).get("modalId") == "newface_metrics_modal" for action in chart["actions"])

    webui = json.loads((SKILL_ROOT / "webui.json").read_text(encoding="utf-8"))
    modal_widgets = webui["registry"]["modals"]["newface_vision_modal"]["schema"]["widgets"]
    modal_by_id = {widget["id"]: widget for widget in modal_widgets}
    modal_ids = [widget["id"] for widget in modal_widgets]
    assert modal_by_id["newface_modal_ratio_chart"]["area"] == "main"
    assert modal_by_id["newface_modal_metrics_chart"]["area"] == "main"
    assert modal_by_id["newface_modal_frame"]["inputs"]["retainLastImageOnEmpty"] is True
    assert modal_by_id["newface_modal_frame_position"]["type"] == "input.frameSlider"
    assert modal_by_id["newface_modal_ratio_chart"]["inputs"]["layoutGroup"] == "newface-charts"
    assert modal_by_id["newface_modal_metrics_chart"]["inputs"]["layoutGroup"] == "newface-charts"
    assert modal_ids.index("newface_modal_frame") < modal_ids.index("newface_modal_ratio_chart") < modal_ids.index("newface_modal_controls")
    assert modal_ids.index("newface_modal_frame") < modal_ids.index("newface_modal_metrics_chart") < modal_ids.index("newface_modal_controls")
    assert modal_by_id["newface_modal_compute"]["dataSource"]["path"] == "data/new_face_vision/current/compute"
    assert modal_by_id["newface_modal_bad_ratio"]["dataSource"]["path"] == "data/new_face_vision/current/quality"
    assert modal_by_id["newface_modal_status"]["dataSource"]["path"] == "data/new_face_vision/current/activity"
    assert modal_by_id["newface_modal_fps"]["dataSource"]["path"] == "data/new_face_vision/current/stats"
    assert {"newface_modal_frames", "newface_modal_model", "newface_modal_threshold"}.isdisjoint(modal_by_id)
    metrics_modal = webui["registry"]["modals"]["newface_metrics_modal"]
    metrics_table = metrics_modal["schema"]["widgets"][0]
    assert metrics_table["type"] == "ui.table"
    assert metrics_table["dataSource"]["path"] == "data/new_face_vision/history"
