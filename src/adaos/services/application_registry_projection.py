from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from adaos.services.artifact_pipeline.storage import mutation_lock


APPLICATION_REGISTRY_PROJECTION_SCHEMA_VERSION = 1
APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION = "apreg.local/1"
DEVELOPMENT_PROJECT_SOURCE_KIND = "dev_project"
DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND = "dev_project_manifest"
APPLICATION_STORE_SOURCE_KIND = "application_store"
APPLICATION_STORE_RECORD_SOURCE_KIND = "application_store_record"


class ApplicationRegistryProjectionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _load_json(value: str | bytes | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _digest_bytes(_json(value).encode("utf-8"))


def _source_identity(source_kind: str, source_path: Path) -> str:
    token = f"{source_kind}:{source_path.expanduser().resolve()}"
    return "source." + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _operation_id(action: str) -> str:
    return f"apreg.{action}.{uuid.uuid4().hex}"


def _search_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).casefold()


def _like_token(value: str) -> str:
    return (
        "%"
        + str(value or "")
        .casefold()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        + "%"
    )


def _fts_query(value: str) -> str:
    tokens = re.findall(r"[\w.-]+", str(value or "").casefold(), flags=re.UNICODE)
    return " ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens)


def _bounded_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]


def _record_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return deepcopy(dict(to_dict()))
    return None


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    observed = getattr(value, name, default)
    if observed is not default:
        return observed
    payload = _record_payload(value)
    if payload is not None:
        return payload.get(name, default)
    return default


def _project_manifest_digest(project: Mapping[str, Any]) -> str:
    return _digest(dict(project))


def _project_row(project: Mapping[str, Any], *, source_path: Path) -> dict[str, Any]:
    payload = deepcopy(dict(project))
    project_id = str(payload.get("id") or "").strip()
    catalog = payload.get("catalog") if isinstance(payload.get("catalog"), Mapping) else {}
    components = payload.get("components") if isinstance(payload.get("components"), Mapping) else {}
    owned = [dict(item) for item in components.get("owned") or [] if isinstance(item, Mapping)]
    primary = next(
        (item for item in owned if str(item.get("role") or "") == "primary"),
        owned[0] if owned else {},
    )
    manifest_digest = _project_manifest_digest(payload)
    return {
        **payload,
        "id": project_id,
        "ref": f"project:{project_id}",
        "title": str(catalog.get("title") or project_id),
        "description": str(catalog.get("description") or ""),
        "profiles": list(payload.get("profiles") or []),
        "categories": list(catalog.get("categories") or []),
        "tags": list(catalog.get("tags") or []),
        "publication": dict(payload.get("publication") or {}),
        "install": dict(payload.get("install") or {}),
        "stage": str((payload.get("publication") or {}).get("stage") or "alpha"),
        "visibility": str((payload.get("publication") or {}).get("visibility") or "unlisted"),
        "default_install": bool((payload.get("install") or {}).get("default") is True),
        "primary_ref": str(primary.get("ref") or "").strip() or None,
        "source_path": str(source_path.expanduser().resolve().parent),
        "manifest_digest": manifest_digest,
        "source_kind": DEVELOPMENT_PROJECT_SOURCE_KIND,
        "validation_status": "valid",
    }


