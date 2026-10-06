# Repo Analysis Tool

Repo Analysis Tool (RAT) is a local web dashboard that calculates Git activity metrics for repositories, files, directories, commit sets, and authors.

## Requirements

- Python 3.10 or newer
- Git 2.30 or newer
- Internet access when cloning a remote repository

## Start

```bash
git clone https://github.com/mavhila-mashudu/SDP-Test.git
cd SDP-Test
chmod +x start.sh
./start.sh
```

Open <http://127.0.0.1:5000> in a browser. `start.sh` installs the pinned Python dependency before starting the application.

Alternatively:

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

## Analyze a repository

The dashboard supports either ingestion method required by the brief:

1. **Clone from URL** — enter a public HTTP(S) Git URL. RAT performs a full clone without a shallow depth.
2. **Upload Git ZIP** — upload a ZIP of up to 200 MB that contains the repository's `.git` directory or file.

The optional **Reference** field accepts a branch, tag, or commit SHA and defaults to `HEAD`. RAT analyzes all non-merge commits reachable from that reference.

Imported repositories and their analysis databases are stored under `data/`, which is excluded from Git. Completed analyses remain available in the repository switcher, so several repositories can be compared without re-importing them.

## Filters

After analysis, the dashboard can focus the results by:

- author contribution rows;
- a repository, directory, or file path;
- an inclusive UTC start date and exclusive UTC end date; or
- a manually selected set of full or uniquely abbreviated commit hashes.

A manual commit set overrides the date range. The recent-commit panel can add hashes to the manual selection field.

## Multiple repositories and author merging

Each successful import creates a separate persistent analysis. Use the repository switcher in the header to move between them; filters and author mappings apply only to the active repository.

Open **Merge author identities** to select raw Git identities and combine them under a chosen display identity. Metrics and ownership are regrouped at query time, preserving the original commit metadata. The mappings can be cleared at any time.

## Metrics

For every measured object, RAT reports:

- **Added lines** and **removed lines** from Git numstat;
- **Growth** = added − removed;
- **Churn** = added + removed;
- **Modifications** = commits with positive churn for the object;
- **Modification frequency** = modifications / selected commit count;
- **Churn rate** = churn / selected commit count.

Directory metrics recursively include descendant files. Repository metrics are directory metrics at `/`. Author rows also report per-author additions, removals, growth, churn, modifications, and **ownership** = author churn / total object churn.

The analyzer follows the assessment definitions:

- merge commits are excluded;
- the root commit is compared with an empty tree;
- committer timestamps define date filters;
- Git rename detection uses a 50% threshold;
- changed renames are attributed to the new path;
- deleted paths remain represented;
- binary files are excluded from measured objects; and
- author identities use the `.mailmap` stored at the selected reference, when present; and
- user-defined author merges are applied non-destructively when querying metrics.

## Tests

Run the automated tests:

```bash
python3 -m unittest discover -s tests -v
```

Compare a local clone against an official reference CSV:

```bash
python3 tests/check_reference.py /path/to/repository /path/to/reference.csv
```

The checker resolves the CSV's fixed `ref_sha` and compares every repository, directory, file, and author row. Integer metrics must match exactly; floating-point metrics use a small tolerance.

## Error handling and safety

- URL inputs must use HTTP or HTTPS.
- Invalid or missing Git references are rejected.
- ZIP path traversal and symbolic links are rejected.
- Invalid archives and archives without `.git` are rejected.
- Unknown or ambiguous commit filters produce a visible error.

## Scope

This submission implements all metric categories, both ingestion methods, fixed-reference analysis, filtering, non-destructive author merging, and persistent multi-repository switching.
