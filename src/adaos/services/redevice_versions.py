from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

REDEVICE_AGENT_COMPONENT = "redevice_agent"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        current = _mapping(current).get(key)
    return current


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, Mapping):
            continue
        token = _text(value)
        if token:
            return token
    return ""


def _repo_root() -> Path:
    explicit = _text(os.environ.get("ADAOS_REPO_ROOT") or os.environ.get("ADAOS_PROJECT_ROOT"))
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parents[3]


def redevice_agent_source_version(repo_root: Path | None = None) -> dict[str, Any]:
    """Return the local ReDevice Agent source version from Gradle properties.

    This is a source-side fallback for dev and non-published builds. Published
    deployments should prefer the aggregate release manifest.
    """

    root = repo_root or _repo_root()
    path = root / "src" / "adaos" / "integrations" / "redevice-agent" / "android" / "gradle.properties"
    if not path.exists():
        return {}
    version = ""
    version_code = ""
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "redeviceAgentVersionName":
                version = value.strip()
            elif key.strip() == "redeviceAgentVersionCode":
                version_code = value.strip()
    except OSError:
        return {}
    if not version:
        return {}
    return {
        "version": version,
        "version_code": version_code,
        "source": "redevice_agent.gradle_properties",
        "source_path": str(path),
    }


def _load_manifest_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _manifest_candidates(repo_root: Path | None = None) -> list[Path]:
    root = repo_root or _repo_root()
    explicit = _text(os.environ.get("ADAOS_VERSION_MANIFEST_PATH") or os.environ.get("ADAOS_RELEASE_MANIFEST_PATH"))
    candidates: list[Path] = []
    if explicit and not explicit.lower().startswith(("http://", "https://")):
        candidates.append(Path(explicit))
    candidates.extend(
        [
            root / "adaos-versions.json",
            root / "version-manifest.json",
            root / ".adaos" / "adaos-versions.json",
            root / ".adaos" / "version-manifest.json",
        ]
    )
    return candidates


def _served_component_from_manifest(manifest: Mapping[str, Any], component: str) -> dict[str, Any]:
    components = _mapping(manifest.get("components"))
    item = _mapping(components.get(component))
    if not item:
        return {}
    served = _mapping(item.get("served")) or item
    version = _first_text(served.get("build_version"), served.get("version"))
    if not version:
        return {}
    return {
        "version": version,
        "base_version": _text(served.get("version")),
        "build_version": _text(served.get("build_version")),
        "commit": _text(served.get("commit")),
        "source": _text(served.get("source") or manifest.get("source")) or "aggregate_manifest",
        "updated_at": _text(served.get("updated_at") or manifest.get("generated_at")),
    }


