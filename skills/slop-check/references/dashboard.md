# Code evolution dashboard

Use this mode when the user requests plots of recorded code metrics. The dashboard
reads the local commit recorder's `.slop-check/history.jsonl`; it does not analyze
or backfill historical commits. Install the hook first to collect new snapshots.
Run commands from this skill's directory, replacing repository paths below.

## Generate an offline dashboard

```sh
uv run --script scripts/generate_dashboard.py --repo /absolute/path/to/repo
```

This writes `.slop-check/dashboard.html` atomically. Plotly is embedded, so the
result opens offline with no CDN. The first generation installs the pinned
Plotly dependency through uv. The file remains a snapshot until regenerated.

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

Default ordering follows HEAD's first-parent Git history. Date view uses UTC and sorts by
commit timestamp, which can differ from ancestry. Measurements outside that
history are omitted. Different analyzer versions, languages, roots, and inclusion
policies appear as separate scopes. Repeated measurements use the latest accepted
attempt for each commit within a scope. Missing detail artifacts leave aggregate
metrics visible and create gaps in hotspot/distribution plots. One measured
commit is a baseline, not an observed trend. Directory renames can move bands
without changing complexity.

## Serve with automatic refresh

```sh
uv run --script scripts/serve_dashboard.py --repo /absolute/path/to/repo --port 8766
```

Open `http://127.0.0.1:8766/`. The server exposes only the dashboard and `/data.json`,
never arbitrary repository files. It binds to loopback. While visible, the page
checks every five seconds using conditional requests, pauses while hidden, and
checks immediately on return. Refresh retains the chosen scope, older snapshot,
and directory/file selection when they remain available. A view at the latest
snapshot follows new measurements. An unmeasured branch clears old metrics.
Errors show an explicit unavailable status and retain the last received view.

The server refreshes its dataset when HEAD, history, or queue state changes.
If you repair or remove report artifacts without changing those inputs, restart
it to reload them. Shut down the foreground server with Ctrl-C.

## Persistent macOS service and Tailscale

For an explicitly requested persistent service:

```sh
python3 scripts/install_server.py --repo /absolute/path/to/repo \
  --port 8766 --mount /slop/project --tailscale
```

This prepares dependencies, copies the dashboard runtime into the excluded
`.slop-check/dashboard-tool/`, and installs a repository-specific user launch
agent. It starts at login and restarts after failures. Omit `--tailscale` for a
loopback-only service. Other operating systems can use the foreground command
with their own service manager. Change the port for additional repositories;
the default URL mount is `/slop/<repository name>`.

Tailscale Serve adds the specified HTTPS path on port 443 and preserves other
handlers. A conflicting handler is rejected. The installer prints the tailnet
URL and saves ownership in `.slop-check/server-settings.json`. Tailscale must be
running, and its Serve/HTTPS prerequisites must be enabled for the tailnet. The
phone must be connected to the tailnet and the computer online. Closing the
terminal does not stop the launch agent; a sleeping or offline computer cannot
serve updates.

Reinstalling prepares dependencies before stopping the existing agent. Activation
failures restore the previous local installation and remove a newly created
proxy mount where cleanup succeeds. Inspect reported cleanup failures before
retrying. Remove the service before changing its port, mount, or Tailscale mode:

```sh
python3 scripts/install_server.py --repo /absolute/path/to/repo --remove
```

Removal targets only this repository's agent and matching owned Tailscale mount.
It retains recorded history and reports. Server diagnostics are in
`.slop-check/server.stdout.log` and `.slop-check/server.stderr.log`.

## Verification

Generate the first dashboard and inspect it at desktop and phone widths. Confirm
metric totals against the accepted analyzer report and preserve missing-data gaps.
For live serving, verify the loopback endpoint, mounted Tailscale URL with and
without its trailing slash, automatic refresh, and rejection of arbitrary file
paths. Inspect existing Tailscale handlers before and after installation. Do not
claim phone connectivity from a desktop viewport test.
