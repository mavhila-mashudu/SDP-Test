from __future__ import annotations

import shutil
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse


class RepositoryError(RuntimeError):
    pass


def _run_git(*args: str, timeout: int = 300) -> None:
    try:
        subprocess.run(
            ["git", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError("The Git operation timed out.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or "").strip()
        raise RepositoryError(message or "The Git operation failed.") from exc


def _new_import_directory(data_root: str | Path) -> Path:
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"repo-{uuid.uuid4().hex}"
    destination.mkdir()
    return destination


def clone_repository(url: str, data_root: str | Path) -> Path:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RepositoryError("Enter a valid public HTTP(S) Git repository URL.")
    destination = _new_import_directory(data_root)
    checkout = destination / "checkout"
    try:
        _run_git("clone", "--no-checkout", "--", url.strip(), str(checkout))
        validate_repository(checkout)
        return checkout
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _safe_zip_member(member: zipfile.ZipInfo, destination: Path) -> None:
    member_path = Path(member.filename)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise RepositoryError("The ZIP contains an unsafe path.")
    mode = member.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise RepositoryError("ZIP archives containing symbolic links are not supported.")
    resolved = (destination / member_path).resolve()
    if destination.resolve() not in resolved.parents and resolved != destination.resolve():
        raise RepositoryError("The ZIP contains a path outside its extraction directory.")


def extract_repository(archive, data_root: str | Path) -> Path:
    destination = _new_import_directory(data_root)
    archive_path = destination / "upload.zip"
    archive.save(archive_path)
    extracted = destination / "extracted"
    extracted.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            for member in bundle.infolist():
                _safe_zip_member(member, extracted)
            bundle.extractall(extracted)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryError("The uploaded file is not a valid ZIP archive.") from exc
    except RepositoryError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        archive_path.unlink(missing_ok=True)

    candidates = sorted(
        (item.parent for item in extracted.rglob(".git") if item.is_dir() or item.is_file()),
        key=lambda item: len(item.parts),
    )
    if not candidates:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryError("The ZIP does not contain a Git repository with a .git entry.")
    repository = candidates[0]
    try:
        validate_repository(repository)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return repository


def validate_repository(path: str | Path) -> None:
    repository = Path(path)
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RepositoryError("The selected content is not a readable Git work tree.") from exc
    if result.stdout.strip() != "true":
        raise RepositoryError("The selected content is not a Git work tree.")
