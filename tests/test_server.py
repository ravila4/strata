import json
import subprocess
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import test_hooks
from strata import recorder
from strata import server as dashboard_server

repo = test_hooks.repo


@pytest.fixture
def server(repo):
    module = dashboard_server
    server = module.make_server(repo, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(server.server_port), repo
    server.shutdown()
    server.server_close()
    thread.join()


def fetch(url, headers=None):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=headers or {}), timeout=10
    )


def test_comparison_serves_feature_commits_without_changing_checkout(repo):
    module = dashboard_server
    base = record_source(repo)
    history = repo / ".strata/history.jsonl"
    base_row = history.read_text()
    branch = test_hooks.git(repo, "branch", "--show-current")
    test_hooks.git(repo, "checkout", "-qb", "feature")
    (repo / "src/main.rs").write_text('fn main() { println!("feature"); }\n')
    test_hooks.git(repo, "add", "src/main.rs")
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "feature")
    feature = record_source(repo)
    history.write_text(base_row + history.read_text())
    test_hooks.git(repo, "checkout", "-q", branch)
    server = module.make_server(repo, 0, commits=["HEAD", "feature"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)
    try:
        with fetch(url + "/data.json") as response:
            data = json.load(response)
        assert data["requested_commits"] == [base, feature]
        assert [p["commit"] for p in data["series"][0]["snapshots"]] == [base, feature]
        with fetch(source_url(url)) as response:
            assert "feature" in json.load(response)["text"]
        assert test_hooks.git(repo, "rev-parse", "HEAD") == base
        test_hooks.git(
            repo,
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "-qm",
            "main change",
        )
        test_hooks.git(repo, "update-ref", "refs/heads/feature", base)
        history.write_text(base_row)
        with fetch(url + "/data.json") as response:
            refreshed = json.load(response)
        assert refreshed["requested_commits"] == [base, feature]
        assert refreshed["head"] == feature
        assert refreshed["revision"] != data["revision"]
        assert [p["commit"] for p in refreshed["series"][0]["snapshots"]] == [base]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_comparison_deduplicates_commit_aliases(repo):
    module = dashboard_server
    commit = test_hooks.git(repo, "rev-parse", "HEAD")
    body, _, data = module.DatasetCache(repo, commits=["HEAD", commit]).snapshot()
    assert json.loads(body)["requested_commits"] == [commit]
    # Source and tree requests reuse the parsed dataset instead of reparsing it.
    assert data == json.loads(body)


@pytest.mark.parametrize("revision", ["missing", "--all"])
def test_comparison_rejects_invalid_commit_references(repo, revision):
    with pytest.raises(subprocess.CalledProcessError):
        dashboard_server.DatasetCache(repo, commits=[revision])


def test_server_serves_only_dashboard_and_metrics(server):
    url, _repo = server
    with fetch(url + "/") as response:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Type"].startswith("text/html")
        assert b'id="sunburst-chart"' in response.read()
    for path in ["/settings.json", "/reports/", "/src/main.rs", "/../.git/config"]:
        with pytest.raises(urllib.error.HTTPError) as error:
            fetch(url + path)
        assert error.value.code == 404


def test_new_history_row_refreshes_dataset(server):
    url, repo = server
    with fetch(url + "/data.json") as response:
        original = json.load(response)
    assert original["repository_path"] == str(repo.resolve())
    output = repo / ".strata"
    output.mkdir(exist_ok=True)
    row = {
        "commit": test_hooks.git(repo, "rev-parse", "HEAD"),
        "timestamp": "2026-10-02",
        "status": "skipped",
        "superseded_by": "new",
    }
    recorder.append_history(output, row)
    with fetch(url + "/data.json") as response:
        updated = json.load(response)
    assert updated["revision"] != original["revision"]
    assert updated["events"][0]["status"] == "skipped"


def test_unchanged_metrics_can_be_requested_conditionally(server):
    url, _ = server
    with fetch(url + "/data.json") as response:
        etag = response.headers["ETag"]
    with pytest.raises(urllib.error.HTTPError) as result:
        fetch(url + "/data.json", {"If-None-Match": etag})
    assert result.value.code == 304


def test_simultaneous_clients_receive_one_consistent_revision(server):
    url, _ = server

    def read_revision(_):
        with fetch(url + "/data.json") as response:
            return json.load(response)["revision"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(read_revision, range(8)))) == 1


