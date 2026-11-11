"""Bounded checks for the lab's device framing, dependency integrity and binary gate."""
from __future__ import annotations

import importlib.util
import json
import plistlib
import struct
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "jailbreak_lab.py"
SPEC = importlib.util.spec_from_file_location("jailbreak_lab", MODULE_PATH)
lab = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lab
SPEC.loader.exec_module(lab)


class FragmentedSocket:
    def __init__(self, data: bytes):
        self.data = data
        self.sent = b""

    def recv(self, length):
        chunk, self.data = self.data[:min(length, 3)], self.data[min(length, 3):]
        return chunk

    def sendall(self, data):
        self.sent += data


def test_usbmux_reassembles_fragmented_packets():
    body = plistlib.dumps({"DeviceList": []})
    connection = FragmentedSocket(struct.pack("<IIII", len(body) + 16, 1, 8, 1) + body)
    assert lab.mux_request(connection, {"MessageType": "ListDevices"}) == {"DeviceList": []}
    assert plistlib.loads(connection.sent[16:])["MessageType"] == "ListDevices"


@pytest.mark.parametrize("data", [b"", struct.pack("<IIII", 12, 1, 8, 1), struct.pack("<IIII", lab.MAX_PACKET + 17, 1, 8, 1)])
def test_usbmux_rejects_truncated_or_oversized_packets(data):
    with pytest.raises(lab.LabError):
        lab.mux_request(FragmentedSocket(data), {})


def test_private_device_identifier_is_not_in_public_report():
    public = lab.public_device({"identifier": "private-device-id", "ProductType": "iPad2,6"})
    assert public == {"ProductType": "iPad2,6"}


def test_backup_catalog_matches_model_without_claiming_restore_and_redacts_identity(tmp_path):
    directory = tmp_path / "private-device-id"
    directory.mkdir()
    (directory / "Info.plist").write_bytes(plistlib.dumps({
        "Product Type": "iPad2,6", "Unique Identifier": "private-device-id",
    }))
    (directory / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": False}))
    (directory / "Status.plist").write_bytes(plistlib.dumps({"SnapshotState": "finished"}))
    result = lab.inspect_backup_catalog([tmp_path], {"product_types": ["iPad2,6"]})
    assert len(result) == 1
    public = lab.public_backup(result[0])
    assert public["snapshot_state"] == "finished"
    assert public["restore_verified"] is False
    assert "private-device-id" not in json.dumps(public)


@pytest.mark.parametrize("info", [[], {"Product Type": "iPhone4,1"}])
def test_backup_catalog_skips_wrong_model_or_malformed_metadata(tmp_path, info):
    directory = tmp_path / "backup"
    directory.mkdir()
    (directory / "Info.plist").write_bytes(plistlib.dumps(info))
    assert lab.inspect_backup_catalog([tmp_path], {"product_types": ["iPad2,6"]}) == []


def test_build_refuses_low_space_before_fetch_or_docker(monkeypatch):
    monkeypatch.setattr(lab, "disk_space_report", lambda: [{"role": "system", "free_bytes": 30 * 1024**2}])
    monkeypatch.setattr(lab, "fetch_asset", lambda *args: pytest.fail("must not fetch on a full host"))
    with pytest.raises(lab.LabError, match="Insufficient free space"):
        lab.build(SimpleNamespace(image_only=False))


def test_build_checks_docker_volume_even_when_workspace_and_system_have_space(monkeypatch, tmp_path):
    storage = tmp_path / "separate-docker-volume"
    monkeypatch.setattr(lab, "docker_storage_volume", lambda: storage)
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda path: SimpleNamespace(
        free=1024 if path == storage else lab.MIN_BUILD_FREE_BYTES * 2))
    report = lab.disk_space_report()
    assert next(item for item in report if item["role"] == "docker")["free_bytes"] == 1024
    monkeypatch.setattr(lab, "fetch_asset", lambda *args: pytest.fail("must not fetch with a full Docker volume"))
    with pytest.raises(lab.LabError, match="docker volume"):
        lab.build(SimpleNamespace(image_only=False))


def test_cached_hash_mismatch_is_not_silently_replaced(tmp_path):
    asset = {"filename": "app.ipa", "sha256": "0" * 64, "url": "https://github.com/example/release/app.ipa"}
    (tmp_path / "app.ipa").write_bytes(b"corrupted")
    with pytest.raises(lab.LabError, match="hash mismatch"):
        lab.fetch_asset(asset, tmp_path)
    assert (tmp_path / "app.ipa").read_bytes() == b"corrupted"


def test_ipa_rejects_archive_traversal(tmp_path):
    path = tmp_path / "bad.ipa"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape", "bad")
        archive.writestr("Payload/App.app/Info.plist", plistlib.dumps({}))
    with pytest.raises(lab.LabError, match="Unsafe path"):
        lab.inspect_ipa(path)


def macho(minimum=0x090000, cpu=12, subtype=9, command_size=16):
    header = struct.pack("<7I", 0xFEEDFACE, cpu, subtype, 2, 1, 16, 0)
    return header + struct.pack("<4I", 0x25, command_size, minimum, 0x090300)


def test_macho_accepts_armv7_ios9_without_claiming_device_success(tmp_path):
    path = tmp_path / "app"
    path.write_bytes(macho())
    result = lab.inspect_macho(path)
    assert result["architecture"] == "armv7"
    assert result["minimum_os"] == "9.0.0"
    assert result["device_execution_verified"] is False


@pytest.mark.parametrize("data", [macho(minimum=0x0A0000), macho(cpu=0x100000C), macho(command_size=80), macho()[:-1]])
def test_macho_rejects_wrong_architecture_os_or_corruption(tmp_path, data):
    path = tmp_path / "app"
    path.write_bytes(data)
    with pytest.raises(lab.LabError):
        lab.inspect_macho(path)


def test_observation_requires_binary_identity_and_explicit_operator(tmp_path):
    output = tmp_path / "observations.jsonl"
    assert lab.main(["record", "--kind", "cold-boot", "--result", "pass", "--build-sha256", "a" * 64,
                     "--operator", "test-operator", "--note", "Synthetic unit test only", "--output", str(output)]) == 0
    event = json.loads(output.read_text())
    assert event["build_sha256"] == "a" * 64
    assert "qualified" not in event
