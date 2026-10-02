# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "plotly==6.3.1", "pytest"]
# ///
"""Browser regression checks for the live source dialog; install Chromium and WebKit first."""

from copy import deepcopy
from playwright.sync_api import sync_playwright
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
point["details"]["functions"] = functions + [
    {"path": "elsewhere/peak.rs", "name": "peak", "line": 1, "end_line": 21,
     "cc": 30, "cognitive": 20, "sloc": 21}
]
html = render_dashboard(data)
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
            assert page.locator("#source-minimap").get_attribute("aria-disabled") == "true"
            assert page.locator("#source-gutter .hot").first.evaluate(
                "e=>getComputedStyle(e).backgroundColor"
            ) == "rgba(180, 85, 36, 0.41)"
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
                "document.activeElement===document.querySelector('#files-body .source-button')"
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
            long_source = deepcopy(source)
            long_source["text"] = "\n".join(
                "" if i % 8 == 0 else "    let value = transform(input);"
                if i % 8 > 1 else "fn transform(input: f64) {"
                for i in range(320)
            ) + "\n"
            long_source["functions"].append(
                {"line": 240, "end_line": 260, "cc": 30, "cognitive": 5, "sloc": 21}
            )
            short_shade = page.locator("#source-gutter .hot").first.evaluate(
                "e=>getComputedStyle(e).backgroundColor"
            )
            page.route("**/source.json?*", lambda r: r.fulfill(json=long_source))
            button.click()
            page.wait_for_function(
                "document.getElementById('source-gutter').children.length===320"
            )
            assert page.locator("#source-gutter .hot").first.evaluate(
                "e=>getComputedStyle(e).backgroundColor"
            ) == short_shade
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
                    bounds["y"] + bounds["height"] * .5,
                )
                assert scroll.evaluate("e=>e.scrollTop") > 2000
            page.mouse.click(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] * .7)
            assert scroll.evaluate("e=>e.scrollTop") > 3000
            thumb = page.locator("#source-minimap-viewport").bounding_box()
            page.mouse.move(thumb["x"] + 10, thumb["y"] + thumb["height"] / 2)
            page.mouse.down()
            page.mouse.move(thumb["x"] + 10, bounds["y"] + bounds["height"] * .2, steps=5)
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
            assert abs(top - 2000 / scroll.evaluate("e=>e.scrollHeight") * bounds["height"]) < 1
            minimap.focus()
            page.keyboard.press("End")
            assert scroll.evaluate("e=>e.scrollTop+e.clientHeight===e.scrollHeight")
            page.keyboard.press("Home")
            assert scroll.evaluate("e=>e.scrollTop") == 0
            page.locator("#source-expand").click()
            assert minimap.bounding_box()["height"] == scroll.bounding_box()["height"]
            assert page.evaluate("document.documentElement.scrollWidth") == width
            page.locator("#source-close").click()
            page.route("**/source.json?*", lambda r: r.fulfill(json=source))
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
                updated["series"][0]["snapshots"][0]["details"]["functions"][1][
                    "cc"
                ] = 120
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
                assert page.locator("#source-gutter .hot").first.evaluate(
                    "e=>getComputedStyle(e).backgroundColor"
                ) == "rgba(180, 85, 36, 0.41)"
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
