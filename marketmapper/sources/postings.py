"""Companies that are hiring, from job postings collected by a browser agent.

Hiring is a timing signal: a plant posting for a maintenance manager, or a
clinic hiring its second radiology tech, is changing something now. Postings are
one input among several here.

Most boards render client-side, so collection runs in a browser agent using the
pinned, read-only snippets in `sources/browser/` (see the README there). The
agent writes a JSON file; this source reads it. It performs no network access.

Prefer boards that filter by location, since a posting's text rarely carries a
clean address. When a posting has no parseable location the account's region is
"unknown", which the search's `region_strict` setting decides.

File shape:
    {"postings": [{"posting_id", "source", "url", "title", "company",
                   "location", "posting_text"}]}

Spec:
    type: postings
    path: data/postings/2026-03-14.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from marketmapper import filters as mf

_CITY_STATE = re.compile(r"([A-Z][A-Za-z .'-]+),\s*([A-Z]{2})\b")


def parse_location(text: str) -> dict[str, str | None]:
    m = _CITY_STATE.search(text or "")
    return {"city": m.group(1).strip(), "region": m.group(2)} if m else {"city": None, "region": None}


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError, make_record

    path = Path(spec.get("path", ""))
    if not path.is_file():
        raise SourceError(f"postings file not found: {path} (run the browser collection first)")
    data = json.loads(path.read_text(encoding="utf-8"))
    by_company: dict[str, dict[str, Any]] = {}
    for p in data.get("postings", []):
        company = mf.display_name(p.get("company", ""))
        key = mf.normalize_name(company)
        if not key:
            continue
        loc = parse_location(p.get("location") or p.get("posting_text", "")[:300])
        rec = by_company.get(key)
        if rec is None:
            rec = by_company[key] = make_record(
                "postings", mf.slug(company), company,
                address={"city": loc["city"], "region": loc["region"],
                         "country": "US" if loc["region"] else None},
            )
        rec["evidence"].append({"kind": "hiring", "detail": f"Hiring: {p.get('title', 'open role')}",
                                "url": p.get("url", "")})
    return list(by_company.values())
