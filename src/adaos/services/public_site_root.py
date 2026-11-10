"""Infrastructure Root publication authority, independent of a node runtime.

The HTTP ingress supplies an authenticated subnet and effective Root policy.
Neither identity nor quota is taken from the public request body.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any, Callable, Mapping
import uuid

from adaos.services.artifact_pipeline.storage import mutation_lock
from adaos.services.public_site_delivery import build_delivery_bundle
from adaos.services.release_secret_scan import assert_release_tree_secret_free
from adaos.services.site_studio.contracts import digest, validate_contract

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")
HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
PLATFORM = re.compile(r"^st_[a-z0-9]{6,32}$")
CHUNK_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_FILES = 4096


class PublicationRootError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise PublicationRootError("invalid_input", "Invalid publication identity.", 400)
    return value


def relative_path(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 500 or "\\" in value
            or "\x00" in value or ":" in value or value.startswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise PublicationRootError("invalid_bundle_path", "Invalid publication artifact path.", 400)
    return value


class PublicSiteRootService:
    def __init__(self, root: Path, delivery: Any, *, publication_authority: bool = True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.delivery = delivery
        self.publication_authority = publication_authority
        self.database = self.root / "publication.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS authority_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sites (
                    site_id TEXT PRIMARY KEY, subnet_id TEXT NOT NULL,
                    platform_path TEXT NOT NULL UNIQUE, verified INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY, subnet_id TEXT NOT NULL, site_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS occupancy (
                    site_id TEXT NOT NULL, environment TEXT NOT NULL, subnet_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(site_id, environment));
                CREATE TABLE IF NOT EXISTS domains (
                    hostname TEXT PRIMARY KEY, subnet_id TEXT NOT NULL, site_id TEXT NOT NULL,
                    status TEXT NOT NULL, body TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO authority_metadata VALUES('authority_id',?)", (uuid.uuid4().hex,))
            self.authority_id = db.execute("SELECT value FROM authority_metadata WHERE key='authority_id'").fetchone()[0]

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _policy(policy: Mapping[str, Any], feature: str = "publication") -> None:
        if policy.get(feature) is not True:
            raise PublicationRootError("site_studio_feature_not_entitled", "Publication feature is not included or is suspended.", 403)

    def usage(self, subnet: str, policy: Mapping[str, Any]) -> dict[str, Any]:
        with self._db() as db:
            rows = db.execute("SELECT site_id,status FROM occupancy WHERE subnet_id=?", (subnet,)).fetchall()
            published = {r["site_id"] for r in rows if r["status"] == "published"}
            reserved = {r["site_id"] for r in rows if r["status"] == "reserved"} - published
            domains = db.execute("SELECT count(*) FROM domains WHERE subnet_id=? AND status!='released'", (subnet,)).fetchone()[0]
        limit = policy.get("published_sites_limit")
        return {"published": len(published), "reserved": len(reserved), "limit": limit,
                "remaining": None if limit is None else max(0, int(limit)-len(published | reserved)),
                "domains": domains, "custom_domains_limit": policy.get("custom_domains_limit")}

    def _operation(self, db, subnet: str, operation_id: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM operations WHERE operation_id=? AND subnet_id=?", (identifier(operation_id), subnet)).fetchone()
        if row is None:
            raise PublicationRootError("publication_not_found", "Publication operation was not found.", 404)
        return json.loads(row["body"])

    @staticmethod
    def _save(db, operation: dict[str, Any]) -> None:
        db.execute("UPDATE operations SET status=?,body=? WHERE operation_id=?",
                   (operation["status"], json.dumps(operation, sort_keys=True), operation["operation_id"]))

    def _path(self, operation_id: str) -> Path:
        return self.root / "operations" / identifier(operation_id)

    def begin(self, subnet: str, request: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
        subnet = identifier(subnet)
        self._policy(policy)
        operation_id = identifier(request.get("operation_id"))
        manifest = request.get("manifest")
        if not isinstance(manifest, dict):
            raise PublicationRootError("invalid_bundle", "Publication manifest is required.", 400)
        validate_contract("public_site_delivery_bundle.v1.schema.json", manifest)
        body = {k: v for k, v in manifest.items() if k != "bundle_digest"}
        if digest(body) != manifest.get("bundle_digest"):
            raise PublicationRootError("invalid_bundle_digest", "Publication manifest digest does not match.", 400)
        site_id = identifier(manifest.get("site_id"))
        platform = str(request.get("platform_path") or "")
        environment = request.get("environment")
        if not PLATFORM.fullmatch(platform) or environment not in {"dev", "prod"}:
            raise PublicationRootError("invalid_address", "Publication address is invalid.", 400)
        artifacts = manifest["artifacts"]
        paths = [relative_path(a["path"]) for a in artifacts]
        if (len(paths) != len(set(paths)) or not 1 <= len(paths) <= MAX_FILES
                or sum(a["size_bytes"] for a in artifacts) > MAX_BUNDLE_BYTES):
            raise PublicationRootError("bundle_bounds_exceeded", "Publication bundle exceeds its bounds.", 413)
        for a in artifacts:
            if not HASH.fullmatch(a["digest"]) or not 0 <= a["size_bytes"] <= MAX_BUNDLE_BYTES:
                raise PublicationRootError("invalid_bundle", "Invalid publication artifact.", 400)
        request_body = dict(request)
        request_digest = digest(request_body)
        with self._db() as db:
            previous = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if previous:
                if previous["subnet_id"] != subnet or previous["request_digest"] != request_digest:
                    raise PublicationRootError("operation_conflict", "Publication operation identity is already bound.")
                return self._view(json.loads(previous["body"]))
            site = db.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
            if site and (site["subnet_id"] != subnet or site["platform_path"] != platform):
                raise PublicationRootError("site_owner_mismatch", "Site ownership or public address does not match.", 403)
            try:
                if not site:
                    db.execute("INSERT INTO sites(site_id,subnet_id,platform_path) VALUES(?,?,?)", (site_id, subnet, platform))
            except sqlite3.IntegrityError:
                raise PublicationRootError("site_address_conflict", "Public address belongs to another site.") from None
            operation = {"operation_id": operation_id, "subnet_id": subnet, "site_id": site_id,
                         "environment": environment, "platform_path": platform,
                         "manifest": manifest, "authoring": request.get("authoring") or {}, "status": "uploading"}
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?)", (operation_id, subnet, site_id, request_digest, "uploading", json.dumps(operation)))
        return self._view(operation)

    def chunk(self, subnet: str, request: Mapping[str, Any]) -> dict[str, Any]:
        with self._db() as db:
            operation = self._operation(db, subnet, request.get("operation_id"))
        if operation["status"] != "uploading":
            raise PublicationRootError("publication_not_uploading", "Publication is no longer accepting artifacts.")
        artifact = next((a for a in operation["manifest"]["artifacts"] if a["digest"] == request.get("digest")), None)
        index = request.get("index")
        if artifact is None or isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise PublicationRootError("invalid_chunk", "Artifact chunk is not in the manifest.", 400)
        try:
            raw = base64.b64decode(request.get("content", ""), validate=True)
        except (ValueError, TypeError):
            raise PublicationRootError("invalid_chunk", "Invalid artifact encoding.", 400) from None
        expected = min(CHUNK_BYTES, artifact["size_bytes"] - index * CHUNK_BYTES)
        if expected < 0 or len(raw) != expected or (not raw and index != 0):
            raise PublicationRootError("invalid_chunk", "Artifact chunk has an invalid length.", 400)
        directory = self._path(operation["operation_id"]) / "chunks" / artifact["digest"][7:]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / str(index)
        with mutation_lock(self._path(operation["operation_id"]) / ".upload.lock", timeout_s=30):
            if target.exists():
                if target.read_bytes() != raw:
                    raise PublicationRootError("chunk_conflict", "Artifact chunk differs from its retained bytes.")
            else:
                temporary = target.with_suffix("." + uuid.uuid4().hex)
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    __import__("os").fsync(stream.fileno())
                temporary.replace(target)
        return {"accepted": True, "index": index}

    def _materialize(self, operation: Mapping[str, Any]) -> Path:
        root = self._path(operation["operation_id"])
        target = root / "site"
        target.mkdir(parents=True, exist_ok=True)
        for artifact in operation["manifest"]["artifacts"]:
            output = target / relative_path(artifact["path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            hasher = hashlib.sha256()
            with output.open("wb") as stream:
                count = max(1, (artifact["size_bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES)
                for index in range(count):
                    source = root / "chunks" / artifact["digest"][7:] / str(index)
                    if not source.is_file():
                        raise PublicationRootError("artifact_incomplete", "Publication artifact upload is incomplete.")
                    raw = source.read_bytes()
                    hasher.update(raw)
                    stream.write(raw)
            if "sha256:" + hasher.hexdigest() != artifact["digest"] or output.stat().st_size != artifact["size_bytes"]:
                raise PublicationRootError("artifact_digest_mismatch", "Publication artifact bytes do not match.")
        actual = build_delivery_bundle(target)
        if actual.manifest != operation["manifest"]:
            raise PublicationRootError("bundle_mismatch", "Admitted public-site bundle differs from the upload.")
        assert_release_tree_secret_free(target)
        return target

    def prepare(self, subnet: str, request: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
        self._policy(policy)
        operation_id = identifier(request.get("operation_id"))
        with mutation_lock(self._path(operation_id) / ".operation.lock", timeout_s=1):
            with self._db() as db:
                operation = self._operation(db, subnet, operation_id)
                if operation["status"] in {"prepared", "published"}:
                    return self._view(operation)
                if operation["status"] != "uploading":
                    raise PublicationRootError("publication_pending", "Publication requires status reconciliation.")
            target = self._materialize(operation)
            workspace = self._path(operation_id) / "provider"
            prepared = self.delivery.prepare(site_dir=target, workspace=workspace,
                platform_path=operation["platform_path"], environment=operation["environment"])
            full = json.loads((workspace / "publication-plan.json").read_text("utf-8"))
            if (prepared.get("deployment_digest") != operation["manifest"]["deployment_digest"]
                    or full.get("pointer", {}).get("deployment_digest") != operation["manifest"]["deployment_digest"]
                    or full.get("environment") != operation["environment"]
                    or full.get("platform_path") != operation["platform_path"]
                    or not HASH.fullmatch(str(prepared.get("plan_sha256", "")))):
                raise PublicationRootError("provider_plan_mismatch", "Provider plan does not match the admitted bundle.")
            with self._db() as db:
                site = db.execute("SELECT * FROM sites WHERE site_id=?", (operation["site_id"],)).fetchone()
                if self.publication_authority and not site["verified"] and full.get("pointer", {}).get("previous_pointer_digest"):
                    previous = full["pointer"]["previous_pointer_digest"]
                    lineage = [json.loads(r[0]) for r in db.execute(
                        "SELECT body FROM operations WHERE site_id=? AND subnet_id=? AND status IN ('publishing','reconciliation_required','published')",
                        (operation["site_id"], subnet))]
                    if not any(r.get("plan", {}).get("pointer", {}).get("pointer_digest") == previous for r in lineage):
                        raise PublicationRootError("site_migration_required", "Existing publication requires verified ownership migration.", 403)
                operation.update(status="prepared", plan_digest=prepared["plan_sha256"],
                    plan={k: full[k] for k in ("environment", "host", "platform_path", "public_url", "pointer") if k in full})
                self._save(db, operation)
            return self._view(operation)

    def publish(self, subnet: str, request: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
        self._policy(policy)
        if request.get("confirmed") is not True:
            raise PublicationRootError("confirmation_required", "Publication requires explicit confirmation.", 400)
        operation_id = identifier(request.get("operation_id"))
        with mutation_lock(self._path(operation_id) / ".operation.lock", timeout_s=1):
            with self._db() as db:
                operation = self._operation(db, subnet, operation_id)
                if request.get("plan_digest") != operation.get("plan_digest"):
                    raise PublicationRootError("publication_plan_conflict", "Reviewed publication plan does not match.")
                if operation["status"] == "published":
                    return self._view(operation)
                if operation["status"] != "prepared":
                    raise PublicationRootError("publication_pending", "Publication requires status reconciliation; it will not be retried.")
                occupied = {r[0] for r in db.execute("SELECT site_id FROM occupancy WHERE subnet_id=?", (subnet,))}
                limit = policy.get("published_sites_limit")
                if limit is not None and operation["site_id"] not in occupied and len(occupied) >= int(limit):
                    raise PublicationRootError("site_studio_site_limit_exhausted", "The subnet's published-site allowance is full.")
                # Keep the old published occupancy during an update and reserve a
                # new environment/site atomically before any provider mutation.
                db.execute("INSERT OR IGNORE INTO occupancy VALUES(?,?,?,?,?)", (operation["site_id"], operation["environment"], subnet, operation_id, "reserved"))
                operation["status"] = "publishing"
                self._save(db, operation)
            try:
                result = self.delivery.publish(site_dir=self._path(operation_id)/"site", workspace=self._path(operation_id)/"provider",
                    platform_path=operation["platform_path"], environment=operation["environment"], plan_digest=operation["plan_digest"])
                if (result.get("deployment_digest") != operation["manifest"]["deployment_digest"]
                        or result.get("pointer_digest") != operation["plan"]["pointer"]["pointer_digest"]):
                    raise PublicationRootError("provider_receipt_mismatch", "Provider receipt does not match the reviewed publication.")
            except BaseException:
                with self._db() as db:
                    operation["status"] = "reconciliation_required"
                    self._save(db, operation)
                raise
            with self._db() as db:
                operation.update(status="published", receipt={k: result[k] for k in ("ok", "deployment_digest", "pointer_digest", "url", "gc_status") if k in result})
                operation["receipt"]["receipt_id"] = operation_id
                db.execute("UPDATE occupancy SET operation_id=?,status='published' WHERE site_id=? AND environment=?",
                    (operation_id, operation["site_id"], operation["environment"]))
                db.execute("UPDATE sites SET verified=1 WHERE site_id=?", (operation["site_id"],))
                self._save(db, operation)
            return self._view(operation)

    def status(self, subnet: str, request: Mapping[str, Any]) -> dict[str, Any]:
        with self._db() as db:
            operation = self._operation(db, subnet, request.get("operation_id"))
        return self._view(operation)

    def site_status(self, subnet: str, request: Mapping[str, Any]) -> dict[str, Any]:
        site_id = identifier(request.get("site_id"))
        with self._db() as db:
            site = db.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
            if site and site["subnet_id"] != subnet:
                raise PublicationRootError("site_owner_mismatch", "Site belongs to another subnet.", 403)
            rows = db.execute("SELECT o.body,c.environment FROM occupancy c JOIN operations o ON c.operation_id=o.operation_id WHERE c.site_id=? AND c.subnet_id=? AND c.status='published'", (site_id, subnet)).fetchall()
        publications = {}
        for row in rows:
            operation = json.loads(row["body"])
            pointer = operation.get("plan", {}).get("pointer", {})
            authoring = operation.get("authoring", {})
            publications[row["environment"]] = {
                "site_id": site_id, "project_id": authoring.get("project_id", site_id),
                "environment": row["environment"], "root_operation_id": operation["operation_id"],
                "platform_path": operation["platform_path"],
                "public_url": operation.get("plan", {}).get("public_url"),
                **{k:authoring.get(k) for k in ("revision_id", "source_digest")},
                **{k:pointer.get(k) for k in ("deployment_digest", "pointer_digest", "generation", "published_at")}}
        return {"site_id": site_id, "known": bool(site), "platform_path": site["platform_path"] if site else None, "publications": publications}

    def domain(self, subnet: str, request: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
        action = request.get("command")
        hostname = str(request.get("hostname") or "").lower().rstrip(".")
        site_id = identifier(request.get("site_id"))
        if (not re.fullmatch(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]+", hostname)
                or hostname == "adaos.io" or hostname.endswith(".adaos.io")):
            raise PublicationRootError("invalid_domain", "Custom domain is invalid or reserved.", 400)
        if action not in {"request", "status", "activate"}:
            raise PublicationRootError("invalid_input", "Unsupported domain operation.", 400)
        if action != "status":
            self._policy(policy, "custom_domains")
        with mutation_lock(self.root/"domain-locks"/(hashlib.sha256(hostname.encode()).hexdigest()+".lock"), timeout_s=1):
            with self._db() as db:
                site = db.execute("SELECT * FROM sites WHERE site_id=? AND subnet_id=? AND verified=1", (site_id, subnet)).fetchone()
                if not site:
                    raise PublicationRootError("site_owner_mismatch", "Domain requires a verified published site.", 403)
                current = db.execute("SELECT * FROM domains WHERE hostname=?", (hostname,)).fetchone()
                if current and (current["subnet_id"] != subnet or current["site_id"] != site_id):
                    raise PublicationRootError("domain_owner_mismatch", "Domain belongs to another site.", 403)
                if action == "request" and current:
                    return json.loads(current["body"])
                if action == "activate" and current:
                    if current["status"] == "active":
                        return json.loads(current["body"])
                    if current["status"] == "activating":
                        raise PublicationRootError("domain_reconciliation_required", "Domain activation requires status reconciliation.")
                if not current:
                    if action != "request":
                        raise PublicationRootError("domain_not_found", "Domain binding was not found.", 404)
                    limit = policy.get("custom_domains_limit")
                    used = db.execute("SELECT count(*) FROM domains WHERE subnet_id=? AND status!='released'", (subnet,)).fetchone()[0]
                    if limit is not None and used >= int(limit):
                        raise PublicationRootError("site_studio_domain_limit_exhausted", "The subnet's custom-domain allowance is full.")
                    pending = {"status": "reconciliation_required", "hostname": hostname}
                    db.execute("INSERT INTO domains VALUES(?,?,?,?,?)", (hostname, subnet, site_id, "requesting", json.dumps(pending)))
                if action == "activate":
                    occupied = db.execute("SELECT operation_id FROM occupancy WHERE site_id=? AND environment='prod' AND status='published'", (site_id,)).fetchone()
                    if not occupied:
                        raise PublicationRootError("publication_required", "Domain activation requires a published production site.")
                    if request.get("confirmed") is not True:
                        raise PublicationRootError("confirmation_required", "Domain activation requires confirmation.", 400)
                    db.execute("UPDATE domains SET status='activating' WHERE hostname=?", (hostname,))
            if action == "request":
                result = self.delivery.request_domain(hostname)
            elif action == "status":
                result = self.delivery.status_domain(hostname)
                # DNS/TLS readiness does not prove that a timed-out routing write
                # completed. Keep the reservation and require operator readback.
                if current["status"] in {"requesting", "activating"}:
                    result = {**result, "status": "reconciliation_required"}
            else:
                provider_plan = self._path(occupied["operation_id"])/"provider/cloudflare/publication-plan.json"
                result = self.delivery.activate_domain_for_plan(hostname, site["platform_path"], provider_plan)
            with self._db() as db:
                retained_status = result.get("status", "pending")
                if action == "status" and current["status"] in {"requesting", "activating"}:
                    retained_status = current["status"]
                elif action != "activate" and retained_status == "active" and (not current or current["status"] != "active"):
                    retained_status = "ready"
                db.execute("UPDATE domains SET status=?,body=? WHERE hostname=?", (retained_status, json.dumps(result), hostname))
            return result

    @staticmethod
    def _view(operation: Mapping[str, Any]) -> dict[str, Any]:
        return {k: operation[k] for k in ("operation_id", "site_id", "platform_path", "environment", "status", "plan_digest", "plan", "receipt") if k in operation}

    def adopt(self, *, subnet: str, site_id: str, platform_path: str, environment: str,
              operation_id: str, expected_pointer_digest: str, verify: Callable[[], Mapping[str, Any]],
              authoring: Mapping[str, Any] | None = None) -> None:
        """Attended migration: live pointer readback, never a first-caller claim."""
        for value in (subnet, site_id, operation_id):
            identifier(value)
        if not PLATFORM.fullmatch(platform_path) or environment not in {"dev", "prod"}:
            raise ValueError("invalid migration binding")
        observed = verify()
        if (not HASH.fullmatch(expected_pointer_digest) or observed.get("pointer_digest") != expected_pointer_digest
                or observed.get("site_id") != site_id):
            raise ValueError("live publication does not match the migration receipt")
        with self._db() as db:
            existing = db.execute("SELECT subnet_id,platform_path FROM sites WHERE site_id=?", (site_id,)).fetchone()
            if existing and tuple(existing) != (subnet, platform_path):
                raise ValueError("migration conflicts with retained ownership")
            db.execute("INSERT INTO sites VALUES(?,?,?,1) ON CONFLICT(site_id) DO UPDATE SET verified=1", (site_id, subnet, platform_path))
            db.execute("INSERT INTO occupancy VALUES(?,?,?,?,?) ON CONFLICT(site_id,environment) DO NOTHING", (site_id, environment, subnet, operation_id, "published"))
            origin = "https://p.adaos.io" if environment == "prod" else "https://dev.p.adaos.io"
            operation = {"operation_id": operation_id, "site_id": site_id, "subnet_id": subnet, "environment": environment,
                "platform_path": platform_path, "status": "published", "authoring": dict(authoring or {}),
                "plan": {"pointer": dict(observed), "public_url": origin+"/"+platform_path+"/"}}
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?) ON CONFLICT(operation_id) DO NOTHING",
                (operation_id, subnet, site_id, digest(operation), "published", json.dumps(operation)))
