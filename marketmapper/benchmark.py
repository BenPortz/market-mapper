"""COVERAGE check: how many establishments should exist, per the Census Bureau.

County Business Patterns counts every establishment with paid employees by NAICS
industry code and county, split by employee size class. It names no companies,
so it cannot find anyone. What it does is say how big the target is: "Census
counts 38 valve manufacturing establishments in these counties, 21 of them with
fewer than 20 employees." Against that number, a list of 9 is visibly thin, and
a coverage-focused run has something concrete to close the gap on.

Caveats carried into the report: an establishment is a location, not a company;
an industry code is broader than any search ("industrial valves" includes oil
and gas valves); and published counts lag a couple of years.

Needs a free API key in CENSUS_API_KEY (https://api.census.gov/data/key_signup.html).

Search keys used:
    coverage:
      naics: ["332911", "332913", "332919"]
      counties: {"17": ["031", "043", "097"], "18": ["089"]}   # state FIPS -> county FIPS
      year: 2022

Usage:
    python -m marketmapper.benchmark --search chicago_water_valves
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from marketmapper.config import Layout, ProfileError, load_profile
from marketmapper.net import DEFAULT_USER_AGENT, Fetcher, NetError

API = "https://api.census.gov/data/{year}/cbp"


class BenchmarkError(RuntimeError):
    pass


def size_upper_bound(label: str) -> int | None:
    """Largest headcount in a size class label, or None for "All establishments".

    Labels look like "Establishments with less than 5 employees", "... with 5 to
    9 employees", or "... with 1,000 employees or more". Parsing the label keeps
    this independent of the size class codes, which have changed between years.
    """
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", label)]
    if not numbers:
        return None
    if "less than" in label.lower():
        return numbers[0] - 1
    if "or more" in label.lower():
        return 10**9
    return max(numbers)


def summarize(rows: list[list[str]]) -> dict[str, Any]:
    """Totals from one CBP API response (header row first)."""
    header, *data = rows
    col = {name: i for i, name in enumerate(header)}
    total = under_20 = 0
    labels = set()
    for row in data:
        label = row[col["EMPSZES_LABEL"]]
        count = int(row[col["ESTAB"]] or 0)
        labels.add(row[col["NAICS2017_LABEL"]])
        bound = size_upper_bound(label)
        if bound is None:
            total += count
        elif bound < 20:
            under_20 += count
    return {"total": total, "under_20": under_20, "labels": sorted(labels)}


def fetch_counts(coverage: dict[str, Any], fetcher, key: str) -> dict[str, Any]:
    year = int(coverage.get("year", 2022))
    naics_codes = [str(n) for n in coverage.get("naics") or []]
    counties = coverage.get("counties") or {}
    if not naics_codes or not counties:
        raise BenchmarkError("coverage needs naics codes and counties")

    by_code: dict[str, dict[str, Any]] = {}
    for code in naics_codes:
        agg = {"total": 0, "under_20": 0, "label": ""}
        for state, county_list in counties.items():
            data = fetcher.get_json(API.format(year=year), params={
                "get": "ESTAB,EMPSZES_LABEL,NAICS2017_LABEL",
                "for": f"county:{','.join(county_list)}",
                "in": f"state:{state}",
                "NAICS2017": code,
                "key": key,
            })
            if not isinstance(data, list) or len(data) < 2:
                continue  # no establishments in these counties for this code
            s = summarize(data)
            agg["total"] += s["total"]
            agg["under_20"] += s["under_20"]
            agg["label"] = agg["label"] or (s["labels"][0] if s["labels"] else "")
        by_code[code] = agg

    return {
        "status": "ok",
        "year": year,
        "naics": by_code,
        "total": sum(v["total"] for v in by_code.values()),
        "under_20": sum(v["under_20"] for v in by_code.values()),
        "county_count": sum(len(v) for v in counties.values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile")
    ap.add_argument("--search", action="append")
    ap.add_argument("--data", default="data")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)

    try:
        profile = load_profile(args.profile)
    except ProfileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    key = os.environ.get("CENSUS_API_KEY", "")
    fetcher = Fetcher(user_agent=profile.http.get("user_agent", DEFAULT_USER_AGENT))
    names = args.search or [n for n, s in profile.searches.items() if s.get("coverage")]
    out = {"date": args.date, "searches": {}}
    for name in names:
        coverage = profile.search(name).get("coverage")
        if not coverage:
            continue
        try:
            if not key:
                raise BenchmarkError("CENSUS_API_KEY is not set")
            out["searches"][name] = fetch_counts(coverage, fetcher, key)
        except (BenchmarkError, NetError, ValueError) as e:
            out["searches"][name] = {"status": "failed", "error": str(e)}
        block = out["searches"][name]
        print(f"  {name}: {block.get('status')} "
              f"{block.get('total', '')} {block.get('error', '')}".rstrip(), file=sys.stderr)

    path = Layout(Path(args.data)).for_date("benchmark", args.date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
