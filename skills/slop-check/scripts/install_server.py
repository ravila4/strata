"""Install or remove a persistent local dashboard server on macOS."""

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
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
            probe.bind(("127.0.0.1", port))
    bundle = output / "dashboard-tool"
    staging = Path(tempfile.mkdtemp(prefix="dashboard-stage-", dir=output))
    scripts = staging / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("serve_dashboard.py", "generate_dashboard.py", "record_commit.py"):
        shutil.copyfile(Path(__file__).with_name(name), scripts / name)
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "assets",
        staging / "assets",
        dirs_exist_ok=True,
    )
    try:
        run([uv, "run", "--script", str(scripts / "serve_dashboard.py"), "--help"])
    except BaseException:
        shutil.rmtree(staging)
        raise
    scripts = bundle / "scripts"
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
    backup = output / "dashboard-tool.previous"
    if backup.exists():
        shutil.rmtree(staging)
        raise ValueError("A previous dashboard installation backup needs inspection")
    if previous_bytes is not None:
        run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
    mount_attempted = False
    published = False
    try:
        if bundle.exists():
            bundle.rename(backup)
        staging.rename(bundle)
        published = True
        plist_path.write_bytes(plistlib.dumps(plist))
        run(["launchctl", "bootstrap", domain, str(plist_path)])
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(
                    proxy + "/data.json", timeout=1
                ) as response:
                    json.load(response)
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
        run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
        if published:
            shutil.rmtree(bundle)
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists():
            backup.rename(bundle)
        if previous_bytes is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(previous_bytes)
        if previous_plist is None:
            plist_path.unlink(missing_ok=True)
        else:
            plist_path.write_bytes(previous_plist)
            run(["launchctl", "bootstrap", domain, str(plist_path)], check=False)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    print(url)
    return url


def remove(repo: Path) -> None:
    """Remove only this repository's launch agent and matching proxy mount."""
    repo = repo.resolve()
    output = repo / ".slop-check"
    path = output / "server-settings.json"
    settings = json.loads(path.read_text())
    label = "dev.slop-check." + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    if settings["label"] != label:
        raise ValueError("Server ownership does not match this repository")
    if settings["tailscale"]:
        tail = shutil.which("tailscale")
        if tail is None:
            raise ValueError("tailscale is required to remove the owned mount")
        config = json.loads(run([tail, "serve", "status", "--json"]).stdout)
        check_mount(config, settings["mount"], f"http://127.0.0.1:{settings['port']}")
        run([tail, "serve", "--https=443", "--set-path=" + settings["mount"], "off"])
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], check=False)
    (Path.home() / "Library/LaunchAgents" / f"{label}.plist").unlink(missing_ok=True)
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
