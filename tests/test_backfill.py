import json

import pytest

import test_hooks
from strata import hooks, recorder
from test_hooks import fake_analyzer, git

repo = test_hooks.repo

INPUTS = recorder.selection(
    {"format": 3, "source_roots": ["src"], "python": "/missing/python"}
)


def recording(commit, identifier, statuses, **selection):
    started = {
        "recording_id": identifier,
        "commit": commit,
        "status": "started",
        "languages": list(statuses),
        "source_roots": ["src"],
        "analyzer": INPUTS["analyzer"],
        "inclusion_policy": INPUTS["inclusion_policy"],
        "scope": INPUTS["scope"],
    } | selection
    results = [
        started | {"language": language, "status": status}
        for language, status in statuses.items()
    ]
    return [started, *results]


FINISHED = {"python": "complete", "javascript": "not_applicable", "rust": "complete"}


def test_finished_recording_counts_as_measured():
    rows = recording("a", "1", FINISHED)
    assert recorder.measured_commits(rows, INPUTS) == {"a"}


def test_recording_with_a_failed_language_is_retried():
    rows = recording("a", "1", FINISHED | {"rust": "failed"})
    assert recorder.measured_commits(rows, INPUTS) == set()


def test_interrupted_recording_is_retried():
    started, *results = recording("a", "1", FINISHED)
    assert recorder.measured_commits([started, *results[:-1]], INPUTS) == set()


def test_recording_with_other_roots_does_not_count():
    rows = recording("a", "1", FINISHED, source_roots=["lib"])
    assert recorder.measured_commits(rows, INPUTS) == set()


def test_recording_under_the_info_severity_policy_is_retried():
    rows = recording("a", "1", FINISHED, inclusion_policy="tracked-source-v1")
    assert recorder.measured_commits(rows, INPUTS) == set()


def test_recording_missing_a_newly_supported_language_is_retried():
    older = {"python": "complete", "javascript": "not_applicable"}
    rows = recording("a", "1", older)
    assert recorder.measured_commits(rows, INPUTS) == set()


def test_later_failure_does_not_undo_earlier_success():
    rows = recording("a", "1", FINISHED) + recording(
        "a", "2", FINISHED | {"rust": "failed"}
    )
    assert recorder.measured_commits(rows, INPUTS) == {"a"}


def commit(repo, message):
    git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--allow-empty",
        "-qm",
        message,
    )
    return git(repo, "rev-parse", "HEAD")


def test_backfill_follows_first_parent_newest_first(repo):
    first = git(repo, "rev-parse", "HEAD")
    main = git(repo, "branch", "--show-current")
    git(repo, "checkout", "-qb", "side")
    side = commit(repo, "side")
    git(repo, "checkout", "-q", main)
    second = commit(repo, "second")
    git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "merge",
        "side",
    )
    merge = git(repo, "rev-parse", "HEAD")
    commits = recorder.backfill_commits(repo, main)
    assert commits == [merge, second, first]
    assert side not in commits


def test_backfill_samples_every_nth_commit_keeping_the_tip(repo):
    commits = [git(repo, "rev-parse", "HEAD")]
    commits += [commit(repo, str(n)) for n in range(4)]
    newest_first = commits[::-1]
    assert recorder.backfill_commits(repo, "HEAD", every=2) == newest_first[::2]


def test_backfill_rejects_nonpositive_sampling(repo):
    with pytest.raises(ValueError, match="--every"):
        recorder.backfill_commits(repo, "HEAD", every=0)


def test_backfill_records_only_unmeasured_commits(repo, tmp_path):
    hooks.install(repo, ["src"])
    output = repo / ".strata"
    settings = json.loads((output / "settings.json").read_text())
    settings["python"] = fake_analyzer(tmp_path)
    (output / "settings.json").write_text(json.dumps(settings))
    first = git(repo, "rev-parse", "HEAD")
    recorder.backfill(repo, "HEAD", None)
    second = commit(repo, "second")
    recorder.backfill(repo, "HEAD", None)
    rows = [
        json.loads(line) for line in (output / "history.jsonl").read_text().splitlines()
    ]
    started = [row["commit"] for row in rows if row["status"] == "started"]
    assert started == [first, second]
    assert recorder.measured_commits(rows, recorder.selection(settings)) == {
        first,
        second,
    }
