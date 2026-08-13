"""Verified, device-aware Vosk model installation and selection.

The model files are data, not Python dependencies.  Keeping their catalog and
selection in this module lets desktop and Android use the same policy without
shipping a model inside the application package.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional
from urllib.request import Request, urlopen


CATALOG_SCHEMA = "adaos-stt-model-catalog.v1"
MODEL_MARKER = ".adaos-model.json"
SELECTION_FILE = "selection.json"
_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")

# Hashes and archive sizes were measured from the model archives published by
# Alpha Cephei.  Large/server models can be supplied as custom descriptors;
# they are deliberately not offered as mobile defaults.
MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "vosk-model-small-ru-0.22": {
        "id": "vosk-model-small-ru-0.22",
        "provider": "vosk",
        "language": "ru-RU",
        "quality_tier": "compact",
        "resource_class": "mobile",
        "archive_url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
        "archive_sha256": "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4",
        "archive_bytes": 46_236_750,
        "unpacked_folder": "vosk-model-small-ru-0.22",
        "expected_runtime_memory_mb": 300,
        "recommended_min_memory_mb": 1_024,
        "license": "Apache-2.0",
        "platforms": ["android-arm64", "linux", "windows", "macos"],
    },
    "vosk-model-small-en-us-0.15": {
        "id": "vosk-model-small-en-us-0.15",
        "provider": "vosk",
        "language": "en-US",
        "quality_tier": "compact",
        "resource_class": "mobile",
        "archive_url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "archive_sha256": "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
        "archive_bytes": 41_205_931,
        "unpacked_folder": "vosk-model-small-en-us-0.15",
        "expected_runtime_memory_mb": 300,
        "recommended_min_memory_mb": 1_024,
        "license": "Apache-2.0",
        "platforms": ["android-arm64", "linux", "windows", "macos"],
    },
}

LANG_PRESETS = {
    "ru": "vosk-model-small-ru-0.22",
    "ru-ru": "vosk-model-small-ru-0.22",
    "en": "vosk-model-small-en-us-0.15",
    "en-us": "vosk-model-small-en-us-0.15",
}


def normalize_language(value: Any, default: str = "ru-RU") -> str:
    token = str(value or "").strip().replace("_", "-").lower()
    if not token:
        return default
    parts = token.split("-", 1)
    language = parts[0]
    region = parts[1].upper() if len(parts) > 1 and parts[1] else language.upper()
    return f"{language}-{region}"


def _default_model_id(language: Any) -> str:
    token = normalize_language(language).lower()
    return LANG_PRESETS.get(token) or LANG_PRESETS.get(token.split("-", 1)[0]) or LANG_PRESETS["en"]


def _validate_descriptor(raw: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(raw)
    model_id = str(item.get("id") or "").strip().lower()
    if not _MODEL_ID.fullmatch(model_id):
        raise ValueError("vosk_model_id_invalid")
    sha = str(item.get("archive_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("vosk_model_sha256_required")
    url = str(item.get("archive_url") or "").strip()
    if not url:
        raise ValueError("vosk_model_archive_url_required")
    item.update(
        {
            "id": model_id,
            "provider": "vosk",
            "language": normalize_language(item.get("language")),
            "archive_url": url,
            "archive_sha256": sha,
            "unpacked_folder": str(item.get("unpacked_folder") or model_id).strip(),
            "quality_tier": str(item.get("quality_tier") or "custom").strip(),
            "resource_class": str(item.get("resource_class") or "custom").strip(),
            "expected_runtime_memory_mb": int(item.get("expected_runtime_memory_mb") or 0),
            "recommended_min_memory_mb": int(item.get("recommended_min_memory_mb") or 0),
        }
    )
    return item


def get_model_descriptor(model_id: str, custom: Mapping[str, Any] | None = None) -> dict[str, Any]:
    token = str(model_id or "").strip().lower()
    if custom is not None:
        item = _validate_descriptor(custom)
        if item["id"] != token:
            raise ValueError("vosk_model_descriptor_id_mismatch")
        return item
    try:
        return dict(MODEL_CATALOG[token])
    except KeyError as exc:
        raise ValueError(f"vosk_model_unknown:{token}") from exc


def model_catalog() -> dict[str, Any]:
    return {
        "schema_version": CATALOG_SCHEMA,
        "models": [dict(item) for item in MODEL_CATALOG.values()],
        "custom_models_supported": True,
        "custom_model_requirements": ["id", "language", "archive_url", "archive_sha256"],
    }


def _print_progress(downloaded: int, total: int | None) -> None:
    if not sys.stderr.isatty() or not total:
        return
    width = 32
    fraction = min(1.0, downloaded / total)
    bar = "#" * int(width * fraction) + "-" * (width - int(width * fraction))
    sys.stderr.write(f"\r[Vosk] |{bar}| {int(fraction * 100):3d}%")
    sys.stderr.flush()
    if downloaded >= total:
        sys.stderr.write("\n")


def _download(url: str, destination: Path) -> None:
    mirror = str(os.environ.get("ADAOS_VOSK_MIRROR") or "").strip()
    candidates = [mirror, url] if mirror else [url]
    error: Exception | None = None
    for candidate in candidates:
        try:
            request = Request(candidate, headers={"User-Agent": "AdaOS/1.0"})
            with urlopen(request, timeout=60) as response, destination.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0) or None
                downloaded = 0
                while True:
                    block = response.read(128 * 1024)
                    if not block:
                        break
                    output.write(block)
                    downloaded += len(block)
                    _print_progress(downloaded, total)
            return
        except Exception as exc:  # pragma: no cover - network failure shape varies
            error = exc
            destination.unlink(missing_ok=True)
    raise RuntimeError(f"vosk_model_download_failed:{error}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_extract(source: Path, destination: Path, *, max_uncompressed_bytes: int) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as archive:
        total = 0
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            unix_mode = info.external_attr >> 16
            total += max(0, int(info.file_size))
            if (
                not name
                or path.is_absolute()
                or ".." in path.parts
                or (path.parts and ":" in path.parts[0])
                or stat.S_ISLNK(unix_mode)
            ):
                raise ValueError("vosk_model_archive_unsafe")
            if total > max_uncompressed_bytes:
                raise ValueError("vosk_model_archive_too_large")
        archive.extractall(destination)


def _selection_path(base_dir: Path) -> Path:
    return base_dir / SELECTION_FILE


def _read_selection(base_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads(_selection_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def select_vosk_model(language: str, model_id: str, base_dir: Path | str = "models/vosk") -> dict[str, Any]:
    base = Path(base_dir)
    target = base / str(model_id).strip().lower()
    if not (target / MODEL_MARKER).is_file():
        raise FileNotFoundError(f"vosk_model_not_installed:{model_id}")
    marker = json.loads((target / MODEL_MARKER).read_text(encoding="utf-8"))
    expected = normalize_language(language)
    actual = normalize_language(marker.get("language"))
    if actual.split("-", 1)[0] != expected.split("-", 1)[0]:
        raise ValueError(f"vosk_model_language_mismatch:{actual}:{expected}")
    selection = _read_selection(base)
    selection.setdefault("schema_version", "adaos-stt-model-selection.v1")
    selection.setdefault("languages", {})[expected] = marker["id"]
    selection["updated_at_epoch_ms"] = int(time.time() * 1000)
    _atomic_json(_selection_path(base), selection)
    return selection


def install_vosk_model(
    model_id: str,
    base_dir: Path | str = "models/vosk",
    *,
    local_zip: Path | str | None = None,
    descriptor: Mapping[str, Any] | None = None,
    select: bool = True,
) -> Path:
    item = get_model_descriptor(model_id, descriptor)
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = base / item["id"]
    marker_path = target / MODEL_MARKER
    if marker_path.is_file():
        return target

    temporary_root = Path(tempfile.mkdtemp(prefix=f".{item['id']}-", dir=base))
    try:
        archive_path = temporary_root / "model.zip"
        source_zip_value = local_zip or os.environ.get("ADAOS_VOSK_ZIP")
        if source_zip_value:
            source_zip = Path(source_zip_value)
            if not source_zip.is_file():
                raise FileNotFoundError(f"vosk_model_archive_not_found:{source_zip}")
            shutil.copyfile(source_zip, archive_path)
        else:
            _download(item["archive_url"], archive_path)
        actual_hash = _sha256(archive_path)
        if actual_hash != item["archive_sha256"]:
            raise ValueError(f"vosk_model_sha256_mismatch:{actual_hash}")

        extracted = temporary_root / "extracted"
        archive_bytes = max(int(item.get("archive_bytes") or 0), archive_path.stat().st_size)
        _safe_extract(archive_path, extracted, max_uncompressed_bytes=max(512 * 1024 * 1024, archive_bytes * 30))
        unpacked = extracted / item["unpacked_folder"]
        if not unpacked.is_dir():
            directories = [entry for entry in extracted.iterdir() if entry.is_dir()]
            if len(directories) != 1:
                raise ValueError("vosk_model_archive_layout_invalid")
            unpacked = directories[0]
        if not any(unpacked.iterdir()):
            raise ValueError("vosk_model_empty")

        stage = base / f".{item['id']}.installing"
        if stage.exists():
            shutil.rmtree(stage)
        shutil.move(str(unpacked), stage)
        marker = {
            **item,
            "schema_version": "adaos-stt-model-install.v1",
            "installed_at_epoch_ms": int(time.time() * 1000),
            "archive_sha256_actual": actual_hash,
            "verification": {},
        }
        _atomic_json(stage / MODEL_MARKER, marker)
        if target.exists():
            shutil.rmtree(target)
        stage.replace(target)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    if select:
        select_vosk_model(item["language"], item["id"], base)
    return target


def ensure_vosk_model(
    lang: str = "en",
    base_dir: Path | str = "models/vosk",
    local_zip: Optional[Path | str] = None,
    *,
    model_id: str | None = None,
    descriptor: Mapping[str, Any] | None = None,
) -> Path:
    """Backward-compatible install entry point used by CLI and STT APIs."""
    selected_id = model_id or _default_model_id(lang)
    return install_vosk_model(selected_id, base_dir, local_zip=local_zip, descriptor=descriptor)


def installed_models(base_dir: Path | str = "models/vosk") -> list[dict[str, Any]]:
    base = Path(base_dir)
    result: list[dict[str, Any]] = []
    if not base.is_dir():
        return result
    for marker_path in sorted(base.glob(f"*/{MODEL_MARKER}")):
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(marker, dict):
            result.append({**marker, "path": str(marker_path.parent.resolve())})
    return result


def resolve_vosk_model(
    language: str,
    base_dir: Path | str = "models/vosk",
    *,
    model_id: str | None = None,
) -> Path | None:
    base = Path(base_dir)
    selected = str(model_id or "").strip().lower()
    if not selected:
        expected = normalize_language(language)
        languages = _read_selection(base).get("languages") or {}
        selected = str(languages.get(expected) or "").strip().lower()
    if selected and (base / selected / MODEL_MARKER).is_file():
        return base / selected

    # Read-only compatibility with installations made by the old manager.
    legacy = "ru-ru" if normalize_language(language).lower().startswith("ru") else "en-us"
    legacy_path = base / legacy
    return legacy_path if legacy_path.is_dir() and any(legacy_path.iterdir()) else None


def mark_model_verified(
    model_id: str,
    base_dir: Path | str,
    *,
    device_id: str,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(base_dir) / str(model_id).strip().lower()
    marker_path = target / MODEL_MARKER
    if not marker_path.is_file():
        raise FileNotFoundError(f"vosk_model_not_installed:{model_id}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    verification = dict(marker.get("verification") or {})
    verification[str(device_id or "local").strip() or "local"] = {
        "verified": True,
        "verified_at_epoch_ms": int(time.time() * 1000),
        "metrics": dict(metrics or {}),
    }
    marker["verification"] = verification
    _atomic_json(marker_path, marker)
    return marker


def is_model_verified(model: Mapping[str, Any], device_id: str) -> bool:
    value = (model.get("verification") or {}).get(str(device_id or "local").strip() or "local") or {}
    return bool(value.get("verified"))


def recommend_model(
    language: str,
    base_dir: Path | str,
    *,
    total_memory_mb: int | None,
    device_id: str,
    require_verified: bool = True,
) -> dict[str, Any] | None:
    expected = normalize_language(language).split("-", 1)[0]
    quality_rank = {"compact": 1, "balanced": 2, "accurate": 3, "server": 4, "custom": 0}
    candidates = []
    for item in installed_models(base_dir):
        if normalize_language(item.get("language")).split("-", 1)[0] != expected:
            continue
        if total_memory_mb and int(item.get("recommended_min_memory_mb") or 0) > int(total_memory_mb):
            continue
        if require_verified and not is_model_verified(item, device_id):
            continue
        candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (quality_rank.get(str(item.get("quality_tier")), 0), -int(item.get("expected_runtime_memory_mb") or 0)), reverse=True)
    return candidates[0]


__all__ = [
    "CATALOG_SCHEMA",
    "LANG_PRESETS",
    "MODEL_CATALOG",
    "MODEL_MARKER",
    "ensure_vosk_model",
    "get_model_descriptor",
    "install_vosk_model",
    "installed_models",
    "is_model_verified",
    "mark_model_verified",
    "model_catalog",
    "normalize_language",
    "recommend_model",
    "resolve_vosk_model",
    "select_vosk_model",
]
