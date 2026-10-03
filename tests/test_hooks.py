import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from strata import hooks, recorder


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
    recorder.validate_report(report(), 1, "rust", 1, "")


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
        recorder.validate_report(data, 1, "rust", code, stderr)


def test_install_preserves_original_hook_location(repo, tmp_path):
    old_hooks = tmp_path / "shared-hooks"
    old_hooks.mkdir()
    (old_hooks / "resource").write_text("original")
    hook = old_hooks / "pre-commit"
    hook.write_text('#!/bin/sh\ncat "$(dirname "$0")/resource" > preserved\n')
    hook.chmod(0o755)
    git(repo, "config", "--local", "core.hooksPath", str(old_hooks))
    hooks.install(repo, ["src"])
    hooks.install(repo, ["src"])
    installed = Path(git(repo, "config", "core.hooksPath"))
    subprocess.run([str(installed / "pre-commit")], cwd=repo, check=True)
    assert (repo / "preserved").read_text() == "original"
    state = json.loads((repo / ".strata/settings.json").read_text())
    assert state["previous_local_hooks_path"] == str(old_hooks)
    assert git(repo, "check-ignore", ".strata/settings.json") == ".strata/settings.json"


def test_missing_interpreter_logs_without_blocking_commit(repo, monkeypatch):
    monkeypatch.setattr(hooks.sys, "executable", "/missing/strata's python")
    hooks.install(repo, ["src"])
    result = subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "advisory"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    diagnostic = (repo / ".strata/worker.log").read_text()
    assert "interpreter missing: /missing/strata's python" in diagnostic
    assert diagnostic.endswith("\n")


def test_hook_runs_installed_package_without_copying_code(repo):
    hooks.install(repo, ["src"])
    output = repo / ".strata"
    settings = json.loads((output / "settings.json").read_text())
    assert settings["python"] == sys.executable
    assert "-I -m strata.worker" in (output / "hooks/post-commit").read_text()
    assert not list(output.rglob("*.py"))


def test_hook_removal_restores_original_configuration_and_keeps_data(repo):
    hooks.install(repo, ["src"])
    output = repo / ".strata"
    (output / "history.jsonl").write_text("retained\n")
    hooks.remove(repo)
    assert (output / "history.jsonl").read_text() == "retained\n"
    assert not (output / "hooks").exists()
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
            check=False,
        ).returncode
        == 1
    )


def test_hook_removal_refuses_changed_configuration(repo):
    hooks.install(repo, ["src"])
    git(repo, "config", "--local", "core.hooksPath", "/changed")
    with pytest.raises(ValueError, match="changed"):
        hooks.remove(repo)


def test_hook_accepts_owned_server_first_installation(repo):
    import hashlib

    output = repo / ".strata"
    output.mkdir()
    (output / "server-settings.json").write_text(
        json.dumps(
            {
                "label": "dev.strata."
                + hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:12],
                "port": 18769,
                "mount": "/slop/test",
                "tailscale": False,
            }
        )
    )
    hooks.install(repo, ["src"])
    assert (output / "server-settings.json").exists()


def test_hook_rejects_foreign_server_first_installation(repo):
    output = repo / ".strata"
    output.mkdir()
    (output / "server-settings.json").write_text(json.dumps({"label": "foreign"}))
    with pytest.raises(ValueError, match="not owned"):
        hooks.install(repo, ["src"])


def test_failed_analysis_is_logged_without_metrics(repo):
    output = repo / "output"
    recorder.record(repo, output, ["src"], "rust", "/missing/uv")
    row = json.loads((output / "history.jsonl").read_text())
    assert row["status"] == "failed"
    assert "metrics" not in row
    assert row["commit"] == git(repo, "rev-parse", "HEAD")


