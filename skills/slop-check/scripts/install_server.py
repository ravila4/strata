"""Install or remove a persistent local dashboard server on macOS."""

import argparse
import errno
import hashlib
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def check_mount(config: dict, mount: str, proxy: str) -> None:
    """Do not replace an existing HTTPS handler belonging to another server."""
    for host, web in config.get("Web", {}).items():
        if host.endswith(":443"):
            handler = web.get("Handlers", {}).get(mount)
            if handler and handler.get("Proxy") != proxy:
                raise ValueError(
                    f"Tailscale mount {mount} already serves another target"
                )


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=30, check=check
    )


def validate_agent_ownership(repo: Path, settings: dict, plist_path: Path) -> None:
    """Require the local configuration and launch agent to identify the same service."""
    label = "dev.slop-check." + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    output = repo / ".slop-check"
    try:
        if (
            output.is_symlink()
            or (output / "server-settings.json").is_symlink()
            or plist_path.is_symlink()
        ):
            raise ValueError("Server ownership cannot be verified through symlinks")
        plist = plistlib.loads(plist_path.read_bytes())
        arguments = plist["ProgramArguments"]
        owned = (
            settings["label"] == label
            and plist["Label"] == label
            and arguments[arguments.index("--repo") + 1] == str(repo)
            and arguments[arguments.index("--port") + 1] == str(settings["port"])
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        plistlib.InvalidFileException,
    ) as error:
        raise ValueError(
            "Launch agent ownership does not match this repository"
        ) from error
    if not owned:
        raise ValueError("Launch agent ownership does not match this repository")


