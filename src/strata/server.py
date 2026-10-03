"""Serve validated dashboard data on loopback for a Tailscale proxy."""

import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from strata.dashboard import load_dataset, render_dashboard
from strata.recorder import git


class DatasetCache:
    """Serialize cache refreshes and publish only successful dataset reads."""

    def __init__(self, repo: Path, commits: list[str] | None = None) -> None:
        self.repo = repo
        self.commits = list(
            dict.fromkeys(
                git(
                    repo,
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    f"{ref}^{{commit}}",
                )
                .decode()
                .strip()
                for ref in commits or []
            )
        )
        self.output = repo / ".strata"
        self.lock = threading.Lock()
        self.key = None
        self.body = b""
        self.etag = ""
        self.data: dict = {}

    def snapshot(self) -> tuple[bytes, str, dict]:
        """Return the serialized dataset, its ETag and the parsed dataset.

        Callers must not mutate the parsed dataset; it is shared across requests.
        """
        with self.lock:
            head = (
                self.commits[-1]
                if self.commits
                else git(self.repo, "rev-parse", "HEAD").decode().strip()
            )
            stamps = []
            for name in ("history.jsonl", "queue.json"):
                path = self.output / name
                stamps.append(
                    (path.stat().st_mtime_ns, path.stat().st_size)
                    if path.exists()
                    else None
                )
            key = (head, self.commits, stamps)
            if key != self.key:
                data = load_dataset(
                    self.repo, self.output, head=head, commits=self.commits
                )
                queue_path = self.output / "queue.json"
                data["recording"] = (
                    json.loads(queue_path.read_text())
                    if queue_path.exists()
                    else {"current": None, "pending": None}
                )
                revision = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
                data["revision"] = revision
                data["source_available"] = True
                data["source_runtime"] = str(Path(__file__).resolve().parent)
                body = json.dumps(data).encode()
                self.body, self.etag, self.key = body, f'"{revision}"', key
                self.data = data
            return self.body, self.etag, self.data


