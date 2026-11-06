from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


class ReleaseSecretScanError(ValueError):
    def __init__(self, findings: Iterable["SecretFinding"]) -> None:
        self.findings = tuple(findings)
        summary = ", ".join(
            f"{item.kind}:{item.path}:{item.line}" for item in self.findings[:8]
        )
        super().__init__(f"release contains secret-like material: {summary}")


@dataclass(frozen=True, slots=True)
class SecretFinding:
    kind: str
    path: str
    line: int
    value_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "value_digest": self.value_digest,
        }


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:ENCRYPTED |RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.IGNORECASE,
        ),
    ),
    ("firebase_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("root_token", re.compile(r"\bmcp_[a-f0-9]{32,128}\b", re.IGNORECASE)),
    (
        "github_token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
        ),
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"
        ),
    ),
)
_ASSIGNMENT = re.compile(
    r"(?im)(?:^|[,{;\s])(?:[\"']?)"
    r"(cloudflare[_-]?(?:api[_-]?)?token|cf[_-]?(?:api[_-]?)?token|"
    r"firebase[_-]?(?:api[_-]?)?key|reg(?:ru)?[_-]?(?:s3[_-]?)?(?:secret|key)|"
    r"aws[_-]?secret[_-]?access[_-]?key|root[_-]?(?:owner[_-]?)?token|"
    r"api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)"
    r"(?:[\"']?)\s*[:=]\s*(?:[\"']?)"
    r"([^\s,;}\"']{12,512})",
)
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:token|secret|password|passwd|api[_-]?key|"
    r"private[_-]?key|access[_-]?key)$",
    re.IGNORECASE,
)
_SAFE_VALUES = re.compile(
    r"^(?:\[?redacted\]?|masked|none|null|true|false|0|"
    r"sha256:[a-f0-9]{64}|credential:[a-f0-9]{32}|"
    r"\$\{[A-Z0-9_]+\}|\$[A-Z][A-Z0-9_]*|"
    r"(?:example|sample|test|placeholder|changeme|dummy)(?:[-_.:].*)?)$",
    re.IGNORECASE,
)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _secret_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if len(token) < 12 or _SAFE_VALUES.fullmatch(token):
        return None
    if token.startswith(("https://", "http://")) and not re.search(
        r"[?&](?:token|key|secret|password)=", token, re.IGNORECASE
    ):
        return None
    classes = sum(
        bool(pattern.search(token))
        for pattern in (
            re.compile(r"[a-z]"),
            re.compile(r"[A-Z]"),
            re.compile(r"\d"),
            re.compile(r"[^A-Za-z0-9]"),
        )
    )
    return token if len(token) >= 20 and classes >= 2 else None


def _json_findings(value: Any, *, path: str, text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, item in node.items():
                if _SENSITIVE_KEY.search(str(key)):
                    secret = _secret_value(item)
                    if secret is not None:
                        offset = text.find(secret)
                        findings.append(
                            SecretFinding(
                                "assigned_secret",
                                path,
                                _line(text, offset),
                                _digest(secret),
                            )
                        )
                visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return findings


def scan_release_bytes(path: str, data: bytes) -> tuple[SecretFinding, ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ()
    findings: list[SecretFinding] = []
    seen: set[tuple[str, int, str]] = set()
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            finding = SecretFinding(kind, path, _line(text, match.start()), _digest(value))
            identity = (finding.kind, finding.line, finding.value_digest)
            if identity not in seen:
                findings.append(finding)
                seen.add(identity)
    for match in _ASSIGNMENT.finditer(text):
        value = match.group(2)
        if _SAFE_VALUES.fullmatch(value):
            continue
        finding = SecretFinding(
            "assigned_secret", path, _line(text, match.start(2)), _digest(value)
        )
        identity = (finding.kind, finding.line, finding.value_digest)
        if identity not in seen:
            findings.append(finding)
            seen.add(identity)
    if path.lower().endswith(".json"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            for finding in _json_findings(parsed, path=path, text=text):
                identity = (finding.kind, finding.line, finding.value_digest)
                if identity not in seen:
                    findings.append(finding)
                    seen.add(identity)
    return tuple(findings)


def scan_release_tree(
    root: Path,
    *,
    max_files: int = 20_000,
    max_bytes: int = 256 * 1024 * 1024,
) -> tuple[SecretFinding, ...]:
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("release scan root must be a directory")
    findings: list[SecretFinding] = []
    files = 0
    total = 0
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        if path.is_symlink():
            raise ReleaseSecretScanError(
                (
                    SecretFinding(
                        "symlink",
                        path.relative_to(root).as_posix(),
                        0,
                        _digest(str(path)),
                    ),
                )
            )
        if not path.is_file():
            continue
        files += 1
        data = path.read_bytes()
        total += len(data)
        if files > max_files or total > max_bytes:
            raise ValueError("release secret scan bounds exceeded")
        findings.extend(scan_release_bytes(path.relative_to(root).as_posix(), data))
    return tuple(findings)


def assert_release_bytes_secret_free(path: str, data: bytes) -> None:
    findings = scan_release_bytes(path, data)
    if findings:
        raise ReleaseSecretScanError(findings)


def assert_release_tree_secret_free(root: Path) -> None:
    findings = scan_release_tree(root)
    if findings:
        raise ReleaseSecretScanError(findings)


__all__ = [
    "ReleaseSecretScanError",
    "SecretFinding",
    "assert_release_bytes_secret_free",
    "assert_release_tree_secret_free",
    "scan_release_bytes",
    "scan_release_tree",
]
