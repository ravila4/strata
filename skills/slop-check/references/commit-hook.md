# Advisory commit metrics

Use this mode when the user asks to record SlopCodeBench metrics automatically
in a repository. It supports macOS and Linux with Python 3.10+, Git, and `uvx`.
For one-off measurements without hooks, use the
[live dashboard workflow](dashboard.md#record-without-installing-a-hook).
Install from the primary checkout; linked worktrees share hook configuration,
so installation from a linked worktree is rejected. Commits made in linked
worktrees use the shared runtime and write to the primary checkout's history.
Choose explicit source roots after inspecting the repository. Each recording
automatically measures Python, JavaScript, and Rust under those roots. Metrics
and reports remain separate per language. TypeScript and JSX are not supported.

Run these commands from the shared skill installation's directory:

```sh
python3 scripts/install_hook.py --repo /absolute/path/to/repo \
  --source-root src --source-root crates
python3 scripts/record_commit.py \
  --repo /absolute/path/to/repo
```

The installer adds `/.slop-check/` to `.git/info/exclude` and sets
repository-local `core.hooksPath`. It saves the resolved shared skill directory
as `source_runtime` and the previous hook configuration in
`.slop-check/settings.json`. Repository hooks invoke that runtime; application
scripts and dashboard assets stay in the shared installation. Executable existing
hooks are delegated to by their original absolute paths, preserving relative
resource lookup. An existing post-commit hook runs before the recorder. Global
Git settings and tracked repository files are not changed. Reinstallation
updates the runtime path and selection while retaining the original hook settings;
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

Updates apply to newly launched processes. Let active scans finish before
replacing or removing runtime files. Keep the installation at its saved path;
if it moves, rerun the installer from the new directory for each repository.
If the shared runtime is missing, the hook records a diagnostic in local
`worker.log` and exits zero. Each repository has its own configuration, queue,
reports, and history even when repositories use the same runtime.

Each nonempty language scan has a 60-second dependency setup timeout and a
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

Each attempt gets a unique `.slop-check/reports/<commit>-<suffix>/` directory
with a source manifest, explicit analyzer config, command, tool versions, raw
JSON, stderr, and a summary. Failed attempts also keep the exported source
snapshot for diagnosis; other attempts discard it, since Git holds the source. `.slop-check/history.jsonl` contains
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

For direct synchronous measurement, invoke the recorder manually. To verify the
background path, run:

```sh
python3 scripts/queue_commit.py \
  --repo /absolute/path/to/repo --output /absolute/path/to/repo/.slop-check
```

Inspect the history after the worker finishes. Check the history row,
parse counts, and stderr. Verify preserved hooks through the installed path,
especially hooks that locate sibling resources. Do not create a commit in the
user's repo solely to test installation; use a temporary Git repo for that.

To disable automatic recording, run from the shared skill directory:

```sh
python3 scripts/install_hook.py --repo /absolute/path/to/repo --remove
```

Removal restores the previous local hook configuration and retains measurements,
logs, and configuration in `.slop-check/`. Keep that directory excluded while
retaining local history. Delete the local history and its exclude entry only
when the user requests their removal.

Existing single-language hook settings require reinstallation from the updated
runtime after active workers finish. The removed `--language` argument is not
accepted. Reinstallation preserves recorded history and original hook ownership.
Old individual measurements remain individual evidence; they do not acquire
mixed-language coverage.