def test_corrupt_history_returns_error_instead_of_stale_live_data(server):
    url, repo = server
    fetch(url + "/data.json").close()
    output = repo / ".strata"
    output.mkdir(exist_ok=True)
    (output / "history.jsonl").write_text("invalid\n")
    with pytest.raises(urllib.error.HTTPError) as result:
        fetch(url + "/data.json")
    assert result.value.code == 503


def record_source(repo, path="src/main.rs", flagged=True):
    import test_dashboard

    output = repo / ".strata"
    report = output / "reports/source"
    report.mkdir(parents=True, exist_ok=True)
    measured = test_dashboard.details()
    measured["files"][0]["path"] = path
    measured["functions"][0]["path"] = path
    aggregate = test_dashboard.aggregate()
    if flagged:
        measured["files"][0].update(
            verbosity_flagged_loc=1, clone_loc=0, verbosity_flagged_lines=[1]
        )
        aggregate.update(verbosity_flagged_loc=1, clone_loc=0)
    (report / "details.json").write_text(json.dumps(measured))
    (report / "analyzer.json").write_text(json.dumps(aggregate))
    (report / "manifest.json").write_text(json.dumps([path]))
    commit = test_hooks.git(repo, "rev-parse", "HEAD")
    row = test_dashboard.row(commit, "reports/source", "2026-10-02")
    row["details"] = "reports/source/details.json"
    (output / "history.jsonl").write_text(json.dumps(row) + "\n")
    return commit


def source_url(url, **overrides):
    from urllib.parse import urlencode

    with fetch(url + "/data.json") as response:
        data = json.load(response)
    query = dict(commit=data["head"], path="src/main.rs", scope=data["series"][0]["id"])
    return url + "/source.json?" + urlencode(query | overrides)


def test_live_source_reads_recorded_commit_instead_of_worktree(server):
    url, repo = server
    commit = record_source(repo)
    (repo / "src/main.rs").write_text("dirty worktree\n")
    with fetch(url + "/data.json") as response:
        assert json.load(response)["source_available"] is True
    with fetch(source_url(url)) as response:
        source = json.load(response)
    assert source["text"] == "fn main() {}\n"
    assert source["commit"] == commit
    assert source["language"] == "rust"
    assert source["flagged_lines"] == [1]
    assert source["functions"][0]["name"] == "main"


@pytest.mark.parametrize(
    "query,status",
    [
        ({"commit": "HEAD"}, 400),
        ({"commit": "0" * 40}, 404),
        ({"path": "../.git/config"}, 404),
        ({"scope": "-1"}, 400),
        ({"scope": "0" * 16}, 404),
        ({"scope": "0.0"}, 400),
        ({"path": ""}, 400),
    ],
)
def test_source_rejects_unauthorized_or_invalid_requests(server, query, status):
    url, repo = server
    record_source(repo)
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch(source_url(url, **query))
    assert error.value.code == status
    assert json.load(error.value)["error"]


@pytest.mark.parametrize("suffix", ["", "?scope=0", "?scope=0&scope=1"])
def test_source_requires_exact_query_fields(server, suffix):
    url, repo = server
    record_source(repo)
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch(url + "/source.json" + suffix)
    assert error.value.code == 400


