# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "plotly==6.3.1", "pytest"]
# ///
"""Browser regression checks for the live source dialog; install Chromium and WebKit first."""

from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright
from test_hooks import load

render_dashboard = load("dashboard").render_dashboard
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
    "measured": True,
    "metric_available": {
        "cc": True,
        "erosion": True,
        "cognitive": True,
        "verbosity": False,
    },
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
            "id": "0123456789abcdef",
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
point["details"]["functions"] = functions + [
    {
        "path": "elsewhere/peak.rs",
        "name": "peak",
        "line": 1,
        "end_line": 21,
        "cc": 30,
        "cognitive": 20,
        "sloc": 21,
    }
]
point["details"]["files"].append({"path": "elsewhere/peak.rs", "sloc": 42})
point["details"]["files"].append(
    {"path": "src/zero.rs", "sloc": 10, "verbosity_flagged_loc": 0}
)
tree = {
    "commit": commit,
    "entries": [
        {"path": path, "kind": kind}
        for path, kind in [
            ("README.md", "file"),
            ("pyproject.toml", "file"),
            ("src/example.rs", "file"),
            ("src/zero.rs", "file"),
            ("elsewhere/peak.rs", "file"),
            ("assets/picture.png", "file"),
            ("link", "symlink"),
            ("vendor", "submodule"),
            ("__proto__/constructor/<script>.py", "file"),
        ]
    ],
}
long_path = (
    "packages/rendering/components/internal/source/"
    + "a_long_descriptive_filename_for_the_rendered_code_browser_navigation_example.rs"
)
point["details"]["files"].append({"path": long_path, "sloc": 12})
point["details"]["functions"].append(
    {
        "path": long_path,
        "name": "navigate",
        "line": 1,
        "end_line": 12,
        "cc": 15,
        "cognitive": 16,
        "sloc": 12,
    }
)
tree["entries"].append({"path": long_path, "kind": "file"})
html = render_dashboard(data)


