"""Install a repository-local advisory SlopCodeBench hook."""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def install(repo: Path, roots: list[str], language: str) -> None:
    """Preserve existing hook locations and activate only local configuration."""
    repo = Path(git(repo.resolve(), "rev-parse", "--show-toplevel"))
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    common_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(git_dir).resolve() != Path(common_dir).resolve():
        raise ValueError(
            "Install from the primary checkout; local hook configuration is shared with linked worktrees"
        )
    for root in roots:
        path = Path(root)
        if path.is_absolute() or ".." in path.parts or not root:
            raise ValueError(
                "Source roots must be relative paths within the repository"
            )
    uvx = shutil.which("uvx")
    if uvx is None:
        raise ValueError("uvx is required; install uv before installing the hook")
    output = repo / ".slop-check"
    hooks = output / "hooks"
    settings_path = output / "settings.json"
    paths = [output, settings_path, hooks]
    if hooks.is_dir():
        paths.extend(hooks.iterdir())
    if any(path.is_symlink() for path in paths):
        raise ValueError("Hook installation paths must not be symlinks")
    if output.exists() and not settings_path.exists():
        history = output / "history.jsonl"
        reports = output / "reports"
        try:
            names = {p.name for p in output.iterdir()}
            logs = {"server.stdout.log", "server.stderr.log"}
            owned = (
                not output.is_symlink()
                and names <= {"history.jsonl", "reports", "server-settings.json"} | logs
                and all(
                    (output / name).is_file() and not (output / name).is_symlink()
                    for name in names
                    & (logs | {"history.jsonl", "server-settings.json"})
                )
                and (
                    "reports" not in names
                    or (reports.is_dir() and not reports.is_symlink())
                )
                and (
                    ({"history.jsonl", "reports"} <= names)
                    or "server-settings.json" in names
                )
                and (
                    "history.jsonl" not in names
                    or all(
                        Path(json.loads(line)["repository"]).resolve() == repo
                        for line in history.read_text().splitlines()
                    )
                )
                and (
                    "server-settings.json" not in names
                    or json.loads((output / "server-settings.json").read_text())[
                        "label"
                    ]
                    == "dev.slop-check."
                    + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            owned = False
        if not owned:
            raise ValueError(
                "Existing .slop-check directory is not owned by this installer"
            )
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        if git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks") != (
            str(hooks) if hooks.exists() else settings["previous_effective_hooks_path"]
        ):
            raise ValueError(
                "Hook configuration changed since installation; inspect before reinstalling"
            )
    else:
        old_hooks = Path(
            git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
        )
        previous = subprocess.run(
            ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
        )
        settings = {
            "previous_local_hooks_path": previous.stdout.strip()
            if previous.returncode == 0
            else None,
            "previous_effective_hooks_path": str(old_hooks),
            "previous_hooks": {
                p.name: str(p)
                for p in old_hooks.iterdir()
                if p.is_file()
                and os.access(p, os.X_OK)
                and not p.name.endswith(".sample")
            }
            if old_hooks.is_dir()
            else {},
        }
    hooks.mkdir(parents=True, exist_ok=True)
    for name, original in settings["previous_hooks"].items():
        if name == "post-commit":
            continue
        wrapper = hooks / name
        wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(original)} "$@"\n')
        wrapper.chmod(0o755)
    runtime = Path(__file__).resolve().parents[1]
    settings.update(
        source_roots=roots,
        language=language,
        uvx=uvx,
        python=sys.executable,
        source_runtime=str(runtime),
    )
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    original_post = settings["previous_hooks"].get("post-commit")
    preserved_post = f'{shlex.quote(original_post)} "$@"\n' if original_post else ""
    post = hooks / "post-commit"
    queue = shlex.quote(str(runtime / "scripts/queue_commit.py"))
    log = shlex.quote(str(output / "worker.log"))
    required = [
        runtime / "scripts" / name
        for name in ("queue_commit.py", "record_commit.py", "analyze_snapshot.py")
    ]
    missing = " || ".join(f"[ ! -r {shlex.quote(str(path))} ]" for path in required)
    error = shlex.quote(f"slop-check: shared runtime missing or incomplete: {runtime}")
    post.write_text(
        "#!/bin/sh\n"
        + preserved_post
        + f"if {missing}; then\n"
        + f"  printf '%s\\n' {error} >> {log}\n"
        + "else\n"
        + f"  {shlex.quote(sys.executable)} {queue} --repo . --output {shlex.quote(str(output))} >> {log} 2>&1\n"
        + "fi\nexit 0\n"
    )
    post.chmod(0o755)
    exclude = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    )
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ""
    if "/.slop-check/" not in current.splitlines():
        with exclude.open("a") as stream:
            stream.write("\n/.slop-check/\n")
    git(repo, "config", "--local", "core.hooksPath", str(hooks))
    print(f"Installed advisory post-commit hook. History: {output / 'history.jsonl'}")


def remove(repo: Path) -> None:
    """Restore original hook configuration while retaining measurements and logs."""
    repo = Path(git(repo.resolve(), "rev-parse", "--show-toplevel"))
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    common_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(git_dir).resolve() != Path(common_dir).resolve():
        raise ValueError("Remove from the primary checkout")
    output = repo / ".slop-check"
    hooks = output / "hooks"
    settings = json.loads((output / "settings.json").read_text())
    if git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks") != str(
        hooks
    ):
        raise ValueError(
            "Hook configuration changed since installation; inspect before removing"
        )
    previous = settings["previous_local_hooks_path"]
    if previous is None:
        git(repo, "config", "--local", "--unset", "core.hooksPath")
    else:
        git(repo, "config", "--local", "core.hooksPath", previous)
    shutil.rmtree(hooks)
    # Retain configuration for workers already processing queued commits.
    print(f"Removed advisory hook. Retained measurements: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-root", action="append")
    parser.add_argument("--language", choices=["rust", "python", "javascript"])
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.remove:
        remove(args.repo)
    else:
        if not args.source_root or not args.language:
            parser.error("--source-root and --language are required for installation")
        install(args.repo, args.source_root, args.language)


if __name__ == "__main__":
    main()
