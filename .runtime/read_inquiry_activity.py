import json
import sqlite3
from pathlib import Path


paths = sorted(
    Path(".adaos/workspace/skills/.runtime/research_orchestrator_skill").glob(
        "v*/data/db/research_orchestrator.db"
    ),
    key=lambda item: item.stat().st_mtime,
)
path = str(paths[-1])
rows = sqlite3.connect(path).execute(
    "SELECT seq, status, detail_json FROM research_activity "
    "WHERE direction_id=? AND stage=? ORDER BY seq DESC LIMIT 20",
    ("evolnomics_inquiry_poc", "inquiry"),
)
for seq, status, raw in rows:
    detail = json.loads(raw)
    print(
        seq,
        status,
        detail.get("provider_job_id"),
        detail.get("validation_error"),
        detail.get("usage"),
    )