def check_file_browser(page, width):
    """Navigate measured and unmeasured files through the expanded dialog."""
    requests = []

    def serve_file(route):
        query = parse_qs(urlsplit(route.request.url).query)
        requests.append(query)
        path = query["path"][0]
        if path in ("link", "vendor", "assets/picture.png"):
            route.fulfill(
                status=422, json={"error": "Only regular text files can be previewed"}
            )
            return
        body = deepcopy(source)
        body["path"] = path
        if path != source["path"]:
            body.update(
                text="# Documentation\n<img src=x onerror='window.sourceInjection=true'>\n",
                language=None,
                measured=False,
                functions=[],
                metric_available={
                    metric: False
                    for metric in ("cc", "erosion", "cognitive", "verbosity")
                },
            )
        route.fulfill(json=body)

    page.route("**/source.json?*", serve_file)
    page.select_option("#explorer-metric", "cc")
    launcher = page.locator('#files-body .source-button[data-path="src/example.rs"]')
    launcher.click()
    page.wait_for_selector("#source-code .hljs-keyword")
    assert not page.locator("#source-files").is_visible()
    page.locator("#source-expand").click()
    page.wait_for_selector(
        '#source-tree button[data-path="src/example.rs"]', state="attached"
    )
    files = page.locator("#source-files")
    toggle = page.locator("#source-files-toggle")
    if width < 600:
        assert not files.is_visible()
        toggle.click()
        assert page.locator("#source-minimap").evaluate("e=>e.inert")
        assert page.locator(".source-scroll").evaluate("e=>e.inert")
    assert files.is_visible()
    if width < 600:
        Path(".scratch").mkdir(exist_ok=True)
        page.screenshot(
            path=f".scratch/source-tree-files-open-{page.context.browser.browser_type.name}-{width}.png"
        )
    current = page.locator('#source-tree button[data-path="src/example.rs"]')
    assert current.get_attribute("aria-current") == "true"
    assert (
        page.locator('#source-tree details[data-path="src"]').get_attribute("open")
        is not None
    )
    assert current.locator(".source-file-value").inner_text() == "12"
    assert (
        page.locator(
            '#source-tree button[data-path="src/zero.rs"] .source-file-value'
        ).inner_text()
        == "0"
    )
    if width == 320:
        parts = long_path.split("/")
        for index in range(1, len(parts)):
            directory = "/".join(parts[:index])
            page.locator(
                f'#source-tree details[data-path="{directory}"] > summary'
            ).click()
        long_file = page.locator(f'#source-tree button[data-path="{long_path}"]')
        assert long_path in long_file.get_attribute("title")
        badge = long_file.locator(".source-file-value")
        assert badge.inner_text() == "15"
        assert badge.is_visible()
        row_bounds, badge_bounds = long_file.bounding_box(), badge.bounding_box()
        assert (
            badge_bounds["x"] + badge_bounds["width"]
            <= row_bounds["x"] + row_bounds["width"]
        )
        assert page.evaluate("document.documentElement.scrollWidth") == width
        page.screenshot(
            path=f".scratch/source-tree-long-path-{page.context.browser.browser_type.name}-{width}.png"
        )
    readme = page.locator('#source-tree button[data-path="README.md"]')
    assert readme.locator(".source-file-value").inner_text() == ""
    assert "Not measured" in readme.get_attribute("title")
    assert "Not measured" not in files.inner_text()
    readme.focus()
    assert (
        page.locator("#source-file-hint")
        .inner_text()
        .endswith("Not measured in this scope.")
    )
    page.locator('#source-tree details[data-path="elsewhere"] summary').click()
    if width < 600:
        page.locator("#source-files-toggle").click()
    page.select_option("#source-metric", "verbosity")
    assert (
        page.locator('#source-tree details[data-path="elsewhere"]').get_attribute(
            "open"
        )
        is not None
    )
    assert (
        page.locator(
            '#source-tree button[data-path="src/example.rs"] .source-file-value'
        ).inner_text()
        == "0"
    )
    missing = page.locator('#source-tree button[data-path="elsewhere/peak.rs"]')
    assert missing.locator(".source-file-value").inner_text() == ""
    assert "Unavailable" in missing.get_attribute("title")
    if width < 600:
        toggle.click()
    page.locator('#source-tree button[data-path="README.md"]').click()
    page.wait_for_function(
        "document.getElementById('source-title').textContent==='README.md' && document.getElementById('source-code').textContent.startsWith('# Documentation')"
    )
    assert requests[-1]["commit"] == [commit]
    assert requests[-1]["scope"] == ["0123456789abcdef"]
    assert page.locator("#source-metric").input_value() == "verbosity"
    assert page.locator("#source-gutter .hot").count() == 0
    assert "Not measured" in page.locator("#source-legend").inner_text()
    assert (
        "syntax highlighting limit" not in page.locator("#source-status").inner_text()
    )
    assert not page.evaluate("window.sourceInjection===true")
    if width < 600:
        assert not files.is_visible()
        assert not page.locator("#source-minimap").evaluate("e=>e.inert")
        assert page.locator("#source-title").evaluate("e=>document.activeElement===e")
        toggle.click()
        page.keyboard.press("Escape")
        assert page.locator("#source-dialog").evaluate("e=>e.open")
        assert toggle.evaluate("e=>document.activeElement===e")
    page.locator("#source-expand").click()
    assert not files.is_visible()
    assert page.locator("#source-title").inner_text() == "README.md"
    page.locator("#source-expand").click()
    if width < 600:
        toggle.click()
    assert (
        page.locator('#source-tree button[data-path="README.md"]').get_attribute(
            "aria-current"
        )
        == "true"
    )
    page.locator('#source-tree button[data-path="link"]').click()
    page.wait_for_function(
        "document.getElementById('source-status').textContent.includes('regular text')"
    )
    assert page.locator("#source-code").inner_text() == ""
    if width < 600:
        toggle.click()
    page.locator('#source-tree button[data-path="src/example.rs"]').click()
    page.wait_for_selector("#source-code .hljs-keyword")
    page.select_option("#source-metric", "cc")
    Path(".scratch").mkdir(exist_ok=True)
    page.screenshot(
        path=f".scratch/source-tree-{page.context.browser.browser_type.name}-{width}.png"
    )
    assert page.evaluate("document.documentElement.scrollWidth") == width
    page.locator("#source-close").click()
    expect(launcher).to_be_focused()
    page.unroute("**/source.json?*", serve_file)


