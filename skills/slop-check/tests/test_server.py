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
