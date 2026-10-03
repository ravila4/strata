"""Export detailed metrics from the same scan as the aggregate report."""

import argparse
import json
import tempfile
from pathlib import Path

from scb_check.pipeline import analyze_files
from scb_check.reporting.score import compute_report

from strata.recorder import validate_details


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    manifest = json.loads(args.manifest.read_text())
    files = tuple(snapshot / path for path in manifest)
    flags = analyze_files(files, include_all=True).flags
    aggregate = compute_report(flags).to_dict()
    clones = {entry.file: entry.lines for entry in flags.lines.clone_sloc_lines_by_file}
    ast = {entry.file: entry.lines for entry in flags.lines.ast_sloc_lines_by_file}
    structural = {
        entry.file: entry.lines for entry in flags.lines.structural_sloc_lines_by_file
    }
    details = {
        "files": [
            {
                "path": str(path.relative_to(snapshot)),
                "sloc": sloc,
                "clone_loc": len(clones.get(path, frozenset())),
                "verbosity_flagged_lines": sorted(
                    clones.get(path, frozenset())
                    | ast.get(path, frozenset())
                    | structural.get(path, frozenset())
                ),
                "verbosity_flagged_loc": len(
                    clones.get(path, frozenset())
                    | ast.get(path, frozenset())
                    | structural.get(path, frozenset())
                ),
            }
            for path, sloc in flags.lines.total_loc_by_file
        ],
        "functions": [
            {
                "path": str(function.file.relative_to(snapshot)),
                "name": function.qualified_name or function.name,
                "line": function.span.start_line,
                "end_line": function.span.end_line,
                "sloc": function.sloc,
                "cc": function.cyc_complexity,
                "cognitive": function.cog_complexity,
            }
            for function in flags.findings.all_functions
        ],
    }
    validate_details(details, aggregate, manifest)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=args.details.parent, delete=False
    ) as stream:
        json.dump(details, stream)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(args.details)
    print(json.dumps(aggregate))


if __name__ == "__main__":
    main()