@pytest.mark.parametrize(
    "path,text",
    [
        ("src/a[1].rs", "fn literal() {}\n"),
        ('src/a\tquote".rs', "fn odd() {}\n"),
    ],
)
def test_source_uses_literal_git_paths(server, path, text):
    url, repo = server
    (repo / path).write_text(text)
    test_hooks.git(repo, "add", ".")
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "literal")
    record_source(repo, path)
    with fetch(source_url(url, path=path)) as response:
        assert json.load(response)["text"] == text


@pytest.mark.parametrize(
    "path,contents",
    [
        ("src/link.rs", None),
        ("src/large.rs", b"a" * (256 * 1024 + 1)),
        ("src/binary.rs", b"a\x00b"),
        ("src/nonutf8.rs", b"\xff"),
        ("src/lines.rs", b"\n" * 10001),
    ],
    ids=["symlink", "large", "binary", "nonutf8", "many-lines"],
)
def test_source_rejects_nonregular_or_unrenderable_blobs(server, path, contents):
    url, repo = server
    if contents is not None:
        (repo / path).write_bytes(contents)
        test_hooks.git(repo, "add", ".")
        test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "blob")
    record_source(repo, path)
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch(source_url(url, path=path))
    assert error.value.code == 422


def test_source_survives_history_changes_after_the_page_loaded(server):
    url, repo = server
    commit = record_source(repo)
    request = source_url(url)
    recorder.append_history(
        repo / ".strata",
        {"commit": commit, "timestamp": "2026-10-03", "status": "skipped"},
    )
    with fetch(request) as response:
        assert json.load(response)["commit"] == commit


def test_source_does_not_invent_old_flagged_locations(server):
    url, repo = server
    record_source(repo, flagged=False)
    with fetch(source_url(url)) as response:
        assert "flagged_lines" not in json.load(response)


def test_source_rejects_unbounded_scope_number(server):
    url, repo = server
    record_source(repo)
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch(source_url(url, scope="9" * 5000))
    assert error.value.code == 400


def test_source_returns_error_when_repository_disappears(server):
    url, repo = server
    record_source(repo)
    request = source_url(url)
    (repo / ".git").rename(repo / "removed-git")
    with pytest.raises(urllib.error.HTTPError) as error:
        fetch(request)
    assert error.value.code == 503
    assert json.load(error.value)["error"]


def test_live_dataset_identifies_shared_runtime(server):
    base, _ = server
    with urllib.request.urlopen(base + "/data.json") as response:
        data = json.load(response)
    assert data["source_runtime"] == str(
        Path(dashboard_server.__file__).resolve().parent
    )


def dashboard_context(repo):
    module = dashboard_server
    _, _, data = module.DatasetCache(repo).snapshot()
    return module, data


def context_query(data, **fields):
    from urllib.parse import urlencode

    return urlencode(
        {"commit": data["head"], "scope": data["series"][0]["id"]} | fields
    )


def test_tree_lists_all_historical_entries(repo):
    commit = record_source(repo)
    (repo / "src/tests.rs").unlink()
    (repo / "untracked.txt").write_text("working tree")
    module, data = dashboard_context(repo)
    assert module.read_tree(repo, data, context_query(data)) == {
        "commit": commit,
        "entries": [
            {"path": "src/link.rs", "kind": "symlink"},
            {"path": "src/main.rs", "kind": "file"},
            {"path": "src/tests.rs", "kind": "file"},
        ],
    }


def test_source_previews_unmeasured_tracked_text(server):
    url, repo = server
    record_source(repo)
    with fetch(source_url(url, path="src/tests.rs")) as response:
        source = json.load(response)
    assert source["text"] == "not production\n"
    assert source["measured"] is False
    assert source["functions"] == []
    assert source["metric_available"] == {
        "cc": False,
        "erosion": False,
        "cognitive": False,
        "verbosity": False,
    }


