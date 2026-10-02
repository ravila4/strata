# Strata

Strata measures code size, complexity, erosion, and duplication across Git history.
It records local commit snapshots and provides an interactive dashboard with
linked directory and historical views.

## Current tools

The working runtime and agent skill live in `skills/slop-check/`. The bundle was
extracted from `ravila4/claude-code-config` at commit `4d43b9e`. Its existing
`slop-check` commands and `.slop-check/` data directory are retained.

- Advisory post-commit recording for Python, Rust, or JavaScript.
- Live dashboard with commit-pinned source windows, syntax highlighting, and metric hotspots.
- Offline HTML snapshot export.
- Sunburst and historical plots for complexity, cyclomatic erosion, cognitive
  erosion, and flagged source lines.
- Persistent macOS service installation and optional Tailscale Serve integration.

See [the skill](skills/slop-check/SKILL.md),
[commit recording](skills/slop-check/references/commit-hook.md), and
[dashboard usage](skills/slop-check/references/dashboard.md) for setup.

## Local skill setup

In the current workspace, `../claude-code-config/claude/skills/slop-check` points
here through a relative symlink. Existing agent skill links resolve through that
path. Keep these two repositories as siblings when using this configuration.
Installed hooks and servers use their own runtime copies; moving the source does
not upgrade those installations. Metric logs stay in each analyzed repository.

## Development

Run from this repository:

```sh
uv run --with pytest --with plotly==6.3.1 pytest -q skills/slop-check/tests
node --test skills/slop-check/tests/dashboard.test.cjs
uvx ruff check skills/slop-check/scripts skills/slop-check/tests
```

Browser checks require Chromium and WebKit installed through Playwright:

```sh
uv run --with playwright playwright install chromium webkit
uv run --script skills/slop-check/tests/source_browser.py
```

## Neovim integration

A toggleable metric overlay is planned, but not implemented. It will reuse the
analyzer and validate snapshot or buffer contents before applying line highlights.
Editor scans will remain separate from committed metric history.