def test_empty_scope_is_not_a_zero_score(repo):
    output = repo / "output"
    recorder.record(repo, output, ["missing"], "rust", "/missing/uv")
    row = json.loads((output / "history.jsonl").read_text())
    assert row["status"] == "not_applicable"
    assert "metrics" not in row
    assert not (output / row["report"] / "snapshot").exists()


def fake_analyzer(tmp_path, exit_code=0):
    analyzer = tmp_path / "python"
    analyzer.write_text(f"""#!{sys.executable}
import json, pathlib, sys
if '-c' in sys.argv:
    print('{{"scb-check":"0.2.0"}}')
else:
    detail = pathlib.Path(sys.argv[sys.argv.index('--details') + 1])
    detail.write_text(json.dumps({{"files":[{{"path":"src/main.rs","sloc":3}}],"functions":[{{"path":"src/main.rs","name":"main","line":1,"end_line":3,"sloc":3,"cc":1,"cognitive":0}}]}}))
    print({json.dumps(json.dumps(report()))})
    sys.exit({exit_code})
""")
    analyzer.chmod(0o755)
    return str(analyzer)


def test_complete_scan_discards_source_copy(repo, tmp_path):
    output = repo / "output"
    recorder.record(repo, output, ["src"], "rust", fake_analyzer(tmp_path))
    row = json.loads((output / "history.jsonl").read_text())
    assert row["status"] == "complete"
    assert not (output / row["report"] / "snapshot").exists()
    assert (output / row["report"] / "manifest.json").exists()


def test_failed_scan_keeps_source_copy_for_diagnosis(repo, tmp_path):
    output = repo / "output"
    recorder.record(repo, output, ["src"], "rust", fake_analyzer(tmp_path, exit_code=2))
    row = json.loads((output / "history.jsonl").read_text())
    assert row["status"] == "failed"
    snapshot = output / row["report"] / "snapshot"
    assert (snapshot / "src/main.rs").read_text() == "fn main() {}\n"


def test_linked_worktree_commit_records_its_own_head(repo, tmp_path):
    hooks.install(repo, ["src"])
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    git(linked, "commit", "--allow-empty", "-qm", "linked commit")
    history = repo / ".strata/history.jsonl"
    wait_for(lambda: history.exists() and len(history.read_text().splitlines()) == 4)
    row = json.loads(history.read_text().splitlines()[-1])
    assert row["commit"] == git(linked, "rev-parse", "HEAD")
    assert row["commit"] != git(repo, "rev-parse", "HEAD")


def test_installer_rejects_linked_worktree(repo, tmp_path):
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    with pytest.raises(ValueError, match="primary"):
        hooks.install(linked, ["src"])


def test_setup_failure_retains_raw_diagnostics(repo, tmp_path):
    uv = tmp_path / "python"
    uv.write_text("#!/bin/sh\necho setup-output\necho setup-error >&2\nexit 2\n")
    uv.chmod(0o755)
    output = repo / "output"
    recorder.record(repo, output, ["src"], "rust", str(uv))
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
        recorder.run_tool(command, tmp_path, os.environ.copy(), "out", "err", 0.1)
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
    recorder.record(repo, output, ["missing"], "rust", "/missing/uv", commit=original)
    assert json.loads((output / "history.jsonl").read_text())["commit"] == original


def test_recorder_cli_measures_revision_without_installing_hooks(repo):
    commit = git(repo, "rev-parse", "HEAD")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strata",
            "scan",
            "--repo",
            str(repo),
            "--source-root",
            "src",
            "HEAD",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rows = [
        json.loads(line)
        for line in (repo / ".strata/history.jsonl").read_text().splitlines()
    ]
    assert {row["commit"] for row in rows} == {commit}
    assert [row["status"] for row in rows] == [
        "started",
        "not_applicable",
        "not_applicable",
        "complete",
    ]
    assert not (repo / ".strata/settings.json").exists()
    assert ".strata/" not in git(repo, "status", "--short")
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True,
        ).returncode
        == 1
    )


