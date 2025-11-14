from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from adaos.services.agent_context import get_ctx


@dataclass
class ScenarioRow:
    workspace_id: str
    scenario_id: str
    version: str
    installed_at: int


def _ensure_schema(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS scenarios(
            workspace_id TEXT NOT NULL,
            scenario_id  TEXT NOT NULL,
            version      TEXT NOT NULL,
            installed_at INTEGER NOT NULL,
            PRIMARY KEY(workspace_id, scenario_id)
        )
        """
    )


def get(workspace_id: str, scenario_id: str) -> Optional[ScenarioRow]:
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute(
            "SELECT workspace_id, scenario_id, version, installed_at "
            "FROM scenarios WHERE workspace_id=? AND scenario_id=?",
            (workspace_id, scenario_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return ScenarioRow(
        workspace_id=row[0],
        scenario_id=row[1],
        version=row[2],
        installed_at=int(row[3]),
    )


def upsert(workspace_id: str, scenario_id: str, version: str) -> ScenarioRow:
    import time as _time

    sql = get_ctx().sql
    now_ms = int(_time.time() * 1000)
    with sql.connect() as con:
        _ensure_schema(con)
        con.execute(
            """
            INSERT INTO scenarios(workspace_id, scenario_id, version, installed_at)
            VALUES(?,?,?,?)
            ON CONFLICT(workspace_id, scenario_id) DO UPDATE SET
                version=excluded.version,
                installed_at=excluded.installed_at
            """,
            (workspace_id, scenario_id, version, now_ms),
        )
        con.commit()
    return ScenarioRow(workspace_id=workspace_id, scenario_id=scenario_id, version=version, installed_at=now_ms)


def list_installed(workspace_id: str) -> List[ScenarioRow]:
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute(
            "SELECT workspace_id, scenario_id, version, installed_at "
            "FROM scenarios WHERE workspace_id=? ORDER BY scenario_id",
            (workspace_id,),
        )
        rows = cur.fetchall()
    return [
        ScenarioRow(workspace_id=row[0], scenario_id=row[1], version=row[2], installed_at=int(row[3]))
        for row in rows
    ]


__all__ = ["ScenarioRow", "get", "upsert", "list_installed"]

