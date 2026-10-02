# /// script
# requires-python = ">=3.10"
# dependencies = ["plotly==6.3.1"]
# ///
"""Generate an offline dashboard from recorded commit measurements."""

import argparse
import fcntl
import json
import re
import tempfile
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


def load_dataset(repo: Path, output: Path, head: str | None = None) -> dict:
    """Keep scopes separate and follow the current first-parent history."""
    output = output.resolve()
    head = head or git(repo, "rev-parse", "HEAD").decode().strip()
    metadata = {}
    for line in (
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
    ):
        commit, date, subject = line.split("\x00", 2)
        metadata[commit] = {"date": date, "subject": subject, "order": len(metadata)}
    rows = read_history(output)
    scopes = {}
    events, warnings = [], []
    excluded = 0
    for row in rows:
        if row["commit"] not in metadata:
            excluded += 1
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
        )
        scope = scopes.setdefault(
            key,
            {
                "analyzer": row["analyzer"],
                "language": row["language"],
                "roots": list(key[2]),
                "policy": row.get("inclusion_policy"),
                "scope": row["scope"],
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
        "series": series,
        "events": events,
        "warnings": list(dict.fromkeys(warnings)),
        "excluded": excluded,
        "head": head,
    }


def render_dashboard(data: dict) -> str:
    """Embed the chart library and data for offline viewing."""
    assets = Path(__file__).resolve().parents[1] / "assets"
    template = (assets / "dashboard.html").read_text()
    replacements = {
        "__PLOTLY__": get_plotlyjs(),
        "__DATA__": embed_json(data),
        "__APP__": (assets / "dashboard.js").read_text(),
    }
    return re.sub(
        r"__PLOTLY__|__DATA__|__APP__", lambda match: replacements[match.group()], template
    )


def write_dashboard(data: dict, output: Path) -> Path:
    """Publish one self-contained HTML file after loading a consistent snapshot."""
    html = render_dashboard(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=output.parent, delete=False
    ) as stream:
        stream.write(html)
        temporary = Path(stream.name)
    temporary.replace(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    destination = args.output or repo / ".slop-check/dashboard.html"
    data = load_dataset(repo, repo / ".slop-check")
    print(write_dashboard(data, destination))


if __name__ == "__main__":
    main()
