import importlib
import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

import test_hooks

repo = test_hooks.repo


@pytest.fixture
def server(repo):
    module = importlib.import_module("serve_dashboard")
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
    module = importlib.import_module("serve_dashboard")
    base = record_source(repo)
    history = repo / ".slop-check/history.jsonl"
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
    module = importlib.import_module("serve_dashboard")
    commit = test_hooks.git(repo, "rev-parse", "HEAD")
    body, _ = module.DatasetCache(repo, commits=["HEAD", commit]).snapshot()
    assert json.loads(body)["requested_commits"] == [commit]


@pytest.mark.parametrize("revision", ["missing", "--all"])
def test_comparison_rejects_invalid_commit_references(repo, revision):
    with pytest.raises(subprocess.CalledProcessError):
        importlib.import_module("serve_dashboard").DatasetCache(
            repo, commits=[revision]
        )


def test_server_serves_only_dashboard_and_metrics(server):
    url, repo = server
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
    output = repo / ".slop-check"
    output.mkdir(exist_ok=True)
    row = {
        "commit": test_hooks.git(repo, "rev-parse", "HEAD"),
        "timestamp": "2026-10-02",
        "status": "skipped",
        "superseded_by": "new",
    }
    test_hooks.load("record_commit").append_history(output, row)
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
    output = repo / ".slop-check"
    output.mkdir(exist_ok=True)
    (output / "history.jsonl").write_text("invalid\n")
    with pytest.raises(urllib.error.HTTPError) as result:
        fetch(url + "/data.json")
    assert result.value.code == 503


def test_route_collision_is_rejected_without_changing_other_routes():
    installer = importlib.import_module("install_server")
    config = {
        "Web": {
            "host:443": {
                "Handlers": {"/slop/aurene": {"Proxy": "http://127.0.0.1:9999"}}
            }
        }
    }
    with pytest.raises(ValueError):
        installer.check_mount(config, "/slop/aurene", "http://127.0.0.1:8766")
    other = {
        "Web": {"host:8899": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8899"}}}}
    }
    installer.check_mount(other, "/slop/aurene", "http://127.0.0.1:8766")


def test_failed_bootstrap_removes_fresh_persistent_state(repo, tmp_path, monkeypatch):
    installer = importlib.import_module("install_server")
    home = tmp_path / "home"
    monkeypatch.setattr(installer.Path, "home", lambda: home)

    def run(command, check=True):
        if command[:2] == ["launchctl", "bootstrap"]:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "run", run)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(repo, port, "/slop/test", False)
    assert not list((home / "Library/LaunchAgents").glob("*.plist"))
    assert not (repo / ".slop-check/server-settings.json").exists()


def test_dependency_failure_does_not_stop_existing_server(repo, monkeypatch):
    installer = importlib.import_module("install_server")
    output = repo / ".slop-check"
    output.mkdir()
    (output / "server-settings.json").write_text(
        json.dumps({"port": 8766, "mount": "/slop/test"})
    )
    commands = []

    def run(command, check=True):
        commands.append(command)
        if command[0].endswith("/uv"):
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(repo, 8766, "/slop/test", False)
    assert not any(command[:2] == ["launchctl", "bootout"] for command in commands)


def test_tailnet_failure_removes_new_mount_and_launch_agent(
    repo, tmp_path, monkeypatch
):
    import io

    installer = importlib.import_module("install_server")
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: "/usr/local/bin/" + name
    )
    monkeypatch.setattr(
        installer.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"{}")
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    commands = []

    def run(command, check=True):
        commands.append(command)
        if command[1:] == ["status", "--json"]:
            raise subprocess.CalledProcessError(1, command)
        config = (
            {
                "Web": {
                    "host:443": {
                        "Handlers": {
                            "/slop/test": {"Proxy": f"http://127.0.0.1:{port}"}
                        }
                    }
                }
            }
            if sum(c[1:] == ["serve", "status", "--json"] for c in commands) > 1
            else {}
        )
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(config), stderr=""
        )

    monkeypatch.setattr(installer, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(repo, port, "/slop/test", True)
    assert [
        "/usr/local/bin/tailscale",
        "serve",
        "--https=443",
        "--set-path=/slop/test",
        "off",
    ] in commands


def test_changing_tailnet_mode_requires_removal_first(repo, monkeypatch):
    installer = importlib.import_module("install_server")
    output = repo / ".slop-check"
    output.mkdir()
    (output / "server-settings.json").write_text(
        json.dumps({"port": 8766, "mount": "/slop/test", "tailscale": True})
    )
    monkeypatch.setattr(
        installer,
        "run",
        lambda command, check=True: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
    )
    with pytest.raises(ValueError, match="Remove the existing server"):
        installer.install(repo, 8766, "/slop/test", False)


def record_source(repo, path="src/main.rs", flagged=True):
    import test_dashboard

    output = repo / ".slop-check"
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
    query = dict(
        commit=data["head"], path="src/main.rs", scope="0", revision=data["revision"]
    )
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
        ({"revision": "stale"}, 409),
        ({"commit": "HEAD"}, 400),
        ({"commit": "0" * 40}, 404),
        ({"path": "src/tests.rs"}, 404),
        ({"path": "../.git/config"}, 404),
        ({"scope": "-1"}, 400),
        ({"scope": "1"}, 404),
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
