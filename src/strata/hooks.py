"""Install a repository-local advisory hook that records each commit."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from strata.recorder import SETTINGS_FORMAT, canonical_roots, exclude_output


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def install(repo: Path, roots: list[str]) -> None:
    """Preserve existing hook locations and activate only local configuration."""
    repo = Path(git(repo.resolve(), "rev-parse", "--show-toplevel"))
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    common_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(git_dir).resolve() != Path(common_dir).resolve():
        raise ValueError(
            "Install from the primary checkout; local hook configuration is shared with linked worktrees"
        )
    roots = canonical_roots(roots)
    output = repo / ".strata"
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
            lock_files = {"worker.lock", "queue.lock"}
            owned = (
                not output.is_symlink()
                and names
                <= {"history.jsonl", "reports", "server-settings.json"}
                | logs
                | lock_files
                and all(
                    (output / name).is_file() and not (output / name).is_symlink()
                    for name in names
                    & (logs | lock_files | {"history.jsonl", "server-settings.json"})
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
                    == "dev.strata."
                    + hashlib.sha256(str(repo).encode()).hexdigest()[:12]
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            owned = False
        if not owned:
            raise ValueError(
                "Existing .strata directory is not owned by this installer"
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
    settings.update(source_roots=roots, format=SETTINGS_FORMAT, python=sys.executable)
    with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as stream:
        json.dump(settings, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(settings_path)
    original_post = settings["previous_hooks"].get("post-commit")
    preserved_post = f'{shlex.quote(original_post)} "$@"\n' if original_post else ""
    post = hooks / "post-commit"
    python = shlex.quote(sys.executable)
    log = shlex.quote(str(output / "worker.log"))
    error = shlex.quote(f"strata: interpreter missing: {sys.executable}")
    # Isolated mode keeps the committing repository off the import path.
    post.write_text(
        "#!/bin/sh\n"
        + preserved_post
        + f"if [ -x {python} ]; then\n"
        + f"  {python} -I -m strata.worker --repo . --output {shlex.quote(str(output))} >> {log} 2>&1\n"
        + "else\n"
        + f"  printf '%s\\n' {error} >> {log}\n"
        + "fi\nexit 0\n"
    )
    post.chmod(0o755)
    exclude_output(repo)
    git(repo, "config", "--local", "core.hooksPath", str(hooks))
    print(f"Installed advisory post-commit hook. History: {output / 'history.jsonl'}")


def remove(repo: Path) -> None:
    """Restore original hook configuration while retaining measurements and logs."""
    repo = Path(git(repo.resolve(), "rev-parse", "--show-toplevel"))
    git_dir = git(repo, "rev-parse", "--absolute-git-dir")
    common_dir = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if Path(git_dir).resolve() != Path(common_dir).resolve():
        raise ValueError("Remove from the primary checkout")
    output = repo / ".strata"
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