class ApplicationRegistryProjection:
    """Private rebuildable Application registry projection.

    The projection is a query surface.  DEV project manifests, ApplicationStore
    records, packages, grants, and skill-owned stores remain authoritative.
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self.state_dir / "applications"

    @property
    def db_path(self) -> Path:
        return self.root / "registry.sqlite3"

    @property
    def lock_path(self) -> Path:
        return self.root / ".registry-projection.lock"

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema(con)
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS projection_epoch(
                epoch_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                validator_version TEXT NOT NULL,
                runtime_instance_id TEXT,
                state TEXT NOT NULL,
                shutdown_kind TEXT,
                shutdown_request_id TEXT,
                seal_status TEXT,
                trusted INTEGER NOT NULL DEFAULT 0,
                source_watermark TEXT,
                projection_digest TEXT,
                started_at TEXT NOT NULL,
                sealed_at TEXT,
                completed_at TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS projection_epoch_latest
                ON projection_epoch(started_at DESC, epoch_id DESC);

            CREATE TABLE IF NOT EXISTS projection_source(
                source_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_path TEXT NOT NULL,
                mtime_ns INTEGER,
                ctime_ns INTEGER,
                size_bytes INTEGER,
                content_digest TEXT,
                schema_digest TEXT,
                parse_status TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                validated_at TEXT,
                payload_digest TEXT,
                error TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS projection_source_ref
                ON projection_source(source_kind, source_ref);
            CREATE INDEX IF NOT EXISTS projection_source_validation
                ON projection_source(source_kind, validation_status);

            CREATE TABLE IF NOT EXISTS application_index(
                source_kind TEXT NOT NULL,
                application_id TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_id TEXT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                version TEXT,
                publisher_ref TEXT,
                visibility TEXT,
                lifecycle TEXT,
                installed INTEGER NOT NULL DEFAULT 0,
                local_beta INTEGER NOT NULL DEFAULT 0,
                release_digest TEXT,
                stable_release_digest TEXT,
                prerelease_release_digest TEXT,
                source_path TEXT,
                search_text TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(source_kind, application_id)
            );
            CREATE INDEX IF NOT EXISTS application_index_search
                ON application_index(source_kind, validation_status, title, application_id);
            CREATE INDEX IF NOT EXISTS application_index_installed
                ON application_index(source_kind, installed, application_id);
            CREATE INDEX IF NOT EXISTS application_index_release
                ON application_index(source_kind, release_digest);

            CREATE TABLE IF NOT EXISTS application_component_index(
                source_kind TEXT NOT NULL,
                component_ref TEXT NOT NULL,
                application_id TEXT NOT NULL,
                component_role TEXT,
                lifecycle TEXT,
                exposure TEXT,
                package_digest TEXT,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(source_kind, component_ref, application_id)
            );
            CREATE INDEX IF NOT EXISTS application_component_owner
                ON application_component_index(source_kind, component_ref);

            CREATE TABLE IF NOT EXISTS application_entrypoint_index(
                source_kind TEXT NOT NULL,
                application_id TEXT NOT NULL,
                entrypoint_id TEXT NOT NULL,
                presentation_ref TEXT,
                is_default INTEGER NOT NULL DEFAULT 0,
                supported_surfaces_json TEXT NOT NULL,
                binding_summary_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(source_kind, application_id, entrypoint_id)
            );

            CREATE TABLE IF NOT EXISTS application_permission_profile_index(
                source_kind TEXT NOT NULL,
                application_id TEXT NOT NULL,
                release_digest TEXT,
                release_key TEXT NOT NULL DEFAULT '',
                permission_profile_digest TEXT,
                required_permissions_json TEXT NOT NULL,
                optional_permissions_json TEXT NOT NULL,
                flat_permissions_json TEXT NOT NULL,
                declaration_summary_json TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(source_kind, application_id, release_key)
            );

            CREATE TABLE IF NOT EXISTS application_role_index(
                source_kind TEXT NOT NULL,
                application_id TEXT NOT NULL,
                release_digest TEXT,
                release_key TEXT NOT NULL DEFAULT '',
                role_id TEXT NOT NULL,
                title TEXT,
                assignable INTEGER NOT NULL DEFAULT 0,
                default_rule TEXT,
                sensitive INTEGER NOT NULL DEFAULT 0,
                capabilities_json TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(source_kind, application_id, release_key, role_id)
            );

            CREATE TABLE IF NOT EXISTS validation_report(
                report_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                validator_version TEXT NOT NULL,
                status TEXT NOT NULL,
                errors_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                checked_digest TEXT,
                checked_at TEXT NOT NULL,
                next_retry_at TEXT,
                last_known_good_digest TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS validation_report_source
                ON validation_report(source_id, checked_at DESC);

            CREATE TABLE IF NOT EXISTS federated_application_fact(
                fact_id TEXT PRIMARY KEY,
                fact_family TEXT NOT NULL,
                origin_ref TEXT NOT NULL,
                authority_ref TEXT NOT NULL,
                application_id TEXT,
                release_digest TEXT,
                observed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                trust_status TEXT NOT NULL,
                replay_key TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS federated_application_fact_freshness
                ON federated_application_fact(fact_family, application_id, trust_status, expires_at);

            CREATE TABLE IF NOT EXISTS projection_journal(
                operation_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                source_path TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                source_watermark TEXT,
                projection_digest TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS projection_journal_latest
                ON projection_journal(action, source_path, updated_at DESC);
            """
        )
        self._ensure_fts(con)
        con.commit()

    def _ensure_fts(self, con: sqlite3.Connection) -> None:
        try:
            con.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS application_index_fts
                USING fts5(application_id, title, description, search_text)
                """
            )
            self._set_meta(con, "fts5_available", True)
        except sqlite3.OperationalError:
            self._set_meta(con, "fts5_available", False)

    def _set_meta(self, con: sqlite3.Connection, key: str, value: Any) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_meta(
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO projection_meta(key, value_json, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (key, _json(value), _now()),
        )

    def _get_meta(self, con: sqlite3.Connection, key: str, default: Any = None) -> Any:
        try:
            row = con.execute("SELECT value_json FROM projection_meta WHERE key=?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return default
        return _load_json(row["value_json"], default) if row else default

    def start_epoch(
        self,
        *,
        runtime_instance_id: str | None = None,
        validator_version: str = APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION,
    ) -> dict[str, Any]:
        epoch_id = "apreg.epoch." + uuid.uuid4().hex
        payload = {
            "schema": "adaos.application.registry_projection_epoch.v1",
            "epoch_id": epoch_id,
            "schema_version": APPLICATION_REGISTRY_PROJECTION_SCHEMA_VERSION,
            "validator_version": validator_version,
            "runtime_instance_id": str(runtime_instance_id or "").strip() or None,
            "state": "open",
            "started_at": _now(),
        }
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                con.execute(
                    """
                    INSERT INTO projection_epoch(
                        epoch_id, schema_version, validator_version, runtime_instance_id,
                        state, trusted, started_at, payload_json
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        epoch_id,
                        APPLICATION_REGISTRY_PROJECTION_SCHEMA_VERSION,
                        validator_version,
                        payload["runtime_instance_id"],
                        "open",
                        0,
                        payload["started_at"],
                        _json_pretty(payload),
                    ),
                )
                con.commit()
        return payload

    def latest_epoch(self) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT payload_json FROM projection_epoch ORDER BY started_at DESC, epoch_id DESC LIMIT 1"
            ).fetchone()
        return _load_json(row["payload_json"], {}) if row else None

    def snapshot_trust_state(
        self,
        *,
        validator_version: str = APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if not self.db_path.exists():
            return {
                "trusted_snapshot": False,
                "reason": "registry_projection_absent",
                "projection_status": "untrusted_rebuild",
                "sqlite_integrity_ms": 0.0,
            }
        with self._connect() as con:
            row = con.execute(
                """
                SELECT *
                FROM projection_epoch
                ORDER BY started_at DESC, epoch_id DESC
                LIMIT 1
                """
            ).fetchone()
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        integrity_ms = round((time.perf_counter() - started) * 1000.0, 3)
        if str(integrity) != "ok":
            return {
                "trusted_snapshot": False,
                "reason": "sqlite_integrity_failed",
                "projection_status": "invalid",
                "sqlite_integrity_ms": integrity_ms,
                "integrity": str(integrity),
            }
        if row is None:
            return {
                "trusted_snapshot": False,
                "reason": "projection_epoch_absent",
                "projection_status": "untrusted_rebuild",
                "sqlite_integrity_ms": integrity_ms,
            }
        reasons = []
        if int(row["schema_version"]) != APPLICATION_REGISTRY_PROJECTION_SCHEMA_VERSION:
            reasons.append("schema_incompatible")
        if str(row["validator_version"]) != str(validator_version):
            reasons.append("validator_incompatible")
        if str(row["state"]) != "closed":
            reasons.append(f"epoch_{row['state']}")
        if str(row["shutdown_kind"] or "") != "graceful":
            reasons.append("shutdown_not_graceful")
        if str(row["seal_status"] or "") != "complete":
            reasons.append("seal_incomplete")
        if not int(row["trusted"] or 0):
            reasons.append("seal_not_trusted")
        trusted = not reasons
        return {
            "trusted_snapshot": trusted,
            "reason": "trusted" if trusted else reasons[0],
            "projection_status": "ready" if trusted else "untrusted_rebuild",
            "epoch_id": row["epoch_id"],
            "runtime_instance_id": row["runtime_instance_id"],
            "shutdown_request_id": row["shutdown_request_id"],
            "source_watermark": row["source_watermark"],
            "projection_digest": row["projection_digest"],
            "sqlite_integrity_ms": integrity_ms,
        }

    def seal_epoch(
        self,
        epoch_id: str,
        *,
        shutdown_request_id: str | None = None,
        shutdown_reason: str | None = None,
        shutdown_scope: str | None = None,
        validator_version: str = APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION,
    ) -> dict[str, Any]:
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                row = con.execute(
                    "SELECT * FROM projection_epoch WHERE epoch_id=?",
                    (str(epoch_id or "").strip(),),
                ).fetchone()
                if row is None:
                    raise ApplicationRegistryProjectionError(f"projection epoch not found: {epoch_id}")
                source_watermark = self._source_watermark(con, DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND)
                projection_digest = self._projection_digest(con)
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                checkpoint = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                trusted = str(integrity) == "ok"
                timestamp = _now()
                payload = {
                    **_load_json(row["payload_json"], {}),
                    "state": "closed",
                    "shutdown_kind": "graceful",
                    "shutdown_request_id": str(shutdown_request_id or "").strip() or None,
                    "shutdown_reason": str(shutdown_reason or "").strip() or None,
                    "shutdown_scope": str(shutdown_scope or "").strip() or None,
                    "seal_status": "complete" if trusted else "integrity_failed",
                    "trusted": trusted,
                    "source_watermark": source_watermark,
                    "projection_digest": projection_digest,
                    "integrity": str(integrity),
                    "checkpoint": list(checkpoint) if checkpoint is not None else None,
                    "sealed_at": timestamp,
                    "completed_at": timestamp,
                }
                con.execute(
                    """
                    UPDATE projection_epoch
                    SET state=?, shutdown_kind=?, shutdown_request_id=?, seal_status=?,
                        trusted=?, source_watermark=?, projection_digest=?,
                        sealed_at=?, completed_at=?, payload_json=?
                    WHERE epoch_id=?
                    """,
                    (
                        "closed",
                        "graceful",
                        payload["shutdown_request_id"],
                        payload["seal_status"],
                        1 if trusted else 0,
                        source_watermark,
                        projection_digest,
                        timestamp,
                        timestamp,
                        _json_pretty(payload),
                        row["epoch_id"],
                    ),
                )
                con.commit()
        return payload

    def development_projects_ready(self, projects_root: Path) -> bool:
        root = str(Path(projects_root).expanduser().resolve())
        with self._connect() as con:
            row = con.execute(
                """
                SELECT 1 FROM application_index
                WHERE source_kind=? AND source_path LIKE ?
                LIMIT 1
                """,
                (DEVELOPMENT_PROJECT_SOURCE_KIND, root + "%"),
            ).fetchone()
            if row is not None:
                return True
            journal = con.execute(
                """
                SELECT status
                FROM projection_journal
                WHERE action='rebuild_development_projects' AND source_path=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (root,),
            ).fetchone()
        return bool(journal and journal["status"] == "completed")

    def rebuild_development_projects(
        self,
        projects_root: Path,
        *,
        parser: Callable[[bytes, bytes], Mapping[str, Any]],
        schema_bytes: bytes,
        operation_id: str | None = None,
        cancel: Callable[[], bool] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
        validator_version: str = APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION,
    ) -> dict[str, Any]:
        root = Path(projects_root).expanduser().resolve()
        op_id = str(operation_id or _operation_id("rebuild")).strip()
        started_at = _now()
        schema_digest = _digest_bytes(bytes(schema_bytes))
        self._record_journal(
            op_id,
            action="rebuild_development_projects",
            status="running",
            source_path=str(root),
            started_at=started_at,
            payload={"root": str(root), "schema_digest": schema_digest},
        )
        rows: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        scanned = 0
        if root.is_dir():
            manifests = sorted(root.glob("*/project.yaml"), key=lambda item: item.parent.name.casefold())
        else:
            manifests = []
        for manifest_path in manifests:
            if cancel is not None and cancel():
                return self._finish_rebuild_journal(
                    op_id,
                    status="cancelled",
                    started_at=started_at,
                    source_path=str(root),
                    source_rows=source_rows,
                    rows=rows,
                    validation_rows=validation_rows,
                    payload={"root": str(root), "scanned": scanned, "indexed": len(rows)},
                )
            scanned += 1
            source_id = _source_identity(DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND, manifest_path)
            observed_at = _now()
            try:
                stat = manifest_path.stat()
                raw = manifest_path.read_bytes()
                source_digest = _digest_bytes(raw)
                project = dict(parser(raw, schema_bytes))
                project_id = str(project.get("id") or manifest_path.parent.name).strip()
                row_payload = _project_row(project, source_path=manifest_path)
                payload_digest = _digest(row_payload)
                source_payload = {
                    "schema": "adaos.application.registry_projection_source.v1",
                    "source_id": source_id,
                    "source_kind": DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND,
                    "source_ref": f"project:{project_id}",
                    "source_path": str(manifest_path),
                    "mtime_ns": int(stat.st_mtime_ns),
                    "ctime_ns": int(stat.st_ctime_ns),
                    "size_bytes": int(stat.st_size),
                    "content_digest": source_digest,
                    "schema_digest": schema_digest,
                    "parse_status": "parsed",
                    "validation_status": "valid",
                    "observed_at": observed_at,
                    "validated_at": observed_at,
                    "payload_digest": payload_digest,
                    "error": None,
                }
                rows.append(
                    {
                        "project": project,
                        "payload": row_payload,
                        "source_id": source_id,
                        "source_digest": source_digest,
                        "payload_digest": payload_digest,
                    }
                )
                validation_rows.append(
                    self._validation_report(
                        source_id=source_id,
                        validator_version=validator_version,
                        status="valid",
                        checked_digest=source_digest,
                        checked_at=observed_at,
                        last_known_good_digest=source_digest,
                    )
                )
            except Exception as exc:
                error = _bounded_error(exc)
                source_ref = f"project:{manifest_path.parent.name}"
                source_digest = None
                try:
                    stat = manifest_path.stat()
                    raw = manifest_path.read_bytes()
                    source_digest = _digest_bytes(raw)
                    size_bytes = int(stat.st_size)
                    mtime_ns = int(stat.st_mtime_ns)
                    ctime_ns = int(stat.st_ctime_ns)
                except OSError:
                    size_bytes = None
                    mtime_ns = None
                    ctime_ns = None
                source_payload = {
                    "schema": "adaos.application.registry_projection_source.v1",
                    "source_id": source_id,
                    "source_kind": DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND,
                    "source_ref": source_ref,
                    "source_path": str(manifest_path),
                    "mtime_ns": mtime_ns,
                    "ctime_ns": ctime_ns,
                    "size_bytes": size_bytes,
                    "content_digest": source_digest,
                    "schema_digest": schema_digest,
                    "parse_status": "invalid",
                    "validation_status": "invalid",
                    "observed_at": observed_at,
                    "validated_at": observed_at,
                    "payload_digest": None,
                    "error": error,
                }
                validation_rows.append(
                    self._validation_report(
                        source_id=source_id,
                        validator_version=validator_version,
                        status="invalid",
                        checked_digest=source_digest,
                        checked_at=observed_at,
                        errors=[error],
                    )
                )
            source_rows.append(source_payload)
            if progress is not None:
                progress({"operation_id": op_id, "scanned": scanned, "indexed": len(rows)})
        return self._finish_rebuild_journal(
            op_id,
            status="completed",
            started_at=started_at,
            source_path=str(root),
            source_rows=source_rows,
            rows=rows,
            validation_rows=validation_rows,
            payload={"root": str(root), "scanned": scanned, "indexed": len(rows)},
        )

    def upsert_development_project(
        self,
        project: Mapping[str, Any],
        *,
        manifest_path: Path,
        schema_bytes: bytes,
        validator_version: str = APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION,
    ) -> dict[str, Any]:
        path = Path(manifest_path).expanduser().resolve()
        stat = path.stat()
        raw = path.read_bytes()
        source_digest = _digest_bytes(raw)
        schema_digest = _digest_bytes(bytes(schema_bytes))
        payload = _project_row(project, source_path=path)
        payload_digest = _digest(payload)
        project_id = str(payload["id"])
        source_id = _source_identity(DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND, path)
        source_payload = {
            "schema": "adaos.application.registry_projection_source.v1",
            "source_id": source_id,
            "source_kind": DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND,
            "source_ref": f"project:{project_id}",
            "source_path": str(path),
            "mtime_ns": int(stat.st_mtime_ns),
            "ctime_ns": int(stat.st_ctime_ns),
            "size_bytes": int(stat.st_size),
            "content_digest": source_digest,
            "schema_digest": schema_digest,
            "parse_status": "parsed",
            "validation_status": "valid",
            "observed_at": _now(),
            "validated_at": _now(),
            "payload_digest": payload_digest,
            "error": None,
        }
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                self._delete_development_project(con, project_id)
                self._upsert_source(con, source_payload)
                self._insert_project(con, payload, source_id=source_id, payload_digest=payload_digest)
                self._replace_project_components(con, project, payload_digest=payload_digest)
                self._replace_project_entrypoints(con, project, payload_digest=payload_digest)
                self._insert_validation_report(
                    con,
                    self._validation_report(
                        source_id=source_id,
                        validator_version=validator_version,
                        status="valid",
                        checked_digest=source_digest,
                        checked_at=_now(),
                        last_known_good_digest=source_digest,
                    ),
                )
                con.commit()
        return payload

    def remove_development_project(self, project_id: str) -> None:
        token = str(project_id or "").strip()
        if not token:
            return
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                self._delete_development_project(con, token)
                con.execute(
                    """
                    DELETE FROM projection_source
                    WHERE source_kind=? AND source_ref=?
                    """,
                    (DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND, f"project:{token}"),
                )
                con.commit()

    def rebuild_application_store(
        self,
        store: Any,
        *,
        operation_id: str | None = None,
        validator_version: str = APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION,
    ) -> dict[str, Any]:
        op_id = str(operation_id or _operation_id("store_rebuild")).strip()
        started_at = _now()
        source_root = str(Path(getattr(store, "root", self.root)).expanduser().resolve())
        applications = list(store.list_applications())
        installations = {
            str(_field(item, "application_id") or ""): item
            for item in store.list_installations()
            if str(_field(item, "status") or "") != "removed"
        }
        runtime_selections: dict[str, list[Any]] = {}
        for selection in store.list_runtime_selections():
            runtime_selections.setdefault(str(_field(selection, "application_id") or ""), []).append(selection)
        rows: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        for application in applications:
            application_payload = _record_payload(application)
            if application_payload is None:
                continue
            application_id = str(application_payload.get("application_id") or "").strip()
            if not application_id:
                continue
            installation = installations.get(application_id)
            installation_payload = _record_payload(installation)
            selections = [
                payload
                for payload in (_record_payload(item) for item in runtime_selections.get(application_id, []))
                if payload is not None
            ]
            try:
                channels_payload = store.get_channels(application_id)
                channels = dict(channels_payload.get("channels") or {})
            except Exception:
                channels = {}
            display = application_payload.get("display") if isinstance(application_payload.get("display"), Mapping) else {}
            title = str(display.get("title") or application_id)
            description = str(display.get("summary") or "")
            release_digest = (
                str(installation_payload.get("installed_release_digest") or "").strip()
                if installation_payload is not None
                else ""
            )
            payload = {
                "schema": "adaos.application.registry_application_summary.v1",
                "source_kind": APPLICATION_STORE_SOURCE_KIND,
                "application_id": application_id,
                "source_ref": f"application:{application_id}",
                "legacy_project_id": application_payload.get("legacy_project_id"),
                "publisher_ref": application_payload.get("publisher_ref"),
                "title": title,
                "description": description,
                "visibility": application_payload.get("visibility"),
                "lifecycle": application_payload.get("lifecycle"),
                "installed": installation_payload is not None,
                "local_beta_active": any(str(item.get("source") or "") == "local_trial" for item in selections),
                "release_digest": release_digest or None,
                "channels": {
                    "stable": channels.get("stable"),
                    "prerelease": channels.get("prerelease"),
                },
                "application": application_payload,
                "installation": installation_payload,
                "runtime_selections": selections,
            }
            payload_digest = _digest(payload)
            source_digest = _digest(
                {
                    "application": application_payload,
                    "installation": installation_payload,
                    "channels": payload["channels"],
                    "runtime_selections": selections,
                }
            )
            source_path = Path(source_root) / f"application-{application_id}.json"
            source_id = _source_identity(APPLICATION_STORE_RECORD_SOURCE_KIND, source_path)
            timestamp = _now()
            source_rows.append(
                {
                    "schema": "adaos.application.registry_projection_source.v1",
                    "source_id": source_id,
                    "source_kind": APPLICATION_STORE_RECORD_SOURCE_KIND,
                    "source_ref": f"application:{application_id}",
                    "source_path": str(source_path),
                    "mtime_ns": None,
                    "ctime_ns": None,
                    "size_bytes": None,
                    "content_digest": source_digest,
                    "schema_digest": None,
                    "parse_status": "parsed",
                    "validation_status": "valid",
                    "observed_at": timestamp,
                    "validated_at": timestamp,
                    "payload_digest": payload_digest,
                    "error": None,
                }
            )
            validation_rows.append(
                self._validation_report(
                    source_id=source_id,
                    validator_version=validator_version,
                    status="valid",
                    checked_digest=source_digest,
                    checked_at=timestamp,
                    last_known_good_digest=source_digest,
                )
            )
            rows.append(
                {
                    "application": application_payload,
                    "installation": installation_payload,
                    "payload": payload,
                    "payload_digest": payload_digest,
                    "source_id": source_id,
                }
            )
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                with self._ignore_missing_fts(con):
                    con.execute(
                        """
                        DELETE FROM application_index_fts
                        WHERE rowid IN (
                            SELECT rowid FROM application_index WHERE source_kind=?
                        )
                        """,
                        (APPLICATION_STORE_SOURCE_KIND,),
                    )
                for table in (
                    "application_component_index",
                    "application_entrypoint_index",
                    "application_index",
                ):
                    con.execute(f"DELETE FROM {table} WHERE source_kind=?", (APPLICATION_STORE_SOURCE_KIND,))
                con.execute(
                    "DELETE FROM projection_source WHERE source_kind=?",
                    (APPLICATION_STORE_RECORD_SOURCE_KIND,),
                )
                for source in source_rows:
                    self._upsert_source(con, source)
                for row in rows:
                    self._insert_application_store_summary(
                        con,
                        row["payload"],
                        source_id=str(row["source_id"]),
                        payload_digest=str(row["payload_digest"]),
                    )
                    self._replace_application_store_components(
                        con,
                        row["payload"],
                        payload_digest=str(row["payload_digest"]),
                    )
                    self._replace_application_store_entrypoints(
                        con,
                        row["application"],
                        payload_digest=str(row["payload_digest"]),
                    )
                for report in validation_rows:
                    self._insert_validation_report(con, report)
                source_watermark = self._source_watermark(con, APPLICATION_STORE_RECORD_SOURCE_KIND)
                projection_digest = self._projection_digest(con)
                completed_at = _now()
                record = {
                    "schema": "adaos.application.registry_projection_journal.v1",
                    "operation_id": op_id,
                    "action": "rebuild_application_store",
                    "status": "completed",
                    "source_path": source_root,
                    "started_at": started_at,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "source_watermark": source_watermark,
                    "projection_digest": projection_digest,
                    "scanned": len(applications),
                    "indexed": len(rows),
                    "invalid": 0,
                }
                con.execute(
                    """
                    INSERT INTO projection_journal(
                        operation_id, action, status, source_path, started_at,
                        updated_at, completed_at, source_watermark,
                        projection_digest, payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(operation_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        completed_at=excluded.completed_at,
                        source_watermark=excluded.source_watermark,
                        projection_digest=excluded.projection_digest,
                        payload_json=excluded.payload_json
                    """,
                    (
                        op_id,
                        "rebuild_application_store",
                        "completed",
                        source_root,
                        started_at,
                        completed_at,
                        completed_at,
                        source_watermark,
                        projection_digest,
                        _json_pretty(record),
                    ),
                )
                con.commit()
        return record

    def list_development_projects(
        self,
        *,
        profile: str | None = None,
        query: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 5000))
        profile_token = str(profile or "").strip()
        query_token = str(query or "").strip()
        params: list[Any] = [DEVELOPMENT_PROJECT_SOURCE_KIND, "valid"]
        where = ["source_kind=?", "validation_status=?"]
        order = "application_id COLLATE NOCASE"
        with self._connect() as con:
            fts_available = bool(self._get_meta(con, "fts5_available", False))
            if query_token and fts_available:
                fts = _fts_query(query_token)
                if fts:
                    where.append(
                        "(rowid IN (SELECT rowid FROM application_index_fts WHERE application_index_fts MATCH ?) OR search_text LIKE ? ESCAPE '\\')"
                    )
                    params.append(fts)
                    params.append(_like_token(query_token))
            if query_token and (not fts_available or not _fts_query(query_token)):
                where.append("search_text LIKE ? ESCAPE '\\'")
                params.append(_like_token(query_token))
            params.append(5000 if profile_token else maximum)
            rows = con.execute(
                f"""
                SELECT payload_json
                FROM application_index
                WHERE {' AND '.join(where)}
                ORDER BY {order}
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        result = []
        for row in rows:
            payload = _load_json(row["payload_json"], {})
            if profile_token and profile_token not in set(payload.get("profiles") or []):
                continue
            result.append(deepcopy(payload))
            if len(result) >= maximum:
                break
        return result

    def get_development_project(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT payload_json
                FROM application_index
                WHERE source_kind=? AND application_id=? AND validation_status='valid'
                """,
                (DEVELOPMENT_PROJECT_SOURCE_KIND, str(project_id or "").strip()),
            ).fetchone()
        return deepcopy(_load_json(row["payload_json"], {})) if row else None

    def project_for_component(self, component_ref: str) -> list[dict[str, Any]]:
        token = str(component_ref or "").strip()
        if not token:
            return []
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT ai.payload_json
                FROM application_component_index ac
                JOIN application_index ai
                  ON ai.source_kind=ac.source_kind AND ai.application_id=ac.application_id
                WHERE ac.source_kind=? AND ac.component_ref=? AND ai.validation_status='valid'
                ORDER BY ai.application_id COLLATE NOCASE
                """,
                (DEVELOPMENT_PROJECT_SOURCE_KIND, token),
            ).fetchall()
        return [deepcopy(_load_json(row["payload_json"], {})) for row in rows]

    def applications_for_component(self, component_ref: str, *, limit: int = 500) -> list[dict[str, Any]]:
        token = str(component_ref or "").strip()
        if not token:
            return []
        maximum = max(1, min(int(limit), 5000))
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT ai.payload_json
                FROM application_component_index ac
                JOIN application_index ai
                  ON ai.source_kind=ac.source_kind AND ai.application_id=ac.application_id
                WHERE ac.source_kind=? AND ac.component_ref=? AND ai.validation_status='valid'
                ORDER BY ai.title COLLATE NOCASE, ai.application_id COLLATE NOCASE
                LIMIT ?
                """,
                (APPLICATION_STORE_SOURCE_KIND, token, maximum),
            ).fetchall()
        return [deepcopy(_load_json(row["payload_json"], {})) for row in rows]

    def installed_summaries(self, *, limit: int = 500) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 5000))
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT payload_json
                FROM application_index
                WHERE source_kind=? AND installed=1 AND validation_status='valid'
                ORDER BY title COLLATE NOCASE, application_id COLLATE NOCASE
                LIMIT ?
                """,
                (APPLICATION_STORE_SOURCE_KIND, maximum),
            ).fetchall()
        return [deepcopy(_load_json(row["payload_json"], {})) for row in rows]

    def release_channel_pointers(self, application_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT application_id, release_digest, stable_release_digest,
                       prerelease_release_digest, payload_digest
                FROM application_index
                WHERE source_kind=? AND application_id=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (APPLICATION_STORE_SOURCE_KIND, str(application_id or "").strip()),
            ).fetchone()
        if row is None:
            return None
        return {
            "application_id": row["application_id"],
            "release_digest": row["release_digest"],
            "channels": {
                "stable": row["stable_release_digest"],
                "prerelease": row["prerelease_release_digest"],
            },
            "payload_digest": row["payload_digest"],
        }

    def validation_counts(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT validation_status, COUNT(*) AS count
                FROM projection_source
                GROUP BY validation_status
                ORDER BY validation_status
                """
            ).fetchall()
        return {str(row["validation_status"]): int(row["count"]) for row in rows}

    def diagnostics(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self._connect() as con:
            source_counts = con.execute(
                """
                SELECT source_kind, validation_status, COUNT(*) AS count
                FROM projection_source
                GROUP BY source_kind, validation_status
                ORDER BY source_kind, validation_status
                """
            ).fetchall()
            app_counts = con.execute(
                """
                SELECT source_kind, validation_status, COUNT(*) AS count
                FROM application_index
                GROUP BY source_kind, validation_status
                ORDER BY source_kind, validation_status
                """
            ).fetchall()
            latest_journal = con.execute(
                """
                SELECT payload_json
                FROM projection_journal
                ORDER BY updated_at DESC, operation_id DESC
                LIMIT 5
                """
            ).fetchall()
            trust = self.snapshot_trust_state()
        return {
            "schema": "adaos.application.registry_projection.diagnostics.v1",
            "db_path": str(self.db_path),
            "schema_version": APPLICATION_REGISTRY_PROJECTION_SCHEMA_VERSION,
            "trusted_snapshot": trust,
            "projection_status": trust.get("projection_status", "warming"),
            "source_counts": [
                {
                    "source_kind": row["source_kind"],
                    "validation_status": row["validation_status"],
                    "count": int(row["count"]),
                }
                for row in source_counts
            ],
            "application_counts": [
                {
                    "source_kind": row["source_kind"],
                    "validation_status": row["validation_status"],
                    "count": int(row["count"]),
                }
                for row in app_counts
            ],
            "recent_operations": [_load_json(row["payload_json"], {}) for row in latest_journal],
            "query_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    def _record_journal(
        self,
        operation_id: str,
        *,
        action: str,
        status: str,
        source_path: str | None,
        started_at: str,
        payload: Mapping[str, Any],
        source_watermark: str | None = None,
        projection_digest: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        timestamp = _now()
        record = {
            "schema": "adaos.application.registry_projection_journal.v1",
            "operation_id": operation_id,
            "action": action,
            "status": status,
            "source_path": source_path,
            "started_at": started_at,
            "updated_at": timestamp,
            "completed_at": completed_at,
            "source_watermark": source_watermark,
            "projection_digest": projection_digest,
            **dict(payload),
        }
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                con.execute(
                    """
                    INSERT INTO projection_journal(
                        operation_id, action, status, source_path, started_at,
                        updated_at, completed_at, source_watermark,
                        projection_digest, payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(operation_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        completed_at=excluded.completed_at,
                        source_watermark=excluded.source_watermark,
                        projection_digest=excluded.projection_digest,
                        payload_json=excluded.payload_json
                    """,
                    (
                        operation_id,
                        action,
                        status,
                        source_path,
                        started_at,
                        timestamp,
                        completed_at,
                        source_watermark,
                        projection_digest,
                        _json_pretty(record),
                    ),
                )
                con.commit()

    def _finish_rebuild_journal(
        self,
        operation_id: str,
        *,
        status: str,
        started_at: str,
        source_path: str,
        source_rows: Sequence[Mapping[str, Any]],
        rows: Sequence[Mapping[str, Any]],
        validation_rows: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        with mutation_lock(self.lock_path):
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                if status == "completed":
                    with self._ignore_missing_fts(con):
                        con.execute(
                            """
                            DELETE FROM application_index_fts
                            WHERE rowid IN (
                                SELECT rowid FROM application_index WHERE source_kind=?
                            )
                            """,
                            (DEVELOPMENT_PROJECT_SOURCE_KIND,),
                        )
                    con.execute(
                        "DELETE FROM application_index WHERE source_kind=?",
                        (DEVELOPMENT_PROJECT_SOURCE_KIND,),
                    )
                    con.execute(
                        "DELETE FROM application_component_index WHERE source_kind=?",
                        (DEVELOPMENT_PROJECT_SOURCE_KIND,),
                    )
                    con.execute(
                        "DELETE FROM application_entrypoint_index WHERE source_kind=?",
                        (DEVELOPMENT_PROJECT_SOURCE_KIND,),
                    )
                    con.execute(
                        "DELETE FROM projection_source WHERE source_kind=?",
                        (DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND,),
                    )
                    for source in source_rows:
                        self._upsert_source(con, source)
                    for row in rows:
                        project = dict(row["project"])
                        self._insert_project(
                            con,
                            row["payload"],
                            source_id=str(row["source_id"]),
                            payload_digest=str(row["payload_digest"]),
                        )
                        self._replace_project_components(
                            con,
                            project,
                            payload_digest=str(row["payload_digest"]),
                        )
                        self._replace_project_entrypoints(
                            con,
                            project,
                            payload_digest=str(row["payload_digest"]),
                        )
                    for report in validation_rows:
                        self._insert_validation_report(con, report)
                source_watermark = self._source_watermark(con, DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND)
                projection_digest = self._projection_digest(con)
                completed_at = _now()
                record = {
                    "schema": "adaos.application.registry_projection_journal.v1",
                    "operation_id": operation_id,
                    "action": "rebuild_development_projects",
                    "status": status,
                    "source_path": source_path,
                    "started_at": started_at,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "source_watermark": source_watermark,
                    "projection_digest": projection_digest,
                    **dict(payload),
                    "invalid": sum(1 for item in source_rows if item.get("validation_status") == "invalid"),
                }
                con.execute(
                    """
                    INSERT INTO projection_journal(
                        operation_id, action, status, source_path, started_at,
                        updated_at, completed_at, source_watermark,
                        projection_digest, payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(operation_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        completed_at=excluded.completed_at,
                        source_watermark=excluded.source_watermark,
                        projection_digest=excluded.projection_digest,
                        payload_json=excluded.payload_json
                    """,
                    (
                        operation_id,
                        "rebuild_development_projects",
                        status,
                        source_path,
                        started_at,
                        completed_at,
                        completed_at,
                        source_watermark,
                        projection_digest,
                        _json_pretty(record),
                    ),
                )
                con.commit()
        return record

    @staticmethod
    def _validation_report(
        *,
        source_id: str,
        validator_version: str,
        status: str,
        checked_digest: str | None,
        checked_at: str,
        errors: Sequence[str] = (),
        warnings: Sequence[str] = (),
        last_known_good_digest: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema": "adaos.application.registry_validation_report.v1",
            "source_id": source_id,
            "validator_version": validator_version,
            "status": status,
            "errors": list(errors),
            "warnings": list(warnings),
            "checked_digest": checked_digest,
            "checked_at": checked_at,
            "next_retry_at": None,
            "last_known_good_digest": last_known_good_digest,
        }
        return {
            "report_id": "apreg.validation." + hashlib.sha256(_json(payload).encode("utf-8")).hexdigest(),
            **payload,
        }

    @staticmethod
    def _upsert_source(con: sqlite3.Connection, source: Mapping[str, Any]) -> None:
        con.execute(
            """
            INSERT INTO projection_source(
                source_id, source_kind, source_ref, source_path, mtime_ns,
                ctime_ns, size_bytes, content_digest, schema_digest,
                parse_status, validation_status, observed_at, validated_at,
                payload_digest, error, payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_ref=excluded.source_ref,
                source_path=excluded.source_path,
                mtime_ns=excluded.mtime_ns,
                ctime_ns=excluded.ctime_ns,
                size_bytes=excluded.size_bytes,
                content_digest=excluded.content_digest,
                schema_digest=excluded.schema_digest,
                parse_status=excluded.parse_status,
                validation_status=excluded.validation_status,
                observed_at=excluded.observed_at,
                validated_at=excluded.validated_at,
                payload_digest=excluded.payload_digest,
                error=excluded.error,
                payload_json=excluded.payload_json
            """,
            (
                source["source_id"],
                source["source_kind"],
                source["source_ref"],
                source["source_path"],
                source.get("mtime_ns"),
                source.get("ctime_ns"),
                source.get("size_bytes"),
                source.get("content_digest"),
                source.get("schema_digest"),
                source["parse_status"],
                source["validation_status"],
                source["observed_at"],
                source.get("validated_at"),
                source.get("payload_digest"),
                source.get("error"),
                _json_pretty(dict(source)),
            ),
        )

    def _insert_project(
        self,
        con: sqlite3.Connection,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        payload_digest: str,
    ) -> None:
        application_id = str(payload["id"])
        title = str(payload.get("title") or application_id)
        description = str(payload.get("description") or "")
        row_values = (
            DEVELOPMENT_PROJECT_SOURCE_KIND,
            application_id,
            f"project:{application_id}",
            source_id,
            "project",
            title,
            description,
            str(payload.get("version") or ""),
            None,
            str(payload.get("visibility") or "unlisted"),
            str((payload.get("lifecycle") or {}).get("status") or "development"),
            0,
            0,
            None,
            None,
            None,
            str(payload.get("source_path") or ""),
            _search_text(application_id, title, description),
            payload_digest,
            "valid",
            _now(),
            _json_pretty(dict(payload)),
        )
        con.execute(
            """
            INSERT INTO application_index(
                source_kind, application_id, source_ref, source_id, kind, title,
                description, version, publisher_ref, visibility, lifecycle,
                installed, local_beta, release_digest, stable_release_digest,
                prerelease_release_digest, source_path, search_text,
                payload_digest, validation_status, updated_at, payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_kind, application_id) DO UPDATE SET
                source_ref=excluded.source_ref,
                source_id=excluded.source_id,
                kind=excluded.kind,
                title=excluded.title,
                description=excluded.description,
                version=excluded.version,
                publisher_ref=excluded.publisher_ref,
                visibility=excluded.visibility,
                lifecycle=excluded.lifecycle,
                installed=excluded.installed,
                local_beta=excluded.local_beta,
                release_digest=excluded.release_digest,
                stable_release_digest=excluded.stable_release_digest,
                prerelease_release_digest=excluded.prerelease_release_digest,
                source_path=excluded.source_path,
                search_text=excluded.search_text,
                payload_digest=excluded.payload_digest,
                validation_status=excluded.validation_status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            row_values,
        )
        rowid = con.execute(
            "SELECT rowid FROM application_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, application_id),
        ).fetchone()["rowid"]
        with self._ignore_missing_fts(con):
            con.execute("DELETE FROM application_index_fts WHERE rowid=?", (rowid,))
            con.execute(
                """
                INSERT INTO application_index_fts(rowid, application_id, title, description, search_text)
                VALUES(?,?,?,?,?)
                """,
                (rowid, application_id, title, description, _search_text(application_id, title, description)),
            )

    def _insert_application_store_summary(
        self,
        con: sqlite3.Connection,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        payload_digest: str,
    ) -> None:
        application_id = str(payload["application_id"])
        title = str(payload.get("title") or application_id)
        description = str(payload.get("description") or "")
        channels = payload.get("channels") if isinstance(payload.get("channels"), Mapping) else {}
        con.execute(
            """
            INSERT INTO application_index(
                source_kind, application_id, source_ref, source_id, kind, title,
                description, version, publisher_ref, visibility, lifecycle,
                installed, local_beta, release_digest, stable_release_digest,
                prerelease_release_digest, source_path, search_text,
                payload_digest, validation_status, updated_at, payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_kind, application_id) DO UPDATE SET
                source_ref=excluded.source_ref,
                source_id=excluded.source_id,
                kind=excluded.kind,
                title=excluded.title,
                description=excluded.description,
                version=excluded.version,
                publisher_ref=excluded.publisher_ref,
                visibility=excluded.visibility,
                lifecycle=excluded.lifecycle,
                installed=excluded.installed,
                local_beta=excluded.local_beta,
                release_digest=excluded.release_digest,
                stable_release_digest=excluded.stable_release_digest,
                prerelease_release_digest=excluded.prerelease_release_digest,
                source_path=excluded.source_path,
                search_text=excluded.search_text,
                payload_digest=excluded.payload_digest,
                validation_status=excluded.validation_status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                APPLICATION_STORE_SOURCE_KIND,
                application_id,
                f"application:{application_id}",
                source_id,
                "application",
                title,
                description,
                str((payload.get("application") or {}).get("version") or ""),
                payload.get("publisher_ref"),
                str(payload.get("visibility") or ""),
                str(payload.get("lifecycle") or ""),
                1 if payload.get("installed") else 0,
                1 if payload.get("local_beta_active") else 0,
                payload.get("release_digest"),
                channels.get("stable"),
                channels.get("prerelease"),
                None,
                _search_text(application_id, title, description),
                payload_digest,
                "valid",
                _now(),
                _json_pretty(dict(payload)),
            ),
        )
        rowid = con.execute(
            "SELECT rowid FROM application_index WHERE source_kind=? AND application_id=?",
            (APPLICATION_STORE_SOURCE_KIND, application_id),
        ).fetchone()["rowid"]
        with self._ignore_missing_fts(con):
            con.execute("DELETE FROM application_index_fts WHERE rowid=?", (rowid,))
            con.execute(
                """
                INSERT INTO application_index_fts(rowid, application_id, title, description, search_text)
                VALUES(?,?,?,?,?)
                """,
                (rowid, application_id, title, description, _search_text(application_id, title, description)),
            )

    @staticmethod
    def _replace_application_store_components(
        con: sqlite3.Connection,
        payload: Mapping[str, Any],
        *,
        payload_digest: str,
    ) -> None:
        application_id = str(payload.get("application_id") or "").strip()
        con.execute(
            "DELETE FROM application_component_index WHERE source_kind=? AND application_id=?",
            (APPLICATION_STORE_SOURCE_KIND, application_id),
        )
        installation = payload.get("installation") if isinstance(payload.get("installation"), Mapping) else {}
        for raw in installation.get("component_refs") or []:
            if not isinstance(raw, Mapping):
                continue
            component_ref = str(raw.get("component_ref") or "").strip()
            if not component_ref:
                continue
            con.execute(
                """
                INSERT INTO application_component_index(
                    source_kind, component_ref, application_id, component_role,
                    lifecycle, exposure, package_digest, payload_digest, payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    APPLICATION_STORE_SOURCE_KIND,
                    component_ref,
                    application_id,
                    "installed",
                    str(raw.get("lifecycle") or ""),
                    "application",
                    raw.get("package_digest"),
                    payload_digest,
                    _json_pretty(dict(raw)),
                ),
            )

    @staticmethod
    def _replace_application_store_entrypoints(
        con: sqlite3.Connection,
        application: Mapping[str, Any],
        *,
        payload_digest: str,
    ) -> None:
        application_id = str(application.get("application_id") or "").strip()
        con.execute(
            "DELETE FROM application_entrypoint_index WHERE source_kind=? AND application_id=?",
            (APPLICATION_STORE_SOURCE_KIND, application_id),
        )
        for raw in application.get("entrypoints") or []:
            if not isinstance(raw, Mapping):
                continue
            entrypoint_id = str(raw.get("entrypoint_id") or raw.get("id") or "").strip()
            if not entrypoint_id:
                continue
            payload = dict(raw)
            con.execute(
                """
                INSERT INTO application_entrypoint_index(
                    source_kind, application_id, entrypoint_id, presentation_ref,
                    is_default, supported_surfaces_json, binding_summary_json,
                    payload_digest, payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    APPLICATION_STORE_SOURCE_KIND,
                    application_id,
                    entrypoint_id,
                    str(payload.get("presentation_ref") or payload.get("presentation") or ""),
                    1 if payload.get("default") is True else 0,
                    _json_pretty(payload.get("surfaces") or []),
                    _json_pretty(payload.get("bindings") or {}),
                    payload_digest,
                    _json_pretty(payload),
                ),
            )

    @staticmethod
    def _replace_project_components(
        con: sqlite3.Connection,
        project: Mapping[str, Any],
        *,
        payload_digest: str,
    ) -> None:
        project_id = str(project.get("id") or "").strip()
        con.execute(
            "DELETE FROM application_component_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, project_id),
        )
        components = project.get("components") if isinstance(project.get("components"), Mapping) else {}
        for raw in components.get("owned") or []:
            if not isinstance(raw, Mapping):
                continue
            component_ref = str(raw.get("ref") or "").strip()
            if not component_ref:
                continue
            payload = dict(raw)
            con.execute(
                """
                INSERT INTO application_component_index(
                    source_kind, component_ref, application_id, component_role,
                    lifecycle, exposure, package_digest, payload_digest, payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    DEVELOPMENT_PROJECT_SOURCE_KIND,
                    component_ref,
                    project_id,
                    str(payload.get("role") or ""),
                    str(payload.get("lifecycle") or ""),
                    str(payload.get("exposure") or ""),
                    payload.get("package_digest"),
                    payload_digest,
                    _json_pretty(payload),
                ),
            )

    @staticmethod
    def _replace_project_entrypoints(
        con: sqlite3.Connection,
        project: Mapping[str, Any],
        *,
        payload_digest: str,
    ) -> None:
        project_id = str(project.get("id") or "").strip()
        con.execute(
            "DELETE FROM application_entrypoint_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, project_id),
        )
        for raw in project.get("entrypoints") or []:
            if not isinstance(raw, Mapping):
                continue
            entrypoint_id = str(raw.get("id") or raw.get("entrypoint_id") or "").strip()
            if not entrypoint_id:
                continue
            payload = dict(raw)
            con.execute(
                """
                INSERT INTO application_entrypoint_index(
                    source_kind, application_id, entrypoint_id, presentation_ref,
                    is_default, supported_surfaces_json, binding_summary_json,
                    payload_digest, payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    DEVELOPMENT_PROJECT_SOURCE_KIND,
                    project_id,
                    entrypoint_id,
                    str(payload.get("presentation") or payload.get("presentation_ref") or ""),
                    1 if payload.get("default") is True else 0,
                    _json_pretty(payload.get("surfaces") or []),
                    _json_pretty(payload.get("bindings") or {}),
                    payload_digest,
                    _json_pretty(payload),
                ),
            )

    @staticmethod
    def _insert_validation_report(con: sqlite3.Connection, report: Mapping[str, Any]) -> None:
        con.execute(
            """
            INSERT OR REPLACE INTO validation_report(
                report_id, source_id, validator_version, status, errors_json,
                warnings_json, checked_digest, checked_at, next_retry_at,
                last_known_good_digest, payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                report["report_id"],
                report["source_id"],
                report["validator_version"],
                report["status"],
                _json_pretty(report.get("errors") or []),
                _json_pretty(report.get("warnings") or []),
                report.get("checked_digest"),
                report["checked_at"],
                report.get("next_retry_at"),
                report.get("last_known_good_digest"),
                _json_pretty(dict(report)),
            ),
        )

    @staticmethod
    def _delete_development_project(con: sqlite3.Connection, project_id: str) -> None:
        con.execute(
            "DELETE FROM application_component_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, project_id),
        )
        con.execute(
            "DELETE FROM application_entrypoint_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, project_id),
        )
        rows = con.execute(
            "SELECT rowid FROM application_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, project_id),
        ).fetchall()
        for row in rows:
            try:
                con.execute("DELETE FROM application_index_fts WHERE rowid=?", (row["rowid"],))
            except sqlite3.OperationalError:
                pass
        con.execute(
            "DELETE FROM application_index WHERE source_kind=? AND application_id=?",
            (DEVELOPMENT_PROJECT_SOURCE_KIND, project_id),
        )

    @staticmethod
    def _source_watermark(con: sqlite3.Connection, source_kind: str) -> str:
        rows = con.execute(
            """
            SELECT source_ref, content_digest, schema_digest, validation_status
            FROM projection_source
            WHERE source_kind=?
            ORDER BY source_ref, source_path
            """,
            (source_kind,),
        ).fetchall()
        return _digest([
            {
                "source_ref": row["source_ref"],
                "content_digest": row["content_digest"],
                "schema_digest": row["schema_digest"],
                "validation_status": row["validation_status"],
            }
            for row in rows
        ])

    @staticmethod
    def _projection_digest(con: sqlite3.Connection) -> str:
        rows = con.execute(
            """
            SELECT source_kind, application_id, payload_digest, validation_status
            FROM application_index
            ORDER BY source_kind, application_id
            """
        ).fetchall()
        return _digest([
            {
                "source_kind": row["source_kind"],
                "application_id": row["application_id"],
                "payload_digest": row["payload_digest"],
                "validation_status": row["validation_status"],
            }
            for row in rows
        ])

    @staticmethod
    def _ignore_missing_fts(con: sqlite3.Connection):
        class _Guard:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, _traceback):
                return exc_type is sqlite3.OperationalError and "application_index_fts" in str(exc)

        return _Guard()


__all__ = [
    "APPLICATION_REGISTRY_PROJECTION_SCHEMA_VERSION",
    "APPLICATION_REGISTRY_PROJECTION_VALIDATOR_VERSION",
    "APPLICATION_STORE_RECORD_SOURCE_KIND",
    "APPLICATION_STORE_SOURCE_KIND",
    "ApplicationRegistryProjection",
    "ApplicationRegistryProjectionError",
    "DEVELOPMENT_PROJECT_MANIFEST_SOURCE_KIND",
    "DEVELOPMENT_PROJECT_SOURCE_KIND",
]