def wait_for_held_request(page, requests, count=1):
    """Let Playwright dispatch route handlers without releasing their replies."""
    for _ in range(200):
        if len(requests) >= count:
            return
        page.wait_for_timeout(10)
    raise AssertionError(f"Expected {count} held requests, got {len(requests)}")


def settle_browser(page):
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )


def source_for_path(path, contents):
    body = deepcopy(source)
    body.update(
        path=path,
        text=contents,
        functions=[],
        language=None,
        measured=False,
        metric_available={
            metric: False for metric in ("cc", "erosion", "cognitive", "verbosity")
        },
    )
    return body


def check_request_races(browser, width):
    """Hold HTTP replies to exercise dialog context, cancellation and stale guards."""

    def new_page():
        page = browser.new_page(
            viewport={"width": width, "height": 900},
            is_mobile=width < 600,
            has_touch=width < 600,
        )
        # Ignore cancellation at the network boundary so an obsolete reply really
        # reaches the promise. Record abort signals independently to verify both
        # cancellation and the sequence checks that protect against late replies.
        page.add_init_script("""(() => {
          const fetchOriginal = window.fetch;
          window.heldRequestSignals = [];
          window.fetch = (url, options = {}) => {
            if (/\\/(source|tree)\\.json\\?/.test(String(url))) {
              const entry = {url: String(url), aborted: false};
              window.heldRequestSignals.push(entry);
              options.signal?.addEventListener('abort', () => entry.aborted = true);
              return fetchOriginal(url, {...options, signal: undefined});
            }
            return fetchOriginal(url, options);
          };
        })();""")
        page.route("**/data.json", lambda route: route.fulfill(json=data))
        page.route("**/source.json?*", lambda route: route.fulfill(json=source))
        page.route("**/tree.json?*", lambda route: route.fulfill(json=tree))
        page.route(
            "**/slop/aurene/",
            lambda route: route.fulfill(body=html, content_type="text/html"),
        )
        page.goto("http://preview/slop/aurene/")
        return page

    def open_source(page):
        page.locator('#files-body .source-button[data-path="src/example.rs"]').click()
        page.wait_for_selector("#source-code .hljs-keyword")

    def expand_loaded_tree(page):
        page.locator("#source-expand").click()
        page.wait_for_selector(
            '#source-tree button[data-path="README.md"]', state="attached"
        )

    def choose_file(page, path):
        if width < 600 and not page.locator("#source-files").is_visible():
            page.locator("#source-files-toggle").click()
        page.locator(f'#source-tree button[data-path="{path}"]').click()

    def refresh(page):
        updated = deepcopy(data)
        updated["revision"] = "race-new-r"
        page.route("**/data.json", lambda route: route.fulfill(json=updated))
        with page.expect_response("**/data.json"):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        settle_browser(page)

    page = new_page()
    try:
        open_source(page)
        expand_loaded_tree(page)
        pending = []
        page.route(
            "**/source.json?*",
            lambda route, _request, pending=pending: pending.append(route),
        )
        choose_file(page, "README.md")
        wait_for_held_request(page, pending)
        choose_file(page, "pyproject.toml")
        wait_for_held_request(page, pending, 2)
        pending[1].fulfill(json=source_for_path("pyproject.toml", "second reply\n"))
        expect(page.locator("#source-code")).to_have_text("second reply\n")
        pending[0].fulfill(json=source_for_path("README.md", "obsolete first reply\n"))
        settle_browser(page)
        assert page.locator("#source-title").inner_text() == "pyproject.toml"
        assert page.locator("#source-code").inner_text() == "second reply\n"
        assert (
            page.locator(
                '#source-tree button[data-path="pyproject.toml"]'
            ).get_attribute("aria-current")
            == "true"
        )
        assert page.evaluate(
            "heldRequestSignals.find(e => e.url.includes('path=README.md')).aborted"
        )
    finally:
        page.close()

    page = new_page()
    try:
        open_source(page)
        pending = []

        def hold_first_tree(route):
            if not pending:
                pending.append(route)
            else:
                route.fulfill(json=tree)

        page.route("**/tree.json?*", hold_first_tree)
        page.locator("#source-expand").click()
        wait_for_held_request(page, pending)
        page.locator("#source-close").click()
        expect(page.locator("#source-dialog")).not_to_be_visible()
        open_source(page)
        expand_loaded_tree(page)
        pending[0].fulfill(
            json={
                "commit": commit,
                "entries": [{"path": "obsolete.md", "kind": "file"}],
            }
        )
        settle_browser(page)
        assert page.locator('#source-tree button[data-path="obsolete.md"]').count() == 0
        assert page.locator('#source-tree button[data-path="README.md"]').count() == 1
        assert page.locator("#source-code").inner_text() == text
        assert page.evaluate(
            "heldRequestSignals.find(e => e.url.includes('/tree.json?')).aborted"
        )
    finally:
        page.close()

    if width < 600:
        page = new_page()
        try:
            open_source(page)
            pending = []
            page.route(
                "**/tree.json?*",
                lambda route, _request, pending=pending: pending.append(route),
            )
            page.locator("#source-expand").click()
            wait_for_held_request(page, pending)
            page.locator("#source-files-toggle").click()
            expect(page.locator("#source-files-title")).to_be_focused()
            pending.pop().abort()
        finally:
            page.close()

    for resource in ("tree", "source"):
        page = new_page()
        try:
            open_source(page)
            pending = []
            if resource == "tree":
                page.route(
                    "**/tree.json?*",
                    lambda route, _request, pending=pending: pending.append(route),
                )
                page.locator("#source-expand").click()
            else:
                expand_loaded_tree(page)
                page.route(
                    "**/source.json?*",
                    lambda route, _request, pending=pending: pending.append(route),
                )
                choose_file(page, "README.md")
            wait_for_held_request(page, pending)
            # Recorded commits do not change, so refreshes leave requests alone.
            refresh(page)
            assert not page.evaluate(
                "resource => heldRequestSignals.filter(e => e.url.includes('/' + resource + '.json?')).at(-1).aborted",
                resource,
            )
            reply = (
                tree
                if resource == "tree"
                else source_for_path("README.md", "reply after refresh\n")
            )
            pending[0].fulfill(json=reply)
            if resource == "tree":
                expect(
                    page.locator('#source-tree button[data-path="README.md"]')
                ).to_be_enabled()
                assert page.locator("#source-code").inner_text() == text
            else:
                expect(page.locator("#source-code")).to_have_text(
                    "reply after refresh\n"
                )
        finally:
            page.close()

    page = new_page()
    try:
        open_source(page)
        pending = []
        page.route(
            "**/tree.json?*",
            lambda route, _request, pending=pending: pending.append(route),
        )
        page.locator("#source-expand").click()
        wait_for_held_request(page, pending)
        pending[0].fulfill(
            status=422, json={"error": "File listing exceeded the node limit"}
        )
        expect(page.locator("#source-tree-status")).to_contain_text("node limit")
        assert page.locator("#source-code").inner_text() == text
        if width < 600:
            page.locator("#source-files-toggle").click()
        page.locator("#source-tree-retry").click()
        wait_for_held_request(page, pending, 2)
        assert page.locator("#source-code").inner_text() == text
        pending[1].fulfill(json=tree)
        page.wait_for_selector(
            '#source-tree button[data-path="README.md"]', state="attached"
        )
        assert page.locator("#source-code").inner_text() == text
        assert page.locator("#source-tree-status").inner_text() == ""
    finally:
        page.close()
    print(
        browser.browser_type.name,
        width,
        "held replies/navigation/reopen/refresh/retry passed",
        flush=True,
    )


