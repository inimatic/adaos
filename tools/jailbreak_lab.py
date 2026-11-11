"""Read-only device inspection and reproducible preparation for the legacy iOS lab.

This tool does not pair, jailbreak, install, reboot, restore, or unlock a device.
Python 3.11+; standard library only. Generated files belong to .tmp/jailbreak;
device identifiers belong to the existing private .adaos/recovery directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "jailbreak"
CACHE = ROOT / ".tmp" / "jailbreak"
MAX_PACKET = 4 * 1024 * 1024
MIN_BUILD_FREE_BYTES = 6 * 1024**3


class LabError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_exact(connection: Any, length: int) -> bytes:
    if not 0 <= length <= MAX_PACKET:
        raise LabError("Invalid device packet length")
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise LabError("Device connection closed before the packet completed")
        result.extend(chunk)
    return bytes(result)


def mux_request(connection: Any, payload: dict[str, Any]) -> dict[str, Any]:
    body = plistlib.dumps(payload)
    connection.sendall(struct.pack("<IIII", len(body) + 16, 1, 8, 1) + body)
    length, version, message, tag = struct.unpack("<IIII", read_exact(connection, 16))
    if length < 16 or version != 1 or message != 8 or tag != 1:
        raise LabError("Unexpected usbmux response header")
    result = plistlib.loads(read_exact(connection, length - 16))
    if not isinstance(result, dict):
        raise LabError("Unexpected usbmux response body")
    return result


def lockdown_value(connection: Any, key: str) -> Any:
    body = plistlib.dumps({"Label": "AdaOSLab", "Request": "GetValue", "Key": key})
    connection.sendall(struct.pack(">I", len(body)) + body)
    length = struct.unpack(">I", read_exact(connection, 4))[0]
    result = plistlib.loads(read_exact(connection, length))
    if result.get("Error"):
        code = str(result["Error"])[:80]
        raise LabError(f"Read-only {key} query denied ({code}); unlock/trust may be required")
    return result.get("Value")


def inspect_devices(host: str = "127.0.0.1", port: int = 27015) -> list[dict[str, Any]]:
    """Use Apple's already running USB bridge; never request pairing or a session."""
    with socket.create_connection((host, port), timeout=3) as connection:
        listing = mux_request(connection, {"MessageType": "ListDevices", "ClientVersionString": "AdaOSLab"})
    devices = []
    for device in listing.get("DeviceList", []):
        properties = device.get("Properties", {})
        if properties.get("ConnectionType", "USB") != "USB":
            continue
        entry = {"identifier": properties.get("SerialNumber", ""), "transport": "USB"}
        try:
            with socket.create_connection((host, port), timeout=3) as connection:
                result = mux_request(connection, {
                    "MessageType": "Connect", "DeviceID": device["DeviceID"],
                    "PortNumber": socket.htons(62078), "ClientVersionString": "AdaOSLab",
                })
                if result.get("Number") != 0:
                    raise LabError("The USB bridge could not open the read-only device service")
                for key in ("ProductType", "ProductVersion", "BuildVersion", "ActivationState"):
                    entry[key] = lockdown_value(connection, key)
        except (OSError, LabError, plistlib.InvalidFileException) as exc:
            entry["inspection_error"] = str(exc)
        devices.append(entry)
    return devices


def public_device(device: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in device.items() if key != "identifier"}


