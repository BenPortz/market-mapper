"""DISCOVER stage: run each search's sources -> raw company records.

Plain Python talking to public APIs. No model. A source that fails (a missing
API key, an outage) is recorded with its error and the rest of the search keeps
going, so a broken source reads as broken in the report instead of as a small
market.

Usage:
    python -m marketmapper.discover
    python -m marketmapper.discover --search conveyor_manufacturers
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from marketmapper.config import Layout, Profile, ProfileError, load_profile
from marketmapper.net import DEFAULT_USER_AGENT, Fetcher, NetError
from marketmapper.sources import SOURCES, SourceError


def run_search(name: str, profile: Profile, fetcher, ctx: dict[str, Any]) -> dict[str, Any]:
    search = profile.search(name)
    region = search.get("region") or {}
    records: list[dict[str, Any]] = []
    source_log = []
    for spec in search.get("sources", []):
        kind = spec.get("type")
        try:
            found = SOURCES[kind](spec, region, fetcher, ctx)
            records.extend(found)
            source_log.append({"type": kind, "status": "ok", "count": len(found)})
        except (SourceError, NetError, ValueError, KeyError) as e:
            source_log.append({"type": kind, "status": "failed", "count": 0, "error": str(e)})

    ok = [s for s in source_log if s["status"] == "ok"]
    status = "ok" if len(ok) == len(source_log) else ("partial" if ok else "failed")
    return {"status": status, "sources": source_log, "records": records}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", help="Profile YAML (default: config/profile.yaml)")
    ap.add_argument("--search", action="append", help="Run only this search (repeatable)")
    ap.add_argument("--data", default="data")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)

    try:
        profile = load_profile(args.profile)
    except ProfileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    names = args.search or list(profile.searches)
    unknown = [n for n in names if n not in profile.searches]
    if unknown:
        print(f"ERROR: unknown search: {', '.join(unknown)}", file=sys.stderr)
        return 1

    fetcher = Fetcher(user_agent=profile.http.get("user_agent", DEFAULT_USER_AGENT),
                      min_interval=float(profile.http.get("min_interval_seconds", 1.0)))
    ctx = {"date": args.date, "data": args.data}
    out = {"date": args.date,
           "searches": {n: run_search(n, profile, fetcher, ctx) for n in names}}

    path = Layout(Path(args.data)).for_date("discovered", args.date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    for n, block in out["searches"].items():
        summary = ", ".join(f"{s['type']} {s['count'] if s['status'] == 'ok' else 'FAILED'}"
                            for s in block["sources"])
        print(f"  {n}: {block['status']} ({summary})", file=sys.stderr)
        for s in block["sources"]:
            if s["status"] != "ok":
                print(f"    {s['type']}: {s['error']}", file=sys.stderr)
    print(f"Wrote {path}")
    return 0 if all(b["status"] != "failed" for b in out["searches"].values()) else 3


if __name__ == "__main__":
    sys.exit(main())
