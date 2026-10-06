from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from analyzer import AnalysisError, analyze_repository
from catalog import AnalysisCatalog
from repository import RepositoryError, clone_repository, extract_repository
from store import MetricStore


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ANALYSES_DIR = DATA_DIR / "analyses"
CATALOG_PATH = DATA_DIR / "catalog.sqlite"

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=200 * 1024 * 1024,
    SECRET_KEY="rat-local-dashboard",
)


def parse_utc_datetime(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date and time: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def parse_object_filter(value: str) -> tuple[str, str] | None:
    if not value:
        return None
    object_type, separator, path = value.partition(":")
    if not separator or object_type not in {"repository", "directory", "file"} or not path:
        raise ValueError("The selected file or directory is invalid.")
    return object_type, path


def parse_commit_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", value.strip()) if token]


@app.template_filter("integer")
def integer(value) -> str:
    return f"{int(value):,}"


@app.template_filter("decimal")
def decimal(value) -> str:
    return f"{float(value):,.4f}".rstrip("0").rstrip(".")


@app.template_filter("percent")
def percent(value) -> str:
    return f"{float(value) * 100:.1f}%"


@app.template_filter("utcdate")
def utcdate(value) -> str:
    return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@app.get("/")
def index():
    catalog = AnalysisCatalog(CATALOG_PATH)
    analyses = catalog.list()
    requested_repository = request.args.get("repository", "")
    active_analysis = catalog.get(requested_repository)
    if active_analysis is None and analyses:
        active_analysis = analyses[0]
    has_analysis = bool(
        active_analysis and Path(active_analysis["database_path"]).is_file()
    )
    store = MetricStore(active_analysis["database_path"]) if has_analysis else None
    metadata = store.metadata() if store else {}
    options = store.filter_options() if store else {
        "authors": [],
        "raw_authors": [],
        "author_merges": [],
        "objects": [],
        "commits": [],
    }
    filters = {
        "repository": active_analysis["id"] if active_analysis else "",
        "author": request.args.get("author", ""),
        "object": request.args.get("object", ""),
        "start": request.args.get("start", ""),
        "end": request.args.get("end", ""),
        "commits": request.args.get("commits", ""),
    }
    metrics = None
    filter_error = None
    if store:
        try:
            commit_tokens = parse_commit_tokens(filters["commits"])
            commit_shas = None
            if commit_tokens:
                commit_shas, invalid = store.resolve_commit_tokens(commit_tokens)
                if invalid:
                    raise ValueError(
                        "Unknown or ambiguous commit: " + ", ".join(invalid[:5])
                    )
            metrics = store.query_metrics(
                start=parse_utc_datetime(filters["start"]),
                end=parse_utc_datetime(filters["end"]),
                commit_shas=commit_shas,
                object_filter=parse_object_filter(filters["object"]),
                author_filter=filters["author"] or None,
                limit=400,
            )
        except ValueError as exc:
            filter_error = str(exc)
            metrics = store.query_metrics(limit=400)
    return render_template(
        "index.html",
        has_analysis=has_analysis,
        analyses=analyses,
        active_analysis=active_analysis,
        metadata=metadata,
        options=options,
        filters=filters,
        metrics=metrics,
        filter_error=filter_error,
    )


@app.post("/analyze")
def analyze():
    source_type = request.form.get("source_type", "")
    reference = request.form.get("reference", "HEAD").strip() or "HEAD"
    analysis_database: Path | None = None
    try:
        if source_type == "url":
            url = request.form.get("repository_url", "").strip()
            if not url:
                raise RepositoryError("Enter a repository URL.")
            repository_path = clone_repository(url, DATA_DIR / "imports")
            display_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        elif source_type == "zip":
            archive = request.files.get("repository_zip")
            if archive is None or not archive.filename:
                raise RepositoryError("Choose a repository ZIP file.")
            if not archive.filename.lower().endswith(".zip"):
                raise RepositoryError("The uploaded repository must be a ZIP file.")
            repository_path = extract_repository(archive, DATA_DIR / "imports")
            display_name = Path(archive.filename).stem
        else:
            raise RepositoryError("Choose URL cloning or ZIP upload.")

        ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
        analysis_database = ANALYSES_DIR / f"{uuid.uuid4().hex}.sqlite"
        result = analyze_repository(
            repository_path,
            analysis_database,
            reference=reference,
            display_name=display_name,
        )
        analysis_id = AnalysisCatalog(CATALOG_PATH).register(result, analysis_database)
        flash(
            f"Analyzed {result['commit_count']:,} commits at {str(result['ref_sha'])[:12]}.",
            "success",
        )
        return redirect(url_for("index", repository=analysis_id))
    except (RepositoryError, AnalysisError) as exc:
        if analysis_database:
            analysis_database.unlink(missing_ok=True)
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/authors/merge")
def merge_authors():
    analysis_id = request.form.get("repository", "")
    analysis = AnalysisCatalog(CATALOG_PATH).get(analysis_id)
    if analysis is None:
        flash("Choose a stored repository analysis first.", "error")
        return redirect(url_for("index"))
    try:
        MetricStore(analysis["database_path"]).merge_authors(
            request.form.getlist("source_authors"),
            request.form.get("canonical_author", ""),
        )
        flash("Author identities were merged for this repository.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("index", repository=analysis_id))


@app.post("/authors/reset")
def reset_authors():
    analysis_id = request.form.get("repository", "")
    analysis = AnalysisCatalog(CATALOG_PATH).get(analysis_id)
    if analysis is None:
        flash("Choose a stored repository analysis first.", "error")
        return redirect(url_for("index"))
    MetricStore(analysis["database_path"]).clear_author_merges()
    flash("Author identity merges were cleared.", "success")
    return redirect(url_for("index", repository=analysis_id))


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    flash("The ZIP exceeds the 200 MB upload limit.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False)
