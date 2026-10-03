---
name: strata
description: Measure and interpret code structure metrics (complexity erosion, verbosity, clones) across Git history with the strata CLI. Use when asked to run strata, scb-check, or SlopCodeBench metrics on a repo or PR, compare base and head, backfill or record commit history, open the metrics dashboard, or find where complexity is concentrating. Scores flag code worth reading, not quality or correctness.
---

# Strata

Strata records static structure metrics for Git commits and serves them in a
live dashboard. A high score means "worth a look", not "bad code".

## Commands

| Command | Does |
|---|---|
| `strata scan [REV] --source-root DIR` | Measure one commit (default `HEAD`) |
| `strata backfill [REV] --source-root DIR` | Measure unmeasured first-parent commits (default: detected default branch); `--since DATE`, `--every N` |
| `strata serve [--commit REV ...]` | Serve the dashboard on loopback (default port 8766) |
| `strata hook install --source-root DIR` / `remove` | Record every commit in the background |
| `strata service install` / `remove` | Persistent macOS dashboard, optionally over Tailscale |

Commands act on the current repository; pass `--repo` for another. Separate
test files, fixtures, and vendored code are excluded; inline tests are kept. Once a hook is
installed, `scan` and `backfill` reuse its roots. If `strata` is missing, ask
before installing it (`uv tool install git+https://github.com/ravila4/strata.git`).

Scan and serve whenever metrics are requested. Install the hook or the macOS
service only when the user asks for automation. A metric check
never authorizes editing code, posting PR comments, or adding CI gates.

## Compare a PR

1. Resolve full hashes: the head commit and the merge base with the target
   branch, not the moving branch tip. For GitHub, `gh pr view` gives both refs.
2. `strata scan <base>` and `strata scan <head>` with the same `--source-root`.
3. `strata serve --commit <base> --commit <head>` shows only those two commits.

## Serve the dashboard

Run `strata serve` in a background session that outlives the reply, logging to
`.strata/server.stdout.log` and `.strata/server.stderr.log`. Before returning
the URL, check that `/data.json` has the right `repository_path` and, for a
comparison, `requested_commits` in order. Reuse a running server only after
the same checks.

## Read the results

- `.strata/history.jsonl`: a `started` row per recording, then one row per
  language. Only `complete` rows carry `metrics`. `not_applicable` means no
  source in that language, not a zero score. `failed` rows have an `error`;
  their `report` directory keeps `analyzer.stderr` and the source snapshot.
- `.strata/reports/<commit>-*/details.json`: per file, `sloc` and
  `verbosity_flagged_lines`; per function, `name`, `line`/`end_line`, `sloc`,
  `cc`, and `cognitive`.

Metrics:

- **Erosion**: share of function mass (`cc * sqrt(sloc)`) held by functions with
  CC above 10, so it measures how concentrated complexity is. Report `high_cc_functions` alongside it. `cog_erosion` is the cognitive analogue.
- **Verbosity**: flagged lines divided by SLOC. Python flags clones, AST rules,
  and trivial wrappers; Rust and JavaScript flag clones only, so don't compare
  verbosity across languages.
- Report ratio changes in percentage points with SLOC beside them. One commit
  is a baseline, not a trend.

## Turn metrics into suggestions

1. Rank functions by `cc` and `cognitive`, and files by flagged lines. For a
   PR, focus on functions it added or made worse, matched by file and name.
2. Read each candidate and its callers before saying anything about it.
3. Label each one:
   - **actionable**: name the structural change, such as splitting
     responsibilities, moving a policy into data, or replacing branching with
     dispatch.
   - **justified**: input validation, protocol handling, and required interfaces
     often need the branching or repetition the rules flag.
   - **uncertain**: say what would settle it.
4. Never suggest extracting helpers just to get under a threshold.

Lead with the dashboard URL and what changed, then the short labeled list. Keep
correctness and test results separate from these measurements. For hook and
dashboard behavior, see `docs/` in the Strata repository.
