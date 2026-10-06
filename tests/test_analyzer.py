from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from analyzer import AnalysisError, analyze_repository
from store import MetricStore


def git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return result.stdout.strip()


class AnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Alice")
        git(self.repo, "config", "user.email", "alice@example.com")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit(self, message: str, timestamp: int, name: str, email: str) -> str:
        git(self.repo, "add", "-A")
        date = f"@{timestamp} +0000"
        return git(
            self.repo,
            "commit",
            "-m",
            message,
            env={
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_AUTHOR_DATE": date,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
                "GIT_COMMITTER_DATE": date,
            },
        ) and git(self.repo, "rev-parse", "HEAD")

    def build_history(self) -> tuple[str, str, str]:
        (self.repo / "src").mkdir()
        (self.repo / "root.txt").write_text("a\nb\n", encoding="utf-8")
        (self.repo / "src" / "item.txt").write_text("one\n", encoding="utf-8")
        (self.repo / "image.bin").write_bytes(b"\x00\x01\x02")
        first = self.commit("initial", 1_700_000_000, "Alice", "alice@example.com")

        with (self.repo / "root.txt").open("a", encoding="utf-8") as handle:
            handle.write("c\n")
        git(self.repo, "mv", "src/item.txt", "src/renamed.txt")
        with (self.repo / "src" / "renamed.txt").open("a", encoding="utf-8") as handle:
            handle.write("two\n")
        second = self.commit("edit and rename", 1_700_000_100, "Bob", "bob@example.com")

        (self.repo / "root.txt").unlink()
        third = self.commit("delete", 1_700_000_200, "Alice", "alice@example.com")
        return first, second, third

    def analyze(self, reference: str = "HEAD") -> tuple[MetricStore, dict]:
        database = self.root / "metrics.sqlite"
        metadata = analyze_repository(self.repo, database, reference)
        return MetricStore(database), metadata

    def test_metrics_include_directories_binary_and_authors(self) -> None:
        self.build_history()
        store, metadata = self.analyze()
        result = store.query_metrics()
        self.assertEqual(metadata["commit_count"], 3)
        self.assertEqual(result["commit_count"], 3)

        objects = {
            (row["object_type"], row["path"]): row for row in result["objects"]
        }
        root = objects[("repository", "/")]
        self.assertEqual((root["added"], root["removed"], root["growth"]), (5, 3, 2))
        self.assertEqual(root["churn"], 8)
        self.assertEqual(root["modifications"], 3)
        self.assertAlmostEqual(root["modification_frequency"], 1.0)
        self.assertAlmostEqual(root["churn_rate"], 8 / 3)

        self.assertEqual(objects[("directory", "src")]["churn"], 2)
        self.assertNotIn(("file", "image.bin"), objects)
        self.assertIn(("file", "src/item.txt"), objects)
        self.assertIn(("file", "src/renamed.txt"), objects)

        repository_authors = {
            row["author"]: row
            for row in result["authors"]
            if row["object_type"] == "repository"
        }
        self.assertEqual(repository_authors["Alice <alice@example.com>"]["churn"], 6)
        self.assertEqual(repository_authors["Bob <bob@example.com>"]["churn"], 2)
        self.assertAlmostEqual(
            repository_authors["Alice <alice@example.com>"]["ownership"], 0.75
        )

    def test_reference_and_commit_filters(self) -> None:
        first, second, _ = self.build_history()
        store, metadata = self.analyze(second)
        self.assertEqual(metadata["ref_sha"], second)
        self.assertEqual(metadata["commit_count"], 2)

        first_only = store.query_metrics(commit_shas=[first])
        self.assertEqual(first_only["commit_count"], 1)
        self.assertEqual(first_only["summary"]["added"], 3)

        second_only = store.query_metrics(start=1_700_000_100, end=1_700_000_200)
        self.assertEqual(second_only["commit_count"], 1)
        self.assertEqual(second_only["summary"]["churn"], 2)

    def test_author_merge_combines_metrics_without_changing_history(self) -> None:
        self.build_history()
        store, _ = self.analyze()
        alice = "Alice <alice@example.com>"
        bob = "Bob <bob@example.com>"
        merged = "Core Team <team@example.com>"

        store.merge_authors([alice, bob], merged)
        result = store.query_metrics(author_filter=merged)
        repository_rows = [
            row for row in result["authors"] if row["object_type"] == "repository"
        ]

        self.assertEqual(len(repository_rows), 1)
        self.assertEqual(repository_rows[0]["author"], merged)
        self.assertEqual(repository_rows[0]["churn"], 8)
        self.assertEqual(repository_rows[0]["ownership"], 1.0)
        self.assertIn(merged, store.filter_options()["authors"])

        store.clear_author_merges()
        self.assertIn(alice, store.filter_options()["authors"])
        self.assertIn(bob, store.filter_options()["authors"])

    def test_invalid_reference_is_rejected(self) -> None:
        self.build_history()
        with self.assertRaises(AnalysisError):
            self.analyze("does-not-exist")


if __name__ == "__main__":
    unittest.main()
