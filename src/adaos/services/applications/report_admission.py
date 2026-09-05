from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from adaos.domain.artifact_release import canonical_json_bytes
from adaos.domain.development_report import DevelopmentReport
from adaos.services.applications.store import ApplicationStore


class DevelopmentReportAdmissionError(ValueError):
    pass


class DevelopmentReportClassificationUnavailable(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        provider: str = "local-oci",
        model: str = "unavailable",
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = str(reason_code or "classification_unavailable")[:80]
        self.provider = str(provider or "local-oci")[:80]
        self.model = str(model or "unavailable")[:300]
        self.provenance = dict(provenance or {})


class DevelopmentReportClassifier(Protocol):
    def classify(self, *, summary: str, details: str, evidence: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class DevelopmentReportAdmissionPolicy:
    max_report_bytes: int = 64_000
    max_evidence_items: int = 8
    max_attachment_bytes: int = 2_000_000
    max_total_attachment_bytes: int = 8_000_000
    max_archive_expanded_bytes: int = 16_000_000
    max_archive_entries: int = 256
    max_reports_per_subnet_day: int = 100


@dataclass(frozen=True, slots=True)
class DevelopmentReportAdmission:
    raw_payload_digest: str
    normalized_summary: str
    normalized_details: str
    normalized_evidence: tuple[Mapping[str, Any], ...]
    redaction_findings: tuple[str, ...]
    model_classification: Mapping[str, Any] | None
    checks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "adaos.application.development_report_admission.v1",
            "raw_payload_digest": self.raw_payload_digest,
            "normalized_summary": self.normalized_summary,
            "normalized_details": self.normalized_details,
            "normalized_evidence": [dict(item) for item in self.normalized_evidence],
            "redaction_findings": list(self.redaction_findings),
            "model_classification": dict(self.model_classification) if self.model_classification is not None else None,
            "checks": list(self.checks),
        }


_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s<>\]\[{}]+", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BIDI_CONTROLS = {chr(value) for value in (0x202A, 0x202B, 0x202D, 0x202E, 0x202C, 0x2066, 0x2067, 0x2068, 0x2069)}
_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("assigned_secret", re.compile(r"\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*([^\s,;]{8,})", re.IGNORECASE)),
)
_ALLOWED_MIME = {
    "text/plain", "application/json", "image/png", "image/jpeg", "image/webp",
    "application/zip", "application/gzip", "application/x-tar",
}
_ARCHIVE_MIME = {"application/zip", "application/gzip", "application/x-tar"}
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _safe_url(value: str) -> None:
    if len(value) > 2048:
        raise DevelopmentReportAdmissionError("report URL exceeds 2048 characters")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DevelopmentReportAdmissionError("report URLs must be credential-free HTTPS URLs")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise DevelopmentReportAdmissionError("report URL targets a local host")
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return
    if not address.is_global:
        raise DevelopmentReportAdmissionError("report URL targets a non-public address")


def _normalize_and_redact(value: str) -> tuple[str, set[str]]:
    text = unicodedata.normalize("NFC", str(value))
    if _CONTROL_RE.search(text) or any(char in text for char in _BIDI_CONTROLS):
        raise DevelopmentReportAdmissionError("report text contains forbidden control characters")
    for match in _URL_RE.findall(text):
        _safe_url(match.rstrip(".,);"))
    findings: set[str] = set()
    for finding, pattern in _SECRET_PATTERNS:
        if finding == "assigned_secret":
            def replace(match: re.Match[str]) -> str:
                findings.add(finding)
                return f"{match.group(1)}=[REDACTED]"
            text = pattern.sub(replace, text)
        elif pattern.search(text):
            findings.add(finding)
            text = pattern.sub("[REDACTED]", text)
    return text, findings


def normalize_report_text(value: str) -> tuple[str, tuple[str, ...]]:
    """Return the same bounded, secret-redacted text used by report admission."""
    text, findings = _normalize_and_redact(value)
    return text, tuple(sorted(findings))


def _archive_path(value: str) -> str:
    path = str(value or "").replace("\\", "/").strip()
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or any(part in {"", ".", ".."} for part in path.split("/")):
        raise DevelopmentReportAdmissionError("archive entry path is unsafe")
    return path


