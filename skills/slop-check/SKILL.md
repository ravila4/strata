---
name: slop-check
description: Assess Python, Rust, or JavaScript source with SlopCodeBench static metrics, compare PR snapshots, or install an explicitly requested local commit-metrics hook. Use for scb-check, verbosity, erosion, local metric history, and code evolution dashboards. Produces advisory measurements; does not replace a correctness review.
---

# Slop Check

Measure a PR with `scb-check`, then inspect the findings in context. The useful
result identifies changed functions or duplicated blocks worth reviewing and
explains why. A score alone is insufficient grounds for a refactor.

## Local commit history

When the user requests automatic local metric recording, use
[references/commit-hook.md](references/commit-hook.md) and the bundled installer.
This mode queues committed snapshots after successful commits and coalesces pending work. It does not
interpret findings or create a PR comparison. Ordinary requests to run a slop
check do not authorize hook installation.

## Code evolution dashboard

Use the live dashboard as the default visual deliverable for metric checks and
comparisons. Follow [references/dashboard.md](references/dashboard.md) to record
the selected commits without installing hooks, start or reuse a server for the
verified repository and requested revisions, and return its working URL. Keep raw measurement artifacts
alongside the dashboard. Persistent macOS and Tailscale setup require a user
request for those modes. Group hotspots by recorded
source roots and the directory hierarchy. Keep the absolute complexity timeline
linked to the open directory or selected file.

## Resolve the inputs

- Accept a PR number, URL, or explicit base/head revisions. Resolve the intended
  repository and PR from live state; ask if multiple candidates remain plausible.
- For GitHub, use `gh pr view` to obtain the title, URL, state, base/head commit
  IDs, and changed files. Fetch missing commits without checking out the branch.
- Compare the merge base with the resolved PR head. Record both full hashes;
  do not substitute the moving base branch tip for the merge base.
- Inspect `git status` and the rename-aware diff. Analyze committed snapshots;
  a dirty checkout is not the PR head. Preserve unrelated working files.

## Prepare comparable snapshots

Export tracked application source (Python `.py`/`.pyw`, Rust `.rs`, JavaScript
`.js`/`.mjs`/`.cjs`) at each revision into separate, fresh
directories under a gitignored output directory. Use `git archive` or an
equivalent export, without importing or executing the target project. Omit
symlinks so analysis cannot follow them outside the snapshot; list omissions.

Choose source roots from the repository layout. Exclude tests, fixtures,
generated files, vendored code, and documentation from the primary measurement.
Apply the same inclusion policy to both revisions, retaining deleted source on
the base side and added source on the head side. Record included paths and
exclusions. The pinned analyzer below measures Python, Rust, and JavaScript
source; its ast-grep slop rules, structural rules, and source directives apply
to Python only, so Rust and JavaScript contribute SLOC, clone detection, and
erosion but no rule findings. The analyzer also discovers TypeScript, Zig,
Haskell, and C++; leave those out of the export unless explicitly requested.
Report unselected languages as unmeasured, rather than silently including them
in a PR-wide claim.

Scan the full selected source at both revisions to retain duplication context.
For a sizable added package, an additional isolated scan can show its own score;
label its scope and explain that it misses clones elsewhere in the repository.
Do not compare a changed-file-only head score with a whole-repository base score.
If a scope has no supported source, report it as not applicable, not zero erosion.

## Run the analyzer

Use **`scb-check==0.2.0`** for this workflow. Keep this pin fixed across comparisons;
upgrading requires rerunning both revisions and recording the new version.
Install through `uvx`, without adding a project dependency or changing its lock.

Create an explicit configuration file in the output directory containing
`exclude = []`. Filter the exported file set deliberately so project or global
configuration cannot silently alter the selection. With `snapshot`, `config`,
and `report` set to the chosen paths, run:

```sh
env -u SCB_CHECK_EXTRA_SLOP_RULES uvx scb-check==0.2.0 check \
  "$snapshot" --config "$config" --report --include-all \
  > "$report.json" 2> "$report.stderr"

env -u SCB_CHECK_EXTRA_SLOP_RULES uvx scb-check==0.2.0 check \
  "$snapshot" --config "$config" --include-all \
  > "$report-findings.txt" 2> "$report-findings.stderr"
```

