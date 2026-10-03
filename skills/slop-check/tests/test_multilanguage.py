import fcntl
import json
import subprocess
import sys

import pytest

import test_hooks
from test_hooks import SCRIPTS, git, load
from test_dashboard import row

repo = test_hooks.repo


def history(output):
    return [
        json.loads(line) for line in (output / "history.jsonl").read_text().splitlines()
    ]


def test_roots_are_canonical_and_language_is_automatic(repo):
    installer = load("install_hook")
    installer.install(repo, ["src/.", "src/sub", "src"])
    settings = json.loads((repo / ".slop-check/settings.json").read_text())
    assert settings["source_roots"] == ["src"]
    assert "language" not in settings
    assert settings["format"] == 2


def test_old_settings_require_reinstall():
    with pytest.raises(ValueError, match="reinstall"):
        load("record_commit").selection(
            {"source_roots": ["src"], "language": "python", "uvx": "uvx"}
        )


def test_mixed_commit_records_all_languages_with_native_analyzer(repo):
    (repo / "src/main.py").write_text("def decision(x):\n    return x + 1\n")
    (repo / "src/main.js").write_text("function decision(x) { return x + 1; }\n")
    (repo / "src/main.ts").write_text("not supported")
    (repo / "src/test_extra.py").write_text("excluded")
    git(repo, "add", ".")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "mixed")
    sha = git(repo, "rev-parse", "HEAD")
    (repo / "src/main.py").write_text("dirty invalid source")
    output = repo / ".slop-check"
    load("record_commit").record_all(repo, output, ["src"], "uvx", commit=sha)
    rows = history(output)
    assert rows[0]["status"] == "started"
    assert rows[0]["languages"] == ["python", "javascript", "rust"]
    results = rows[1:]
    assert [r["status"] for r in results] == ["complete"] * 3
    assert {r["commit"] for r in rows} == {sha}
    assert len({r["recording_id"] for r in rows}) == 1
    assert len({r["report"] for r in results}) == 3
    for result in results:
        manifest = json.loads((output / result["report"] / "manifest.json").read_text())
        assert len(manifest) == 1


def test_unexpected_language_failure_does_not_stop_remaining_results(repo, monkeypatch):
    recorder = load("record_commit")
    original = recorder.record

    def fail_python(*args, **kwargs):
        if args[3] == "python":
            raise RuntimeError("unexpected parser failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(recorder, "record", fail_python)
    output = repo / ".slop-check"
    recorder.record_all(repo, output, ["empty"], "/missing/uv")
    assert [(r.get("language"), r["status"]) for r in history(output)[1:]] == [
        ("python", "failed"),
        ("javascript", "not_applicable"),
        ("rust", "not_applicable"),
    ]


def test_enqueue_captures_selection_before_settings_change(repo, monkeypatch):
    load("install_hook").install(repo, ["src"])
    queue = load("queue_commit")
    monkeypatch.setattr(queue, "spawn_worker", lambda *args: None)
    output = repo / ".slop-check"
    queue.enqueue(repo, output)
    job = queue.read_queue(output)["pending"]
    settings_path = output / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings["source_roots"] = ["other"]
    settings_path.write_text(json.dumps(settings))
    assert job["source_roots"] == ["src"]
    assert job["languages"] == ["python", "javascript", "rust"]
    observed = []
    monkeypatch.setattr(
        queue, "record_all", lambda *args, **kwargs: observed.append((args, kwargs))
    )
    queue.work(output)
    assert observed[0][0][2] == ["src"]
    assert observed[0][1]["commit"] == job["commit"]


def test_manual_scan_fails_busy_without_starting_attempt(repo):
    output = repo / ".slop-check"
    output.mkdir()
    with (output / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "record_commit.py"),
                "--repo",
                str(repo),
                "--source-root",
                "src",
            ],
            capture_output=True,
            text=True,
        )
    assert result.returncode != 0
    assert "busy" in result.stderr.lower()
    assert not (output / "history.jsonl").exists()


def start(sha, identifier):
    return dict(
        commit=sha,
        repository="unused",
        timestamp="2026-10-02",
        recording_id=identifier,
        status="started",
        languages=["python", "javascript", "rust"],
        source_roots=["src"],
        analyzer="scb-check==0.2.0",
        inclusion_policy="tracked-source-v1",
        scope="inline tests retained",
    )


def attempt_row(sha, identifier, language, status):
    result = row(sha, "reports/" + identifier + language, "2026-10-02")
    result.update(recording_id=identifier, language=language, status=status)
    if status != "complete":
        result.pop("metrics")
    return result


