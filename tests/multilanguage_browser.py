"""Rendered mixed-language recording navigation and source regression checks."""

import json
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from strata import recorder
from strata import server as server_module
from test_hooks import git


def check_live_following(browser, recorder, server_module) -> None:
    """A live page shows new recordings whether it opened empty or at the latest."""
    with tempfile.TemporaryDirectory() as temporary:
        repo = Path(temporary)
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.com")
        (repo / "src").mkdir()
        (repo / "src/main.py").write_text("def decision(x):\n    return x + 1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "initial")
        output = repo / ".strata"
        server = server_module.make_server(repo, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_port}")
            assert page.locator("#recording-coverage").is_hidden()
            for subject in ("first", "second"):
                if subject == "second":
                    git(repo, "commit", "--allow-empty", "-qm", subject)
                recorder.record_all(repo, output, ["src"], sys.executable)
                commit = git(repo, "rev-parse", "HEAD")[:8]
                page.wait_for_function(
                    "commit=>!document.getElementById('recording-coverage').hidden"
                    " && document.querySelector('#snapshot option:checked')"
                    "?.textContent.startsWith(commit)",
                    arg=commit,
                    timeout=10000,
                )
                assert (
                    page.locator("#recording option:checked")
                    .inner_text()
                    .startswith(commit)
                )
            page.close()
        finally:
            server.shutdown()
            server.server_close()


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repo = Path(temporary)
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.com")
        (repo / "src").mkdir()
        (repo / "web").mkdir()
        (repo / "src/main.py").write_text("def decision(x):\n    return x + 1\n")
        (repo / "web/main.js").write_text("function decision(x) { return x + 1; }\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "initial mixed source")
        output = repo / ".strata"
        recorder.record_all(repo, output, ["src", "web"], sys.executable)
        first = git(repo, "rev-parse", "HEAD")
        git(repo, "commit", "--allow-empty", "-qm", "second")
        recorder.record_all(repo, output, ["src", "web"], sys.executable)
        rows = [
            json.loads(line)
            for line in (output / "history.jsonl").read_text().splitlines()
        ]
        server = server_module.make_server(repo, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = f"http://127.0.0.1:{server.server_port}"
        screenshots = Path(".scratch")
        screenshots.mkdir(exist_ok=True)
        try:
            with sync_playwright() as playwright:
                for engine in (playwright.chromium, playwright.webkit):
                    browser = engine.launch()
                    try:
                        for width in (320, 390, 1440):
                            page = browser.new_page(
                                viewport={"width": width, "height": 900},
                                is_mobile=width < 500,
                                has_touch=width < 500,
                            )
                            errors = []
                            page.on(
                                "pageerror", lambda error: errors.append(str(error))
                            )
                            page.goto(address)
                            page.wait_for_selector("#files-body .source-button")
                            page.locator("#recording").select_option(
                                rows[0]["recording_id"]
                            )
                            for language, path in [
                                ("python", "src/main.py"),
                                ("javascript", "web/main.js"),
                            ]:
                                page.locator("#recording-language").select_option(
                                    language
                                )
                                assert (
                                    first[:8]
                                    in page.locator("#coverage-status").inner_text()
                                )
                                page.locator(
                                    f'#files-body .source-button[data-path="{path}"]'
                                ).click()
                                page.wait_for_selector("#source-code .hljs-keyword")
                                assert (
                                    page.locator("#source-title").inner_text() == path
                                )
                                page.locator("#source-close").click()
                                assert page.locator("#functions-body tr").count() == 1
                                if language == "python":
                                    page.locator("#root").select_option("src")
                                    page.locator("#files-body .file-button").click()
                                    assert page.locator("#clear-file").is_visible()
                            page.locator("#recording-language").select_option("rust")
                            assert (
                                "No eligible source"
                                in page.locator("#coverage-status").inner_text()
                            )
                            assert (
                                page.locator("#loc-value").inner_text() == "Unavailable"
                            )
                            assert page.locator("#files-body tr").count() == 0
                            page.locator("#recording-language").select_option(
                                "javascript"
                            )
                            page.locator("#root").select_option("web")
                            page.locator("#scope").select_option("0")
                            assert (
                                first[:8]
                                in page.locator("#coverage-status").inner_text()
                            )
                            page.locator("#recording-language").select_option(
                                "javascript"
                            )
                            page.locator("#root").select_option("web")
                            recorder.append_history(
                                output,
                                {
                                    "commit": first,
                                    "timestamp": rows[0]["timestamp"],
                                    "status": "skipped",
                                    "superseded_by": first,
                                },
                            )
                            # Polling must preserve the chosen commit, root and language.
                            page.wait_for_timeout(5500)
                            assert (
                                page.locator("#recording").input_value()
                                == rows[0]["recording_id"]
                            )
                            assert page.locator("#root").input_value() == "web"
                            assert (
                                page.locator("#recording-language").input_value()
                                == "javascript"
                            )
                            assert (
                                page.evaluate("document.documentElement.scrollWidth")
                                == width
                            )
                            page.screenshot(
                                path=str(
                                    screenshots / f"mixed-{engine.name}-{width}.png"
                                ),
                                full_page=True,
                            )
                            assert errors == [], errors
                            page.close()
                        # Individual evidence remains explicitly selected across a data refresh.
                        individual = rows[2].copy()
                        individual.pop("recording_id")
                        recorder.append_history(output, individual)
                        page = browser.new_page(viewport={"width": 390, "height": 900})
                        page.goto(address)
                        page.wait_for_function(
                            "[...document.querySelectorAll('#scope option')].some(node=>node.textContent.includes('individual measurement'))"
                        )
                        legacy_scope = page.locator("#scope option").evaluate_all(
                            "nodes=>nodes.find(node=>node.textContent.includes('individual measurement')).value"
                        )
                        page.locator("#scope").select_option(legacy_scope)
                        assert page.locator("#recording-coverage").is_hidden()
                        recorder.append_history(
                            output,
                            {
                                "commit": first,
                                "timestamp": rows[0]["timestamp"],
                                "status": "skipped",
                            },
                        )
                        page.wait_for_timeout(5500)
                        assert page.locator("#recording-coverage").is_hidden()
                        assert (
                            first[:8]
                            in page.locator("#snapshot option:checked").inner_text()
                        )
                        page.close()
                        # A new failed attempt must remove old success and remain navigable.
                        failed = rows[0] | {"recording_id": "failed-attempt"}
                        recorder.append_history(output, failed)
                        for language in failed["languages"]:
                            recorder.append_history(
                                output,
                                failed | {"language": language, "status": "failed"},
                            )
                        page = browser.new_page(viewport={"width": 390, "height": 900})
                        page.goto(address)
                        page.locator("#recording").select_option("failed-attempt")
                        assert page.locator("#scope").is_enabled()
                        assert page.locator("#loc-value").inner_text() == "Unavailable"
                        assert "Failed" in page.locator("#coverage-status").inner_text()
                        page.screenshot(
                            path=str(screenshots / f"mixed-failed-{engine.name}.png"),
                            full_page=True,
                        )
                        page.locator("#scope").select_option("0")
                        assert (
                            page.locator("#recording").input_value() == "failed-attempt"
                        )
                        assert (
                            first[:8] in page.locator("#coverage-status").inner_text()
                        )
                        page.locator("#scope").select_option(legacy_scope)
                        assert page.locator("#snapshot").is_enabled()
                        assert page.locator("#root").is_enabled()
                        assert page.locator("#no-data").is_hidden()
                        assert page.locator("#files-body tr").count() == 1
                        # Start without a terminal row is incomplete, not a successful recording.
                        recorder.append_history(
                            output, failed | {"recording_id": "interrupted"}
                        )
                        page.reload()
                        page.locator("#recording").select_option("interrupted")
                        assert (
                            "No result recorded"
                            in page.locator("#coverage-status").inner_text()
                        )
                        assert page.locator("#recording-language").is_enabled()
                        page.close()
                        check_live_following(browser, recorder, server_module)
                    finally:
                        browser.close()
                    # Restore original fixture for the next browser.
                    (output / "history.jsonl").write_text(
                        "".join(json.dumps(row) + "\n" for row in rows)
                    )
        finally:
            server.shutdown()
            server.server_close()
    print(
        "Mixed-language browser checks passed in Chromium and WebKit at desktop/mobile sizes"
    )


if __name__ == "__main__":
    main()
