# /// script
# requires-python = ">=3.10"
# dependencies = ["plotly==6.3.1"]
# ///
"""Serve validated dashboard data on loopback for a Tailscale proxy."""

import argparse
import hashlib
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

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
                body = json.dumps(data).encode()
                self.body, self.etag, self.key = body, f'"{revision}"', key
            return self.body, self.etag


def make_server(repo: Path, port: int) -> ThreadingHTTPServer:
    """Allow only dashboard HTML and metric JSON, never filesystem paths."""
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
                except (OSError, ValueError, KeyError, TypeError) as error:
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
