# Code evolution dashboard

Use the live dashboard for metric checks, comparisons, and recorded history. It
reads `.slop-check/history.jsonl`; record the selected commits before serving.
Run commands from the shared skill installation's directory, replacing
repository paths below. Each repository keeps its own measurements and service
configuration in `.slop-check/`; scripts and dashboard assets remain shared.

## Record without installing a hook

```sh
python3 scripts/record_commit.py --repo /absolute/path/to/repo \
  --source-root src --language python --commit HEAD
```

Choose explicit relative source roots and one of `python`, `rust`, or
`javascript`. Repeat `--source-root` for multiple roots. For comparisons, run the
command once for each resolved base/head revision with the same selection. The
recorder resolves revisions to full commit hashes and retains inline tests;
keep this scope distinct from a production-only PR report. Inspect each accepted
history row and its diagnostics. Failed or empty measurements are not zero scores.
This command excludes `.slop-check/` locally and leaves hooks unchanged. Existing
hook installations can omit selection arguments to use their saved settings.

## Start or reuse the live dashboard

```sh
uv run --script scripts/serve_dashboard.py --repo /absolute/path/to/repo --port 8766
```

For a comparison, add `--commit <base-hash> --commit <head-hash>` to that command.
The server displays only those commits, in the supplied order, without changing
the checkout. References resolve once at startup; aliases for the same commit
are combined. New recordings refresh the measurements, while the selected
hashes stay fixed. Restart with new hashes to review an updated PR.

Run the server in a background session that remains alive after the reply, retain
its session or process identifier, save logs as `.slop-check/server.stdout.log`
and `.slop-check/server.stderr.log`, and return `http://127.0.0.1:8766/`.
Verify that `/` responds and `/data.json` has `repository_path` matching the
canonical repository path, `source_runtime` matching the resolved shared skill
directory, and the intended measurements. For a comparison,
`requested_commits` must match the ordered resolved hashes and `head` must match
the last selected hash. For history serving, `requested_commits` must be empty
and `head` must match checkout HEAD.
Reuse a server only after those checks. If the port belongs to another process,
choose a free port and report its URL. A server that failed to start is not a
deliverable. Local serving does not require a login service or Tailscale.

Plotly and syntax highlighting are bundled locally; no CDN is required.
The first launch installs the pinned Plotly dependency through uv.

## Explore the measurements

The plots show source lines, cyclomatic and cognitive erosion, function complexity
median/90th percentile/maximum, verbosity, flagged source lines, and scan duration. Counts are integers; displayed
percentages and timings have at most one decimal. Full precision stays in logs.

Verbosity is the recorded flagged-line ratio. Rust and JavaScript count clone
lines only. Python uses the union of cloned, AST-flagged, and structural-rule lines;
component counts overlap and must not be added. Missing measurements remain gaps.

The main hotspot view is a sunburst for the selected commit. Its concentric rings
follow repository directories down to files. Tap a directory to open it, the
center to go up, or Reset view to return to all selected source. Three levels are
visible at a time. Tap a file to select its functions in the table. The Plot metric selector controls wedge sizes and the absolute timeline together.
Complexity uses summed function CC. Cyclomatic erosion uses `CC * sqrt(function SLOC)`
for functions above CC 10; cognitive erosion uses the corresponding cognitive
complexity mass above 10. Verbosity uses flagged source lines counted once,
including files with no functions. These additive amounts size the wedges;
percentage rates remain in tooltips. Missing per-file verbosity makes the
sunburst unavailable; historical gaps are scoped to the selected directory or
file. A known zero amount stays zero and preserves navigation when switching
metrics. Colors identify branches in every mode.

Hover percentages refer to all selected source even when
zoomed. Small labels are hidden instead of overlapping; the tables retain access
to individual files. Refresh preserves the open directory while it still exists,
otherwise the path returns to all selected source. Tooltips and the selected
region's metric row show cyclomatic/cognitive erosion, function CC median/p90/max,
and verbosity. Erosion uses the analyzer's complexity-times-square-root-of-function-
SLOC mass and threshold above 10. Function-free measured regions have zero erosion
and unavailable percentiles. Region verbosity requires per-file flagged counts for
all selected files; incomplete historical detail shows Unavailable. The recorder
exports exact native flagged-line unions and validates their sums against the
aggregate report.

The absolute timeline follows the open directory, or the selected file. Its
stacked height is the selected metric's absolute amount, with units shown on the axis. Immediate child directories and files form the
bands. The top eight children by peak selected metric amount remain fixed across the displayed
history; remaining children form Other. Colors match those child categories in
the sunburst and do not indicate severity. Opening a directory, selecting a file,
or going up updates the timeline and tables together. Clearing a file returns to
its containing directory. Root filtering applies to all three views; global
metric plots retain their full measurement scope.

Selecting a timeline measurement updates the sunburst to that commit, retaining
the open path if it exists. Unknown details preserve the selection and appear as
gaps. A known snapshot with zero complexity remains zero. Isolated measurements
use stacked columns; consecutive measurements use stacked areas. Known absent
paths reset the snapshot selection to all selected source. Each timeline segment
retains zero values for paths absent from earlier or later complete snapshots.

