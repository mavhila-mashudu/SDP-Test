from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from store import MetricStore


COMMIT_MARKER = b"RAT_COMMIT_4F7C9A"


class AnalysisError(RuntimeError):
    pass


def _git(repo_path: Path, *args: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise AnalysisError(stderr.strip() or "Git could not read this repository.") from exc
    return result.stdout.strip()


def resolve_reference(repo_path: str | Path, reference: str | None = None) -> str:
    path = Path(repo_path)
    requested = (reference or "HEAD").strip()
    if not requested or requested.startswith("-"):
        raise AnalysisError("Enter a valid Git reference or commit SHA.")
    return _git(path, "rev-parse", "--verify", f"{requested}^{{commit}}")


def repository_name(repo_path: str | Path) -> str:
    path = Path(repo_path)
    remote = _git(path, "config", "--get", "remote.origin.url") if _has_remote(path) else ""
    if remote:
        name = remote.rstrip("/").rsplit("/", 1)[-1]
        return name.removesuffix(".git") or path.name
    return path.name


def _has_remote(repo_path: Path) -> bool:
    try:
        return bool(_git(repo_path, "remote", timeout=10))
    except AnalysisError:
        return False


def _has_mailmap(repo_path: Path, ref_sha: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "cat-file", "-e", f"{ref_sha}:.mailmap"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )
    return result.returncode == 0


def _iter_nul_tokens(stream: BinaryIO) -> Iterator[bytes]:
    buffer = b""
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        buffer += chunk
        pieces = buffer.split(b"\x00")
        yield from pieces[:-1]
        buffer = pieces[-1]
    if buffer:
        yield buffer


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _has_binary_attribute(repo_path: Path, sha: str, path: str) -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "check-attr",
                f"--source={sha}",
                "-z",
                "diff",
                "--",
                path,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    fields = result.stdout.split(b"\x00")
    return len(fields) >= 3 and fields[2] == b"unset"


def _add_object_change(
    totals: dict[tuple[str, str], list[int]],
    object_type: str,
    path: str,
    added: int,
    removed: int,
) -> None:
    current = totals[(object_type, path)]
    current[0] += added
    current[1] += removed


def _add_path_changes(
    totals: dict[tuple[str, str], list[int]],
    path: str,
    added: int,
    removed: int,
) -> None:
    clean_path = path.strip("/")
    if not clean_path:
        return
    _add_object_change(totals, "file", clean_path, added, removed)
    _add_object_change(totals, "repository", "/", added, removed)
    parts = PurePosixPath(clean_path).parts
    for depth in range(1, len(parts)):
        directory = "/".join(parts[:depth])
        _add_object_change(totals, "directory", directory, added, removed)


def _record(
    sha: str,
    committed_at: int,
    author: str,
    totals: dict[tuple[str, str], list[int]],
) -> tuple[str, int, str, list[tuple[str, str, int, int]]]:
    changes = [
        (object_type, path, values[0], values[1])
        for (object_type, path), values in totals.items()
    ]
    return sha, committed_at, author, changes


def iter_commit_records(
    repo_path: str | Path, ref_sha: str
) -> Iterator[tuple[str, int, str, list[tuple[str, str, int, int]]]]:
    path = Path(repo_path)
    command = ["git", "-C", str(path), "-c", "core.quotepath=false"]
    if _has_mailmap(path, ref_sha):
        command.extend(["-c", f"mailmap.blob={ref_sha}:.mailmap"])
    command.extend(
        [
            "log",
            "--reverse",
            "--no-merges",
            "--root",
            "--use-mailmap",
            "--find-renames=50%",
            "--numstat",
            "-z",
            f"--format=%x00{_decode(COMMIT_MARKER)}%x00%H%x00%ct%x00%aN%x00%aE",
            ref_sha,
        ]
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    tokens = iter(_iter_nul_tokens(process.stdout))
    sha: str | None = None
    committed_at = 0
    author = ""
    totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    try:
        for raw_token in tokens:
            token = raw_token.lstrip(b"\r\n")
            if not token:
                continue
            if token == COMMIT_MARKER:
                if sha is not None:
                    yield _record(sha, committed_at, author, totals)
                try:
                    sha = _decode(next(tokens)).strip()
                    committed_at = int(next(tokens))
                    name = _decode(next(tokens)).strip()
                    email = _decode(next(tokens)).strip()
                except (StopIteration, ValueError) as exc:
                    raise AnalysisError("Git returned incomplete commit metadata.") from exc
                author = f"{name} <{email}>"
                totals = defaultdict(lambda: [0, 0])
                continue
            if sha is None:
                continue
            fields = token.split(b"\t", 2)
            if len(fields) != 3:
                continue
            added_raw, removed_raw, path_raw = fields
            old_path: str | None = None
            if path_raw:
                changed_path = _decode(path_raw)
            else:
                try:
                    old_path = _decode(next(tokens))
                    changed_path = _decode(next(tokens))
                except StopIteration as exc:
                    raise AnalysisError("Git returned an incomplete rename record.") from exc
            if added_raw == b"-" or removed_raw == b"-":
                continue
            try:
                added = int(added_raw)
                removed = int(removed_raw)
            except ValueError as exc:
                raise AnalysisError("Git returned invalid line statistics.") from exc
            attribute_sha = f"{sha}^" if added == 0 and removed > 0 else sha
            attribute_path = old_path if old_path and added == 0 else changed_path
            if added + removed <= 2 and _has_binary_attribute(
                path, attribute_sha, attribute_path
            ):
                continue
            if old_path:
                _add_path_changes(totals, old_path, 0, 0)
            _add_path_changes(totals, changed_path, added, removed)
        if sha is not None:
            yield _record(sha, committed_at, author, totals)
    finally:
        process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    if process.stderr:
        process.stderr.close()
    return_code = process.wait()
    if return_code != 0:
        raise AnalysisError(stderr.strip() or "Git history analysis failed.")


def analyze_repository(
    repo_path: str | Path,
    database_path: str | Path,
    reference: str | None = None,
    display_name: str | None = None,
) -> dict[str, str | int]:
    path = Path(repo_path).resolve()
    ref_sha = resolve_reference(path, reference)
    name = display_name or repository_name(path)
    store = MetricStore(database_path)
    metadata = {
        "name": name,
        "repo_path": str(path),
        "ref_sha": ref_sha,
        "requested_ref": (reference or "HEAD").strip() or "HEAD",
    }
    commit_count = store.replace_analysis(
        metadata, iter_commit_records(path, ref_sha)
    )
    if commit_count == 0:
        raise AnalysisError("No non-merge commits are reachable from this reference.")
    return {**metadata, "commit_count": commit_count}