def dataset(repo, rows):
    output = repo / ".slop-check"
    output.mkdir(exist_ok=True)
    (output / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return load("dashboard").load_dataset(repo, output)


def test_latest_recording_does_not_reuse_previous_language_success(repo):
    sha = git(repo, "rev-parse", "HEAD")
    data = dataset(
        repo,
        [
            start(sha, "a"),
            attempt_row(sha, "a", "python", "complete"),
            start(sha, "b"),
            attempt_row(sha, "b", "javascript", "complete"),
            attempt_row(sha, "b", "python", "failed"),
        ],
    )
    assert [s["language"] for s in data["series"]] == ["javascript"]
    coverage = data["recordings"][0]
    assert coverage["recording_id"] == "b"
    assert coverage["results"] == {
        "python": "failed",
        "javascript": "complete",
        "rust": "missing",
    }


def test_interrupted_attempt_remains_navigable_without_complete_series(repo):
    sha = git(repo, "rev-parse", "HEAD")
    data = dataset(repo, [start(sha, "a")])
    assert data["series"] == []
    assert data["recordings"][0]["results"] == {
        "python": "missing",
        "javascript": "missing",
        "rust": "missing",
    }


def test_later_empty_language_does_not_keep_old_success(repo):
    sha = git(repo, "rev-parse", "HEAD")
    data = dataset(
        repo,
        [
            start(sha, "a"),
            attempt_row(sha, "a", "python", "complete"),
            start(sha, "b"),
            attempt_row(sha, "b", "python", "not_applicable"),
        ],
    )
    assert data["series"] == []
    assert data["recordings"][0]["results"]["python"] == "not_applicable"


def test_manual_scan_hands_off_hook_enqueued_during_analysis(repo, monkeypatch):
    recorder = load("record_commit")
    queue = load("queue_commit")
    output = repo / ".slop-check"
    load("install_hook").install(repo, ["missing"])
    monkeypatch.setattr(queue, "spawn_worker", lambda *args: None)

    def enqueue_during_scan(*args, **kwargs):
        queue.enqueue(repo, output)
        raise RuntimeError("interrupted manual scan")

    monkeypatch.setattr(recorder, "record_all", enqueue_during_scan)
    with pytest.raises(RuntimeError, match="interrupted"):
        recorder.record_manual(
            repo,
            output,
            recorder.selection(json.loads((output / "settings.json").read_text())),
            git(repo, "rev-parse", "HEAD"),
        )
    test_hooks.wait_for(
        lambda: (output / "history.jsonl").exists() and len(history(output)) == 4
    )
    assert [r["status"] for r in history(output)] == ["started"] + [
        "not_applicable"
    ] * 3
    test_hooks.wait_for(lambda: queue.read_queue(output)["current"] is None)


def test_individual_history_is_separate_from_mixed_recording(repo):
    sha = git(repo, "rev-parse", "HEAD")
    individual = row(sha, "reports/individual", "2026-10-01")
    data = dataset(
        repo,
        [
            individual,
            start(sha, "mixed"),
            attempt_row(sha, "mixed", "rust", "complete"),
        ],
    )
    assert len(data["series"]) == 2
    assert {series["recording_mode"] for series in data["series"]} == {
        "individual",
        "mixed",
    }


def test_source_for_superseded_recording_is_not_authorized(repo):
    from urllib.parse import urlencode
    import test_server

    sha = test_server.record_source(repo)
    output = repo / ".slop-check"
    result = history(output)[0]
    first = start(sha, "a")
    second = start(sha, "b")
    result.update(recording_id="a")
    # Keep a second commit's Rust success so the mixed Rust scope still exists.
    git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--allow-empty",
        "-qm",
        "newer",
    )
    newer = git(repo, "rev-parse", "HEAD")
    newer_result = result | {"commit": newer, "recording_id": "c"}
    data = dataset(
        repo,
        [
            first,
            result,
            second,
            attempt_row(sha, "b", "rust", "failed"),
            start(newer, "c"),
            newer_result,
        ],
    )
    data["revision"] = "new-revision"
    server = load("serve_dashboard")
    query = urlencode(
        {"commit": sha, "path": "src/main.rs", "scope": "0", "revision": "new-revision"}
    )
    with pytest.raises(server.SourceError) as error:
        server.read_source(repo, data, query)
    assert error.value.status == 404


def test_hook_install_accepts_manual_recording_lock_files(repo):
    recorder = load("record_commit")
    output = repo / ".slop-check"
    inputs = recorder.selection({"format": 2, "source_roots": ["empty"], "uvx": "uvx"})
    recorder.record_manual(repo, output, inputs, git(repo, "rev-parse", "HEAD"))
    saved = (output / "history.jsonl").read_bytes()
    load("install_hook").install(repo, ["src"])
    assert (output / "history.jsonl").read_bytes() == saved
    assert (output / "settings.json").exists()


@pytest.mark.parametrize("filename", ["worker.lock", "queue.lock"])
@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_hook_install_rejects_nonregular_manual_lock_files(repo, filename, kind):
    recorder = load("record_commit")
    output = repo / ".slop-check"
    recorder.record_manual(
        repo,
        output,
        recorder.selection({"format": 2, "source_roots": ["empty"], "uvx": "uvx"}),
        git(repo, "rev-parse", "HEAD"),
    )
    lock_path = output / filename
    lock_path.unlink()
    if kind == "directory":
        lock_path.mkdir()
    else:
        lock_path.symlink_to(repo / "src/main.rs")
    with pytest.raises(ValueError, match="not owned"):
        load("install_hook").install(repo, ["src"])