class DevelopmentReportAdmissionService:
    def __init__(
        self,
        *,
        application_store: ApplicationStore,
        policy: DevelopmentReportAdmissionPolicy | None = None,
        classifier: DevelopmentReportClassifier | None = None,
    ) -> None:
        self.application_store = application_store
        self.policy = policy or DevelopmentReportAdmissionPolicy()
        self.classifier = classifier

    def verify_local_installation(self, report: DevelopmentReport) -> None:
        try:
            installation = self.application_store.get_installation(report.application_id)
        except FileNotFoundError as exc:
            raise DevelopmentReportAdmissionError("reporter does not have this Application installed") from exc
        proof = report.installation_proof
        if installation.installation_id != proof["installation_id"] or installation.revision != proof["installation_revision"]:
            raise DevelopmentReportAdmissionError("installation proof does not match local authority")
        if installation.installed_release_digest != report.installed_release_digest or installation.status not in {"active", "degraded"}:
            raise DevelopmentReportAdmissionError("installed release proof is not active")

    def _evidence(self, items: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        if len(items) > self.policy.max_evidence_items:
            raise DevelopmentReportAdmissionError("report evidence item limit exceeded")
        normalized: list[dict[str, Any]] = []
        total = 0
        for raw in items:
            allowed = {"kind", "mime_type", "size_bytes", "digest", "artifact_ref", "url", "archive"}
            if set(raw) - allowed:
                raise DevelopmentReportAdmissionError("evidence metadata contains unsupported fields")
            mime = str(raw.get("mime_type") or "").strip().lower()
            if mime not in _ALLOWED_MIME:
                raise DevelopmentReportAdmissionError("evidence MIME type is not allowed")
            try:
                size = int(raw.get("size_bytes"))
            except (TypeError, ValueError) as exc:
                raise DevelopmentReportAdmissionError("evidence size is invalid") from exc
            if size < 0 or size > self.policy.max_attachment_bytes:
                raise DevelopmentReportAdmissionError("evidence item exceeds byte limit")
            total += size
            if total > self.policy.max_total_attachment_bytes:
                raise DevelopmentReportAdmissionError("report evidence total exceeds byte limit")
            digest = str(raw.get("digest") or "").strip().lower()
            if not _DIGEST_RE.fullmatch(digest):
                raise DevelopmentReportAdmissionError("evidence digest is invalid")
            artifact_ref = str(raw.get("artifact_ref") or "").strip()
            if not artifact_ref or len(artifact_ref) > 300:
                raise DevelopmentReportAdmissionError("bounded evidence artifact_ref is required")
            item: dict[str, Any] = {"kind": str(raw.get("kind") or "attachment").strip().lower(), "mime_type": mime, "size_bytes": size, "digest": digest, "artifact_ref": artifact_ref}
            if raw.get("url") is not None:
                url = str(raw["url"]).strip()
                _safe_url(url)
                item["url"] = url
            archive = raw.get("archive")
            if mime in _ARCHIVE_MIME:
                if not isinstance(archive, Mapping) or set(archive) != {"expanded_size_bytes", "entries"}:
                    raise DevelopmentReportAdmissionError("archive evidence requires expansion metadata")
                expanded = int(archive["expanded_size_bytes"])
                entries = archive["entries"]
                if expanded < 0 or expanded > self.policy.max_archive_expanded_bytes or not isinstance(entries, list) or len(entries) > self.policy.max_archive_entries:
                    raise DevelopmentReportAdmissionError("archive expansion policy exceeded")
                if size and expanded > size * 100:
                    raise DevelopmentReportAdmissionError("archive expansion ratio is unsafe")
                item["archive"] = {"expanded_size_bytes": expanded, "entries": [_archive_path(entry) for entry in entries]}
            elif archive is not None:
                raise DevelopmentReportAdmissionError("non-archive evidence cannot declare archive metadata")
            normalized.append(item)
        return tuple(normalized)

    @staticmethod
    def _model_output(value: Mapping[str, Any], *, input_digest: str) -> dict[str, Any]:
        allowed = {
            "provider", "model", "category", "confidence", "tags", "summary",
            "provenance", "status", "reason_code",
        }
        if set(value) - allowed:
            raise DevelopmentReportAdmissionError("classifier returned authority-bearing or unsupported fields")
        status = str(value.get("status") or "completed").strip().lower()
        if status not in {"completed", "unavailable"}:
            raise DevelopmentReportAdmissionError("classifier status is invalid")
        category = str(value.get("category") or "unknown").strip().lower()
        if category not in {"bug", "feature", "compatibility", "usability", "performance", "security", "unknown"}:
            raise DevelopmentReportAdmissionError("classifier category is invalid")
        confidence = float(value.get("confidence") or 0.0)
        if not 0.0 <= confidence <= 1.0:
            raise DevelopmentReportAdmissionError("classifier confidence is invalid")
        raw_tags = value.get("tags") or []
        if not isinstance(raw_tags, (list, tuple)) or len(raw_tags) > 50:
            raise DevelopmentReportAdmissionError("classifier tags are invalid")
        tags: list[str] = []
        for raw_tag in raw_tags:
            tag, findings = _normalize_and_redact(str(raw_tag).strip().lower())
            if findings or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", tag):
                raise DevelopmentReportAdmissionError("classifier tag is invalid")
            tags.append(tag)
        tags = sorted(set(tags))[:12]
        summary, findings = _normalize_and_redact(str(value.get("summary") or category))
        if findings:
            raise DevelopmentReportAdmissionError("classifier emitted secret-like content")
        provider = str(value.get("provider") or "local-isolated").strip()
        model = str(value.get("model") or "unspecified").strip()
        if not provider or len(provider) > 80 or not model or len(model) > 300:
            raise DevelopmentReportAdmissionError("classifier provenance identity is invalid")
        provenance = value.get("provenance")
        if provenance is not None:
            if not isinstance(provenance, Mapping):
                raise DevelopmentReportAdmissionError("classifier provenance is invalid")
            if provenance.get("input_digest") != input_digest:
                raise DevelopmentReportAdmissionError("classifier provenance input digest mismatch")
        return {
            "schema": "adaos.application.development_report_classification.v1",
            "provider": provider,
            "model": model,
            "category": category, "confidence": confidence, "tags": tags,
            "summary": summary[:500], "input_digest": input_digest,
            "status": status,
            "reason_code": (
                str(value.get("reason_code") or "classification_unavailable")[:80]
                if status == "unavailable"
                else None
            ),
            "provenance": dict(provenance) if provenance is not None else None,
            "authority": "advisory_only",
            "isolation": {
                "tools": False,
                "network": False,
                "secrets": False,
                "host_files": "scratch_only",
            },
        }

    def admit(
        self,
        report: DevelopmentReport | Mapping[str, Any],
        *,
        recent_report_count: int = 0,
        known_idempotency_keys: Sequence[str] = (),
        verify_local_installation: bool = False,
    ) -> DevelopmentReportAdmission:
        value = report if isinstance(report, DevelopmentReport) else DevelopmentReport.from_mapping(report)
        raw = canonical_json_bytes(value.to_dict())
        if len(raw) > self.policy.max_report_bytes:
            raise DevelopmentReportAdmissionError("DevelopmentReport exceeds byte limit")
        if recent_report_count >= self.policy.max_reports_per_subnet_day:
            raise DevelopmentReportAdmissionError("reporter daily quota exceeded")
        if value.idempotency_key in set(known_idempotency_keys):
            raise DevelopmentReportAdmissionError("DevelopmentReport replay detected")
        application = self.application_store.get_application(value.application_id)
        if application.publisher_ref != value.publisher_ref:
            raise DevelopmentReportAdmissionError("DevelopmentReport publisher binding is invalid")
        try:
            self.application_store.get_release(value.application_id, value.installed_release_digest)
        except FileNotFoundError as exc:
            raise DevelopmentReportAdmissionError("DevelopmentReport references an unknown release") from exc
        if verify_local_installation:
            self.verify_local_installation(value)
        summary, summary_findings = _normalize_and_redact(value.summary)
        details, detail_findings = _normalize_and_redact(value.details)
        evidence = self._evidence(value.evidence)
        normalized_input = {"summary": summary, "details": details, "evidence": list(evidence)}
        input_digest = f"sha256:{hashlib.sha256(canonical_json_bytes(normalized_input)).hexdigest()}"
        classification = None
        if self.classifier is not None:
            try:
                model_output = self.classifier.classify(
                    summary=summary,
                    details=details,
                    evidence=evidence,
                )
            except DevelopmentReportClassificationUnavailable as exc:
                model_output = {
                    "provider": exc.provider,
                    "model": exc.model,
                    "category": "unknown",
                    "confidence": 0.0,
                    "tags": ["classification_unavailable"],
                    "summary": "Classification unavailable",
                    "status": "unavailable",
                    "reason_code": exc.reason_code,
                    "provenance": {**exc.provenance, "input_digest": input_digest},
                }
            classification = self._model_output(model_output, input_digest=input_digest)
        return DevelopmentReportAdmission(
            raw_payload_digest=f"sha256:{hashlib.sha256(raw).hexdigest()}",
            normalized_summary=summary, normalized_details=details,
            normalized_evidence=evidence,
            redaction_findings=tuple(sorted(summary_findings | detail_findings)),
            model_classification=classification,
            checks=("schema", "bytes", "mime", "archive", "unicode", "url", "quota", "replay", "release_proof", "secret_redaction"),
        )