class SourceError(ValueError):
    """A source request that cannot be served to the client."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


TREE_MAX_BYTES = 4 * 1024 * 1024
TREE_MAX_NODES = 10000
TREE_MAX_JSON_BYTES = 8 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 5


def request_context(data: dict, query: str, *, source: bool) -> tuple[dict, dict, dict]:
    """Authorize a recorded snapshot in the current dataset.

    Recorded commits are immutable, so a request made before the dataset
    refreshed stays valid while its scope still records that commit.
    """
    fields = parse_qs(query, keep_blank_values=True)
    required = {"commit", "scope"} | ({"path"} if source else set())
    if set(fields) != required or any(
        len(values) != 1 or not values[0] for values in fields.values()
    ):
        raise SourceError(400, "Provide each required query field exactly once")
    params = {key: values[0] for key, values in fields.items()}
    if (
        not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", params["commit"])
        or not re.fullmatch(r"[0-9a-f]{16}", params["scope"])
        or "\x00" in params.get("path", "")
    ):
        raise SourceError(400, "Invalid commit, scope or path")
    scope = next(
        (entry for entry in data["series"] if entry["id"] == params["scope"]), None
    )
    if scope is None:
        raise SourceError(404, "Measurement scope not found")
    snapshot = next(
        (point for point in scope["snapshots"] if point["commit"] == params["commit"]),
        None,
    )
    if snapshot is None:
        raise SourceError(404, "Commit was not recorded in this measurement scope")
    return params, scope, snapshot


def bounded_tree_output(repo: Path, commit: str) -> bytes:
    """Collect Git output with limits enforced while the subprocess is running."""
    try:
        with subprocess.Popen(
            ["git", "-C", str(repo), "ls-tree", "-rz", "--full-tree", commit],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                output = bytearray()
                deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise SourceError(
                                422, "File listing exceeded the time limit"
                            )
                        chunk = os.read(
                            process.stdout.fileno(), min(65536, TREE_MAX_BYTES + 1)
                        )
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > TREE_MAX_BYTES:
                            raise SourceError(
                                422, "File listing exceeded the byte limit"
                            )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError(422, "File listing exceeded the time limit")
                if process.wait(timeout=remaining):
                    raise SourceError(422, "File listing is unavailable in Git")
                return bytes(output)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    except subprocess.TimeoutExpired as error:
        raise SourceError(422, "File listing exceeded the time limit") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise SourceError(422, "File listing is unavailable in Git") from error


def read_tree(repo: Path, data: dict, query: str) -> dict:
    """List every tracked entry at an authorized commit without silently truncating."""
    params, _, _ = request_context(data, query, source=False)
    entries = []
    directories = set()
    try:
        for entry in bounded_tree_output(repo, params["commit"]).split(b"\x00"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, _ = metadata.split()
            path = raw_path.decode("utf-8")
            entry_kind = (
                "submodule"
                if mode == b"160000"
                else "symlink"
                if mode == b"120000"
                else "file"
            )
            if mode not in (b"100644", b"100755", b"120000", b"160000") or kind not in (
                b"blob",
                b"commit",
            ):
                raise SourceError(422, "Unsupported tracked entry in Git")
            entries.append({"path": path, "kind": entry_kind})
            parts = path.split("/")
            directories.update(
                "/".join(parts[:index]) for index in range(1, len(parts))
            )
            if len(entries) + len(directories) > TREE_MAX_NODES:
                raise SourceError(422, "File listing exceeded the node limit")
    except UnicodeDecodeError as error:
        raise SourceError(422, "File listing contains a non-UTF-8 path") from error
    result = {"commit": params["commit"], "entries": entries}
    if len(json.dumps(result).encode()) > TREE_MAX_JSON_BYTES:
        raise SourceError(422, "File listing exceeded the JSON response limit")
    return result


def read_source(repo: Path, data: dict, query: str) -> dict:
    """Read a bounded regular Git blob at a recorded commit."""
    params, scope, snapshot = request_context(data, query, source=True)
    commit, path = params["commit"], params["path"]
    if path.startswith("/") or any(part in {".", "..", ""} for part in path.split("/")):
        raise SourceError(404, "File is not tracked at this commit")
    details = snapshot.get("details") or {}
    file = next(
        (entry for entry in details.get("files", []) if entry["path"] == path), None
    )

    def run(*args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "-C", str(repo), "--literal-pathspecs", *args],
                check=True,
                capture_output=True,
                timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise SourceError(422, "Recorded source is unavailable in Git") from error

    tree = run("ls-tree", "-z", commit, "--", path).split(b"\x00")
    entries = [entry for entry in tree if entry]
    if len(entries) != 1:
        raise SourceError(404, "File is not tracked at this commit")
    metadata, tree_path = entries[0].split(b"\t", 1)
    mode, kind, object_id = metadata.split()
    if (
        tree_path != path.encode("utf-8")
        or mode not in (b"100644", b"100755")
        or kind != b"blob"
    ):
        raise SourceError(422, "Only regular source files can be previewed")
    object_id = object_id.decode("ascii")
    if int(run("cat-file", "-s", object_id)) > 256 * 1024:
        raise SourceError(422, "Source exceeds the 256 KiB preview limit")
    raw = run("cat-file", "blob", object_id)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceError(422, "Source is not UTF-8 text") from error
    if "\x00" in text or len(text.splitlines()) > 10000:
        raise SourceError(
            422, "Source is binary or exceeds the 10000 line preview limit"
        )
    functions = (
        [
            function
            for function in details.get("functions", [])
            if function["path"] == path
        ]
        if file is not None
        else []
    )

    def available(*fields: str) -> bool:
        return file is not None and all(
            all(
                isinstance(fn.get(field), (int, float))
                and not isinstance(fn.get(field), bool)
                and math.isfinite(fn[field])
                for field in fields
            )
            for fn in functions
        )

    suffix = Path(path).suffix.lower()
    language = {
        ".py": "python",
        ".pyi": "python",
        ".rs": "rust",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
    }.get(suffix)
    if language is None and file is not None:
        language = scope["language"]
    result = {
        "commit": commit,
        "path": path,
        "language": language,
        "text": text,
        "functions": functions,
        "measured": file is not None,
        "metric_available": {
            "cc": available("cc"),
            "erosion": available("cc", "sloc"),
            "cognitive": available("cognitive", "sloc"),
            "verbosity": file is not None
            and isinstance(file.get("verbosity_flagged_lines"), list),
        },
    }
    if result["metric_available"]["verbosity"]:
        result["flagged_lines"] = file["verbosity_flagged_lines"]

    return result


def make_server(
    repo: Path, port: int, commits: list[str] | None = None
) -> ThreadingHTTPServer:
    """Serve dashboard data and recorded Git source without filesystem routes."""
    cache = DatasetCache(repo, commits=commits)
    _, _, data = cache.snapshot()
    html = render_dashboard(data).encode()

    class Handler(BaseHTTPRequestHandler):
        def send(
            self, status: int, body: bytes, content_type: str, etag: str = ""
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self.send(200, html, "text/html; charset=utf-8")
            elif path == "/data.json":
                try:
                    body, etag, _ = cache.snapshot()
                    unchanged = self.headers.get("If-None-Match") == etag
                    self.send(
                        304 if unchanged else 200,
                        b"" if unchanged else body,
                        "application/json; charset=utf-8",
                        etag,
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    subprocess.SubprocessError,
                ) as error:
                    traceback.print_exception(error)
                    self.send(
                        503,
                        b'{"error":"Dashboard data could not be refreshed"}',
                        "application/json",
                    )
            elif path in {"/source.json", "/tree.json"}:
                try:
                    _, _, data = cache.snapshot()
                    read = read_tree if path == "/tree.json" else read_source
                    source = read(repo, data, urlsplit(self.path).query)
                    self.send(
                        200,
                        json.dumps(source).encode(),
                        "application/json; charset=utf-8",
                    )
                except SourceError as error:
                    self.send(
                        error.status,
                        json.dumps({"error": str(error)}).encode(),
                        "application/json",
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    subprocess.SubprocessError,
                ) as error:
                    traceback.print_exception(error)
                    self.send(
                        503,
                        b'{"error":"Dashboard data could not be refreshed"}',
                        "application/json",
                    )
            else:
                self.send(404, b"Not found", "text/plain")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
