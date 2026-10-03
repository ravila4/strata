"""Command-line entry point for recording and serving code metrics."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from strata import hooks, recorder, server, service


def scan(args: argparse.Namespace) -> None:
    recorder.scan(args.repo, args.revision, args.source_root)


def hook_install(args: argparse.Namespace) -> None:
    hooks.install(args.repo, args.source_root)


def hook_remove(args: argparse.Namespace) -> None:
    hooks.remove(args.repo)


def serve(args: argparse.Namespace) -> None:
    with server.make_server(
        args.repo.resolve(), args.port, commits=args.commit
    ) as httpd:
        print(
            f"Dashboard listening on http://127.0.0.1:{httpd.server_port}/", flush=True
        )
        httpd.serve_forever()


def service_install(args: argparse.Namespace) -> None:
    mount = args.mount or "/strata/" + re.sub(
        r"[^a-zA-Z0-9_-]", "-", args.repo.resolve().name
    )
    service.install(args.repo, args.port, mount.rstrip("/"), args.tailscale)


def service_remove(args: argparse.Namespace) -> None:
    service.remove(args.repo)


def parser() -> argparse.ArgumentParser:
    repo = argparse.ArgumentParser(add_help=False)
    repo.add_argument(
        "--repo", type=Path, default=Path("."), help="repository (default: current)"
    )
    roots = argparse.ArgumentParser(add_help=False)
    roots.add_argument(
        "--source-root",
        action="append",
        help="directory to measure, relative to the repository; repeatable",
    )
    port = argparse.ArgumentParser(add_help=False)
    port.add_argument("--port", type=int, default=8766)

    root = argparse.ArgumentParser(prog="strata", description=__doc__)
    commands = root.add_subparsers(required=True, metavar="command")

    command = commands.add_parser(
        "scan",
        parents=[repo, roots],
        help="measure one commit",
        description="Measure one commit with explicit source roots or the hook settings.",
    )
    command.add_argument("revision", nargs="?", default="HEAD")
    command.set_defaults(run=scan)

    hook = commands.add_parser("hook", help="record every commit in the background")
    hook_commands = hook.add_subparsers(required=True, metavar="action")
    command = hook_commands.add_parser(
        "install", parents=[repo, roots], help="install the post-commit hook"
    )
    command.set_defaults(run=hook_install, require_roots=True)
    command = hook_commands.add_parser(
        "remove", parents=[repo], help="restore the previous hook configuration"
    )
    command.set_defaults(run=hook_remove)

    command = commands.add_parser(
        "serve", parents=[repo, port], help="serve the dashboard on loopback"
    )
    command.add_argument(
        "--commit",
        action="append",
        help="show only these revisions in the supplied order; repeat for a comparison",
    )
    command.set_defaults(run=serve)

    svc = commands.add_parser("service", help="run the dashboard persistently (macOS)")
    svc_commands = svc.add_subparsers(required=True, metavar="action")
    command = svc_commands.add_parser(
        "install", parents=[repo, port], help="install a launch agent"
    )
    command.add_argument(
        "--mount", help="tailnet URL path (default: /strata/<repository name>)"
    )
    command.add_argument(
        "--tailscale", action="store_true", help="also serve over Tailscale HTTPS"
    )
    command.set_defaults(run=service_install)
    command = svc_commands.add_parser(
        "remove", parents=[repo], help="remove this repository's launch agent"
    )
    command.set_defaults(run=service_remove)
    return root


def main(argv: list[str] | None = None) -> None:
    root = parser()
    args = root.parse_args(argv)
    if getattr(args, "require_roots", False) and not args.source_root:
        root.error("--source-root is required")
    try:
        args.run(args)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode(errors="replace").strip()
        root.error(detail or f"git failed: {' '.join(map(str, error.cmd))}")
    except (OSError, ValueError) as error:
        root.error(str(error))
    except KeyboardInterrupt:
        sys.exit(130)
