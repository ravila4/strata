# Dashboard

The dashboard reads `.strata/history.jsonl`, so record commits before serving.

```sh
strata scan --source-root src          # or strata backfill
strata serve
```

Open <http://127.0.0.1:8766/>. The server binds to loopback and the page
refreshes every five seconds while visible. Plotly and syntax highlighting are
bundled; no CDN is needed. Stop a foreground server with Ctrl-C.

## Compare two commits

```sh
strata scan <base> --source-root src
strata scan <head> --source-root src
strata serve --commit <base> --commit <head>
```

The dashboard then shows only those commits, in that order. References resolve
once at startup, so restart with new hashes to review an updated PR.

## Modes and scopes

History mode follows HEAD's first-parent history; comparison mode shows only the
commits you pass. The date view sorts by UTC commit time, which can differ from
ancestry. Different analyzer versions, languages, roots, and inclusion policies
appear as separate scopes. Each commit shows its latest recording, and a failed
or missing language never falls back to an older success.

## Plots

The plots show source lines, cyclomatic and cognitive erosion, function
complexity (median, 90th percentile, maximum), verbosity, flagged lines, and
scan duration. Python verbosity counts the union of cloned, AST-flagged, and
structural-rule lines; the component counts overlap, so don't add them. Rust
and JavaScript count clone lines only.

The sunburst shows the selected commit's directories as rings. Tap a directory
to open it, the center to go up, or Reset view to see everything. Tap a file to
list its functions in the table. The Plot metric selector sets both the wedge
sizes and the timeline. Hover percentages are relative to all selected source,
even when zoomed.

The timeline follows the open directory or file. Its bands are the immediate
children: the top eight by peak value stay fixed and the rest are grouped as
Other. Band colors match the sunburst and don't indicate severity. Selecting a
point on the timeline moves the sunburst to that commit.

Missing measurements show as gaps, never as zero. Refresh keeps the selected
scope, commit, and directory while they still exist.

## Source view

The code icon beside a file or function opens its source at the selected
commit, even if the working tree has changed. Metric sets the gutter shading:
function ranges by complexity or erosion, or the exact flagged lines for
verbosity. The minimap shows hotspots across the file. Measurements recorded
without line locations show an unavailable notice instead of guessing.

A sidebar lists the commit's tracked files, with each file's value for the
selected metric. Files toggles it, and on mobile opens it as an overlay. Expand
fills the screen. Untracked files and submodule contents aren't listed.

Files over 256 KiB or 10,000 lines, binary files, symlinks, and submodules
can't be previewed. A tree too large to list shows an error with a Retry button;
the open file stays usable.

If you repair or delete report directories, restart the server to reload them.

## Persistent service on macOS

```sh
strata service install --port 8766 --tailscale
strata service remove
```

This installs a launch agent for the repository that starts at login and
restarts after failures. Omit `--tailscale` for loopback only. Each repository
needs its own port; the default tailnet mount is `/strata/<repository name>`,
and `--mount` overrides it.

With `--tailscale`, Tailscale Serve adds that HTTPS path on port 443 and leaves
other handlers alone; a conflicting handler is rejected. Tailscale must be
running with Serve and HTTPS enabled for the tailnet. A failed install restores
the previous service and removes a mount it created.

Remove the service before changing its port, mount, or Tailscale mode. Removal
keeps history and reports. Server logs are `.strata/server.stdout.log` and
`.strata/server.stderr.log`.

After upgrading Strata, rerun `strata service install` with the same options and
reload the browser. Live refresh updates measurements, not server code or page
assets.
