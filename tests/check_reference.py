from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer import analyze_repository
from store import MetricStore


INTEGER_FIELDS = ("added", "removed", "growth", "churn", "modifications")
FLOAT_FIELDS = ("modification_frequency", "churn_rate", "ownership")


def expected_rows(csv_path: Path) -> tuple[str, dict]:
    rows = {}
    reference = ""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            reference = row["ref_sha"]
            key = (row["object_type"], row["path"], row["author"])
            rows[key] = row
    return reference, rows


def actual_rows(store: MetricStore) -> dict:
    result = store.query_metrics()
    rows = {}
    for row in result["objects"]:
        rows[(row["object_type"], row["path"], "ALL")] = row
    for row in result["authors"]:
        rows[(row["object_type"], row["path"], row["author"])] = row
    return rows


def compare(expected: dict, actual: dict) -> list[str]:
    problems: list[str] = []
    missing = expected.keys() - actual.keys()
    unexpected = actual.keys() - expected.keys()
    for key in sorted(missing)[:20]:
        problems.append(f"Missing row: {key}")
    for key in sorted(unexpected)[:20]:
        problems.append(f"Unexpected row: {key}")

    for key in expected.keys() & actual.keys():
        expected_row = expected[key]
        actual_row = actual[key]
        for field in INTEGER_FIELDS:
            if int(expected_row[field]) != int(actual_row[field]):
                problems.append(
                    f"{key} {field}: expected {expected_row[field]}, got {actual_row[field]}"
                )
        for field in FLOAT_FIELDS:
            if not expected_row[field]:
                continue
            if abs(float(expected_row[field]) - float(actual_row[field])) > 1e-9:
                problems.append(
                    f"{key} {field}: expected {expected_row[field]}, got {actual_row[field]}"
                )
        if len(problems) >= 100:
            break
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare RAT output with a supplied CSV.")
    parser.add_argument("repository", type=Path)
    parser.add_argument("reference_csv", type=Path)
    arguments = parser.parse_args()

    reference, expected = expected_rows(arguments.reference_csv)
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reference.sqlite"
        metadata = analyze_repository(arguments.repository, database, reference)
        actual = actual_rows(MetricStore(database))

    problems = compare(expected, actual)
    print(
        f"Compared {len(expected):,} expected rows at {metadata['ref_sha']} "
        f"across {metadata['commit_count']:,} commits."
    )
    if problems:
        for problem in problems[:100]:
            print(problem)
        print(f"FAILED with at least {len(problems)} difference(s).")
        return 1
    print("PASS: every reference metric matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
