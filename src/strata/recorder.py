"""Record committed source metrics without affecting commit success."""

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
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

EXTENSIONS = {
    "python": {".py", ".pyw"},
    "javascript": {".js", ".mjs", ".cjs"},
    "rust": {".rs"},
}
ANALYZER = "scb-check==0.2.0"
POLICY = "tracked-source-v1"
SETTINGS_FORMAT = 3
SCOPE = "separate test files excluded; inline tests retained"


def canonical_roots(roots: list[str]) -> list[str]:
    if not roots or any(
        not root or Path(root).is_absolute() or ".." in Path(root).parts
        for root in roots
    ):
        raise ValueError("Source roots must be relative paths within the repository")
    paths = {Path(root) for root in roots}
    return sorted(
        str(path) for path in paths if not any(p in paths for p in path.parents)
    )


def selection(settings: dict) -> dict:
    if settings.get("format") != SETTINGS_FORMAT:
        raise ValueError("Unsupported recorder settings; reinstall the hook")
    return {
        "source_roots": canonical_roots(settings["source_roots"]),
        "languages": list(EXTENSIONS),
        "python": settings["python"],
        "analyzer": ANALYZER,
        "inclusion_policy": POLICY,
        "scope": SCOPE,
    }


def spawn_worker(repo: Path, output: Path) -> None:
    environment = os.environ.copy()
    for name in git(repo, "rev-parse", "--local-env-vars").decode().splitlines():
        environment.pop(name, None)
    with (output / "worker.log").open("ab") as log:
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-m",
                "strata.worker",
                "--worker",
                "--output",
                str(output),
            ],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )


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
    python: str,
    commit: str | None = None,
    recording_id: str | None = None,
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
        "analyzer": ANALYZER,
        "language": language,
        "source_roots": roots,
        "scope": SCOPE,
        "inclusion_policy": POLICY,
        "report": str(directory.relative_to(output)),
        "status": "failed",
    }
    if recording_id is not None:
        row["recording_id"] = recording_id
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
            # Isolated mode keeps the measured repository off the import path.
            command = [
                python,
                "-I",
                "-m",
                "strata.analyze",
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
            versions_command = [
                python,
                "-I",
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
    # Git holds every recorded commit's source; keep a copy only to diagnose a failed scan.
    if row["status"] != "failed":
        shutil.rmtree(directory / "snapshot", ignore_errors=True)
    row["duration_seconds"] = round(time.monotonic() - started, 3)
    (directory / "summary.json").write_text(json.dumps(row, indent=2) + "\n")
    append_history(output, row)
    print(
        f"strata: {row['status']} for {commit[:12]} ({row['duration_seconds']:.1f}s); {output / 'history.jsonl'}",
        file=sys.stderr,
    )


def record_all(
    repo: Path,
    output: Path,
    roots: list[str],
    python: str,
    commit: str | None = None,
    languages: list[str] | None = None,
) -> None:
    """Record one committed selection without filling gaps from other attempts."""
    roots = canonical_roots(roots)
    commit = commit or git(repo, "rev-parse", "HEAD").decode().strip()
    languages = list(EXTENSIONS) if languages is None else languages
    output.mkdir(parents=True, exist_ok=True)
    started = {
        "recording_id": uuid.uuid4().hex,
        "repository": str(repo),
        "commit": commit,
        "source_roots": roots,
        "languages": languages,
        "analyzer": ANALYZER,
        "inclusion_policy": POLICY,
        "scope": SCOPE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "started",
    }
    append_history(output, started)
    for language in languages:
        try:
            record(
                repo,
                output,
                roots,
                language,
                python,
                commit=commit,
                recording_id=started["recording_id"],
            )
        except Exception as error:
            # Isolate unexpected failures so the remaining languages still run.
            traceback.print_exc()
            append_history(
                output,
                started
                | {"language": language, "status": "failed", "error": str(error)},
            )


def record_manual(repo: Path, output: Path, inputs: dict, commits: list[str]) -> None:
    """Record commits in order while holding the worker lock for the whole run."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / "worker.lock").open("a") as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Recorder busy; retry after the active recording finishes"
            ) from error
        try:
            for index, commit in enumerate(commits, 1):
                if len(commits) > 1:
                    print(
                        f"strata: [{index}/{len(commits)}] {commit[:12]}",
                        file=sys.stderr,
                    )
                record_all(
                    repo,
                    output,
                    inputs["source_roots"],
                    inputs["python"],
                    commit=commit,
                    languages=inputs["languages"],
                )
        finally:
            # Hooks can enqueue while a manual scan owns the worker lock.
            with (output / "queue.lock").open("a") as queue_lock:
                fcntl.flock(queue_lock, fcntl.LOCK_EX)
                fcntl.flock(worker_lock, fcntl.LOCK_UN)
                queue_path = output / "queue.json"
                if queue_path.exists() and json.loads(queue_path.read_text()).get(
                    "pending"
                ):
                    spawn_worker(repo, output)


def measured_commits(rows: list[dict], inputs: dict) -> set[str]:
    """Commits with a recording of this selection that finished every current language."""
    keys = ("source_roots", "analyzer", "inclusion_policy", "scope")
    finished = {"complete", "not_applicable"}
    recordings = {}
    for row in rows:
        identifier = row.get("recording_id")
        if identifier is None:
            continue
        if row["status"] == "started":
            if all(row.get(key) == inputs[key] for key in keys):
                recordings[identifier] = (row["commit"], set())
        elif identifier in recordings and row["status"] in finished:
            recordings[identifier][1].add(row["language"])
    expected = set(inputs["languages"])
    return {commit for commit, done in recordings.values() if expected <= done}


def backfill_commits(
    repo: Path, revision: str, since: str | None = None, every: int = 1
) -> list[str]:
    """First-parent history of a revision, newest first, sampled from the tip."""
    if every < 1:
        raise ValueError("--every must be a positive number of commits")
    command = ["rev-list", "--first-parent"]
    if since:
        command.append(f"--since={since}")
    commits = git(repo, *command, "--end-of-options", revision).decode().split()
    return commits[::every]


def exclude_output(repo: Path) -> None:
    """Keep local measurements out of Git status without editing .gitignore."""
    exclude = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
        .decode()
        .strip()
    )
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ""
    if "/.strata/" not in current.splitlines():
        with exclude.open("a") as stream:
            stream.write("\n/.strata/\n")


def recording_inputs(repo: Path, roots: list[str] | None) -> dict:
    """Use explicit roots with this interpreter, or the installed hook settings."""
    if roots:
        return selection(
            {
                "format": SETTINGS_FORMAT,
                "source_roots": canonical_roots(roots),
                "python": sys.executable,
            }
        )
    settings_path = repo / ".strata" / "settings.json"
    if not settings_path.exists():
        raise ValueError("Pass --source-root or install the hook first")
    return selection(json.loads(settings_path.read_text()))


def toplevel(repo: Path) -> Path:
    return Path(git(repo.resolve(), "rev-parse", "--show-toplevel").decode().strip())


def scan(repo: Path, revision: str, roots: list[str] | None) -> None:
    """Measure one revision with explicit roots or the installed hook settings."""
    repo = toplevel(repo)
    commit = (
        git(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
        .decode()
        .strip()
    )
    inputs = recording_inputs(repo, roots)
    exclude_output(repo)
    record_manual(repo, repo / ".strata", inputs, [commit])


def backfill(
    repo: Path,
    revision: str,
    roots: list[str] | None,
    since: str | None = None,
    every: int = 1,
) -> None:
    """Measure first-parent history that this selection has not finished."""
    repo = toplevel(repo)
    inputs = recording_inputs(repo, roots)
    output = repo / ".strata"
    commits = backfill_commits(repo, revision, since, every)
    history = output / "history.jsonl"
    rows = (
        [json.loads(line) for line in history.read_text().splitlines() if line.strip()]
        if history.exists()
        else []
    )
    measured = measured_commits(rows, inputs)
    pending = [commit for commit in commits if commit not in measured]
    print(
        f"strata: {len(pending)} of {len(commits)} commits need measuring",
        file=sys.stderr,
    )
    if pending:
        exclude_output(repo)
        record_manual(repo, output, inputs, pending)
