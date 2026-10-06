from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    database_path TEXT NOT NULL,
    ref_sha TEXT NOT NULL,
    requested_ref TEXT NOT NULL,
    commit_count INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
"""


class AnalysisCatalog:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def register(self, metadata: dict, database_path: str | Path) -> str:
        analysis_id = uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses(
                    id, name, repo_path, database_path, ref_sha,
                    requested_ref, commit_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    metadata["name"],
                    metadata["repo_path"],
                    str(Path(database_path).resolve()),
                    metadata["ref_sha"],
                    metadata["requested_ref"],
                    int(metadata["commit_count"]),
                    int(time.time()),
                ),
            )
        return analysis_id

    def list(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, analysis_id: str | None) -> dict | None:
        if not analysis_id:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        return dict(row) if row else None