def test_recorder_cli_rejects_removed_language_selection(repo):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strata",
            "scan",
            "--repo",
            str(repo),
            "--source-root",
            "src",
            "--language",
            "rust",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert not (repo / ".strata").exists()


def test_hook_install_preserves_one_off_recording(repo):
    output = repo / ".strata"
    recorder.record(repo, output, ["src"], "python", "/missing/uv")
    history = (output / "history.jsonl").read_bytes()
    hooks.install(repo, ["src"])
    assert (output / "history.jsonl").read_bytes() == history
    assert git(repo, "config", "--local", "core.hooksPath") == str(output / "hooks")


def test_hook_install_preserves_recording_with_server_logs(repo):
    output = repo / ".strata"
    recorder.record(repo, output, ["src"], "python", "/missing/uv")
    history = (output / "history.jsonl").read_bytes()
    for name in ("server.stdout.log", "server.stderr.log"):
        (output / name).write_text("server output\n")
    hooks.install(repo, ["src"])
    assert (output / "history.jsonl").read_bytes() == history
    for name in ("server.stdout.log", "server.stderr.log"):
        assert (output / name).read_text() == "server output\n"


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_hook_install_rejects_nonregular_server_logs(repo, kind):
    output = repo / ".strata"
    recorder.record(repo, output, ["src"], "python", "/missing/uv")
    log = output / "server.stderr.log"
    if kind == "directory":
        log.mkdir()
    else:
        log.symlink_to(output / "history.jsonl")
    with pytest.raises(ValueError, match="not owned"):
        hooks.install(repo, ["src"])


@pytest.mark.parametrize("root", ["", "/src", "../src"])
def test_recorder_cli_rejects_roots_outside_repository(repo, root):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "strata",
            "scan",
            "--repo",
            str(repo),
            "--source-root",
            root,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Source roots must be relative paths" in result.stderr
    assert not (repo / ".strata").exists()


@pytest.mark.parametrize("mutation", ["unknown", "foreign", "invalid", "symlink"])
def test_hook_install_rejects_unowned_recording_directory(repo, mutation):
    output = repo / ".strata"
    recorder.record(repo, output, ["src"], "python", "/missing/uv")
    history = output / "history.jsonl"
    if mutation == "unknown":
        (output / "unrelated.txt").write_text("keep")
    elif mutation == "foreign":
        row = json.loads(history.read_text())
        history.write_text(json.dumps(row | {"repository": "/elsewhere"}) + "\n")
    elif mutation == "invalid":
        history.write_text("invalid\n")
    else:
        history.rename(output / "saved")
        history.symlink_to(output / "saved")
    with pytest.raises(ValueError, match="not owned"):
        hooks.install(repo, ["src"])


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("Background worker did not reach the expected state")


def test_background_finishes_current_then_measures_newest_pending(repo, tmp_path):
    hooks.install(repo, ["src"])
    output = repo / ".strata"
    analyzer = tmp_path / "python"
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
    settings["python"] = str(analyzer)
    (output / "settings.json").write_text(json.dumps(settings))
    command = [
        sys.executable,
        "-m",
        "strata.worker",
        "--repo",
        str(repo),
        "--output",
        str(output),
    ]
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


def test_hook_can_be_reinstalled_after_removal(repo):
    hooks.install(repo, ["src"])
    hooks.remove(repo)
    hooks.install(repo, ["src"])
    hooks.remove(repo)
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
            check=False,
        ).returncode
        == 1
    )


@pytest.mark.parametrize("entry", ["settings.json", "hooks", "post-commit"])
def test_reinstall_rejects_symlinked_configuration(repo, tmp_path, entry):
    hooks.install(repo, ["src"])
    output = repo / ".strata"
    original = (
        output / entry if entry != "post-commit" else output / "hooks/post-commit"
    )
    target = tmp_path / "external"
    original.rename(target)
    original.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(ValueError, match="symlink"):
        hooks.install(repo, ["src"])
