# Advisory commit metrics

Use this mode when the user asks to record SlopCodeBench metrics automatically
in a repository. It supports macOS and Linux with Git and the `strata` tool.
For one-off measurements without hooks, use the
[live dashboard workflow](dashboard.md#record-without-installing-a-hook).
Install from the primary checkout; linked worktrees share hook configuration,
so installation from a linked worktree is rejected. Commits made in linked
worktrees use the same hook and write to the primary checkout's history.
Choose explicit source roots after inspecting the repository. Each recording
automatically measures Python, JavaScript, and Rust under those roots. Metrics
and reports remain separate per language. TypeScript and JSX are not supported.

```sh
strata hook install --repo /absolute/path/to/repo \
  --source-root src --source-root crates
strata scan --repo /absolute/path/to/repo
```

The installer adds `/.strata/` to `.git/info/exclude` and sets
repository-local `core.hooksPath`. It saves the interpreter of the installed
`strata` tool as `python` and the previous hook configuration in
`.strata/settings.json`. The hook runs `python -I -m strata.worker`; no code
is copied into the repository. Executable existing
hooks are delegated to by their original absolute paths, preserving relative
resource lookup. An existing post-commit hook runs before the recorder. Global
Git settings and tracked repository files are not changed. Reinstallation
updates the interpreter path and selection while retaining the original hook settings;
it refuses to proceed if the active hook configuration has changed.
For a directory without hook configuration, installation accepts recorded
history or owned server settings and regular `server.stdout.log`/`server.stderr.log`
files. It rejects unexpected files and symlinks instead of overwriting them.

The post-commit hook captures the committing checkout and full SHA, queues the
job, launches a detached worker, and exits zero. One analysis runs at a time.
While it runs, the newest pending commit replaces any older pending commit;
replaced commits receive `skipped` history rows with the replacement SHA. The
current analysis finishes before the newest pending commit begins. With no
pending work, the worker exits; the next commit launches it again. There is no
commit-count threshold or timer between runs.

Upgrades (`uv tool upgrade strata`) apply to newly launched processes; let
active scans finish first. If the saved interpreter is missing, for example
after reinstalling Strata under another Python, the hook records a diagnostic in
local `worker.log` and exits zero; rerun `strata hook install`. Each repository
has its own configuration, queue, reports, and history.

Each nonempty language scan has a 60-second tool-version probe and a
120-second analysis timeout.
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
directories, and unsupported file extensions. **Inline test code is retained.** This
scope differs from a production-only report that strips inline tests. Keep
roots and this inclusion policy fixed when comparing history; filter
rows by scope if selection changes. Rust and JavaScript verbosity measures
clone lines only, while erosion measures complexity concentration, not defects.

Each attempt gets a unique `.strata/reports/<commit>-<suffix>/` directory
with a source manifest, explicit analyzer config, command, tool versions, raw
JSON, stderr, and a summary. Failed attempts also keep the exported source
snapshot for diagnosis; other attempts discard it, since Git holds the source. `.strata/history.jsonl` contains
a start event identifying each recording and its expected languages, followed by
one result per language: commit, time, scope, status, duration, report path, and
accepted metrics. All results share the recording identifier. Only `complete` rows have metrics. Empty scopes are
`not_applicable`; tool failures, parser coverage mismatches, diagnostics, and
invalid metrics are `failed`. Scheduling rows for skipped commits contain their
checkout, captured SHA, enqueue time, and replacement SHA without metrics.
A file lock protects history appends. `queue.json` records the current and
pending jobs; `worker.log` retains worker and launch diagnostics.

Use one pinned native analyzer scan per nonempty language for this recording mode. It writes aggregate
JSON and scalar per-file/per-function details without function bodies. Validate
detail totals against aggregate SLOC, function counts, and complexity masses
before accepting the row. Details support the evolution dashboard; the native
API is checked against the official CLI on Python, Rust, and JavaScript fixtures. A later PR review should
run the full comparison workflow, including text findings, against its own
resolved commits. Repeated manual recordings of the same commit remain separate
attempts. The dashboard selects the latest started recording within the same
commit and scope; failed or missing languages never reuse an older success.
A stopped recording remains incomplete. Manual recording fails with a busy
message while another recording owns the worker lock. A language failure does
not suppress remaining languages. No automatic report cleanup occurs; delete old report directories if
space becomes a concern, keeping their history rows as provenance.

For direct synchronous measurement, run `strata scan`. To verify the
background path, run the installed hook from the repository root:

```sh
.strata/hooks/post-commit
```

Inspect the history after the worker finishes. Check the history row,
parse counts, and stderr. Verify preserved hooks through the installed path,
especially hooks that locate sibling resources. Do not create a commit in the
user's repo solely to test installation; use a temporary Git repo for that.

To disable automatic recording:

```sh
strata hook remove --repo /absolute/path/to/repo
```

Removal restores the previous local hook configuration and retains measurements,
logs, and configuration in `.strata/`. Keep that directory excluded while
retaining local history. Delete the local history and its exclude entry only
when the user requests their removal.

Settings from earlier recorder formats require `strata hook install` again
after active workers finish. Reinstallation preserves recorded history and
original hook ownership. Old single-language measurements remain individual
evidence; they do not acquire mixed-language coverage.
