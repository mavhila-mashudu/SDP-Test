from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from catalog import AnalysisCatalog
from store import MetricStore


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog_path = self.root / "catalog.sqlite"
        self.analyses_dir = self.root / "analyses"
        self.catalog_patch = patch.object(app_module, "CATALOG_PATH", self.catalog_path)
        self.directory_patch = patch.object(app_module, "ANALYSES_DIR", self.analyses_dir)
        self.catalog_patch.start()
        self.directory_patch.start()
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        self.directory_patch.stop()
        self.catalog_patch.stop()
        self.temporary.cleanup()

    def register_analysis(self, name: str) -> tuple[str, Path]:
        self.analyses_dir.mkdir(exist_ok=True)
        database = self.analyses_dir / f"{name}.sqlite"
        metadata = {
            "name": name,
            "repo_path": str(self.root / name),
            "ref_sha": name[0] * 40,
            "requested_ref": "HEAD",
        }
        records = [
            ("1" * 40, 1_700_000_000, "Alice <alice@example.com>", [("repository", "/", 2, 0)]),
            ("2" * 40, 1_700_000_100, "Bob <bob@example.com>", [("repository", "/", 1, 1)]),
        ]
        commit_count = MetricStore(database).replace_analysis(metadata, records)
        analysis_id = AnalysisCatalog(self.catalog_path).register(
            {**metadata, "commit_count": commit_count}, database
        )
        return analysis_id, database

    def test_switches_between_persisted_repositories(self) -> None:
        first_id, _ = self.register_analysis("alpha")
        second_id, _ = self.register_analysis("beta")

        response = self.client.get("/", query_string={"repository": first_id})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"alpha", response.data)
        self.assertIn(second_id.encode(), response.data)

    def test_merges_authors_for_selected_repository(self) -> None:
        analysis_id, database = self.register_analysis("alpha")

        response = self.client.post(
            "/authors/merge",
            data={
                "repository": analysis_id,
                "source_authors": [
                    "Alice <alice@example.com>",
                    "Bob <bob@example.com>",
                ],
                "canonical_author": "Core Team <team@example.com>",
            },
        )

        self.assertEqual(response.status_code, 302)
        authors = MetricStore(database).query_metrics()["authors"]
        repository_rows = [row for row in authors if row["object_type"] == "repository"]
        self.assertEqual(len(repository_rows), 1)
        self.assertEqual(repository_rows[0]["author"], "Core Team <team@example.com>")


if __name__ == "__main__":
    unittest.main()
