"""Durable browser-safe observations, not another lifecycle authority."""
from __future__ import annotations

import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from adaos.domain.artifact_release import canonical_payload_digest


class LifecycleEvents:
    def __init__(self, state: Path):
        self.path = Path(state) / "applications/lifecycle-observations.sqlite3"

    def record(self, application_id: str, observation: dict, *, observed_at: str) -> bool:
        """Persist only when semantic facts change, including clock-only expiry."""
        digest = canonical_payload_digest(observation)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=.25)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS observations(application_id TEXT PRIMARY KEY,digest TEXT NOT NULL,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,application_id TEXT NOT NULL,payload TEXT NOT NULL)")
            previous = db.execute("SELECT digest FROM observations WHERE application_id=?", (application_id,)).fetchone()
            if previous and previous[0] == digest:
                return False
            payload = {"schema": "adaos.application.lifecycle_event.v1", "application_id": application_id,
                       "observed_at": observed_at, "observation": observation}
            db.execute("INSERT INTO events(application_id,payload) VALUES (?,?)", (application_id, json.dumps(payload)))
            db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?)", (application_id, digest, json.dumps(payload)))
        return True

    def poll(self, *, application_id: str | None = None, cursor: str | None = None, limit: int = 50) -> dict:
        after = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if token.get("stream") != "application_lifecycle" or type(token.get("after")) is not int or token["after"] < 0:
                    raise ValueError()
                after = token["after"]
            except Exception:
                raise ValueError("Invalid lifecycle event cursor") from None
        if not self.path.is_file():
            return {"schema": "adaos.application.lifecycle_event_page.v1", "events": [], "cursor": cursor,
                    "observation_cursor": cursor, "invalidates": []}
        size = max(1, min(int(limit), 200))
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=.25)) as db:
            query = "SELECT sequence,payload FROM events WHERE sequence>?"
            params = [after]
            if application_id:
                query += " AND application_id=?"
                params.append(application_id)
            rows = db.execute(query + " ORDER BY sequence LIMIT ?", (*params, size)).fetchall()
            # Projection consumers reread current authority, so they may
            # coalesce historical events at this boundary. Journal consumers
            # keep using the ordinary cursor and receive every retained event.
            head_query = "SELECT MAX(sequence) FROM events"
            head = db.execute(head_query + (" WHERE application_id=?" if application_id else ""),
                              (application_id,) if application_id else ()).fetchone()[0]
        events = [{**json.loads(raw), "sequence": seq, "event_id": f"application-lifecycle:{seq}"} for seq, raw in rows]
        if rows:
            cursor = base64.urlsafe_b64encode(json.dumps({"stream": "application_lifecycle", "after": rows[-1][0]}).encode()).decode().rstrip("=")
        observation_cursor = base64.urlsafe_b64encode(json.dumps({
            "stream": "application_lifecycle", "after": max(after, head or 0)}).encode()).decode().rstrip("=")
        return {"schema": "adaos.application.lifecycle_event_page.v1", "events": events, "cursor": cursor,
                "observation_cursor": observation_cursor, "invalidates": [
                    "applications.catalog", "applications.detail", "applications.lifecycle"] if events else []}
