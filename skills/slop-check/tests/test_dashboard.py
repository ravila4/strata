import json
import subprocess
from pathlib import Path

import pytest

import test_hooks

git, load, repo = test_hooks.git, test_hooks.load, test_hooks.repo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def details():
    return {
        "files": [{"path": "src/main.rs", "sloc": 3}],
        "functions": [
            {
                "path": "src/main.rs",
                "name": "main",
                "line": 1,
                "end_line": 3,
                "sloc": 3,
                "cc": 1,
                "cognitive": 0,
            }
        ],
    }


def aggregate():
    return {
        "files_scanned": 1,
        "total_loc": 3,
        "total_functions": 1,
        "total_mass": 3**0.5,
        "total_cog_mass": 0,
    }


def test_details_match_aggregate():
    load("record_commit").validate_details(details(), aggregate(), ["src/main.rs"])


@pytest.mark.parametrize(
    "mutation",
    [
        {"files": []},
        {"functions": []},
        {"files": [{"path": "../outside.rs", "sloc": 3}]},
        {
            "functions": [
                {
                    "path": "src/main.rs",
                    "name": "bad",
                    "line": 1,
                    "end_line": 3,
                    "sloc": 3,
                    "cc": 2,
                    "cognitive": 0,
                }
            ]
        },
    ],
)
def test_inconsistent_details_are_rejected(mutation):
    with pytest.raises(ValueError):
        load("record_commit").validate_details(
            details() | mutation, aggregate(), ["src/main.rs"]
        )


