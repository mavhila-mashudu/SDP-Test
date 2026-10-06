from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commits (
    sha TEXT PRIMARY KEY,
    committed_at INTEGER NOT NULL,
    author TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    commit_sha TEXT NOT NULL,
    object_type TEXT NOT NULL,
    path TEXT NOT NULL,
    added INTEGER NOT NULL,
    removed INTEGER NOT NULL,
    PRIMARY KEY (commit_sha, object_type, path),
    FOREIGN KEY (commit_sha) REFERENCES commits(sha) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS author_aliases (
    source_author TEXT PRIMARY KEY,
    canonical_author TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commits_date ON commits(committed_at);
CREATE INDEX IF NOT EXISTS idx_commits_author ON commits(author);
CREATE INDEX IF NOT EXISTS idx_changes_object ON changes(object_type, path);
"""


class MetricStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def replace_analysis(
        self,
        metadata: dict[str, str],
        records: Iterable[tuple[str, int, str, list[tuple[str, str, int, int]]]],
    ) -> int:
        commit_count = 0
        with self.connect() as connection:
            connection.execute("DELETE FROM changes")
            connection.execute("DELETE FROM commits")
            connection.execute("DELETE FROM metadata")
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
            )
            for sha, committed_at, author, changes in records:
                connection.execute(
                    "INSERT INTO commits(sha, committed_at, author) VALUES (?, ?, ?)",
                    (sha, committed_at, author),
                )
                if changes:
                    connection.executemany(
                        """
                        INSERT INTO changes(commit_sha, object_type, path, added, removed)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            (sha, object_type, path, added, removed)
                            for object_type, path, added, removed in changes
                        ),
                    )
                commit_count += 1
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('commit_count', ?)",
                (str(commit_count),),
            )
        return commit_count

    def has_analysis(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM metadata WHERE key = 'ref_sha'"
            ).fetchone()
        return row is not None

    def metadata(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def merge_authors(self, source_authors: list[str], canonical_author: str) -> None:
        sources = list(dict.fromkeys(author.strip() for author in source_authors if author.strip()))
        canonical = canonical_author.strip()
        if not sources or not canonical:
            raise ValueError("Choose author identities and enter their merged identity.")
        with self.connect() as connection:
            known = {
                row[0]
                for row in connection.execute(
                    f"SELECT author FROM commits WHERE author IN ({','.join('?' for _ in sources)})",
                    sources,
                )
            }
            if len(known) != len(sources):
                raise ValueError("One or more selected author identities are unknown.")
            connection.executemany(
                "INSERT OR REPLACE INTO author_aliases(source_author, canonical_author) VALUES (?, ?)",
                ((source, canonical) for source in sources),
            )

    def clear_author_merges(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM author_aliases")

    def filter_options(self) -> dict[str, list]:
        with self.connect() as connection:
            raw_authors = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT author FROM commits ORDER BY author COLLATE NOCASE"
                )
            ]
            authors = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT COALESCE(am.canonical_author, c.author) AS author
                    FROM commits c
                    LEFT JOIN author_aliases am ON am.source_author = c.author
                    ORDER BY author COLLATE NOCASE
                    """
                )
            ]
            author_merges = [
                dict(row)
                for row in connection.execute(
                    "SELECT source_author, canonical_author FROM author_aliases ORDER BY canonical_author, source_author"
                )
            ]
            objects = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT DISTINCT object_type, path
                    FROM changes
                    ORDER BY CASE object_type
                        WHEN 'repository' THEN 0
                        WHEN 'directory' THEN 1
                        ELSE 2 END,
                        path COLLATE NOCASE
                    """
                )
            ]
            commits = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT c.sha, c.committed_at,
                           COALESCE(am.canonical_author, c.author) AS author
                    FROM commits c
                    LEFT JOIN author_aliases am ON am.source_author = c.author
                    ORDER BY c.committed_at DESC, c.sha
                    LIMIT 250
                    """
                )
            ]
        return {
            "authors": authors,
            "raw_authors": raw_authors,
            "author_merges": author_merges,
            "objects": objects,
            "commits": commits,
        }

    def resolve_commit_tokens(self, tokens: list[str]) -> tuple[list[str], list[str]]:
        resolved: list[str] = []
        invalid: list[str] = []
        with self.connect() as connection:
            for token in dict.fromkeys(tokens):
                clean = token.strip().lower()
                if not clean or len(clean) < 4 or any(c not in "0123456789abcdef" for c in clean):
                    invalid.append(token)
                    continue
                rows = connection.execute(
                    "SELECT sha FROM commits WHERE sha LIKE ? LIMIT 2", (f"{clean}%",)
                ).fetchall()
                if len(rows) != 1:
                    invalid.append(token)
                else:
                    resolved.append(rows[0]["sha"])
        return resolved, invalid

    @staticmethod
    def _commit_conditions(
        start: int | None,
        end: int | None,
        commit_shas: list[str] | None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        values: list = []
        if commit_shas:
            placeholders = ",".join("?" for _ in commit_shas)
            clauses.append(f"c.sha IN ({placeholders})")
            values.extend(commit_shas)
        else:
            if start is not None:
                clauses.append("c.committed_at >= ?")
                values.append(start)
            if end is not None:
                clauses.append("c.committed_at < ?")
                values.append(end)
        return (" AND ".join(clauses) if clauses else "1 = 1", values)

    @staticmethod
    def _metric_row(row: sqlite3.Row, commit_count: int) -> dict:
        added = int(row["added"] or 0)
        removed = int(row["removed"] or 0)
        churn = added + removed
        modifications = int(row["modifications"] or 0)
        return {
            "object_type": row["object_type"],
            "path": row["path"],
            "added": added,
            "removed": removed,
            "growth": added - removed,
            "churn": churn,
            "modifications": modifications,
            "modification_frequency": modifications / commit_count if commit_count else 0.0,
            "churn_rate": churn / commit_count if commit_count else 0.0,
        }

    def query_metrics(
        self,
        *,
        start: int | None = None,
        end: int | None = None,
        commit_shas: list[str] | None = None,
        object_filter: tuple[str, str] | None = None,
        author_filter: str | None = None,
        limit: int | None = None,
    ) -> dict:
        condition, values = self._commit_conditions(start, end, commit_shas)
        object_clause = ""
        object_values: list = []
        if object_filter:
            object_clause = " AND ch.object_type = ? AND ch.path = ?"
            object_values.extend(object_filter)

        with self.connect() as connection:
            commit_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM commits c WHERE {condition}", values
                ).fetchone()[0]
            )

            object_sql = f"""
                SELECT ch.object_type, ch.path,
                       SUM(ch.added) AS added,
                       SUM(ch.removed) AS removed,
                       SUM(CASE WHEN ch.added + ch.removed > 0 THEN 1 ELSE 0 END)
                           AS modifications
                FROM changes ch
                JOIN commits c ON c.sha = ch.commit_sha
                WHERE {condition}{object_clause}
                GROUP BY ch.object_type, ch.path
                ORDER BY CASE ch.object_type
                    WHEN 'repository' THEN 0
                    WHEN 'directory' THEN 1
                    ELSE 2 END,
                    (SUM(ch.added) + SUM(ch.removed)) DESC,
                    ch.path COLLATE NOCASE
            """
            object_rows = [
                self._metric_row(row, commit_count)
                for row in connection.execute(object_sql, values + object_values)
            ]

            author_clause = ""
            author_values: list = []
            if author_filter:
                author_clause = " AND COALESCE(am.canonical_author, c.author) = ?"
                author_values.append(author_filter)
            author_sql = f"""
                SELECT ch.object_type, ch.path,
                       COALESCE(am.canonical_author, c.author) AS author,
                       SUM(ch.added) AS added,
                       SUM(ch.removed) AS removed,
                       SUM(CASE WHEN ch.added + ch.removed > 0 THEN 1 ELSE 0 END)
                           AS modifications
                FROM changes ch
                JOIN commits c ON c.sha = ch.commit_sha
                LEFT JOIN author_aliases am ON am.source_author = c.author
                WHERE {condition}{object_clause}{author_clause}
                GROUP BY ch.object_type, ch.path,
                         COALESCE(am.canonical_author, c.author)
                HAVING SUM(ch.added) + SUM(ch.removed) > 0
                ORDER BY (SUM(ch.added) + SUM(ch.removed)) DESC,
                         author COLLATE NOCASE,
                         ch.path COLLATE NOCASE
            """
            author_rows_raw = connection.execute(
                author_sql, values + object_values + author_values
            ).fetchall()

        totals = {
            (row["object_type"], row["path"]): row["churn"] for row in object_rows
        }
        author_rows = []
        for row in author_rows_raw:
            metric = self._metric_row(row, commit_count)
            metric["author"] = row["author"]
            total_churn = totals.get((row["object_type"], row["path"]), 0)
            metric["ownership"] = metric["churn"] / total_churn if total_churn else 0.0
            author_rows.append(metric)

        summary = next(
            (
                row
                for row in object_rows
                if row["object_type"] == "repository" and row["path"] == "/"
            ),
            object_rows[0] if object_rows else self.empty_metric(),
        )
        total_objects = len(object_rows)
        total_author_rows = len(author_rows)
        if limit is not None:
            object_rows = object_rows[:limit]
            author_rows = author_rows[:limit]

        return {
            "commit_count": commit_count,
            "summary": summary,
            "objects": object_rows,
            "authors": author_rows,
            "total_objects": total_objects,
            "total_author_rows": total_author_rows,
        }

    @staticmethod
    def empty_metric() -> dict:
        return {
            "object_type": "repository",
            "path": "/",
            "added": 0,
            "removed": 0,
            "growth": 0,
            "churn": 0,
            "modifications": 0,
            "modification_frequency": 0.0,
            "churn_rate": 0.0,
        }