History serving follows HEAD's first-parent Git history. Comparison serving uses
the supplied commit order, including commits outside that history. Date view uses UTC and sorts by
commit timestamp, which can differ from ancestry. Measurements outside that
history are omitted in history mode; comparison mode includes only the requested
hashes. Different analyzer versions, languages, roots, and inclusion
policies appear as separate scopes. Repeated measurements use the latest accepted
attempt for each commit within a scope. Missing detail artifacts leave aggregate
metrics visible and create gaps in hotspot/distribution plots. One measured
commit is a baseline, not an observed trend. Directory renames can move bands
without changing complexity.

The server exposes the dashboard, `/data.json`, and
`/source.json` for files admitted by the recorded measurement. It binds to loopback. While visible, the page
checks every five seconds using conditional requests, pauses while hidden, and
checks immediately on return. Refresh retains the chosen scope, older snapshot,
and directory/file selection when they remain available. A view at the latest
snapshot follows new measurements. In history mode, an unmeasured branch clears old metrics.
Errors show an explicit unavailable status and retain the last received view.

### View source and hotspots

Click the small code icon beside a file or function to open its source window.
The filename still filters the dashboard. Function source buttons scroll to the
recorded function range. Close the window with Close or Escape.

Source comes from the selected Git commit, including when the working tree has
changed. The open window stays on that commit during live refresh. Use Metric
hotspots to change its gutter colors; this also selects the dashboard plot metric.
Python, Rust, and JavaScript syntax highlighting is bundled locally.

Complexity shades function ranges by CC. Cyclomatic and cognitive erosion shade
functions above the corresponding complexity threshold of 10 using complexity
multiplied by the square root of function SLOC. Intensity is relative within the
file; overlapping ranges use the highest score. Verbosity marks the exact union
of flagged source lines. Measurements recorded without line locations display
an unavailable notice for that metric; they do not infer locations from counts.

Files over 256 KiB or 10,000 lines, binary content, and unavailable Git objects
cannot be previewed. Files over 128,000 characters or 4,000 lines display plain
text with the metric gutter. Excessive highlighting markup also uses plain text.
If the measurement changes before a source request arrives, refresh and reopen
it. Measurements must include recorded line locations for metric highlighting.

The server refreshes its dataset when history or queue state changes. History
mode also follows changes to checkout HEAD; comparisons retain their selected
commits.
If you repair or remove report artifacts without changing those inputs, restart
it to reload them. Shut down the foreground server with Ctrl-C.

## Persistent macOS service and Tailscale

For an explicitly requested persistent service:

```sh
python3 scripts/install_server.py --repo /absolute/path/to/repo \
  --port 8766 --mount /slop/project --tailscale
```

This prepares dependencies and installs a repository-specific user launch agent
that runs the shared dashboard server. It saves the resolved skill directory as
`source_runtime` in `.slop-check/server-settings.json`; `/data.json` reports the
running server's runtime path. It starts at login and restarts after failures. Omit `--tailscale` for a
loopback-only service. Other operating systems can use the foreground command
with their own service manager. Change the port for additional repositories;
the default URL mount is `/slop/<repository name>`. Choose a distinct port and
mount for each repository. Each service reads only its repository's measurements.

Tailscale Serve adds the specified HTTPS path on port 443 and preserves other
handlers. A conflicting handler is rejected. The installer prints the tailnet
URL and saves ownership in `.slop-check/server-settings.json`. Tailscale must be
running, and its Serve/HTTPS prerequisites must be enabled for the tailnet. The
phone must be connected to the tailnet and the computer online. Closing the
terminal does not stop the launch agent; a sleeping or offline computer cannot
serve updates.

Reinstalling prepares dependencies before stopping the existing agent. Activation
failures restore the previous service configuration and remove a newly created
proxy mount where cleanup succeeds. Inspect reported cleanup failures before
retrying. Remove the service before changing its port, mount, or Tailscale mode:

```sh
python3 scripts/install_server.py --repo /absolute/path/to/repo --remove
```

Removal targets only this repository's agent and matching owned Tailscale mount.
It retains recorded history and reports. Server diagnostics are in
`.slop-check/server.stdout.log` and `.slop-check/server.stderr.log`.

After updating server code or dashboard assets in the shared installation,
restart the server and reload the browser. For a persistent service, rerun the
installation command with the same port, mount, and Tailscale mode. For a
foreground server, stop it and run the serving command again. Automatic metric
refresh updates measurements within the running server; it does not reload
server code or the browser's dashboard assets. If the installation moves, rerun
each repository's installers from its new location to update `source_runtime`.

## Verification

Start the dashboard and inspect it at desktop and phone widths. Confirm
metric totals against the accepted analyzer report and preserve missing-data gaps.
For live serving, verify the loopback endpoint, mounted Tailscale URL with and
without its trailing slash, automatic refresh, and rejection of arbitrary file
paths. Inspect existing Tailscale handlers before and after installation. Do not
claim phone connectivity from a desktop viewport test.
