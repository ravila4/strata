import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.rs").write_text("fn main() {}\n")
    (tmp_path / "src/tests.rs").write_text("not production\n")
    (tmp_path / "src/link.rs").symlink_to("/etc/passwd")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "initial")
    return tmp_path


def test_export_uses_commit_even_when_worktree_is_dirty(repo, tmp_path):
    recorder = load("record_commit")
    (repo / "src/main.rs").write_text("dirty\n")
    output = tmp_path / "snapshot"
    manifest = recorder.export_snapshot(
        repo, git(repo, "rev-parse", "HEAD"), output, ["src"], "rust"
    )
    assert manifest == ["src/main.rs"]
    assert (output / "src/main.rs").read_text() == "fn main() {}\n"


def report():
    return dict(
        files_scanned=1,
        syntax_by_language={"rust": {"tree_count": 1}},
        total_loc=3,
        verbosity=0.0,
        erosion=0.0,
        cog_erosion=0.0,
        verbosity_flagged_loc=0,
        clone_loc=0,
        total_functions=1,
        high_cc_functions=0,
        high_cog_functions=0,
        total_mass=3**0.5,
        total_cog_mass=0,
    )


def test_findings_exit_is_accepted():
    load("record_commit").validate_report(report(), 1, "rust", 1, "")


@pytest.mark.parametrize(
    "mutation,stderr,code",
    [
        ({"files_scanned": 0}, "", 0),
        ({"syntax_by_language": {"rust": {"tree_count": 0}}}, "", 0),
        ({}, "parser failed", 0),
        ({}, "", 2),
        ({"erosion": float("nan")}, "", 0),
        ({"verbosity": 2.0}, "", 0),
    ],
)
def test_incomplete_report_is_rejected(mutation, stderr, code):
    data = report() | mutation
    with pytest.raises(ValueError):
        load("record_commit").validate_report(data, 1, "rust", code, stderr)


def test_install_preserves_original_hook_location(repo, tmp_path):
    old_hooks = tmp_path / "shared-hooks"
    old_hooks.mkdir()
    (old_hooks / "resource").write_text("original")
    hook = old_hooks / "pre-commit"
    hook.write_text('#!/bin/sh\ncat "$(dirname "$0")/resource" > preserved\n')
    hook.chmod(0o755)
    git(repo, "config", "--local", "core.hooksPath", str(old_hooks))
    installer = load("install_hook")
    installer.install(repo, ["src"], "rust")
    installer.install(repo, ["src"], "rust")
    hooks = Path(git(repo, "config", "core.hooksPath"))
    subprocess.run([str(hooks / "pre-commit")], cwd=repo, check=True)
    assert (repo / "preserved").read_text() == "original"
    state = json.loads((repo / ".slop-check/settings.json").read_text())
    assert state["previous_local_hooks_path"] == str(old_hooks)
    assert (
        git(repo, "check-ignore", ".slop-check/settings.json")
        == ".slop-check/settings.json"
    )


def test_commit_succeeds_when_recorder_fails(repo):
    load("install_hook").install(repo, ["src"], "rust")
    (repo / ".slop-check/record_commit.py").write_text(
        'raise RuntimeError("analyzer unavailable")\n'
    )
    result = subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "advisory"],
        capture_output=True,
    )
    assert result.returncode == 0
    assert git(repo, "log", "-1", "--format=%s") == "advisory"


def test_failed_analysis_is_logged_without_metrics(repo):
    recorder = load("record_commit")
    output = repo / "output"
    recorder.record(repo, output, ["src"], "rust", "/missing/uv")
    row = json.loads((output / "history.jsonl").read_text())
    assert row["status"] == "failed"
    assert "metrics" not in row
    assert row["commit"] == git(repo, "rev-parse", "HEAD")


def test_empty_scope_is_not_a_zero_score(repo):
    recorder = load("record_commit")
    output = repo / "output"
    recorder.record(repo, output, ["missing"], "rust", "/missing/uv")
    row = json.loads((output / "history.jsonl").read_text())
    assert row["status"] == "not_applicable"
    assert "metrics" not in row


def test_linked_worktree_commit_records_its_own_head(repo, tmp_path):
    load("install_hook").install(repo, ["src"], "python")
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    git(linked, "commit", "--allow-empty", "-qm", "linked commit")
    wait_for(lambda: (repo / ".slop-check/history.jsonl").exists())
    row = json.loads((repo / ".slop-check/history.jsonl").read_text())
    assert row["commit"] == git(linked, "rev-parse", "HEAD")
    assert row["commit"] != git(repo, "rev-parse", "HEAD")