def test_source_measurement_availability_keeps_zero(repo):
    record_source(repo, flagged=False)
    module, data = dashboard_context(repo)
    details = data["series"][0]["snapshots"][0]["details"]
    details["functions"][0].update(cc=0, cognitive=0)
    source = module.read_source(repo, data, context_query(data, path="src/main.rs"))
    assert source["measured"] is True
    assert source["metric_available"] == {
        "cc": True,
        "erosion": True,
        "cognitive": True,
        "verbosity": False,
    }


@pytest.mark.parametrize(
    "path,language",
    [
        ("doc.md", None),
        ("code.py", "python"),
        ("code.pyi", "python"),
        ("code.mjs", "javascript"),
        ("code.cjs", "javascript"),
        ("code.js", "javascript"),
    ],
)
def test_unmeasured_source_language_follows_extension(repo, path, language):
    (repo / path).write_text("text\n")
    test_hooks.git(repo, "add", path)
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "text")
    record_source(repo)
    module, data = dashboard_context(repo)
    assert (
        module.read_source(repo, data, context_query(data, path=path))["language"]
        == language
    )


def test_tree_authorizes_snapshot_without_details(repo):
    record_source(repo)
    module, data = dashboard_context(repo)
    data["series"][0]["snapshots"][0]["details"] = None
    assert len(module.read_tree(repo, data, context_query(data))["entries"]) == 3
    assert (
        module.read_source(repo, data, context_query(data, path="src/main.rs"))[
            "measured"
        ]
        is False
    )


@pytest.mark.parametrize(
    "fields,status",
    [
        ({"commit": "HEAD"}, 400),
        ({"commit": "0" * 40}, 404),
        ({"scope": "0" * 16}, 404),
        ({"scope": "-1"}, 400),
        ({"path": "src/main.rs"}, 400),
    ],
)
def test_tree_rejects_invalid_context(repo, fields, status):
    record_source(repo)
    module, data = dashboard_context(repo)
    with pytest.raises(module.SourceError) as error:
        module.read_tree(repo, data, context_query(data, **fields))
    assert error.value.status == status


def test_tree_survives_history_changes_after_the_page_loaded(server):
    url, repo = server
    commit = record_source(repo)
    with fetch(url + "/data.json") as response:
        request = url + "/tree.json?" + context_query(json.load(response))
    recorder.append_history(
        repo / ".strata",
        {"commit": commit, "timestamp": "2026-10-03", "status": "skipped"},
    )
    with fetch(request) as response:
        assert json.load(response)["commit"] == commit


def test_tree_http_route(server):
    url, repo = server
    record_source(repo)
    with fetch(url + "/data.json") as response:
        data = json.load(response)
    with fetch(url + "/tree.json?" + context_query(data)) as response:
        assert len(json.load(response)["entries"]) == 3


@pytest.mark.parametrize(
    "limit", ["TREE_MAX_BYTES", "TREE_MAX_NODES", "TREE_MAX_JSON_BYTES"]
)
def test_tree_reports_resource_limit_instead_of_partial_listing(
    repo, monkeypatch, limit
):
    record_source(repo)
    module, data = dashboard_context(repo)
    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(module.SourceError, match="limit") as error:
        module.read_tree(repo, data, context_query(data))
    assert error.value.status == 422


def test_tree_reports_unavailable_git_objects(repo):
    record_source(repo)
    module, data = dashboard_context(repo)
    (repo / ".git").rename(repo / "missing-git")
    with pytest.raises(module.SourceError) as error:
        module.read_tree(repo, data, context_query(data))
    assert error.value.status == 422


@pytest.mark.parametrize(
    "path",
    [
        "__proto__",
        "constructor",
        "src/a[1].rs",
        "src/a\nname.rs",
        'src/a\tquote".rs',
        "src/日本語.rs",
    ],
)
def test_tree_preserves_literal_paths(repo, path):
    (repo / path).write_text("literal\n")
    test_hooks.git(repo, "add", "--", path)
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "literal")
    record_source(repo)
    module, data = dashboard_context(repo)
    assert {"path": path, "kind": "file"} in module.read_tree(
        repo, data, context_query(data)
    )["entries"]
    assert (
        module.read_source(repo, data, context_query(data, path=path))["text"]
        == "literal\n"
    )


