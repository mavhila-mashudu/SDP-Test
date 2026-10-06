from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from catalog import AnalysisCatalog


class AnalysisCatalogTests(unittest.TestCase):
    def test_registers_lists_and_retrieves_multiple_analyses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = AnalysisCatalog(root / "catalog.sqlite")
            first_db = root / "first.sqlite"
            second_db = root / "second.sqlite"
            first_db.touch()
            second_db.touch()

            first_id = catalog.register(
                {
                    "name": "first",
                    "repo_path": str(root / "first"),
                    "ref_sha": "a" * 40,
                    "requested_ref": "HEAD",
                    "commit_count": 10,
                },
                first_db,
            )
            second_id = catalog.register(
                {
                    "name": "second",
                    "repo_path": str(root / "second"),
                    "ref_sha": "b" * 40,
                    "requested_ref": "v1",
                    "commit_count": 20,
                },
                second_db,
            )

            self.assertEqual(len(catalog.list()), 2)
            self.assertEqual(catalog.get(first_id)["name"], "first")
            self.assertEqual(catalog.get(second_id)["commit_count"], 20)
            self.assertIsNone(catalog.get("missing"))


if __name__ == "__main__":
    unittest.main()
