import json
import socket
import subprocess
import sys
import threading

import pytest
import test_hooks
from strata import service

repo = test_hooks.repo

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="the persistent service uses launchd"
)


def test_route_collision_is_rejected_without_changing_other_routes():
    installer = service
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
    installer = service
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
    assert not (repo / ".strata/server-settings.json").exists()


def test_tailnet_failure_removes_new_mount_and_launch_agent(
    repo, tmp_path, monkeypatch
):
    import io

    installer = service
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(
        installer.shutil, "which", lambda name: "/usr/local/bin/" + name
    )
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(
            json.dumps(
                {
                    "repository_path": str(repo.resolve()),
                    "source_runtime": str(
                        installer.Path(installer.__file__).resolve().parent
                    ),
                }
            ).encode()
        ),
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
    installer = service
    output = repo / ".strata"
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


@pytest.fixture
def persistent_installer(tmp_path, monkeypatch):
    import io

    installer = service
    home = tmp_path / "service-home"
    monkeypatch.setattr(installer.Path, "home", lambda: home)
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/bin/" + name)
    calls = []

    def run(command, check=True):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "run", run)
    identity = {}
    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(identity).encode()),
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return installer, home, calls, identity, port


def test_persistent_server_uses_shared_runtime_without_copies(
    repo, persistent_installer
):
    import plistlib

    installer, home, _calls, identity, port = persistent_installer
    runtime = installer.Path(installer.__file__).resolve().parent
    identity.update(repository_path=str(repo.resolve()), source_runtime=str(runtime))
    installer.install(repo, port, "/slop/test", False)
    plist = plistlib.loads(
        next((home / "Library/LaunchAgents").glob("*.plist")).read_bytes()
    )
    # Background launch agents run throttled, which slows every request.
    assert plist["ProcessType"] == "Standard"
    args = plist["ProgramArguments"]
    assert args[:5] == [sys.executable, "-I", "-m", "strata", "serve"]
    assert args[args.index("--repo") + 1] == str(repo.resolve())
    settings = json.loads((repo / ".strata/server-settings.json").read_text())
    assert settings["source_runtime"] == str(runtime)
    assert not (repo / ".strata/dashboard-tool").exists()
    assert not list((repo / ".strata").glob("dashboard-stage-*"))


@pytest.mark.parametrize("field", ["repository_path", "source_runtime"])
def test_persistent_server_rejects_foreign_endpoint_identity(
    repo, persistent_installer, field
):
    installer, home, _calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    identity[field] = "/foreign"
    with pytest.raises(ValueError, match="identity"):
        installer.install(repo, port, "/slop/test", False)
    assert not (repo / ".strata/server-settings.json").exists()
    assert not list((home / "Library/LaunchAgents").glob("*.plist"))


def test_server_reinstall_restores_previous_configuration_on_failure(
    repo, persistent_installer, monkeypatch
):
    installer, home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    installer.install(repo, port, "/slop/test", False)
    settings_path = repo / ".strata/server-settings.json"
    plist_path = next((home / "Library/LaunchAgents").glob("*.plist"))
    original_settings, original_plist = (
        settings_path.read_bytes(),
        plist_path.read_bytes(),
    )
    history = repo / ".strata/history.jsonl"
    history.write_text("evidence\n")
    identity["source_runtime"] = "/foreign"
    with pytest.raises(ValueError, match="identity"):
        installer.install(repo, port, "/slop/test", False)
    assert settings_path.read_bytes() == original_settings
    assert plist_path.read_bytes() == original_plist
    assert history.read_text() == "evidence\n"
    assert calls[-1][:2] == ["launchctl", "bootstrap"]


def test_server_reinstall_refuses_changed_launch_agent(repo, persistent_installer):
    import plistlib

    installer, home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    installer.install(repo, port, "/slop/test", False)
    plist_path = next((home / "Library/LaunchAgents").glob("*.plist"))
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    args[args.index("--repo") + 1] = "/foreign"
    plist_path.write_bytes(plistlib.dumps(plist))
    calls.clear()
    with pytest.raises(ValueError, match="ownership"):
        installer.install(repo, port, "/slop/test", False)
    assert not any(c[:2] == ["launchctl", "bootout"] for c in calls)


