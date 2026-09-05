from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from adaos.domain.application import TrialAccessGrant, utc_now
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock

from .service import ApplicationService, ApplicationServiceError
from .store import _read


class TrialAccessError(ApplicationServiceError):
    pass


class TrialAccessService:
    """Issues and redeems bounded capability links without persisting bearer secrets."""

    def __init__(self, applications: ApplicationService) -> None:
        self.applications = applications
        self.store = applications.store

    @property
    def _issuer_key_path(self) -> Path:
        return self.store.root / "keys" / "trial-link-issuer.key"

    def _issuer_key(self) -> bytes:
        path = self._issuer_key_path
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            if path.is_file():
                key = path.read_bytes()
                if len(key) != 32:
                    raise TrialAccessError("Trial link issuer key is corrupt")
                return key
            key = secrets.token_bytes(32)
            atomic_write_bytes(path, key)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return key

    @staticmethod
    def _timestamp(value: str, *, field: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise TrialAccessError(f"{field} must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise TrialAccessError(f"{field} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def _token(self, grant_id: str) -> str:
        digest = hmac.new(self._issuer_key(), grant_id.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"tal_{digest}"

    def _credential_path(self, grant_id: str) -> Path:
        return self.store.root / "trial_access_credentials" / f"{hashlib.sha256(grant_id.encode()).hexdigest()}.json"

    def _redemption_path(self, redemption_id: str) -> Path:
        token = hashlib.sha256(redemption_id.encode("utf-8")).hexdigest()
        return self.store.root / "trial_access_redemptions" / f"{token}.json"

    def issue(
        self,
        application_id: str,
        *,
        publisher_ref: str,
        recipient_subnet_ref: str,
        recipient_key_ref: str,
        scope: str,
        expires_at: str,
        allowed_zones: tuple[str, ...],
        idempotency_key: str,
        release_digest: str | None = None,
        max_uses: int = 1,
    ) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise TrialAccessError("only the Application publisher may issue Trial access")
        if self._timestamp(expires_at, field="expires_at") <= datetime.now(timezone.utc):
            raise TrialAccessError("Trial access expiry must be in the future")
        key = str(idempotency_key or "").strip()
        if not key:
            raise TrialAccessError("idempotency_key is required")
        identity = ":".join((application_id, publisher_ref, key))
        grant_id = "trialgrant." + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        nonce = hashlib.sha256(f"{grant_id}:nonce".encode("utf-8")).hexdigest()
        value = TrialAccessGrant(
            grant_id=grant_id,
            application_id=application_id,
            publisher_ref=publisher_ref,
            scope=scope,
            release_digest=release_digest,
            recipient_subnet_ref=recipient_subnet_ref,
            recipient_key_ref=recipient_key_ref,
            expires_at=expires_at,
            max_uses=max_uses,
            uses=0,
            nonce=nonce,
            allowed_zones=allowed_zones,
            status="active",
            revision=1,
        )
        if release_digest is not None:
            self.store.get_release(application_id, release_digest)
        token = self._token(grant_id)
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            try:
                existing = self.store.get_grant(grant_id)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                expected = replace(value, issued_at=existing.issued_at)
                if existing != expected:
                    raise TrialAccessError("idempotency key already names different Trial access")
                value = existing
            credential = {
                "schema": "adaos.application.trial_access_credential.v1",
                "grant_id": grant_id,
                "token_hash": "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "idempotency_key_hash": "sha256:"
                + hashlib.sha256(key.encode("utf-8")).hexdigest(),
                "issued_at": value.issued_at,
            }
            if existing is None:
                self.store.save_grant(value, expected_revision=0)
                atomic_write_json(self._credential_path(grant_id), credential)
            else:
                stored_credential = _read(self._credential_path(grant_id))
                if stored_credential != credential:
                    raise TrialAccessError("Trial access credential record is inconsistent")
        return {
            "grant": value.to_dict(),
            "link": f"adaos://applications/trial/{grant_id}?token={token}",
        }

    @staticmethod
    def _parse_link(link: str) -> tuple[str, str]:
        parsed = urlparse(str(link or ""))
        parts = [part for part in parsed.path.split("/") if part]
        token = (parse_qs(parsed.query).get("token") or [""])[0]
        if parsed.scheme != "adaos" or parsed.netloc != "applications" or len(parts) != 2 or parts[0] != "trial":
            raise TrialAccessError("unsupported Trial capability link")
        if not token:
            raise TrialAccessError("Trial capability token is required")
        return parts[1], token

    def resolve(
        self,
        link: str,
        *,
        recipient_subnet_ref: str,
        recipient_key_ref: str,
        zone: str,
        redemption_id: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        grant_id, token = self._parse_link(link)
        redemption_token = str(redemption_id or "").strip()
        if not redemption_token:
            raise TrialAccessError("redemption_id is required")
        current_time = self._timestamp(now or utc_now(), field="now")
        with mutation_lock(self.store.lock_path, timeout_s=30.0):
            grant = self.store.get_grant(grant_id)
            credential = _read(self._credential_path(grant_id))
            if credential.get("grant_id") != grant_id:
                raise TrialAccessError("Trial access credential record is inconsistent")
            actual_hash = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(str(credential.get("token_hash") or ""), actual_hash):
                raise TrialAccessError("Trial capability token is invalid")
            if grant.recipient_subnet_ref != recipient_subnet_ref:
                raise TrialAccessError("Trial access belongs to another subnet")
            if grant.recipient_key_ref != recipient_key_ref:
                raise TrialAccessError("Trial access belongs to another recipient key")
            if zone not in grant.allowed_zones:
                raise TrialAccessError("Trial access is not valid in this zone")
            receipt_path = self._redemption_path(redemption_token)
            if receipt_path.is_file():
                receipt = _read(receipt_path)
                expected_receipt_identity = {
                    "schema": "adaos.application.trial_access_redemption.v1",
                    "redemption_id": redemption_token,
                    "grant_id": grant_id,
                    "application_id": grant.application_id,
                    "recipient_subnet_ref": recipient_subnet_ref,
                    "recipient_key_ref": recipient_key_ref,
                    "zone": zone,
                }
                if any(
                    receipt.get(field) != value
                    for field, value in expected_receipt_identity.items()
                ):
                    raise TrialAccessError("redemption identity already names another capability use")
                return {**receipt, "idempotent_replay": True}
            if grant.status != "active":
                raise TrialAccessError(f"Trial access is {grant.status}")
            if current_time >= self._timestamp(grant.expires_at, field="expires_at"):
                expired = replace(grant, status="expired", revision=grant.revision + 1)
                self.store.save_grant(expired, expected_revision=grant.revision)
                raise TrialAccessError("Trial access has expired")
            if grant.scope == "exact_release":
                release_digest = str(grant.release_digest)
            else:
                release_digest = str(
                    (self.store.get_channels(grant.application_id).get("channels") or {}).get("prerelease") or ""
                )
                if not release_digest:
                    raise TrialAccessError("Application has no current prerelease")
            self.store.get_release(grant.application_id, release_digest)
            uses = grant.uses + 1
            updated = replace(
                grant,
                uses=uses,
                status="consumed" if uses >= grant.max_uses else "active",
                revision=grant.revision + 1,
            )
            self.store.save_grant(updated, expected_revision=grant.revision)
            receipt = {
                "schema": "adaos.application.trial_access_redemption.v1",
                "redemption_id": redemption_token,
                "grant_id": grant_id,
                "application_id": grant.application_id,
                "release_digest": release_digest,
                "recipient_subnet_ref": recipient_subnet_ref,
                "recipient_key_ref": recipient_key_ref,
                "zone": zone,
                "redeemed_at": current_time.isoformat(),
                "grant_revision": updated.revision,
                "idempotent_replay": False,
            }
            atomic_write_json(receipt_path, receipt)
            return receipt

    def revoke(self, grant_id: str, *, publisher_ref: str, expected_revision: int) -> TrialAccessGrant:
        grant = self.store.get_grant(grant_id)
        if grant.publisher_ref != publisher_ref:
            raise TrialAccessError("only the Application publisher may revoke Trial access")
        if grant.revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(expected=expected_revision, observed=grant.revision)
        if grant.status == "revoked":
            return grant
        revoked = replace(grant, status="revoked", revision=grant.revision + 1)
        return self.store.save_grant(revoked, expected_revision=grant.revision)


__all__ = ["TrialAccessError", "TrialAccessService"]
