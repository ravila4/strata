"""Install a repository-local advisory SlopCodeBench hook."""

import argparse
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
    if output.exists() and not settings_path.exists():
        raise ValueError(
            "Existing .slop-check directory is not owned by this installer"
        )
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        if git(
            repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks"
        ) != str(hooks):
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
    shutil.copyfile(
        Path(__file__).with_name("record_commit.py"), output / "record_commit.py"
    )
    shutil.copyfile(
        Path(__file__).with_name("queue_commit.py"), output / "queue_commit.py"
    )
    shutil.copyfile(
        Path(__file__).with_name("analyze_snapshot.py"), output / "analyze_snapshot.py"
    )
    settings.update(
        source_roots=roots, language=language, uvx=uvx, python=sys.executable
    )
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    original_post = settings["previous_hooks"].get("post-commit")
    preserved_post = f'{shlex.quote(original_post)} "$@"\n' if original_post else ""
    post = hooks / "post-commit"
    post.write_text(
        "#!/bin/sh\n"
        + preserved_post
        + f"{shlex.quote(sys.executable)} {shlex.quote(str(output / 'queue_commit.py'))} --repo . --output {shlex.quote(str(output))}\n"
        + "exit 0\n"
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-root", action="append", required=True)
    parser.add_argument(
        "--language", choices=["rust", "python", "javascript"], required=True
    )
    args = parser.parse_args()
    install(args.repo, args.source_root, args.language)


if __name__ == "__main__":
    main()
