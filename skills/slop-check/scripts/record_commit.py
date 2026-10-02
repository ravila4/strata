"""Record committed source metrics without affecting commit success."""

import argparse
import fcntl
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

EXTENSIONS = {
    "rust": {".rs"},
    "python": {".py", ".pyw"},
    "javascript": {".js", ".mjs", ".cjs"},
}
METRICS = (
    "total_loc",
    "verbosity",
    "erosion",
    "cog_erosion",
    "verbosity_flagged_loc",
    "clone_loc",
    "total_functions",
    "high_cc_functions",
    "high_cog_functions",
)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], timeout=30)


def export_snapshot(
    repo: Path, commit: str, output: Path, roots: list[str], language: str
) -> list[str]:
    """Export regular source blobs, never dirty worktree files or symlinks."""
    manifest = []
    tree = git(repo, "ls-tree", "-r", "-z", commit)
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        path = Path(os.fsdecode(raw_path))
        in_scope = any(
            path == Path(root) or Path(root) in path.parents for root in roots
        )
        test_file = (
            path.stem in {"test", "tests", "conftest"}
            or path.stem.startswith("test_")
            or path.stem.endswith(("_test", "_tests", ".test", ".spec"))
        )
        excluded = {
            "test",
            "tests",
            "__tests__",
            "fixtures",
            "vendor",
            "vendored",
            "generated",
            "node_modules",
        }
        if (
            mode not in {b"100644", b"100755"}
            or kind != b"blob"
            or not in_scope
            or path.suffix not in EXTENSIONS[language]
            or test_file
            or excluded.intersection(path.parts)
        ):
            continue
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(git(repo, "cat-file", "blob", object_id.decode()))
        manifest.append(path.as_posix())
    return manifest


def validate_report(
    report: dict, count: int, language: str, exit_code: int, stderr: str
) -> None:
    """Do not accept partial analyzer output as a measurement."""
    if exit_code not in {0, 1} or stderr.strip():
        raise ValueError(
            f"Analyzer diagnostics or failure (exit {exit_code}); see analyzer.stderr"
        )
    parsed = report.get("syntax_by_language", {}).get(language, {}).get("tree_count")
    if report.get("files_scanned") != count or parsed != count:
        raise ValueError(f"Incomplete scan: expected {count} files, parsed {parsed}")
    for name in METRICS:
        value = report.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"Invalid or missing metric: {name}")
        if name in {"verbosity", "erosion", "cog_erosion"} and value > 1:
            raise ValueError(f"Invalid ratio: {name}")


def run_tool(
    command: list[str],
    directory: Path,
    env: dict[str, str],
    stdout_name: str,
    stderr_name: str,
    timeout: float,
) -> int:
    """Bound uv resolution and analysis, including child processes."""
    with (
        (directory / stdout_name).open("wb") as stdout,
        (directory / stderr_name).open("wb") as stderr,
    ):
        with subprocess.Popen(
            command, stdout=stdout, stderr=stderr, env=env, start_new_session=True
        ) as process:
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise


def validate_details(details: dict, report: dict, manifest: list[str]) -> None:
    """Require detailed measurements to describe the accepted aggregate scan."""
    files, functions = details["files"], details["functions"]
    paths = [file["path"] for file in files]
    if len(paths) != len(set(paths)) or set(paths) != set(manifest):
        raise ValueError("Detailed file selection differs from source manifest")
    if (
        len(files) != report["files_scanned"]
        or sum(f["sloc"] for f in files) != report["total_loc"]
    ):
        raise ValueError("Detailed file SLOC differs from aggregate")
    if len(functions) != report["total_functions"]:
        raise ValueError("Detailed function count differs from aggregate")
    for file in files:
        if not isinstance(file["sloc"], int) or file["sloc"] < 0:
            raise ValueError("Invalid file SLOC")
    for file in files:
        if "verbosity_flagged_lines" in file:
            lines = file["verbosity_flagged_lines"]
            if (
                not isinstance(lines, list)
                or any(type(line) is not int or line < 1 for line in lines)
                or lines != sorted(set(lines))
                or len(lines) != file.get("verbosity_flagged_loc")
            ):
                raise ValueError("Invalid flagged line locations")
    if any("verbosity_flagged_loc" in file or "clone_loc" in file for file in files):
        for file in files:
            for key in ("verbosity_flagged_loc", "clone_loc"):
                value = file.get(key)
                if type(value) is not int or not 0 <= value <= file["sloc"]:
                    raise ValueError(f"Invalid file {key}")
            if file["clone_loc"] > file["verbosity_flagged_loc"]:
                raise ValueError("Clone lines exceed flagged union")
        for key in ("verbosity_flagged_loc", "clone_loc"):
            if sum(file[key] for file in files) != report[key]:
                raise ValueError(f"Detailed {key} differs from aggregate")
    for function in functions:
        if function["path"] not in paths or not isinstance(function["name"], str):
            raise ValueError("Invalid function identity")
        for key in ("sloc", "cc", "cognitive", "line", "end_line"):
            if not isinstance(function[key], int) or function[key] < 0:
                raise ValueError(f"Invalid function {key}")
        if function["line"] < 1 or function["end_line"] < function["line"]:
            raise ValueError("Invalid function span")
    for key, aggregate_key in (("cc", "total_mass"), ("cognitive", "total_cog_mass")):
        mass = sum(f[key] * math.sqrt(f["sloc"]) for f in functions)
        if not math.isclose(mass, report[aggregate_key], rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"Detailed {key} mass differs from aggregate")


