# /// script
# requires-python = ">=3.10"
# dependencies = ["plotly==6.3.1"]
# ///
"""Serve validated dashboard data on loopback for a Tailscale proxy."""

import argparse
import hashlib
import json
import re
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from generate_dashboard import load_dataset, render_dashboard
from record_commit import git


class DatasetCache:
    """Serialize cache refreshes and publish only successful dataset reads."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.output = repo / ".slop-check"
        self.lock = threading.Lock()
        self.key = None
        self.body = b""
        self.etag = ""

    def snapshot(self) -> tuple[bytes, str]:
        with self.lock:
            head = git(self.repo, "rev-parse", "HEAD").decode().strip()
            stamps = []
            for name in ("history.jsonl", "queue.json"):
                path = self.output / name
                stamps.append(
                    (path.stat().st_mtime_ns, path.stat().st_size)
                    if path.exists()
                    else None
                )
            key = (head, stamps)
            if key != self.key:
                data = load_dataset(self.repo, self.output, head=head)
                queue_path = self.output / "queue.json"
                data["recording"] = (
                    json.loads(queue_path.read_text())
                    if queue_path.exists()
                    else {"current": None, "pending": None}
                )
                revision = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
                data["revision"] = revision
                data["source_available"] = True
                body = json.dumps(data).encode()
                self.body, self.etag, self.key = body, f'"{revision}"', key
            return self.body, self.etag


class SourceError(ValueError):
    """A source request that cannot be served to the client."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def read_source(repo: Path, data: dict, query: str) -> dict:
    """Read a bounded regular Git blob authorized by one dataset revision."""
    fields = parse_qs(query, keep_blank_values=True)
    if set(fields) != {"commit", "path", "scope", "revision"} or any(
        len(values) != 1 or not values[0] for values in fields.values()
    ):
        raise SourceError(400, "Provide commit, path, scope and revision exactly once")
    params = {key: values[0] for key, values in fields.items()}
    commit, path = params["commit"], params["path"]
    if (
        not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)
        or not re.fullmatch(r"[0-9]{1,9}", params["scope"])
        or "\x00" in path
    ):
        raise SourceError(400, "Invalid commit, scope or path")
    if params["revision"] != data["revision"]:
        raise SourceError(409, "Measurements changed; refresh and reopen the source")
    scope_index = int(params["scope"])
    if scope_index >= len(data["series"]):
        raise SourceError(404, "Measurement scope not found")
    scope = data["series"][scope_index]
    snapshot = next(
        (point for point in scope["snapshots"] if point["commit"] == commit), None
    )
    details = snapshot.get("details") if snapshot else None
    file = (
        next((entry for entry in details["files"] if entry["path"] == path), None)
        if details
        else None
    )
    if file is None:
        raise SourceError(404, "File was not recorded in this measurement")

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
        raise SourceError(422, "Recorded source is unavailable in Git")
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
    result = {
        "commit": commit,
        "path": path,
        "language": scope["language"],
        "text": text,
        "functions": [
            function for function in details["functions"] if function["path"] == path
        ],
    }
    if "verbosity_flagged_lines" in file:
        result["flagged_lines"] = file["verbosity_flagged_lines"]
    return result


def make_server(repo: Path, port: int) -> ThreadingHTTPServer:
    """Serve dashboard data and recorded Git source without filesystem routes."""
    cache = DatasetCache(repo)
    body, _ = cache.snapshot()
    html = render_dashboard(json.loads(body)).encode()

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
                    body, etag = cache.snapshot()
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
            elif path == "/source.json":
                try:
                    body, _ = cache.snapshot()
                    source = read_source(
                        repo, json.loads(body), urlsplit(self.path).query
                    )
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with make_server(args.repo.resolve(), args.port) as server:
        print(
            f"Dashboard listening on http://127.0.0.1:{server.server_port}/", flush=True
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