@pytest.mark.parametrize(
    "language,source",
    [
        ("rust", "fn decision(x: bool) -> i32 {\n if x { 1 } else { 0 }\n}\n"),
        ("python", "def decision(x):\n    if x:\n        return 1\n    return 0\n"),
        (
            "javascript",
            "function decision(x) {\n if (x) { return 1; }\n return 0;\n}\n",
        ),
    ],
)
def test_analyzer_details_preserve_cli_aggregate(tmp_path, language, source):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    extension = {"rust": ".rs", "python": ".py", "javascript": ".js"}[language]
    path = "decision" + extension
    (snapshot / path).write_text(source)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([path]))
    detail_path = tmp_path / "details.json"
    command = [
        "uvx",
        "--from",
        "scb-check==0.2.0",
        "python",
        str(SCRIPTS / "analyze_snapshot.py"),
        "--snapshot",
        str(snapshot),
        "--manifest",
        str(manifest),
        "--details",
        str(detail_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    config = tmp_path / "scb-check.toml"
    config.write_text("exclude = []\n")
    cli = subprocess.run(
        [
            "uvx",
            "scb-check==0.2.0",
            "check",
            str(snapshot),
            "--config",
            str(config),
            "--report",
            "--include-all",
        ],
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == json.loads(cli.stdout)
    measured = json.loads(detail_path.read_text())
    assert measured["files"][0]["verbosity_flagged_lines"] == sorted(
        set(measured["files"][0]["verbosity_flagged_lines"])
    )
    assert (
        len(measured["files"][0]["verbosity_flagged_lines"])
        == measured["files"][0]["verbosity_flagged_loc"]
    )
    assert measured["functions"][0]["cc"] == 2
    assert measured["functions"][0]["path"] == path
    assert (
        sum(file["verbosity_flagged_loc"] for file in measured["files"])
        == json.loads(cli.stdout)["verbosity_flagged_loc"]
    )
    assert (
        sum(file["clone_loc"] for file in measured["files"])
        == json.loads(cli.stdout)["clone_loc"]
    )


def row(commit, report, timestamp, policy="tracked-source-v1"):
    return {
        "commit": commit,
        "timestamp": timestamp,
        "report": report,
        "status": "complete",
        "source_roots": ["src"],
        "language": "rust",
        "analyzer": "scb-check==0.2.0",
        "scope": "inline tests retained",
        "inclusion_policy": policy,
        "metrics": {"total_loc": 3, "erosion": 0, "cog_erosion": 0},
    }


def test_dataset_deduplicates_measurements_in_commit_order(repo):
    first = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--allow-empty",
        "-qm",
        "second",
    )
    second = git(repo, "rev-parse", "HEAD")
    output = repo / ".slop-check"
    output.mkdir()
    rows = [
        row(second, "reports/second", "2026-10-03"),
        row(first, "reports/old", "2026-10-01"),
        row(first, "reports/new", "2026-10-02"),
    ]
    (output / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    data = load("dashboard").load_dataset(repo, output)
    assert [r["commit"] for r in data["series"][0]["snapshots"]] == [first, second]
    assert data["series"][0]["snapshots"][0]["report"] == "reports/new"
    assert data["series"][0]["snapshots"][0]["details"] is None


def test_dataset_separates_measurement_policies(repo):
    commit = git(repo, "rev-parse", "HEAD")
    output = repo / ".slop-check"
    output.mkdir()
    (output / "history.jsonl").write_text(
        "\n".join(
            json.dumps(row(commit, "reports/r", "2026-10-02", p)) for p in ["v1", "v2"]
        )
        + "\n"
    )
    assert len(load("dashboard").load_dataset(repo, output)["series"]) == 2


def test_unsafe_detail_reference_is_not_read(repo):
    output = repo / ".slop-check"
    output.mkdir()
    record = row(git(repo, "rev-parse", "HEAD"), "../outside", "2026-10-02")
    record["details"] = "../outside/details.json"
    (output / "history.jsonl").write_text(json.dumps(record) + "\n")
    data = load("dashboard").load_dataset(repo, output)
    assert data["series"][0]["snapshots"][0]["details"] is None
    assert data["warnings"]


def test_json_embedding_cannot_close_script():
    encoded = load("dashboard").embed_json(
        {"name": "</script><script>alert(1)</script>"}
    )
    assert "<" not in encoded
    assert json.loads(encoded)["name"].startswith("</script>")


@pytest.mark.parametrize(
    "field,value",
    [("verbosity_flagged_loc", 4), ("clone_loc", 2), ("verbosity_flagged_loc", True)],
)
def test_invalid_file_verbosity_counts_are_rejected(field, value):
    measured = details()
    measured["files"][0].update(verbosity_flagged_loc=1, clone_loc=1)
    measured["files"][0][field] = value
    report = aggregate() | {"verbosity_flagged_loc": 1, "clone_loc": 1}
    with pytest.raises(ValueError):
        load("record_commit").validate_details(measured, report, ["src/main.rs"])


def test_file_verbosity_totals_match_aggregate():
    measured = details()
    measured["files"][0].update(verbosity_flagged_loc=1, clone_loc=1)
    with pytest.raises(ValueError):
        load("record_commit").validate_details(
            measured,
            aggregate() | {"verbosity_flagged_loc": 2, "clone_loc": 1},
            ["src/main.rs"],
        )


@pytest.mark.parametrize("marker", ["__APP__", "__DATA__", "__PLOTLY__"])
def test_dashboard_preserves_template_markers_in_data(marker):
    data = {"repository": marker, "series": [], "subject": marker}
    html = load("dashboard").render_dashboard(data)
    embedded = html.split('<script id="data" type="application/json">', 1)[1].split(
        "</script>", 1
    )[0]
    assert json.loads(embedded) == data


@pytest.mark.parametrize("lines", [[0], [True], [1, 1], [2, 1], [1.5], [1, 2]])
def test_invalid_flagged_line_locations_are_rejected(lines):
    measured = details()
    measured["files"][0].update(
        verbosity_flagged_loc=1, clone_loc=0, verbosity_flagged_lines=lines
    )
    with pytest.raises(ValueError, match="flagged"):
        load("record_commit").validate_details(
            measured,
            aggregate() | {"verbosity_flagged_loc": 1, "clone_loc": 0},
            ["src/main.rs"],
        )