def append_history(output: Path, row: dict) -> None:
    """Serialize append-only records from workers and enqueue processes."""
    with (output / "history.jsonl").open("a") as history:
        fcntl.flock(history, fcntl.LOCK_EX)
        history.write(json.dumps(row) + "\n")
        history.flush()


def record(
    repo: Path,
    output: Path,
    roots: list[str],
    language: str,
    uv: str,
    commit: str | None = None,
) -> None:
    """Append a complete or failed attempt, retaining raw evidence per run."""
    commit = commit or git(repo, "rev-parse", "HEAD").decode().strip()
    output.mkdir(parents=True, exist_ok=True)
    reports = output / "reports"
    reports.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f"{commit}-", dir=reports))
    started = time.monotonic()
    row = {
        "commit": commit,
        "repository": str(repo),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "analyzer": "scb-check==0.2.0",
        "language": language,
        "source_roots": roots,
        "scope": "separate test files excluded; inline tests retained",
        "inclusion_policy": "tracked-source-v1",
        "report": str(directory.relative_to(output)),
        "status": "failed",
    }
    try:
        commit_date, subject = (
            git(repo, "show", "-s", "--format=%cI%x00%s", commit)
            .decode()
            .strip()
            .split("\x00", 1)
        )
        row.update(commit_date=commit_date, subject=subject)
        snapshot = directory / "snapshot"
        snapshot.mkdir()
        manifest = export_snapshot(repo, commit, snapshot, roots, language)
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        row["files_selected"] = len(manifest)
        if not manifest:
            row["status"] = "not_applicable"
        else:
            config = directory / "scb-check.toml"
            config.write_text("exclude = []\n")
            command = [
                uv,
                "--from",
                "scb-check==0.2.0",
                "python",
                str(Path(__file__).with_name("analyze_snapshot.py")),
                "--snapshot",
                str(snapshot),
                "--manifest",
                str(directory / "manifest.json"),
                "--details",
                str(directory / "details.json"),
            ]
            (directory / "command.json").write_text(json.dumps(command) + "\n")
            env = os.environ.copy()
            env.pop("SCB_CHECK_EXTRA_SLOP_RULES", None)
            # Populate the uv environment first so install chatter cannot mask analyzer diagnostics.
            versions_command = [
                uv,
                "--from",
                "scb-check==0.2.0",
                "python",
                "-c",
                'import importlib.metadata as m,json; print(json.dumps({d.metadata["Name"]:d.version for d in m.distributions()}))',
            ]
            setup_code = run_tool(
                versions_command,
                directory,
                env,
                "tool-versions.json",
                "tool-setup.stderr",
                60,
            )
            if setup_code != 0:
                raise ValueError(
                    f"Tool setup failed (exit {setup_code}); see tool-setup.stderr"
                )
            code = run_tool(
                command, directory, env, "analyzer.json", "analyzer.stderr", 120
            )
            row["exit_code"] = code
            report = json.loads((directory / "analyzer.json").read_text())
            validate_report(
                report,
                len(manifest),
                language,
                code,
                (directory / "analyzer.stderr").read_text(),
            )
            row["status"] = "complete"
            row["metrics"] = {name: report[name] for name in METRICS}
            validate_details(
                json.loads((directory / "details.json").read_text()), report, manifest
            )
            row["details"] = str((directory / "details.json").relative_to(output))
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        row["status"] = "failed"
        row.pop("metrics", None)
        row["error"] = str(error)
        (directory / "error.txt").write_text(str(error) + "\n")
    row["duration_seconds"] = round(time.monotonic() - started, 3)
    (directory / "summary.json").write_text(json.dumps(row, indent=2) + "\n")
    append_history(output, row)
    print(
        f"slop-check: {row['status']} for {commit[:12]} ({row['duration_seconds']:.1f}s); {output / 'history.jsonl'}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-root", action="append")
    parser.add_argument("--language", choices=list(EXTENSIONS))
    parser.add_argument("--commit", default="HEAD")
    args = parser.parse_args()
    if bool(args.source_root) != bool(args.language):
        parser.error("--source-root and --language must be supplied together")
    repo = Path(
        git(args.repo.resolve(), "rev-parse", "--show-toplevel").decode().strip()
    )
    commit = (
        git(repo, "rev-parse", "--verify", f"{args.commit}^{{commit}}").decode().strip()
    )
    output = repo / ".slop-check"
    if args.source_root:
        for root in args.source_root:
            if not root or Path(root).is_absolute() or ".." in Path(root).parts:
                parser.error(
                    "Source roots must be relative paths within the repository"
                )
        uvx = shutil.which("uvx")
        if uvx is None:
            parser.error("uvx is required; install uv before recording")
        roots, language = args.source_root, args.language
    else:
        settings = json.loads((output / "settings.json").read_text())
        roots, language, uvx = (
            settings["source_roots"],
            settings["language"],
            settings["uvx"],
        )
    exclude = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
        .decode()
        .strip()
    )
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ""
    if "/.slop-check/" not in current.splitlines():
        with exclude.open("a") as stream:
            stream.write("\n/.slop-check/\n")
    record(repo, output, roots, language, uvx, commit=commit)


if __name__ == "__main__":
    main()
