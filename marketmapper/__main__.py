"""market-mapper command line.

    market-mapper run          discover -> enrich -> filter -> judge -> report
    market-mapper discover     find companies from every configured source
    market-mapper enrich       read company websites
    market-mapper filter       merge, filter, rank, queue for the judge
    market-mapper benchmark    Census coverage counts (needs CENSUS_API_KEY)
    market-mapper judge        Claude decides which companies belong (needs Claude API access)
    market-mapper report       render the report and CSV exports

Every stage accepts --profile, --data, and --date, and writes a dated file, so
any stage can be re-run on its own.
"""

from __future__ import annotations

import sys

from marketmapper import benchmark, discover, enrich, judge, pipeline, report

STAGES = {
    "discover": discover.main,
    "enrich": enrich.main,
    "filter": pipeline.main,
    "benchmark": benchmark.main,
    "judge": judge.main,
    "report": report.main,
}
RUN_ORDER = ["discover", "enrich", "filter", "judge", "report"]


def _shared(argv: list[str], stage: str) -> list[str]:
    """Pass only the flags a stage understands (all stages share these)."""
    out, i = [], 0
    allowed = {"--profile", "--data", "--date"} | ({"--search"} if stage in ("discover", "judge", "benchmark") else set())
    while i < len(argv):
        if argv[i] in allowed and i + 1 < len(argv):
            out += argv[i:i + 2]
            i += 2
        else:
            i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command in STAGES:
        return STAGES[command](rest)
    if command == "run":
        skip_judge = "--no-judge" in rest
        for stage in RUN_ORDER:
            if stage == "judge" and skip_judge:
                continue
            print(f"== {stage}", file=sys.stderr)
            code = STAGES[stage](_shared(rest, stage))
            # A partial discover (one source failed) still produces usable records.
            if code not in (0, 3):
                print(f"Stopped: {stage} exited with {code}.", file=sys.stderr)
                return code
        return 0
    print(f"Unknown command '{command}'.\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