Run both forms for base and head: JSON contains aggregate scores, while the text
report supplies the locations needed to compare functions and duplicate blocks.
Quote paths and derive command arguments from verified metadata.

Check exit status, parse the JSON, and inspect stderr for every invocation.
Version 0.2.0 can report partial results after a parser or ast-grep failure:
success status alone is insufficient. Match `files_scanned` to the selected file
count, and confirm `syntax_by_language` reports parsed files for each language
in the export. If ast-grep reports missing tools, subprocess failures, or malformed
output, diagnose and rerun; do not present degraded verbosity as a valid score.
Zero AST findings alone do not prove failure. Preserve errors and clearly mark
incomplete measurements when a stage cannot be recovered.

Record analyzer and ast-grep versions, resolved tool dependency versions, config,
source manifests, commit hashes, commands, raw reports, and diagnostics alongside
the summary. Never reuse artifacts from another head revision without labeling it.

## Review the changes

Use the diff and both location reports to identify newly flagged or worsened
functions touched by the PR. Match functions by file and qualified name, accounting
for renames; line numbers alone are unstable. Read their complete bodies and
relevant callers before recommending changes. Separate inherited findings from
those introduced by the PR.

Inspect clone groups with at least one added or modified block, including clones
of pre-existing code. Repeated declarations, required interfaces, and independent
contracts can legitimately look alike. Label findings as actionable candidates,
justified structure, or uncertain, with brief reasons and source links.

Interpret the scores using these limits:

- **Verbosity:** the union of clone, AST-rule, and trivial-wrapper SLOC divided
  by total SLOC in this analyzer version. The component counts overlap; do not
  sum them. A lower ratio can coexist with more flagged lines. Rust and
  JavaScript contribute clone lines only, so their verbosity understates what
  the Python rule set would flag; do not compare verbosity ratios across
  languages as if the numerators meant the same thing.
- **Erosion:** the share of total function mass held by functions with cyclomatic
  complexity above 10, where mass is `complexity * sqrt(SLOC)`. It is not the
  fraction of defective code. Report the high-complexity function count too.
  Version 0.2.0 also reports cognitive erosion (`cog_erosion`), which covers
  Rust and JavaScript functions on equal footing with Python; include it as a
  secondary signal.
- Version 0.2.0 flags custom exception subclasses, protocol declarations, and
  some useful wrappers. `--include-all` also bypasses per-rule count thresholds;
  a message about many dataclasses may appear below that count. Inspect evidence
  rather than accepting the rule message as a diagnosis.
- Validation of external input and recovery contracts can require branching.
  Preserve those checks. Extracting helpers just to cross below the threshold
  does not establish improved maintainability.
- These are static snapshot measurements, not the iterative benchmark's solve
  rate. The paper's historical tooling and its displayed verbosity formula
  differ from this analyzer. Do not rank a PR against its human/agent averages
  without establishing methodological comparability.

## Deliver the report

Lead with the observed change and its practical meaning. Include a compact
base/head table with SLOC, verbosity, erosion, high-complexity function counts,
and deltas. Express changes in ratio scores as percentage points; show code-size
changes and flagged-line counts so denominator effects remain visible.

Follow with a short list of reviewed findings in changed code, their locations,
and reasons to act or retain the code. Link raw artifacts and the rerun command.
State excluded languages, incomplete stages, and other material limitations.
Keep correctness/test results separate from these measurements.

Lead the delivery with the verified live dashboard URL and the observed change.
Keep the comparison table and reviewed findings in the reply. Recorder snapshots
retain inline tests; label that scope separately from the production-only PR
comparison above. Serve a PR comparison with `--commit <base-hash> --commit
<head-hash>` so the dashboard shows the requested snapshots while the checkout
stays on its current branch. Verify `requested_commits` in `/data.json` before
reusing a server or returning its URL. Ordinary history serving omits these
flags and follows checkout HEAD.

Return the report locally. Invoking this skill does not authorize source edits,
posting PR comments, or adding CI gates. Install a local advisory hook only when
the user requests automation, and verify its first recorded measurement.

Sources: [analyzer v0.2.0](https://github.com/gabeorlanski/scb-check/tree/v0.2.0),
[benchmark integration](https://github.com/SprocketLab/slop-code-bench/blob/main/src/slop_code/metrics/checkpoint/driver.py),
and [paper methodology](https://arxiv.org/html/2603.24755v1#S2.SS3).
