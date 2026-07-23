from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


SNAPSHOT_SCHEMA = "adaos.e2e.snapshot.v1"
MANIFEST_SCHEMA = "adaos.e2e.manifest.v1"
REDACTION_VERSION = "adaos.e2e.redaction.v1"
RUNNER_VERSION = "0.1.0"
RESULTS = {"passed", "failed", "inconclusive", "skipped"}
PROFILES = {"observe", "canary", "storm", "soak"}
SECRET_KEYS = {"authorization", "cookie", "jwt", "password", "secret", "token", "x-adaos-token"}
QUERY_SECRET_KEYS = SECRET_KEYS | {"access_token", "refresh_token", "session"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_run_id(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    return token[:96] or f"e2e-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value))
    except Exception:
        return str(value)
    if not parsed.scheme or not parsed.netloc:
        return str(value)
    query = [
        (key, "[REDACTED]" if key.strip().lower() in QUERY_SECRET_KEYS else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:access_token|refresh_token|token|jwt|session)=)[^&#\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,})?\b", "[REDACTED]", text)
    return text


def _is_secret_key(value: str) -> bool:
    token = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    if token.endswith("env"):
        return False
    return token in {"authorization", "cookie", "jwt", "password", "secret", "token", "xadaostoken"} or token.endswith(
        ("cookie", "jwt", "password", "secret", "token")
    )


def redact_value(value: Any, secrets: tuple[str, ...] = (), *, key: str = "") -> Any:
    if _is_secret_key(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): redact_value(item, secrets, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        candidate = redact_url(value)
        return redact_text(candidate, secrets)
    return value


def _inline_secret_paths(value: Any, prefix: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            if _is_secret_key(key) and item not in (None, ""):
                findings.append(path)
            findings.extend(_inline_secret_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_inline_secret_paths(item, f"{prefix}[{index}]"))
    return findings


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _command_text(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = str(result.stdout or "").strip()
    return value or None


def _repository_metadata(repo_root: Path) -> dict[str, Any]:
    status = _command_text(["git", "status", "--porcelain"], repo_root)
    client_root = repo_root / "src" / "adaos" / "integrations" / "adaos-client"
    return {
        "commit": _command_text(["git", "rev-parse", "HEAD"], repo_root),
        "dirty_worktree": bool(status),
        "client_commit": _command_text(["git", "rev-parse", "HEAD"], client_root) if client_root.exists() else None,
    }


def _runtime_metadata(repo_root: Path) -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "node": _command_text(["node", "--version"], repo_root),
        "runner_version": RUNNER_VERSION,
    }


Validator = Callable[[dict[str, Any]], tuple[bool, str | None, str | None]]


class StandRunner:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        output_root: Path,
        run_id: str | None = None,
        environment: Mapping[str, str] | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.config = dict(config)
        self.environment = dict(os.environ if environment is None else environment)
        self.repo_root = (repo_root or Path.cwd()).resolve()
        self.run_id = _safe_run_id(run_id or str(self.config.get("runId") or ""))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.bundle_dir = Path(output_root).resolve() / f"{stamp}-{self.run_id}"
        self.started_at = utc_now()
        self.checks: list[dict[str, Any]] = []
        self.omitted: list[dict[str, str]] = []
        self.token_env = str(self.config.get("tokenEnv") or "ADAOS_E2E_TOKEN").strip()
        self.token = str(self.environment.get(self.token_env) or "").strip()
        self.secrets = tuple(item for item in (self.token,) if item)
        self.timeout_s = max(0.5, float(dict(self.config.get("timeouts") or {}).get("httpSeconds") or 10.0))

    def _validate_config(self) -> list[str]:
        errors: list[str] = []
        for key in ("firebaseClientUrl", "rootApiUrl", "hubApiUrl", "subnetId", "webspaceId", "browserDeviceId"):
            if not str(self.config.get(key) or "").strip():
                errors.append(f"missing required config field: {key}")
        profile = str(self.config.get("profile") or "observe").strip().lower()
        if profile not in PROFILES:
            errors.append(f"profile must be one of: {', '.join(sorted(PROFILES))}")
        inline_secrets = _inline_secret_paths(self.config)
        if inline_secrets:
            errors.append("inline secrets are forbidden; use environment references: " + ", ".join(inline_secrets))
        return errors

    def _record_check(
        self,
        check_id: str,
        result: str,
        *,
        duration_ms: float = 0.0,
        category: str | None = None,
        detail: str | None = None,
        evidence: str | None = None,
        required: bool = True,
    ) -> dict[str, Any]:
        if result not in RESULTS:
            raise ValueError(f"unsupported E2E result: {result}")
        check = {
            "id": check_id,
            "result": result,
            "required": bool(required),
            "duration_ms": round(max(0.0, duration_ms), 3),
            "category": category,
            "detail": redact_text(detail or "", self.secrets) or None,
            "evidence": evidence,
        }
        self.checks.append(check)
        return check

    def _snapshot_path(self, check_id: str) -> Path:
        name = re.sub(r"[^a-z0-9]+", "-", check_id.lower()).strip("-") + ".json"
        return self.bundle_dir / "snapshots" / name

    def _write_snapshot(
        self,
        *,
        check_id: str,
        source: str,
        target: str,
        status: str,
        duration_ms: float,
        payload: Any,
    ) -> str:
        path = self._snapshot_path(check_id)
        envelope = {
            "schema": SNAPSHOT_SCHEMA,
            "run_id": self.run_id,
            "observed_at": utc_now(),
            "source": source,
            "target": target,
            "status": status,
            "duration_ms": round(max(0.0, duration_ms), 3),
            "payload": redact_value(payload, self.secrets),
        }
        _json_write(path, envelope)
        return path.relative_to(self.bundle_dir).as_posix()

    def _open_json(self, url: str, *, authenticated: bool) -> tuple[int, dict[str, Any], dict[str, str]]:
        headers = {"Accept": "application/json", "User-Agent": f"adaos-stand-e2e/{RUNNER_VERSION}"}
        if authenticated and self.token:
            headers["X-AdaOS-Token"] = self.token
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310 - target is operator supplied
            body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body) if body.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("endpoint returned non-object JSON")
            return int(response.status), payload, {str(key): str(value) for key, value in response.headers.items()}

    def _probe(
        self,
        *,
        check_id: str,
        source: str,
        target: str,
        url: str,
        authenticated: bool,
        validator: Validator,
        required: bool = True,
    ) -> None:
        started = time.perf_counter()
        if authenticated and not self.token:
            duration_ms = (time.perf_counter() - started) * 1000.0
            evidence = self._write_snapshot(
                check_id=check_id,
                source=source,
                target=target,
                status="unavailable",
                duration_ms=duration_ms,
                payload={"error": f"environment variable {self.token_env} is not set", "url": redact_url(url)},
            )
            self._record_check(
                check_id,
                "inconclusive",
                duration_ms=duration_ms,
                category="credentials_invalid",
                detail=f"environment variable {self.token_env} is not set",
                evidence=evidence,
                required=required,
            )
            return
        try:
            status_code, payload, headers = self._open_json(url, authenticated=authenticated)
            duration_ms = (time.perf_counter() - started) * 1000.0
            passed, category, detail = validator(payload)
            result = "passed" if passed else "failed"
            evidence = self._write_snapshot(
                check_id=check_id,
                source=source,
                target=target,
                status="ok" if passed else "failed",
                duration_ms=duration_ms,
                payload={
                    "url": redact_url(url),
                    "status_code": status_code,
                    "server_date": headers.get("Date"),
                    "body": payload,
                },
            )
            self._record_check(
                check_id,
                result,
                duration_ms=duration_ms,
                category=category,
                detail=detail,
                evidence=evidence,
                required=required,
            )
        except HTTPError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            code = int(getattr(exc, "code", 0) or 0)
            category = "credentials_invalid" if code in {401, 403} else "diagnostics_missing" if code == 404 else "hub_runtime_unhealthy"
            result = "inconclusive" if code in {401, 403, 404} else "failed"
            evidence = self._write_snapshot(
                check_id=check_id,
                source=source,
                target=target,
                status="unavailable" if result == "inconclusive" else "failed",
                duration_ms=duration_ms,
                payload={"url": redact_url(url), "status_code": code, "error": str(exc)},
            )
            self._record_check(check_id, result, duration_ms=duration_ms, category=category, detail=str(exc), evidence=evidence, required=required)
        except (URLError, TimeoutError, OSError) as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            evidence = self._write_snapshot(
                check_id=check_id,
                source=source,
                target=target,
                status="unavailable",
                duration_ms=duration_ms,
                payload={"url": redact_url(url), "error": f"{type(exc).__name__}: {exc}"},
            )
            self._record_check(check_id, "inconclusive", duration_ms=duration_ms, category="target_unreachable", detail=str(exc), evidence=evidence, required=required)
        except (ValueError, json.JSONDecodeError) as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            evidence = self._write_snapshot(
                check_id=check_id,
                source=source,
                target=target,
                status="unavailable",
                duration_ms=duration_ms,
                payload={"url": redact_url(url), "error": f"{type(exc).__name__}: {exc}"},
            )
            self._record_check(check_id, "inconclusive", duration_ms=duration_ms, category="diagnostics_missing", detail=str(exc), evidence=evidence, required=required)

    @staticmethod
    def _url(base: str, path: str, query: Mapping[str, Any] | None = None) -> str:
        url = str(base).rstrip("/") + "/" + str(path).lstrip("/")
        values = [(str(key), str(value)) for key, value in (query or {}).items() if value not in (None, "")]
        return url + ("?" + urlencode(values) if values else "")

    @staticmethod
    def _ok_validator(payload: dict[str, Any]) -> tuple[bool, str | None, str | None]:
        passed = bool(payload) and payload.get("ok") is not False
        return passed, None if passed else "hub_runtime_unhealthy", None if passed else "endpoint reported ok=false or an empty payload"

    @staticmethod
    def _authorize_validator(payload: dict[str, Any]) -> tuple[bool, str | None, str | None]:
        passed = payload.get("allowed") is True
        return passed, None if passed else "auth_denied", None if passed else str(payload.get("reason") or "browser device was not authorized")

    @staticmethod
    def _materialization_validator(payload: dict[str, Any]) -> tuple[bool, str | None, str | None]:
        materialization = payload.get("materialization") if isinstance(payload.get("materialization"), dict) else {}
        missing = materialization.get("missing_branches") or materialization.get("missingBranches") or []
        passed = payload.get("ok") is not False and materialization.get("ready") is True and not missing
        detail = None if passed else f"materialization ready={materialization.get('ready')!r}, missing_branches={list(missing) if isinstance(missing, list) else missing!r}"
        return passed, None if passed else "materialization_incomplete", detail

    def _run_http_checks(self) -> None:
        root = str(self.config["rootApiUrl"])
        hub = str(self.config["hubApiUrl"])
        webspace_id = str(self.config["webspaceId"])
        device_id = str(self.config["browserDeviceId"])
        paths = dict(self.config.get("paths") or {})
        root_ping = str(paths.get("rootPing") or "/api/ping")
        hub_ping = str(paths.get("hubPing") or "/api/ping")
        self._probe(check_id="root.ping", source="root", target="health", url=self._url(root, root_ping), authenticated=False, validator=self._ok_validator)
        self._probe(check_id="hub.ping", source="hub", target="health", url=self._url(hub, hub_ping), authenticated=False, validator=self._ok_validator)
        self._probe(check_id="hub.node-status", source="hub", target="node_status", url=self._url(hub, "/api/node/status"), authenticated=True, validator=self._ok_validator)
        self._probe(
            check_id="browser.authorize",
            source="hub",
            target="browser_authorization",
            url=self._url(hub, "/api/browser/session/authorize", {"dev": device_id, "ws": webspace_id, "browser_family": "playwright"}),
            authenticated=False,
            validator=self._authorize_validator,
        )
        self._probe(
            check_id="hub.reliability-summary",
            source="hub",
            target="reliability",
            url=self._url(hub, "/api/node/reliability/summary", {"mode": "thin", "webspace_id": webspace_id}),
            authenticated=True,
            validator=self._ok_validator,
        )
        self._probe(
            check_id="hub.status-cards",
            source="hub",
            target="status_cards",
            url=self._url(hub, "/api/node/status/cards", {"webspace_id": webspace_id}),
            authenticated=True,
            validator=self._ok_validator,
        )
        self._probe(
            check_id="hub.yjs-runtime",
            source="hub",
            target="yjs_runtime",
            url=self._url(hub, "/api/node/yjs/runtime", {"webspace_id": webspace_id}),
            authenticated=True,
            validator=self._ok_validator,
        )
        self._probe(
            check_id="hub.materialization",
            source="hub",
            target="materialization",
            url=self._url(hub, f"/api/node/yjs/webspaces/{quote(webspace_id, safe='')}/materialization", {"include_runtime": "true", "verify_live": "true"}),
            authenticated=True,
            validator=self._materialization_validator,
        )

    def _run_browser(self) -> None:
        browser = dict(self.config.get("browser") or {})
        project_dir = Path(str(browser.get("projectDir") or self.repo_root / "e2e" / "stand" / "browser"))
        if not project_dir.is_absolute():
            project_dir = (self.repo_root / project_dir).resolve()
        raw_command = browser.get("command") or ["npm", "run", "smoke"]
        if not isinstance(raw_command, list) or not raw_command or not all(isinstance(item, str) and item for item in raw_command):
            self._record_check("browser.smoke", "inconclusive", category="runner_unavailable", detail="browser.command must be a non-empty string array")
            return
        command = list(raw_command)
        executable = shutil.which(command[0])
        if executable:
            command[0] = executable
        env = dict(self.environment)
        env.update(
            {
                "ADAOS_E2E_RUN_ID": self.run_id,
                "ADAOS_E2E_OUTPUT": str(self.bundle_dir),
                "ADAOS_E2E_CLIENT_URL": str(self.config["firebaseClientUrl"]),
                "ADAOS_E2E_HUB_URL": str(self.config["hubApiUrl"]),
                "ADAOS_E2E_SUBNET_ID": str(self.config["subnetId"]),
                "ADAOS_E2E_WEBSPACE_ID": str(self.config["webspaceId"]),
                "ADAOS_E2E_BROWSER_DEVICE_ID": str(self.config["browserDeviceId"]),
            }
        )
        storage_state = str(browser.get("storageStatePath") or "").strip()
        if storage_state:
            storage_path = Path(storage_state)
            if not storage_path.is_absolute():
                storage_path = (self.repo_root / storage_path).resolve()
            env["ADAOS_E2E_STORAGE_STATE"] = str(storage_path)
        started = time.perf_counter()
        log_path = self.bundle_dir / "client" / "browser-command.log"
        try:
            completed = subprocess.run(
                command,
                cwd=project_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=max(30.0, float(dict(self.config.get("timeouts") or {}).get("browserSeconds") or 120.0)),
                check=False,
            )
            output = redact_text((completed.stdout or "") + (completed.stderr or ""), self.secrets)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(output, encoding="utf-8")
            duration_ms = (time.perf_counter() - started) * 1000.0
            if completed.returncode == 0:
                result, category, detail = "passed", None, None
            elif "Cannot find package '@playwright/test'" in output or "not recognized" in output:
                result, category, detail = "inconclusive", "runner_unavailable", "Playwright dependencies or browser are not installed"
            else:
                result, category, detail = "failed", "browser_boot_failed", f"Playwright exited with code {completed.returncode}"
            self._record_check(
                "browser.smoke",
                result,
                duration_ms=duration_ms,
                category=category,
                detail=detail,
                evidence=log_path.relative_to(self.bundle_dir).as_posix(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._record_check("browser.smoke", "inconclusive", duration_ms=duration_ms, category="runner_unavailable", detail=f"{type(exc).__name__}: {exc}")

    def _overall_result(self) -> str:
        if any(check["result"] == "failed" for check in self.checks):
            return "failed"
        if any(check["required"] and check["result"] == "inconclusive" for check in self.checks):
            return "inconclusive"
        return "passed"

    def _write_manifest(self, result: str) -> dict[str, Any]:
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "ended_at": utc_now(),
            "result": result,
            "primary_category": next((item["category"] for item in self.checks if item["result"] in {"failed", "inconclusive"}), None),
            "environment": str(self.config.get("environment") or "stand"),
            "profile": str(self.config.get("profile") or "observe"),
            "target": {
                "subnet_id": self.config.get("subnetId"),
                "hub_id": self.config.get("hubId"),
                "webspace_id": self.config.get("webspaceId"),
                "browser_device_id": self.config.get("browserDeviceId"),
            },
            "repository": _repository_metadata(self.repo_root),
            "runtime": _runtime_metadata(self.repo_root),
            "redaction_version": REDACTION_VERSION,
            "checks": self.checks,
            "omitted_or_unavailable": self.omitted,
        }
        _json_write(self.bundle_dir / "checks.json", {"schema": "adaos.e2e.checks.v1", "run_id": self.run_id, "checks": self.checks})
        _json_write(self.bundle_dir / "manifest.json", manifest)
        return manifest

    def run(self, *, with_browser: bool = False) -> dict[str, Any]:
        self.bundle_dir.mkdir(parents=True, exist_ok=False)
        _json_write(self.bundle_dir / "config.redacted.json", redact_value(self.config, self.secrets))
        config_errors = self._validate_config()
        if config_errors:
            self._record_check("runner.config", "inconclusive", category="runner_unavailable", detail="; ".join(config_errors))
            return self._write_manifest("inconclusive")
        self._run_http_checks()
        if with_browser:
            self._run_browser()
        else:
            self.omitted.append({"evidence": "client/playwright", "reason": "browser smoke was not requested"})
            self._record_check("browser.smoke", "skipped", detail="run again with --browser", required=False)
        return self._write_manifest(self._overall_result())


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stand E2E config must be a JSON object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AdaOS post-deploy stand acceptance checks")
    parser.add_argument("--config", required=True, type=Path, help="JSON target config without inline secrets")
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/e2e-runs"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--browser", action="store_true", help="also run the Playwright headless-browser smoke")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        manifest = StandRunner(config, output_root=args.output_root, run_id=args.run_id).run(with_browser=args.browser)
    except Exception as exc:
        print(json.dumps({"result": "inconclusive", "category": "runner_unavailable", "detail": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps({"result": manifest["result"], "run_id": manifest["run_id"], "bundle": str(args.output_root)}, ensure_ascii=True))
    return 0 if manifest["result"] == "passed" else 1 if manifest["result"] == "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