def inspect_backup_catalog(roots: list[Path], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Inspect only metadata immediately beneath known Apple backup directories."""
    entries = []
    for root in roots:
        if not root.is_dir():
            continue
        for directory in root.iterdir():
            info_path = directory / "Info.plist"
            if not directory.is_dir() or not info_path.is_file() or info_path.stat().st_size > MAX_PACKET:
                continue
            try:
                info = plistlib.loads(info_path.read_bytes())
                if not isinstance(info, dict):
                    continue
                product = info.get("Product Type", info.get("ProductType"))
                if product not in profile["product_types"]:
                    continue
                metadata = {}
                for filename in ("Manifest.plist", "Status.plist"):
                    path = directory / filename
                    if path.is_file() and path.stat().st_size <= MAX_PACKET:
                        values = plistlib.loads(path.read_bytes())
                        if not isinstance(values, dict):
                            raise ValueError("Backup metadata must be a dictionary")
                        metadata.update(values)
                date = info.get("Last Backup Date")
                entries.append({"path": str(directory), "identifier": info.get("Unique Identifier", ""),
                                "product_type": product,
                                "last_backup_date": date.isoformat() if isinstance(date, datetime) else str(date or "unknown"),
                                "is_encrypted": metadata.get("IsEncrypted"),
                                "snapshot_state": metadata.get("SnapshotState", "unknown"),
                                "restore_verified": False})
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
    return entries


def docker_storage_volume() -> Path | None:
    """Locate the registered Windows Docker data disk without exposing its path."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as distros:
            for index in range(winreg.QueryInfoKey(distros)[0]):
                with winreg.OpenKey(distros, winreg.EnumKey(distros, index)) as distro:
                    name = winreg.QueryValueEx(distro, "DistributionName")[0]
                    if name == "docker-desktop-data":
                        base = str(winreg.QueryValueEx(distro, "BasePath")[0])
                        if base.startswith("\\\\?\\"):
                            base = base[4:]
                        return Path(Path(base).anchor)
    except OSError:
        return None
    return None


def disk_space_report() -> list[dict[str, Any]]:
    roots = {"workspace": Path(ROOT.anchor)}
    if os.name == "nt":
        roots["system"] = Path(os.environ.get("SystemDrive", "C:") + "\\")
    storage = docker_storage_volume()
    if storage is not None:
        roots["docker"] = storage
    return [{"role": role, "free_bytes": shutil.disk_usage(root).free,
             "minimum_build_free_bytes": MIN_BUILD_FREE_BYTES}
            for role, root in roots.items()]


def require_build_space() -> None:
    for volume in disk_space_report():
        if volume["free_bytes"] < MIN_BUILD_FREE_BYTES:
            raise LabError(f"Insufficient free space on {volume['role']} volume; "
                           "at least 6 GiB is required before building. Check Docker's storage volume too.")


def public_backup(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key not in {"path", "identifier"}}


def command_check(arguments: list[str]) -> dict[str, Any]:
    if not shutil.which(arguments[0]):
        return {"available": False, "reason": "not installed"}
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, timeout=12)
        return {"available": completed.returncode == 0,
                "detail": (completed.stdout or completed.stderr).strip()[:500]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": type(exc).__name__}


def doctor(args: argparse.Namespace) -> int:
    profile = read_json(LAB / "device-profile.json")
    report: dict[str, Any] = {
        "schema": "adaos.jailbreak.preflight.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "host": {"system": platform.system(), "python": platform.python_version()},
        "docker": command_check(["docker", "info", "--format", "{{.OSType}}"]),
        "git": command_check(["git", "--version"]),
        "disk_space": disk_space_report(),
        "device_mutations": False,
    }
    backup_roots = [Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Apple" / "MobileSync" / "Backup",
                    Path(os.environ.get("APPDATA", str(Path.home()))) / "Apple Computer" / "MobileSync" / "Backup"]
    backups = inspect_backup_catalog(backup_roots, profile)
    report["model_backup_metadata"] = [public_backup(entry) for entry in backups]
    try:
        private_devices = inspect_devices(args.usbmux_host, args.usbmux_port)
        connected_identifiers = {str(device.get("identifier", "")).lower()
                                 for device in private_devices if device.get("identifier")}
        for entry in backups:
            entry["matches_connected_device"] = bool(entry["identifier"]) and str(entry["identifier"]).lower() in connected_identifiers
        report["model_backup_metadata"] = [public_backup(entry) for entry in backups]
        report["devices"] = [public_device(device) for device in private_devices]
        report["target_matches"] = sum(
            device.get("ProductType") in profile["product_types"]
            and device.get("ProductVersion") == profile["product_version"]
            and device.get("BuildVersion") == profile["expected_build"]
            for device in private_devices
        )
        if args.capture_private:
            recovery = ROOT / ".adaos" / "recovery"
            if not recovery.is_dir():
                raise LabError("The existing private .adaos/recovery directory is missing")
            write_json(recovery / "jailbreak" / "device-inventory.json", {
                "observed_at": report["observed_at"], "devices": private_devices, "backup_catalog": backups,
            })
            report["private_inventory"] = ".adaos/recovery/jailbreak/device-inventory.json"
    except (OSError, LabError, plistlib.InvalidFileException) as exc:
        report["device_inspection_error"] = str(exc)
        report["target_matches"] = 0
    report["target_identified"] = report["target_matches"] == 1
    report["ready_for_device_changes"] = False  # Backup/recovery and operator gates are separate.
    write_json(args.output, report)
    print(json.dumps(report, indent=2))
    return int(args.require_target and not report["target_identified"])


def fetch_asset(asset: dict[str, Any], cache: Path = CACHE / "downloads") -> Path:
    name, expected = asset["filename"], asset["sha256"]
    if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise LabError("Dependency must have a simple filename and pinned SHA-256")
    if not asset["url"].startswith("https://github.com/"):
        raise LabError("Only the locked HTTPS upstream download origin is permitted")
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / name
    if destination.exists():
        if sha256(destination) != expected:
            raise LabError(f"Cached dependency hash mismatch: {name}")
        return destination
    partial = cache / (name + ".part")
    request = urllib.request.Request(asset["url"], headers={"User-Agent": "AdaOS-jailbreak-lab"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        if sha256(partial) != expected:
            raise LabError(f"Downloaded dependency hash mismatch: {name}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def fetch(args: argparse.Namespace) -> int:
    assets = read_json(LAB / "dependencies.lock.json")["assets"]
    selected = args.only or list(assets)
    for name in selected:
        path = fetch_asset(assets[name])
        print(json.dumps({"asset": name, "path": str(path), "sha256": sha256(path)}))
    return 0


def build(args: argparse.Namespace) -> int:
    require_build_space()
    lock = read_json(LAB / "dependencies.lock.json")
    toolchain = fetch_asset(lock["assets"]["toolchain"])
    context = CACHE / "docker-context"
    context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LAB / "toolchain" / "Dockerfile", context / "Dockerfile")
    shutil.copyfile(toolchain, context / "iOSToolchain-x86_64.tar.xz")
    # The context contains only the Dockerfile and compiler archive, never repository state.
    image = "adaos-jailbreak-lab:" + sha256(LAB / "dependencies.lock.json")[:12]
    command = ["docker", "build", "--platform", "linux/amd64", "--tag", image]
    for key, value in {
        "BASE_IMAGE": lock["builder"]["base_image"],
        "THEOS_COMMIT": lock["sources"]["theos"]["commit"],
        "SDK_COMMIT": lock["sources"]["sdks"]["commit"],
        "TOOLCHAIN_SHA256": lock["assets"]["toolchain"]["sha256"],
    }.items():
        command.extend(["--build-arg", f"{key}={value}"])
    subprocess.run(command + [str(context)], check=True)
    image_id = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()
    if args.image_only:
        print(f"Prepared builder {image}; no device operation was performed.")
        return 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    work = CACHE / "work" / run_id
    output = CACHE / "output" / run_id
    shutil.copytree(LAB / "smoke", work)
    output.mkdir(parents=True)
    subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--volume", f"{work}:/work",
        "--volume", f"{output}:/out", image, "bash", "-lc",
        'make clean package THEOS_PACKAGE_DIR=/out && '
        'for package in /out/com.inimatic.adaos.lab_*.deb; do dpkg-deb -x "$package" /out/extracted; done',
    ], check=True)
    binary = output / "extracted" / "Applications" / "AdaOSLab.app" / "AdaOSLab"
    inspection = inspect_macho(binary)
    package_files = list(output.glob("com.inimatic.adaos.lab_*.deb"))
    if len(package_files) != 1:
        raise LabError("Build must produce exactly one lab package")
    source_digests = {path.relative_to(work).as_posix(): sha256(path) for path in sorted(work.rglob("*"))
                      if path.is_file() and ".theos" not in path.parts}
    receipt = {"schema": "adaos.jailbreak.build.v1", "built_at": datetime.now(timezone.utc).isoformat(),
               "image": image, "builder_image_id": image_id,
               "builder_dockerfile_sha256": sha256(LAB / "toolchain" / "Dockerfile"),
               "dependency_lock_sha256": sha256(LAB / "dependencies.lock.json"),
               "source_files": source_digests, "binary": inspection,
               "package": {"filename": package_files[0].name, "sha256": sha256(package_files[0])},
               "device_installation_verified": False}
    write_json(output / "build.json", receipt)
    print(json.dumps({"output": str(output), "receipt": receipt}, indent=2))
    return 0


def inspect_ipa(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name for name in names):
            raise LabError("Unsafe path in IPA archive")
        infos = [name for name in names if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
        if len(infos) != 1:
            raise LabError("IPA must contain exactly one application Info.plist")
        info_member = archive.getinfo(infos[0])
        if info_member.file_size > MAX_PACKET:
            raise LabError("IPA Info.plist exceeds the inspection size limit")
        info = plistlib.loads(archive.read(info_member))
        return {
            "sha256": sha256(path), "bundle_id": info.get("CFBundleIdentifier"),
            "version": info.get("CFBundleShortVersionString"),
            "minimum_os": info.get("MinimumOSVersion"),
            "executable": info.get("CFBundleExecutable"),
        }


def inspect_macho(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 28 or data[:4] != b"\xce\xfa\xed\xfe":
        raise LabError("Expected a thin 32-bit little-endian Mach-O executable")
    _, cpu, subtype, filetype, ncmds, command_bytes, _ = struct.unpack_from("<7I", data)
    if cpu != 12 or subtype & 0xFFFFFF != 9 or filetype != 2:
        raise LabError("Expected an ARMv7 Mach-O application executable")
    end = 28 + command_bytes
    if end > len(data):
        raise LabError("Truncated Mach-O load commands")
    offset, minimum, signed = 28, None, False
    for _ in range(ncmds):
        if offset + 8 > end:
            raise LabError("Truncated Mach-O command header")
        command, size = struct.unpack_from("<2I", data, offset)
        if size < 8 or offset + size > end:
            raise LabError("Invalid Mach-O load command length")
        if command == 0x25 and size >= 16:  # LC_VERSION_MIN_IPHONEOS
            version = struct.unpack_from("<I", data, offset + 8)[0]
            minimum = ((version >> 16) & 0xFFFF, (version >> 8) & 0xFF, version & 0xFF)
        signed |= command == 0x1D  # LC_CODE_SIGNATURE; not a signature validity claim
        offset += size
    if offset != end or minimum is None or minimum > (9, 3, 6):
        raise LabError("Binary load commands do not establish compatibility with iOS 9.3.6")
    return {"architecture": "armv7", "minimum_os": ".".join(map(str, minimum)),
            "code_signature_present": signed, "sha256": sha256(path),
            "device_execution_verified": False}


def record(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[0-9a-f]{64}", args.build_sha256):
        raise LabError("An exact application binary SHA-256 is required")
    event = {"schema": "adaos.jailbreak.observation.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
             "kind": args.kind, "result": args.result, "build_sha256": args.build_sha256,
             "operator": args.operator, "note": args.note}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    print("Recorded operator observation; no experiment gate was automatically qualified.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("doctor", help="Read-only host and connected-device inventory")
    check.add_argument("--usbmux-host", default="127.0.0.1", choices=["127.0.0.1"])
    check.add_argument("--usbmux-port", default=27015, type=int)
    check.add_argument("--capture-private", action="store_true")
    check.add_argument("--require-target", action="store_true")
    check.add_argument("--output", type=Path, default=CACHE / "preflight.json")
    check.set_defaults(handler=doctor)
    download = sub.add_parser("fetch", help="Fetch or verify pinned upstream assets; never execute")
    download.add_argument("--only", nargs="+", choices=["everpwnage", "impactor", "toolchain"])
    download.set_defaults(handler=fetch)
    compilation = sub.add_parser("build", help="Build the isolated native ARMv7 smoke package; never install")
    compilation.add_argument("--image-only", action="store_true")
    compilation.set_defaults(handler=build)
    for name, function in (("inspect-ipa", inspect_ipa), ("inspect-macho", inspect_macho)):
        inspection = sub.add_parser(name)
        inspection.add_argument("path", type=Path)
        inspection.set_defaults(handler=lambda args, fn=function: print(json.dumps(fn(args.path), indent=2)) or 0)
    observation = sub.add_parser("record", help="Append an explicit operator observation to the lab journal")
    observation.add_argument("--kind", required=True, choices=["cold-boot", "smoke", "connectivity", "soak", "expiry", "update"])
    observation.add_argument("--result", required=True, choices=["pass", "fail", "inconclusive"])
    observation.add_argument("--build-sha256", required=True)
    observation.add_argument("--operator", required=True)
    observation.add_argument("--note", required=True)
    observation.add_argument("--output", type=Path, default=CACHE / "observations.jsonl")
    observation.set_defaults(handler=record)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (LabError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile, plistlib.InvalidFileException) as exc:
        print(f"jailbreak-lab: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