with sync_playwright() as p:
    for engine in (p.webkit, p.chromium):
        browser = engine.launch()
        for width in (320, 390, 1440):
            page = browser.new_page(
                viewport={"width": width, "height": 900},
                is_mobile=width < 600,
                has_touch=width < 600,
            )
            page.route("**/data.json", lambda r: r.fulfill(json=data))
            page.route("**/source.json?*", lambda r: r.fulfill(json=source))
            page.route("**/tree.json?*", lambda r: r.fulfill(json=tree))
            page.route(
                "**/slop/aurene/",
                lambda r: r.fulfill(body=html, content_type="text/html"),
            )
            page.goto("http://preview/slop/aurene/")
            initial_file_order = page.locator("#files-body").inner_text()
            function_cc_sort = page.locator('[data-function-sort="cc"]')
            function_cc_sort.click(timeout=1500)
            assert (
                function_cc_sort.locator("..").get_attribute("aria-sort") == "ascending"
            )
            assert function_cc_sort.locator(".sort-triangle").inner_text() == "▲"
            assert (
                page.locator("#functions-body tr")
                .first.inner_text()
                .startswith("example (")
            )
            page.locator('[data-function-sort="cognitive"]').click()
            assert (
                page.locator("#functions-body tr")
                .first.inner_text()
                .startswith("peak (")
            )
            page.locator('[data-function-sort="sloc"]').click()
            assert (
                page.locator("#functions-body tr")
                .first.inner_text()
                .startswith("peak (")
            )
            function_cc_sort.click()
            assert (
                function_cc_sort.locator("..").get_attribute("aria-sort")
                == "descending"
            )
            assert function_cc_sort.locator(".sort-triangle").inner_text() == "▼"
            assert page.locator("#files-body").inner_text() == initial_file_order
            assert (
                page.locator('[data-file-sort="cc"]')
                .locator("..")
                .get_attribute("aria-sort")
                == "descending"
            )
            function_row = page.locator("#functions-body tr").filter(
                has_text="example ("
            )
            colors = function_row.locator("td.numeric").evaluate_all(
                "cells=>cells.map(e=>getComputedStyle(e).backgroundColor)"
            )
            assert colors == [
                "rgba(180, 85, 36, 0.192)",
                "rgba(180, 85, 36, 0.29)",
                "rgba(180, 85, 36, 0.12)",
            ]
            file_row = page.locator("#files-body tr").filter(has_text="src/example.rs")
            assert file_row.locator(".file-button .source-path-segment").count() == 2
            file_colors = file_row.locator("td.numeric").evaluate_all(
                "cells=>cells.map(e=>getComputedStyle(e).backgroundColor)"
            )
            assert file_colors[:2] == [
                "rgba(180, 85, 36, 0.1)",
                "rgba(180, 85, 36, 0.192)",
            ]
            assert file_row.locator("td.numeric").nth(2).inner_text() == "0%"
            assert file_colors[3] != "rgba(0, 0, 0, 0)"
            sloc_sort = page.locator('[data-file-sort="sloc"]')
            sloc_sort.click()
            assert sloc_sort.locator("..").get_attribute("aria-sort") == "descending"
            sloc_sort.click()
            assert sloc_sort.locator("..").get_attribute("aria-sort") == "ascending"
            assert (
                page.locator("#files-body tr")
                .first.inner_text()
                .startswith("src/example.rs")
            )
            page.locator('[data-file-sort="verbosity"]').click()
            assert (
                page.locator("#files-body tr")
                .last.locator("td.numeric")
                .nth(2)
                .inner_text()
                == "Unavailable"
            )
            page.locator('[data-file-sort="cc"]').click()
            file_row.locator(".file-button").click()
            assert (
                file_row.locator("td.numeric").evaluate_all(
                    "cells=>cells.map(e=>getComputedStyle(e).backgroundColor)"
                )[:2]
                == file_colors[:2]
            )
            assert file_row.locator("td.numeric").nth(3).inner_text() == "100%"
            assert (
                file_row.locator("td.numeric")
                .nth(3)
                .evaluate("e=>getComputedStyle(e).backgroundColor")
                == "rgba(180, 85, 36, 0.36)"
            )
            assert (
                function_row.locator("td.numeric").evaluate_all(
                    "cells=>cells.map(e=>getComputedStyle(e).backgroundColor)"
                )
                == colors
            )
            page.locator("#clear-file").click()
            button = page.locator(
                '#files-body .source-button[data-path="src/example.rs"]'
            )
            assert button.locator('svg[aria-hidden="true"]').count() == 1
            assert button.inner_text() == ""
            icon = button.locator("svg").bounding_box()
            assert icon["width"] == 16 and icon["height"] == 16
            button.click()
            page.wait_for_selector("#source-code .hljs-keyword")
            assert page.locator("#source-dialog").evaluate("(e)=>e.open")
            assert page.locator("#source-title").text_content() == source["path"]
            assert page.locator("#source-title .source-path-segment").count() == len(
                source["path"].split("/")
            )
            assert page.locator("#source-code").inner_text() == text
            line_count = len(text.splitlines())
            code_height = page.locator("#source-code").bounding_box()["height"]
            gutter_height = page.locator("#source-gutter").bounding_box()["height"] - 24
            assert code_height == gutter_height == line_count * 20
            assert not page.evaluate("window.sourceInjection===true")
            assert page.locator("#source-gutter .hot").count() == 3
            assert (
                page.locator("#source-minimap").get_attribute("aria-disabled") == "true"
            )
            assert (
                page.locator("#source-gutter .hot").first.evaluate(
                    "e=>getComputedStyle(e).backgroundColor"
                )
                == "rgba(180, 85, 36, 0.41)"
            )
            assert page.evaluate("document.documentElement.scrollWidth") == width
            dialog = page.locator("#source-dialog")
            initial_bounds = dialog.bounding_box()
            expand = page.locator("#source-expand")
            assert expand.inner_text() == "Expand"
            expand.click(timeout=1500)
            assert expand.get_attribute("aria-pressed") == "true"
            assert expand.inner_text() == "Restore"
            bounds = dialog.bounding_box()
            assert bounds == {"x": 0, "y": 0, "width": width, "height": 900}
            expand.click()
            assert expand.get_attribute("aria-pressed") == "false"
            assert dialog.bounding_box() == initial_bounds
            expand.click()
            page.select_option("#source-metric", "verbosity")
            assert "not recorded" in page.locator("#source-legend").inner_text()
            page.select_option("#source-metric", "erosion")
            assert page.locator("#source-gutter .hot").count() == 3
            page.keyboard.press("Escape")
            assert not page.locator("#source-dialog").evaluate("(e)=>e.open")
            page.wait_for_function(
                "document.activeElement===document.querySelector('#files-body .source-button[data-path=\"src/example.rs\"]')"
            )
            page.locator("#functions-body .source-button").first.click()
            page.wait_for_selector("#source-code .hljs-keyword")
            assert dialog.bounding_box() == initial_bounds
            assert (
                page.get_by_role("button", name="Expand", exact=True).get_attribute(
                    "aria-pressed"
                )
                == "false"
            )
            page.locator("#source-close").click()
            check_file_browser(page, width)
            long_source = deepcopy(source)
            long_source["text"] = (
                "\n".join(
                    ""
                    if i % 8 == 0
                    else "    let value = transform(input);"
                    if i % 8 > 1
                    else "fn transform(input: f64) {"
                    for i in range(320)
                )
                + "\n"
            )
            long_source["functions"].append(
                {"line": 240, "end_line": 260, "cc": 30, "cognitive": 5, "sloc": 21}
            )
            short_shade = page.locator("#source-gutter .hot").first.evaluate(
                "e=>getComputedStyle(e).backgroundColor"
            )
            page.route(
                "**/source.json?*",
                lambda r, _request, long_source=long_source: r.fulfill(
                    json=long_source
                ),
            )
            button.click()
            page.wait_for_function(
                "document.getElementById('source-gutter').children.length===320"
            )
            assert (
                page.locator("#source-gutter .hot").first.evaluate(
                    "e=>getComputedStyle(e).backgroundColor"
                )
                == short_shade
            )
            minimap = page.locator("#source-minimap")
            assert minimap.is_visible()
            scroll = page.locator(".source-scroll")
            scroll.evaluate("e=>e.scrollTop=0")
            page.wait_for_function(
                "document.getElementById('source-minimap').getAttribute('aria-valuenow')==='0'"
            )
            canvas = page.locator("#source-minimap canvas")
            pixels = canvas.evaluate("e=>e.toDataURL()")
            page.select_option("#source-metric", "cognitive")
            assert canvas.evaluate("e=>e.toDataURL()") != pixels
            bounds = minimap.bounding_box()
            if width < 600:
                page.touchscreen.tap(
                    bounds["x"] + bounds["width"] / 2,
                    bounds["y"] + bounds["height"] * 0.5,
                )
                assert scroll.evaluate("e=>e.scrollTop") > 2000
            page.mouse.click(
                bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] * 0.7
            )
            assert scroll.evaluate("e=>e.scrollTop") > 3000
            thumb = page.locator("#source-minimap-viewport").bounding_box()
            page.mouse.move(thumb["x"] + 10, thumb["y"] + thumb["height"] / 2)
            page.mouse.down()
            page.mouse.move(
                thumb["x"] + 10, bounds["y"] + bounds["height"] * 0.2, steps=5
            )
            page.mouse.up()
            assert scroll.evaluate("e=>e.scrollTop") < 2000
            page.mouse.click(bounds["x"] + 10, bounds["y"] + bounds["height"] - 1)
            assert scroll.evaluate("e=>e.scrollTop+e.clientHeight===e.scrollHeight")
            scroll.evaluate("e=>e.scrollTop=2000")
            page.wait_for_function(
                "document.getElementById('source-minimap').getAttribute('aria-valuenow')==='2000'"
            )
            top = page.locator("#source-minimap-viewport").evaluate(
                "e=>parseFloat(e.style.top)"
            )
            assert (
                abs(
                    top - 2000 / scroll.evaluate("e=>e.scrollHeight") * bounds["height"]
                )
                < 1
            )
            minimap.focus()
            page.keyboard.press("End")
            assert scroll.evaluate("e=>e.scrollTop+e.clientHeight===e.scrollHeight")
            page.keyboard.press("Home")
            assert scroll.evaluate("e=>e.scrollTop") == 0
            page.locator("#source-expand").click()
            assert minimap.bounding_box()["height"] == scroll.bounding_box()["height"]
            assert page.evaluate("document.documentElement.scrollWidth") == width
            if width < 600:
                page.wait_for_selector(
                    '#source-tree button[data-path="README.md"]', state="attached"
                )
                page.locator("#source-files-toggle").click()
                assert scroll.evaluate(
                    "e => e.scrollHeight > e.clientHeight && e.inert"
                )
                assert minimap.evaluate("e => e.inert")
                for _ in range(25):
                    page.keyboard.press("Tab")
                    assert not page.evaluate(
                        "document.activeElement.closest('.source-scroll, #source-minimap') !== null"
                    )
                page.locator("#source-files-toggle").click()
                assert not scroll.evaluate("e => e.inert")
                assert not minimap.evaluate("e => e.inert")
                minimap.focus()
                expect(minimap).to_be_focused()
            page.locator("#source-close").click()
            page.route("**/source.json?*", lambda r: r.fulfill(json=source))
            page.route(
                "**/source.json?*",
                lambda r: r.fulfill(
                    status=422, json={"error": "Source is not UTF-8 text"}
                ),
            )
            button.click()
            page.wait_for_function(
                "document.getElementById('source-status').textContent.includes('UTF-8')"
            )
            assert page.locator("#source-code").inner_text() == ""
            page.locator("#source-close").click()
            if width == 390:
                pending = []
                page.route(
                    "**/source.json?*",
                    lambda r, _request, pending=pending: pending.append(r),
                )
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
                page.locator("#source-expand").click()
                page.wait_for_selector(
                    '#source-tree button[data-path="README.md"]', state="attached"
                )
                updated = deepcopy(data)
                updated["head"] = "b" * 40
                updated["revision"] = "new-r"
                updated["series"][0]["snapshots"][0]["commit"] = "b" * 40
                updated["series"][0]["snapshots"][0]["details"]["functions"][0][
                    "cc"
                ] = 2
                updated["series"][0]["snapshots"][0]["details"]["functions"][1][
                    "cc"
                ] = 120
                page.route(
                    "**/data.json",
                    lambda r, _request, updated=updated: r.fulfill(json=updated),
                )
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.wait_for_function(
                    "document.getElementById('head-value').textContent==='bbbbbbbb'"
                )
                page.select_option("#source-metric", "cc")
                assert "aaaaaaaaaaaa" in page.locator("#source-meta").inner_text()
                assert "12 CC" in page.locator(
                    "#source-gutter .hot"
                ).first.get_attribute("title")
                assert (
                    page.locator("#source-gutter .hot").first.evaluate(
                        "e=>getComputedStyle(e).backgroundColor"
                    )
                    == "rgba(180, 85, 36, 0.41)"
                )
                assert page.locator("#source-code").inner_text() == text
                assert page.locator(
                    '#source-tree button[data-path="README.md"]'
                ).is_enabled()
                page.locator("#source-close").click()
            print(
                engine.name,
                width,
                "source/syntax/heat/errors/focus/width passed",
                flush=True,
            )
            page.close()
            check_request_races(browser, width)
        browser.close()
