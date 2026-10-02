# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "plotly==6.3.1", "pytest"]
# ///
"""Browser regression checks for the live source dialog; install Chromium and WebKit first."""

from copy import deepcopy
from playwright.sync_api import sync_playwright
from test_hooks import load

render_dashboard = load("generate_dashboard").render_dashboard
text = '// <img src=x onerror="window.sourceInjection=true">\nfn example() {\n    let text = "multi\\nline";\n}\n'
commit = "a" * 40
functions = [
    {
        "path": "src/example.rs",
        "name": "example",
        "line": 2,
        "end_line": 4,
        "cc": 12,
        "cognitive": 15,
        "sloc": 3,
    }
]
source = {
    "commit": commit,
    "path": "src/example.rs",
    "language": "rust",
    "text": text,
    "functions": functions,
}
point = {
    "commit": commit,
    "order": 0,
    "date": "2026-10-02T12:00:00Z",
    "subject": "Example",
    "duration_seconds": 1,
    "metrics": {
        "total_loc": 3,
        "total_functions": 1,
        "erosion": 0.5,
        "cog_erosion": 0.5,
        "verbosity": 0,
        "verbosity_flagged_loc": 0,
        "clone_loc": 0,
    },
    "details": {
        "files": [
            {
                "path": "src/example.rs",
                "sloc": 3,
                "clone_loc": 0,
                "verbosity_flagged_loc": 0,
            }
        ],
        "functions": functions,
    },
}
data = {
    "repository": "Example",
    "head": commit,
    "series": [
        {
            "language": "rust",
            "roots": ["src"],
            "policy": "production",
            "analyzer": "scb-check",
            "scope": "full",
            "snapshots": [point],
        }
    ],
    "events": [],
    "warnings": [],
    "source_available": True,
    "revision": "test-r",
}
html = render_dashboard(data)
with sync_playwright() as p:
    for engine in (p.webkit, p.chromium):
        browser = engine.launch()
        for width in (320, 390, 1440):
            page = browser.new_page(
                viewport={"width": width, "height": 900}, is_mobile=width < 600
            )
            page.route("**/data.json", lambda r: r.fulfill(json=data))
            page.route("**/source.json?*", lambda r: r.fulfill(json=source))
            page.route(
                "**/slop/aurene/",
                lambda r: r.fulfill(body=html, content_type="text/html"),
            )
            page.goto("http://preview/slop/aurene/")
            button = page.locator("#files-body .source-button").first
            assert button.locator('svg[aria-hidden="true"]').count() == 1
            assert button.inner_text() == ""
            icon = button.locator("svg").bounding_box()
            assert icon["width"] == 16 and icon["height"] == 16
            button.click()
            page.wait_for_selector("#source-code .hljs-keyword")
            assert page.locator("#source-dialog").evaluate("(e)=>e.open")
            assert page.locator("#source-code").inner_text() == text
            line_count = len(text.splitlines())
            code_height = page.locator("#source-code").bounding_box()["height"]
            gutter_height = page.locator("#source-gutter").bounding_box()["height"] - 24
            assert code_height == gutter_height == line_count * 20
            assert not page.evaluate("window.sourceInjection===true")
            assert page.locator("#source-gutter .hot").count() == 3
            assert page.evaluate("document.documentElement.scrollWidth") == width
            page.select_option("#source-metric", "verbosity")
            assert "not recorded" in page.locator("#source-legend").inner_text()
            page.select_option("#source-metric", "erosion")
            assert page.locator("#source-gutter .hot").count() == 3
            page.keyboard.press("Escape")
            assert not page.locator("#source-dialog").evaluate("(e)=>e.open")
            page.wait_for_function(
                "document.activeElement===document.querySelector('#files-body .source-button')"
            )
            page.locator("#functions-body .source-button").first.click()
            page.wait_for_selector("#source-code .hljs-keyword")
            page.locator("#source-close").click()
            page.route(
                "**/source.json?*",
                lambda r: r.fulfill(
                    status=409, json={"error": "Measurements changed. Reopen source."}
                ),
            )
            button.click()
            page.wait_for_function(
                "document.getElementById('source-status').textContent.includes('Reopen')"
            )
            assert page.locator("#source-code").inner_text() == ""
            page.locator("#source-close").click()
            if width == 390:
                pending = []
                page.route("**/source.json?*", lambda r: pending.append(r))
                button.click()
                page.wait_for_function(
                    "document.getElementById('source-status').textContent==='Loading source…'"
                )
                for _ in range(100):
                    if pending:
                        break
                    page.wait_for_timeout(10)
                assert len(pending) == 1
                page.locator("#source-close").click()
                pending.pop().abort()
                page.route("**/source.json?*", lambda r: r.fulfill(json=source))
                button.click()
                page.wait_for_selector("#source-code .hljs-keyword")
                assert page.locator("#source-code").inner_text() == text
                updated = deepcopy(data)
                updated["head"] = "b" * 40
                updated["revision"] = "new-r"
                updated["series"][0]["snapshots"][0]["commit"] = "b" * 40
                updated["series"][0]["snapshots"][0]["details"]["functions"][0][
                    "cc"
                ] = 2
                page.route("**/data.json", lambda r: r.fulfill(json=updated))
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_function(
                    "document.getElementById('head-value').textContent==='bbbbbbbb'"
                )
                page.select_option("#source-metric", "cc")
                assert "aaaaaaaaaaaa" in page.locator("#source-meta").inner_text()
                assert "12 CC" in page.locator(
                    "#source-gutter .hot"
                ).first.get_attribute("title")
                assert page.locator("#source-code").inner_text() == text
                page.locator("#source-close").click()
            print(
                engine.name,
                width,
                "source/syntax/heat/errors/focus/width passed",
                flush=True,
            )
            page.close()
        browser.close()