def test_installer_rejects_linked_worktree(repo, tmp_path):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    with pytest.raises(ValueError, match="primary"):
        load("install_hook").install(linked, ["src"], "rust")


def test_setup_failure_retains_raw_diagnostics(repo, tmp_path):
    uv = tmp_path / "uvx"
    uv.write_text("#!/bin/sh\necho setup-output\necho setup-error >&2\nexit 2\n")
    uv.chmod(0o755)
    output = repo / "output"
    load("record_commit").record(repo, output, ["src"], "rust", str(uv))
    row = json.loads((output / "history.jsonl").read_text())
    directory = output / row["report"]
    assert (directory / "tool-setup.stderr").read_text() == "setup-error\n"
    assert (directory / "tool-versions.json").read_text() == "setup-output\n"


def test_timeout_retains_output(tmp_path):
    command = [
        sys.executable,
        "-c",
        'import time; print("before timeout", flush=True); time.sleep(10)',
    ]
    with pytest.raises(subprocess.TimeoutExpired):
        load("record_commit").run_tool(
            command, tmp_path, os.environ.copy(), "out", "err", 0.1
        )
    assert (tmp_path / "out").read_text() == "before timeout\n"


def test_record_can_measure_a_fixed_commit_after_head_changes(repo):
    original = git(repo, "rev-parse", "HEAD")
    git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--allow-empty",
        "-qm",
        "newer",
    )
    output = repo / "output"
    load("record_commit").record(
        repo, output, ["missing"], "rust", "/missing/uv", commit=original
    )
    assert json.loads((output / "history.jsonl").read_text())["commit"] == original


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("Background worker did not reach the expected state")


def test_background_finishes_current_then_measures_newest_pending(repo, tmp_path):
    load("install_hook").install(repo, ["src"], "rust")
    output = repo / ".slop-check"
    analyzer = tmp_path / "uvx"
    analyzer.write_text(f"""#!{sys.executable}
import json, pathlib, sys, time
output = pathlib.Path({str(output)!r})
if '-c' in sys.argv:
    print('{{"scb-check":"0.2.0"}}')
else:
    snapshot = pathlib.Path(sys.argv[sys.argv.index('--snapshot') + 1])
    commit = snapshot.parent.name.split('-')[0]
    with (output / 'started').open('a') as stream:
        stream.write(commit + '\\n')
    deadline = time.monotonic() + 10
    while not (output / 'release').exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    detail = pathlib.Path(sys.argv[sys.argv.index('--details') + 1])
    detail.write_text(json.dumps({{"files":[{{"path":"src/main.rs","sloc":3}}],"functions":[{{"path":"src/main.rs","name":"main","line":1,"end_line":3,"sloc":3,"cc":1,"cognitive":0}}]}}))
    print({json.dumps(json.dumps(report()))})
""")
    analyzer.chmod(0o755)
    settings = json.loads((output / "settings.json").read_text())
    settings["uvx"] = str(analyzer)
    (output / "settings.json").write_text(json.dumps(settings))
    queue = output / "queue_commit.py"
    command = [sys.executable, str(queue), "--repo", str(repo), "--output", str(output)]
    subprocess.run(command, check=True)
    first = git(repo, "rev-parse", "HEAD")
    wait_for(lambda: (output / "started").exists())
    try:
        started = time.monotonic()
        git(repo, "commit", "--allow-empty", "-qm", "middle")
        middle = git(repo, "rev-parse", "HEAD")
        git(repo, "commit", "--allow-empty", "-qm", "newest")
        newest = git(repo, "rev-parse", "HEAD")
        assert time.monotonic() - started < 3
        assert (output / "started").read_text().splitlines() == [first]
    finally:
        (output / "release").touch()

    def completed():
        rows = [
            json.loads(line)
            for line in (output / "history.jsonl").read_text().splitlines()
        ]
        return [r["commit"] for r in rows if r["status"] == "complete"] == [
            first,
            newest,
        ]

    wait_for(completed)
    rows = [
        json.loads(line) for line in (output / "history.jsonl").read_text().splitlines()
    ]
    assert [r["commit"] for r in rows if r["status"] == "skipped"] == [middle]
    assert (output / "started").read_text().splitlines() == [first, newest]
    wait_for(
        lambda: json.loads((output / "queue.json").read_text()).get("current") is None
    )
    git(repo, "commit", "--allow-empty", "-qm", "restart")
    restart = git(repo, "rev-parse", "HEAD")
    wait_for(
        lambda: (
            (output / "started").read_text().splitlines() == [first, newest, restart]
        )
    )