def served_component_version(component: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    for path in _manifest_candidates(repo_root):
        if not path.exists():
            continue
        served = _served_component_from_manifest(_load_manifest_file(path), component)
        if served:
            served["manifest_path"] = str(path)
            return served
    return {}


def default_redevice_agent_served_version(*, repo_root: Path | None = None) -> dict[str, Any]:
    env_version = _text(
        os.environ.get("ADAOS_REDEVICE_AGENT_SERVED_VERSION")
        or os.environ.get("ADAOS_REDEVICE_AGENT_VERSION")
    )
    if env_version:
        return {
            "version": env_version,
            "build_version": _text(os.environ.get("ADAOS_REDEVICE_AGENT_SERVED_BUILD_VERSION")),
            "source": "environment",
        }
    served = served_component_version(REDEVICE_AGENT_COMPONENT, repo_root=repo_root)
    if served:
        return served
    return redevice_agent_source_version(repo_root)


def _endpoint_payloads(endpoint: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    diagnostics = _mapping(endpoint.get("diagnostics"))
    manifest = _mapping(endpoint.get("endpoint_manifest")) or _mapping(diagnostics.get("endpoint_manifest"))
    diagnostic_report = _mapping(endpoint.get("diagnostic_report")) or _mapping(diagnostics.get("diagnostic_report"))
    endpoint_health = _mapping(endpoint.get("endpoint_health")) or _mapping(diagnostics.get("endpoint_health"))
    service_state = _mapping(endpoint.get("service_state")) or _mapping(diagnostics.get("service_state"))
    policy = _mapping(endpoint.get("endpoint_policy")) or _mapping(diagnostics.get("endpoint_policy"))
    runtime = _mapping(endpoint.get("runtime"))
    return {
        "endpoint": dict(endpoint),
        "diagnostics": diagnostics,
        "manifest": manifest,
        "diagnostic_report": diagnostic_report,
        "endpoint_health": endpoint_health,
        "service_state": service_state,
        "policy": policy,
        "runtime": runtime,
        "manifest_agent_build": _mapping(manifest.get("agent_build")) or _mapping(manifest.get("build")),
        "diagnostic_build": _mapping(diagnostic_report.get("build")),
    }


def _used_version(payloads: Mapping[str, Mapping[str, Any]]) -> tuple[str, str, str]:
    candidates = [
        ("manifest.agent_version", payloads["manifest"].get("agent_version")),
        ("diagnostic_report.agent_version", payloads["diagnostic_report"].get("agent_version")),
        ("service_state.agent_version", payloads["service_state"].get("agent_version")),
        ("endpoint_health.agent_version", payloads["endpoint_health"].get("agent_version")),
        ("manifest.agent_build.version_name", payloads["manifest_agent_build"].get("version_name")),
        ("diagnostic_report.build.version_name", payloads["diagnostic_build"].get("version_name")),
        ("manifest.software.version", _nested(payloads["manifest"], "software", "version")),
        ("manifest.software.build_version", _nested(payloads["manifest"], "software", "build_version")),
        ("diagnostic_report.software.version", _nested(payloads["diagnostic_report"], "software", "version")),
        ("service_state.software.version", _nested(payloads["service_state"], "software", "version")),
        ("runtime.software_version", payloads["runtime"].get("software_version")),
        ("runtime.runtime_version", payloads["runtime"].get("runtime_version")),
        ("endpoint.software_version", payloads["endpoint"].get("software_version")),
        ("endpoint.version", payloads["endpoint"].get("version")),
    ]
    for source, value in candidates:
        token = _text(value)
        if token:
            return token, source, _used_version_code(payloads)
    return "", "", _used_version_code(payloads)


def _used_version_code(payloads: Mapping[str, Mapping[str, Any]]) -> str:
    return _first_text(
        payloads["manifest"].get("agent_version_code"),
        payloads["diagnostic_report"].get("agent_version_code"),
        payloads["service_state"].get("agent_version_code"),
        payloads["endpoint_health"].get("agent_version_code"),
        payloads["manifest_agent_build"].get("version_code"),
        payloads["diagnostic_build"].get("version_code"),
        _nested(payloads["manifest"], "software", "version_code"),
        _nested(payloads["diagnostic_report"], "software", "version_code"),
        payloads["runtime"].get("software_version_code"),
        payloads["endpoint"].get("software_version_code"),
    )


def _policy_served_version(payloads: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    policy = payloads["policy"]
    endpoint = payloads["endpoint"]
    candidates = [
        ("policy.redevice_agent.build_version", _nested(policy, "redevice_agent", "build_version")),
        ("policy.redevice_agent.version", _nested(policy, "redevice_agent", "version")),
        ("policy.software.target_version", _nested(policy, "software", "target_version")),
        ("policy.software.version", _nested(policy, "software", "version")),
        ("policy.target_agent_version", policy.get("target_agent_version")),
        ("policy.desired_agent_version", policy.get("desired_agent_version")),
        ("policy.agent_version", policy.get("agent_version")),
        ("runtime.served_version", payloads["runtime"].get("served_version")),
        ("endpoint.served_version", endpoint.get("served_version")),
        ("endpoint.target_agent_version", endpoint.get("target_agent_version")),
    ]
    for source, value in candidates:
        token = _text(value)
        if token:
            return token, source
    return "", ""


def _comparison_token(value: str) -> str:
    token = _text(value).lower()
    if "+" in token:
        token = token.split("+", 1)[0]
    if token.endswith("-legacy"):
        token = token[: -len("-legacy")]
    return token


def endpoint_version_info(
    endpoint: Mapping[str, Any],
    *,
    served_fallback: Mapping[str, Any] | None = None,
    use_default_served: bool = True,
) -> dict[str, Any]:
    payloads = _endpoint_payloads(endpoint)
    used_version, used_source, used_code = _used_version(payloads)
    policy_served, policy_served_source = _policy_served_version(payloads)
    fallback = dict(served_fallback or {})
    if use_default_served and not fallback:
        fallback = default_redevice_agent_served_version()
    served_version = policy_served or _first_text(fallback.get("build_version"), fallback.get("version"))
    served_source = policy_served_source or _text(fallback.get("source")) or ""
    served_code = _text(
        _nested(payloads["policy"], "redevice_agent", "version_code")
        or _nested(payloads["policy"], "software", "version_code")
        or payloads["runtime"].get("served_version_code")
        or fallback.get("version_code")
    )
    if used_version and served_version:
        status = "ok" if _comparison_token(used_version) == _comparison_token(served_version) else "drift"
    else:
        status = "unknown"
    return {
        "software_version": used_version,
        "software_version_code": used_code,
        "software_version_source": used_source,
        "served_version": served_version,
        "served_version_code": served_code,
        "served_version_source": served_source,
        "version_status": status,
        "version_status_label": status,
    }