def test_server_removal_preserves_another_repository(
    repo, persistent_installer, tmp_path
):
    installer, home, calls, identity, port = persistent_installer
    runtime = str(installer.Path(installer.__file__).resolve().parent)
    identity.update(repository_path=str(repo.resolve()), source_runtime=runtime)
    installer.install(repo, port, "/slop/test", False)
    other = tmp_path / "other-repo"
    other.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        other_port = probe.getsockname()[1]
    identity["repository_path"] = str(other.resolve())
    installer.install(other, other_port, "/slop/other", False)
    other_settings = other / ".strata/server-settings.json"
    original = other_settings.read_bytes()
    history = repo / ".strata/history.jsonl"
    history.write_text("evidence\n")
    calls.clear()
    installer.remove(repo)
    assert other_settings.read_bytes() == original
    assert len(list((home / "Library/LaunchAgents").glob("*.plist"))) == 1
    assert history.read_text() == "evidence\n"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    ["repository", "port", "plist_symlink", "settings_symlink", "output_symlink"],
)
def test_server_removal_refuses_modified_ownership(
    repo, persistent_installer, mutation, monkeypatch
):
    import plistlib

    installer, home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    installer.install(repo, port, "/slop/test", False)
    output = repo / ".strata"
    settings_path = output / "server-settings.json"
    plist_path = next((home / "Library/LaunchAgents").glob("*.plist"))
    settings = json.loads(settings_path.read_text())
    settings["tailscale"] = True
    settings_path.write_text(json.dumps(settings))
    if mutation in {"repository", "port"}:
        plist = plistlib.loads(plist_path.read_bytes())
        args = plist["ProgramArguments"]
        flag = "--repo" if mutation == "repository" else "--port"
        args[args.index(flag) + 1] = (
            "/foreign" if mutation == "repository" else str(port + 1)
        )
        plist_path.write_bytes(plistlib.dumps(plist))
    else:
        target = {
            "plist_symlink": plist_path,
            "settings_symlink": settings_path,
            "output_symlink": output,
        }[mutation]
        saved = target.with_name(target.name + ".saved")
        target.rename(saved)
        target.symlink_to(saved)
    before_settings, before_plist = settings_path.read_bytes(), plist_path.read_bytes()
    calls.clear()

    def run(command, check=True):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(installer, "run", run)
    with pytest.raises(ValueError, match="ownership"):
        installer.remove(repo)
    assert calls == []
    assert settings_path.read_bytes() == before_settings
    assert plist_path.read_bytes() == before_plist


def test_server_reinstall_rejects_listener_surviving_owned_service_stop(
    repo, persistent_installer
):
    installer, _home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    installer.install(repo, port, "/slop/test", False)
    original_settings = (repo / ".strata/server-settings.json").read_bytes()
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", port))
        foreign.listen()
        calls.clear()
        with pytest.raises(OSError):
            installer.install(repo, port, "/slop/test", False)
    assert (repo / ".strata/server-settings.json").read_bytes() == original_settings
    assert len([c for c in calls if c[:2] == ["launchctl", "bootstrap"]]) == 1


def test_server_reinstall_waits_for_owned_listener_to_close(
    repo, persistent_installer, monkeypatch
):
    import time

    installer, _home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    installer.install(repo, port, "/slop/test", False)
    old = socket.socket()
    old.bind(("127.0.0.1", port))
    old.listen()
    closer = None

    def close_later():
        time.sleep(0.1)
        old.close()

    def run(command, check=True):
        nonlocal closer
        calls.append(command)
        if command[:2] == ["launchctl", "bootout"] and closer is None:
            closer = threading.Thread(target=close_later)
            closer.start()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "run", run)
    try:
        installer.install(repo, port, "/slop/test", False)
    finally:
        if closer:
            closer.join()
        old.close()


def test_server_rollback_retries_bootstrap_while_launchd_finishes_bootout(
    repo, persistent_installer, monkeypatch
):
    installer, _home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    installer.install(repo, port, "/slop/test", False)
    original = (repo / ".strata/server-settings.json").read_bytes()
    calls.clear()
    bootstraps = 0

    def run(command, check=True):
        nonlocal bootstraps
        calls.append(command)
        if command[:2] == ["launchctl", "bootstrap"]:
            bootstraps += 1
            if bootstraps == 2:
                return subprocess.CompletedProcess(
                    command, 5, stdout="", stderr="service still unloading"
                )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "run", run)
    identity["source_runtime"] = "/foreign"
    with pytest.raises(ValueError, match="identity"):
        installer.install(repo, port, "/slop/test", False)
    assert bootstraps == 3
    assert (repo / ".strata/server-settings.json").read_bytes() == original


def test_server_install_accepts_port_from_closed_http_server(
    repo, persistent_installer
):
    installer, _home, _calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    with socket.socket() as previous:
        previous.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        previous.bind(("127.0.0.1", port))
        previous.listen()
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = previous.accept()
            connection.close()
            assert client.recv(1) == b""
    installer.install(repo, port, "/slop/test", False)


def test_failed_tailnet_reinstall_preserves_owned_mount(
    repo, persistent_installer, monkeypatch
):
    installer, _home, calls, identity, port = persistent_installer
    identity.update(
        repository_path=str(repo.resolve()),
        source_runtime=str(installer.Path(installer.__file__).resolve().parent),
    )
    config = {
        "Web": {
            "host:443": {
                "Handlers": {"/slop/test": {"Proxy": f"http://127.0.0.1:{port}"}}
            }
        }
    }

    def run(command, check=True):
        calls.append(command)
        data = (
            {"Self": {"DNSName": "host.example."}}
            if command[1:] == ["status", "--json"]
            else config
        )
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(data), stderr=""
        )

    monkeypatch.setattr(installer, "run", run)
    assert (
        installer.install(repo, port, "/slop/test", True)
        == "https://host.example/slop/test/"
    )
    original = (repo / ".strata/server-settings.json").read_bytes()
    calls.clear()
    identity["source_runtime"] = "/foreign"
    with pytest.raises(ValueError, match="identity"):
        installer.install(repo, port, "/slop/test", True)
    assert (repo / ".strata/server-settings.json").read_bytes() == original
    assert not any(c[-1] == "off" for c in calls)
