# Strata

Strata records code metrics from Git commits and displays them in a live dashboard.
It uses [scb-check](https://github.com/gabeorlanski/scb-check) to measure Python,
Rust, and JavaScript source.

Named for geological layers, Strata helps you explore how your codebase builds up
and changes over time.

Browse directories and files, follow changes across commits, and open the source
behind a measurement with syntax highlighting and metric hotspots. Source windows
show the selected commit's code, even when the working tree has changed. A file
sidebar navigates the commit's tracked files with complexity shading.

[![Strata's directory explorer and commit timeline](docs/images/dashboard.png)](docs/images/dashboard.png)

Strata's Python recorder and server measured across two commits.

[![Commit source with syntax highlighting, complexity hotspots, and a minimap](docs/images/source-hotspots.png)](docs/images/source-hotspots.png)

The source window shades function ranges by the selected metric. The minimap
shows hotspots across the file and lets you jump to them. Exact flagged-line
locations are available for measurements recorded with line details.

## Setup

Requires Git and [uv](https://docs.astral.sh/uv/). Install the `strata` command:

```sh
uv tool install git+https://github.com/ravila4/strata.git
```

In a project with source under `src`, install the commit hook and record the
current commit:

```sh
strata hook install --source-root src
strata scan
```

Every recording scans Python, JavaScript, and Rust under the source roots, with
separate metrics per language. The hook records future commits in the
background. Each project's `.strata/` directory, excluded from Git, holds its
configuration, measurements, reports, queue state, and logs.

Start the live dashboard:

```sh
strata serve
```

Open <http://127.0.0.1:8766/>. New measurements refresh automatically while the
dashboard is visible. Use `--port` to serve several projects at once.
Measurements are advisory; they describe source structure and complexity concentration.

| Command | Does |
|---|---|
| `strata scan [REV]` | Measure one commit (default `HEAD`) |
| `strata hook install` / `remove` | Record every commit in the background |
| `strata serve` | Serve the dashboard on loopback |
| `strata service install` / `remove` | Keep the dashboard running on macOS, optionally over Tailscale |

Commands act on the current repository; pass `--repo` for another. See
[commit recording](skills/slop-check/references/commit-hook.md) and
[dashboard usage](skills/slop-check/references/dashboard.md) for details. After
`uv tool upgrade strata`, restart running servers.

## Development

```sh
uv run pytest -q tests
node --test tests/dashboard.test.cjs
uv run ruff check src tests
```

Browser checks require Chromium and WebKit installed through Playwright:

```sh
uv run --with playwright playwright install chromium webkit
uv run --with playwright python tests/source_browser.py
uv run --with playwright python tests/multilanguage_browser.py
```

## Credits

[scb-check](https://github.com/gabeorlanski/scb-check), by
[Gabriel Orlanski](https://github.com/gabeorlanski), provides Strata's metric analysis.
Strata currently pins version 0.2.0 and adds commit recording and visualization
around its results.

[SlopCodeBench](https://github.com/SprocketLab/slop-code-bench), from SprocketLab,
and the [SlopCodeBench paper](https://arxiv.org/abs/2603.24755) provide the research
and metric definitions behind this project.

## License

Strata is licensed under the [MIT License](LICENSE). Bundled third-party assets
retain their own licenses in [the vendor directory](src/strata/assets/vendor/).
