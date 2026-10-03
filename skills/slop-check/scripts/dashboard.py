"""Load recorded commit measurements and render the live dashboard."""

import fcntl
import hashlib
import json
import re
from pathlib import Path

from plotly.offline import get_plotlyjs

from record_commit import git, validate_details


def embed_json(value: object) -> str:
    """Keep untrusted source names inside the embedded JSON script."""
    return (
        json.dumps(value, ensure_ascii=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def read_history(output: Path) -> list[dict]:
    path = output / "history.jsonl"
    if not path.exists():
        return []
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        return [json.loads(line) for line in stream if line.strip()]


def load_dataset(
    repo: Path, output: Path, head: str | None = None, commits: list[str] | None = None
) -> dict:
    """Keep scopes separate within selected commits or first-parent history."""
    output = output.resolve()
    head = (
        commits[-1]
        if commits
        else head or git(repo, "rev-parse", "HEAD").decode().strip()
    )
    metadata = {}
    if commits:
        lines = [
            git(repo, "show", "-s", "--format=%H%x00%cI%x00%s", commit).decode().strip()
            for commit in commits
        ]
    else:
        lines = (
            git(
                repo,
                "log",
                "--first-parent",
                "--reverse",
                "--format=%H%x00%cI%x00%s",
                head,
            )
            .decode()
            .splitlines()
        )
    for line in lines:
        commit, date, subject = line.split("\x00", 2)
        metadata[commit] = {"date": date, "subject": subject, "order": len(metadata)}
    rows = read_history(output)
    recordings = {}
    attempts = {}
    for row in rows:
        if row["commit"] not in metadata:
            continue
        if row["status"] == "started":
            key = (
                row["commit"],
                row["analyzer"],
                tuple(row["source_roots"]),
                row["inclusion_policy"],
                row["scope"],
            )
            recording = (
                row
                | metadata[row["commit"]]
                | {"results": dict.fromkeys(row["languages"], "missing")}
            )
            recordings[key] = recording
            attempts[row["recording_id"]] = recording
        elif row.get("recording_id") in attempts and row.get("language"):
            attempts[row["recording_id"]]["results"][row["language"]] = row["status"]
    selected_ids = {r["recording_id"] for r in recordings.values()}
    scopes = {}
    events, warnings = [], []
    excluded = 0
    for row in rows:
        if row["commit"] not in metadata:
            excluded += 1
            continue
        if row.get("recording_id") and row["recording_id"] not in selected_ids:
            continue
        if row["status"] == "started":
            continue
        if row["status"] != "complete":
            events.append(row | metadata[row["commit"]])
            continue
        key = (
            row["analyzer"],
            row["language"],
            tuple(sorted(row["source_roots"])),
            row.get("inclusion_policy"),
            row["scope"],
            "mixed" if row.get("recording_id") else "individual",
        )
        scope = scopes.setdefault(
            key,
            {
                "id": hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16],
                "analyzer": row["analyzer"],
                "language": row["language"],
                "roots": list(key[2]),
                "policy": row.get("inclusion_policy"),
                "scope": row["scope"],
                "recording_mode": key[5],
                "by_commit": {},
            },
        )
        point = row | metadata[row["commit"]] | {"details": None}
        if row.get("details"):
            detail_path = (output / row["details"]).resolve()
            report_path = (output / row["report"]).resolve()
            try:
                if not detail_path.is_relative_to(
                    output
                ) or not report_path.is_relative_to(output):
                    raise ValueError(
                        "Artifact reference escapes local history directory"
                    )
                details = json.loads(detail_path.read_text())
                aggregate = json.loads((report_path / "analyzer.json").read_text())
                manifest = json.loads((report_path / "manifest.json").read_text())
                validate_details(details, aggregate, manifest)
                point["details"] = details
            except (OSError, ValueError, KeyError, TypeError) as error:
                warnings.append(f"{row['commit'][:12]}: details unavailable ({error})")
        else:
            warnings.append(
                f"{row['commit'][:12]}: file/function details were not recorded"
            )
        scope["by_commit"][row["commit"]] = point
    series = []
    for scope in scopes.values():
        by_commit = scope.pop("by_commit")
        scope["snapshots"] = sorted(
            by_commit.values(), key=lambda point: point["order"]
        )
        series.append(scope)
    return {
        "repository": repo.name,
        "repository_path": str(repo.resolve()),
        "series": series,
        "events": events,
        "warnings": list(dict.fromkeys(warnings)),
        "excluded": excluded,
        "head": head,
        "requested_commits": commits or [],
        "recordings": sorted(recordings.values(), key=lambda r: r["order"]),
    }


def render_dashboard(data: dict) -> str:
    """Embed local dashboard assets and the initial dataset."""
    assets = Path(__file__).resolve().parents[1] / "assets"
    template = (assets / "dashboard.html").read_text()
    replacements = {
        "__PLOTLY__": get_plotlyjs(),
        "__DATA__": embed_json(data),
        "__APP__": (assets / "dashboard.js").read_text(),
        "__HIGHLIGHT__": (assets / "vendor/highlight.min.js").read_text(),
        "__SOURCE_ICON__": (assets / "vendor/code.svg").read_text(),
    }
    return re.sub(
        r"__PLOTLY__|__DATA__|__APP__|__HIGHLIGHT__|__SOURCE_ICON__",
        lambda match: replacements[match.group()],
        template,
    )
