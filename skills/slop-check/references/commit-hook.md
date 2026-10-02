# Advisory commit metrics

Use this mode when the user asks to record SlopCodeBench metrics automatically
in a repository. It supports macOS and Linux with Python 3.10+, Git, and `uvx`.
Install from the primary checkout; linked worktrees share hook configuration,
so installation from a linked worktree is rejected. Commits made in linked
worktrees use the primary checkout's recorder and shared history.
Choose explicit source roots and one of `rust`, `python`, or `javascript` after
inspecting the repository. For mixed-language projects, discuss the desired
measurement scope before installing; this installer measures one language.

Run the scripts relative to this skill's directory:

```sh
python3 scripts/install_hook.py --repo /absolute/path/to/repo \
  --source-root src --source-root crates --language rust
python3 /absolute/path/to/repo/.slop-check/record_commit.py \
  --repo /absolute/path/to/repo
```

The installer copies the recorder into `.slop-check/`, adds `/.slop-check/` to
`.git/info/exclude`, and sets repository-local `core.hooksPath`. It records the
previous hook configuration in `.slop-check/settings.json`. Executable existing
hooks are delegated to by their original absolute paths, preserving relative
resource lookup. An existing post-commit hook runs before the recorder. Global
Git settings and tracked repository files are not changed. Reinstallation
updates the recorder and selection while retaining the original hook settings;
it refuses to proceed if the active hook configuration has changed.

The post-commit hook captures the committing checkout and full SHA, queues the
job, launches a detached worker, and exits zero. One analysis runs at a time.
While it runs, the newest pending commit replaces any older pending commit;
replaced commits receive `skipped` history rows with the replacement SHA. The
current analysis finishes before the newest pending commit begins. With no
pending work, the worker exits; the next commit launches it again. There is no
commit-count threshold or timer between runs.

Dependency setup has a 60-second timeout and analysis has a 120-second timeout.
Those waits happen in the worker. Record actual analysis durations from history
and measure enqueue latency during setup rather than assuming a fixed runtime.
This scans the full selected source: runtime depends primarily on total source
size, rather than the size of the diff. Analyzer errors cannot reject a commit.
Existing pre-commit checks keep their existing blocking behavior. The hook
exports source from the captured commit,
ignoring staged and unstaged edits. The hook records commits made with
`--no-verify` too; Git still runs post-commit for those commits. Git operations
that do not invoke post-commit require a manual recorder invocation.

Snapshots omit symlinks, separate test files, test/fixture/vendor/generated
directories, and unselected languages. **Inline test code is retained.** This
scope differs from a production-only report that strips inline tests. Keep
roots, language, and this inclusion policy fixed when comparing history; filter
rows by scope if selection changes. Rust and JavaScript verbosity measures
clone lines only, while erosion measures complexity concentration, not defects.

Each attempt gets a unique `.slop-check/reports/<commit>-<suffix>/` directory
with a source snapshot and manifest, explicit analyzer config, command, tool
versions, raw JSON, stderr, and a summary. `.slop-check/history.jsonl` contains
one row per measurement attempt: commit, time, scope, status, duration, report path, and
accepted metrics. Only `complete` rows have metrics. Empty scopes are
`not_applicable`; tool failures, parser coverage mismatches, diagnostics, and
invalid metrics are `failed`. Scheduling rows for skipped commits contain their
checkout, captured SHA, enqueue time, and replacement SHA without metrics.
A file lock protects history appends. `queue.json` records the current and
pending jobs; `worker.log` retains worker and launch diagnostics.

Use one pinned native analyzer scan for this recording mode. It writes aggregate
JSON and scalar per-file/per-function details without function bodies. Validate
detail totals against aggregate SLOC, function counts, and complexity masses
before accepting the row. Details support the evolution dashboard; the native
API is checked against the official CLI on Python, Rust, and JavaScript fixtures. A later PR review should
run the full comparison workflow, including text findings, against its own
resolved commits. Repeated manual recordings of the same commit remain separate
attempts. No automatic report cleanup occurs; delete old report directories if
space becomes a concern, keeping their history rows as provenance.

For direct synchronous measurement, invoke the recorder manually. To verify the
background path, run:

```sh
python3 /absolute/path/to/repo/.slop-check/queue_commit.py \
  --repo /absolute/path/to/repo --output /absolute/path/to/repo/.slop-check
```

Inspect the history after the worker finishes. Check the history row,
parse counts, and stderr. Verify preserved hooks through the installed path,
especially hooks that locate sibling resources. Do not create a commit in the
user's repo solely to test installation; use a temporary Git repo for that.

To disable, restore `previous_local_hooks_path` from `settings.json` using
`git config --local core.hooksPath <saved-value>`, or if it is null, run
`git config --local --unset core.hooksPath`. Keep `.slop-check/` excluded while
retaining logs. After restoration, the directory and its exclude entry may be
removed if the user also wants to delete the local history.