def wait_for_port_release(port: int, timeout: float) -> None:
    """Allow an owned service to finish closing, rejecting surviving listeners."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
            return
        except OSError as error:
            if error.errno != errno.EADDRINUSE or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def restore_agent(domain: str, plist_path: Path) -> None:
    """Retry registration while launchd finishes unloading the replaced service."""
    deadline = time.monotonic() + 5
    command = ["launchctl", "bootstrap", domain, str(plist_path)]
    while True:
        result = run(command, check=False)
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise subprocess.CalledProcessError(
                result.returncode, command, result.stdout, result.stderr
            )
        time.sleep(0.1)


def install(repo: Path, port: int, mount: str, tailscale: bool) -> str:
    """Install a launch agent and optionally an isolated Tailscale mount."""
    if sys.platform != "darwin":
        raise ValueError(
            "Persistent installation supports macOS; run serve_dashboard.py in the foreground elsewhere"
        )
    repo = repo.resolve()
    if (
        not 1024 <= port <= 65535
        or not re.fullmatch(r"/[a-zA-Z0-9_/-]+", mount)
        or mount == "/"
    ):
        raise ValueError("Use a port from 1024 to 65535 and a non-root URL path")
    uv = shutil.which("uv")
    tail = shutil.which("tailscale")
    if uv is None or (tailscale and tail is None):
        raise ValueError("uv and, when requested, tailscale must be installed")
    proxy = f"http://127.0.0.1:{port}"
    original_mount = None
    if tailscale:
        config = json.loads(run([tail, "serve", "status", "--json"]).stdout)
        check_mount(config, mount, proxy)
        original_mount = next(
            (
                web.get("Handlers", {}).get(mount)
                for host, web in config.get("Web", {}).items()
                if host.endswith(":443")
            ),
            None,
        )
    output = repo / ".slop-check"
    output.mkdir(exist_ok=True)
    settings_path = output / "server-settings.json"
    label = "dev.slop-check." + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    domain = f"gui/{os.getuid()}"
    previous_bytes = settings_path.read_bytes() if settings_path.exists() else None
    if settings_path.exists():
        previous = json.loads(settings_path.read_text())
        if (
            previous["port"] != port
            or previous["mount"] != mount
            or previous.get("tailscale", False) != tailscale
        ):
            raise ValueError(
                "Remove the existing server before changing its port or URL mount"
            )
    if previous_bytes is None:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    scripts = Path(__file__).resolve().parent
    runtime = str(scripts.parent)
    run([uv, "run", "--script", str(scripts / "serve_dashboard.py"), "--help"])
    command = [
        uv,
        "run",
        "--offline",
        "--script",
        str(scripts / "serve_dashboard.py"),
        "--repo",
        str(repo),
        "--port",
        str(port),
    ]
    plist_path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": label,
        "ProgramArguments": command,
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "StandardOutPath": str(output / "server.stdout.log"),
        "StandardErrorPath": str(output / "server.stderr.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    }
    previous_plist = plist_path.read_bytes() if plist_path.exists() else None
    if previous_bytes is not None:
        validate_agent_ownership(repo, previous, plist_path)
        registered = run(["launchctl", "print", f"{domain}/{label}"], check=False)
        if registered.returncode != 0:
            raise ValueError("Existing launch agent ownership could not be verified")
        run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
    mount_attempted = False
    launch_attempted = False
    try:
        wait_for_port_release(port, 5 if previous_bytes is not None else 0)
        plist_path.write_bytes(plistlib.dumps(plist))
        launch_attempted = True
        run(["launchctl", "bootstrap", domain, str(plist_path)])
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(
                    proxy + "/data.json", timeout=1
                ) as response:
                    data = json.load(response)
                if (
                    data.get("repository_path") != str(repo)
                    or data.get("source_runtime") != runtime
                ):
                    raise ValueError(
                        "Dashboard endpoint identity does not match the repository and shared runtime"
                    )
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Dashboard failed to start; inspect {output / 'server.stderr.log'}"
                    )
                time.sleep(0.2)
        url = proxy + "/"
        settings = {
            "label": label,
            "port": port,
            "mount": mount,
            "tailscale": False,
            "url": url,
            "source_runtime": runtime,
        }
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        if tailscale:
            mount_attempted = True
            result = run(
                [tail, "serve", "--bg", "--https=443", "--set-path=" + mount, proxy]
            )
            state = json.loads(run([tail, "status", "--json"]).stdout)
            dns = state["Self"]["DNSName"].rstrip(".")
            url = f"https://{dns}{mount}/"
            settings["tailscale"] = True
            settings["url"] = url
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            print(result.stdout.strip())
    except BaseException:
        if mount_attempted and original_mount is None:
            try:
                config = json.loads(run([tail, "serve", "status", "--json"]).stdout)
                check_mount(config, mount, proxy)
                run([tail, "serve", "--https=443", "--set-path=" + mount, "off"])
            except (OSError, ValueError, subprocess.SubprocessError):
                print(
                    f"Inspect Tailscale mount {mount}; automatic cleanup could not complete",
                    file=sys.stderr,
                )
        if launch_attempted:
            run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
        if previous_bytes is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(previous_bytes)
        if previous_plist is None:
            plist_path.unlink(missing_ok=True)
        else:
            plist_path.write_bytes(previous_plist)
            try:
                restore_agent(domain, plist_path)
            except (OSError, subprocess.SubprocessError) as error:
                print(
                    f"Previous dashboard could not be restarted; inspect {plist_path}: {error}",
                    file=sys.stderr,
                )
        raise
    print(url)
    return url


def remove(repo: Path) -> None:
    """Remove only this repository's launch agent and matching proxy mount."""
    repo = repo.resolve()
    output = repo / ".slop-check"
    path = output / "server-settings.json"
    settings = json.loads(path.read_text())
    label = "dev.slop-check." + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    plist_path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    validate_agent_ownership(repo, settings, plist_path)
    if settings["tailscale"]:
        tail = shutil.which("tailscale")
        if tail is None:
            raise ValueError("tailscale is required to remove the owned mount")
        config = json.loads(run([tail, "serve", "status", "--json"]).stdout)
        check_mount(config, settings["mount"], f"http://127.0.0.1:{settings['port']}")
        run([tail, "serve", "--https=443", "--set-path=" + settings["mount"], "off"])
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], check=False)
    plist_path.unlink()
    path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--mount", help="Tailnet URL path; defaults to /slop/<repository name>"
    )
    parser.add_argument("--tailscale", action="store_true")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.remove:
        remove(args.repo)
    else:
        mount = args.mount or "/slop/" + re.sub(
            r"[^a-zA-Z0-9_-]", "-", args.repo.resolve().name
        )
        install(args.repo, args.port, mount.rstrip("/"), args.tailscale)


if __name__ == "__main__":
    main()
