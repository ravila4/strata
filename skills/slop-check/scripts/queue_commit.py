"""Finish the active analysis, then measure only the newest pending commit."""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from record_commit import append_history, git, record


def read_queue(output: Path) -> dict:
    path = output / "queue.json"
    return (
        json.loads(path.read_text())
        if path.exists()
        else {"current": None, "pending": None}
    )


def save_queue(output: Path, state: dict) -> None:
    """Replace state atomically; callers hold queue.lock."""
    with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as stream:
        json.dump(state, stream)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(output / "queue.json")


def enqueue(repo: Path, output: Path) -> None:
    """Capture provenance before detaching from Git's hook environment."""
    repo = Path(git(repo, "rev-parse", "--show-toplevel").decode().strip())
    job = {
        "repository": str(repo),
        "commit": git(repo, "rev-parse", "HEAD").decode().strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    environment = os.environ.copy()
    for name in git(repo, "rev-parse", "--local-env-vars").decode().splitlines():
        environment.pop(name, None)
    with (output / "queue.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_queue(output)
        previous = state.get("pending")
        if previous:
            append_history(
                output, previous | {"status": "skipped", "superseded_by": job["commit"]}
            )
        state["pending"] = job
        save_queue(output, state)
        with (output / "worker.log").open("ab") as log:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--output",
                    str(output),
                ],
                cwd=repo,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )


def work(output: Path) -> None:
    """Use one worker lock; release it under the queue lock at idle exit."""
    with (output / "worker.lock").open("a") as worker_lock:
        try:
            fcntl.flock(worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            with (output / "queue.lock").open("a") as queue_lock:
                fcntl.flock(queue_lock, fcntl.LOCK_EX)
                state = read_queue(output)
                job = state.get("pending")
                if job is None:
                    state["current"] = None
                    save_queue(output, state)
                    fcntl.flock(worker_lock, fcntl.LOCK_UN)
                    return
                state.update(current=job, pending=None)
                save_queue(output, state)
            try:
                settings = json.loads((output / "settings.json").read_text())
                record(
                    Path(job["repository"]),
                    output,
                    settings["source_roots"],
                    settings["language"],
                    settings["uvx"],
                    commit=job["commit"],
                )
            except Exception as error:
                # Isolate unexpected recorder failures so newer pending commits still run.
                traceback.print_exc()
                append_history(
                    output,
                    job
                    | {
                        "status": "failed",
                        "error": str(error),
                        "diagnostics": "worker.log",
                    },
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.worker:
        work(output)
    else:
        if args.repo is None:
            parser.error("--repo is required when enqueueing")
        try:
            enqueue(args.repo.resolve(), output)
        except Exception:
            with (output / "worker.log").open("a") as log:
                traceback.print_exc(file=log)
            print(
                f"slop-check: could not enqueue; see {output / 'worker.log'}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
