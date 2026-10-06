from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from werkzeug.datastructures import FileStorage

from repository import RepositoryError, clone_repository, extract_repository, validate_repository


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_repository_zip(self) -> Path:
        repository = self.root / "source" / "sample"
        repository.mkdir(parents=True)
        git(repository, "init", "-b", "main")
        git(repository, "config", "user.name", "Test User")
        git(repository, "config", "user.email", "test@example.com")
        (repository / "hello.txt").write_text("hello\n", encoding="utf-8")
        git(repository, "add", ".")
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": "@1700000000 +0000",
            "GIT_COMMITTER_DATE": "@1700000000 +0000",
        }
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "initial"],
            check=True,
            capture_output=True,
            env=env,
        )
        archive_path = self.root / "sample.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in repository.rglob("*"):
                if path.is_file():
                    bundle.write(path, Path("sample") / path.relative_to(repository))
        return archive_path

    def test_extracts_zip_with_git_directory(self) -> None:
        archive_path = self.create_repository_zip()
        with archive_path.open("rb") as handle:
            upload = FileStorage(stream=handle, filename="sample.zip")
            extracted = extract_repository(upload, self.root / "imports")
        validate_repository(extracted)
        self.assertEqual(extracted.name, "sample")

    def test_rejects_zip_path_traversal(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as bundle:
            bundle.writestr("../outside.txt", "unsafe")
        payload.seek(0)
        upload = FileStorage(stream=payload, filename="unsafe.zip")
        with self.assertRaises(RepositoryError):
            extract_repository(upload, self.root / "imports")

    def test_rejects_non_http_clone_url(self) -> None:
        with self.assertRaises(RepositoryError):
            clone_repository("file:///tmp/repository", self.root / "imports")


if __name__ == "__main__":
    unittest.main()