def test_tree_includes_submodule_placeholder(repo):
    commit = test_hooks.git(repo, "rev-parse", "HEAD")
    test_hooks.git(
        repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor"
    )
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "gitlink")
    record_source(repo)
    module, data = dashboard_context(repo)
    assert {"path": "vendor", "kind": "submodule"} in module.read_tree(
        repo, data, context_query(data)
    )["entries"]
    with pytest.raises(module.SourceError, match="regular"):
        module.read_source(repo, data, context_query(data, path="vendor"))


def test_source_partial_metric_availability(repo):
    record_source(repo)
    module, data = dashboard_context(repo)
    details = data["series"][0]["snapshots"][0]["details"]
    del details["functions"][0]["cognitive"]
    source = module.read_source(repo, data, context_query(data, path="src/main.rs"))
    assert source["metric_available"] == {
        "cc": True,
        "erosion": True,
        "cognitive": False,
        "verbosity": True,
    }


def test_tree_rejects_invalid_filename_encoding(repo):
    object_id = test_hooks.git(repo, "rev-parse", "HEAD:src/main.rs")
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "-z", "--index-info"],
        input=b"100644 " + object_id.encode() + b"\tbad-\xff\x00",
        check=True,
    )
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "encoding")
    record_source(repo)
    module, data = dashboard_context(repo)
    with pytest.raises(module.SourceError, match="non-UTF-8"):
        module.read_tree(repo, data, context_query(data))


def test_bounded_tree_timeout_reaps_process(repo, tmp_path, monkeypatch):
    module = dashboard_server
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(0o755)
    import os

    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    monkeypatch.setattr(module, "GIT_TIMEOUT_SECONDS", 0.02)
    processes = []
    original = module.subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    with pytest.raises(module.SourceError, match="time limit"):
        module.bounded_tree_output(repo, "0" * 40)
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_bounded_tree_overflow_reaps_process(repo, tmp_path, monkeypatch):
    module = dashboard_server
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nexec yes output\n")
    script.chmod(0o755)
    import os

    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    monkeypatch.setattr(module, "TREE_MAX_BYTES", 10)
    processes = []
    original = module.subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    with pytest.raises(module.SourceError, match="byte limit"):
        module.bounded_tree_output(repo, "0" * 40)
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_tree_node_budget_counts_derived_directories(repo, monkeypatch):
    record_source(repo)
    module, data = dashboard_context(repo)
    monkeypatch.setattr(module, "TREE_MAX_NODES", 3)
    with pytest.raises(module.SourceError, match="node limit"):
        module.read_tree(repo, data, context_query(data))


@pytest.mark.parametrize("extra", ["&scope=0", "&path=", "&unknown=value"])
def test_tree_rejects_duplicate_or_extra_query_fields(repo, extra):
    record_source(repo)
    module, data = dashboard_context(repo)
    with pytest.raises(module.SourceError) as error:
        module.read_tree(repo, data, context_query(data) + extra)
    assert error.value.status == 400


def test_measured_source_uses_extension_before_scope_language(repo):
    (repo / "code.py").write_text("pass\n")
    test_hooks.git(repo, "add", "code.py")
    test_hooks.git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "python")
    record_source(repo, "code.py")
    module, data = dashboard_context(repo)
    assert (
        module.read_source(repo, data, context_query(data, path="code.py"))["language"]
        == "python"
    )


def test_tree_reports_timeout_after_stdout_closes(repo, tmp_path, monkeypatch):
    import os

    module = dashboard_server
    script = tmp_path / "git"
    script.write_text("#!/bin/sh\nexec 1>&-\nexec sleep 30\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    monkeypatch.setattr(module, "GIT_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(module.SourceError, match="time limit"):
        module.bounded_tree_output(repo, "0" * 40)
