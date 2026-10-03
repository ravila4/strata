# Recording commits

The post-commit hook records every commit in the background on macOS and Linux.

```sh
strata hook install --source-root src --source-root crates
strata scan
```

Install from the primary checkout. Linked worktrees share the hook and write to
the primary checkout's history; installing from one is rejected.

Each recording measures Python, JavaScript, and Rust separately. TypeScript and
JSX are not supported. Snapshots skip symlinks, separate test files,
test/fixture/vendor/generated directories, and other extensions. **Inline test
code is kept.** Keep roots fixed when comparing history; a different selection
shows up as a separate scope.

## What the hook does

The installer excludes `/.strata/` from Git and sets a repository-local
`core.hooksPath`. Existing hooks keep running, and an existing post-commit hook
runs before the recorder. Global Git settings and tracked files are untouched.

The hook queues the commit, launches a detached worker, and never blocks or
fails the commit. One analysis runs at a time. If commits arrive meanwhile, only
the newest pending one runs; replaced commits get `skipped` rows naming their
replacement. `--no-verify` commits are recorded too. Commits that arrive without
post-commit, such as ones pulled from a remote, need `strata scan` or
`strata backfill`.

The hook measures the committed snapshot, not staged or unstaged edits. Runtime
scales with total source size, not the diff. Each language scan has a 60-second
tool-version probe and a 120-second analysis timeout.

After `uv tool upgrade git-strata`, new scans use the new version; let active scans
finish first. If the saved interpreter disappears, for example after
reinstalling Strata under another Python, the hook logs to `.strata/worker.log`
and exits zero. Rerun `strata hook install`.

To test the background path, run the installed hook from the repository root:

```sh
.strata/hooks/post-commit
```

## Results

`.strata/history.jsonl` has a `started` row per recording, then one row per
language with status `complete`, `not_applicable`, or `failed`. Only `complete`
rows have metrics. Each attempt writes a directory under `.strata/reports/`;
failed attempts also keep the analyzed source snapshot.

Recording the same commit again adds a new attempt; the dashboard shows the
latest one. A failing language doesn't stop the others. A manual scan fails as
busy while another recording is running.

Reports are never cleaned up automatically. Deleting an old report directory
keeps its history row but drops that commit's file and function details from
the dashboard.

## Removing the hook

```sh
strata hook remove
```

This restores the previous hook configuration and keeps `.strata/`.
