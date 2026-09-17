"""Software companies from the Y Combinator company directory.

A community-maintained JSON mirror of YC's public directory
(https://github.com/yc-oss/api) lists every YC company with its website,
locations, industry, tags, team size, batch, status, and whether it is hiring.
For software markets that is the closest thing to a registry: a website to read,
a size, and two timing signals for free (a recent batch means recently funded; an
open hiring flag means growing).

Spec:
    type: yc_directory
    url: https://yc-oss.github.io/api/companies/all.json   # default; or `path:` to a saved copy
    industries: ["B2B"]                 # match any (industry or sub-industries)
    tags_any: ["SaaS", "Developer Tools"]
    statuses: ["Active"]
    team_size: [10, 150]                # inclusive range
    hiring_only: true
    batch_since: 2023                   # batch year >= this becomes a "funded" signal

Region keys used:
    countries / regions / cities       # matched against every listed location
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from marketmapper import filters as mf

DEFAULT_URL = "https://yc-oss.github.io/api/companies/all.json"
MAX_BYTES = 40_000_000
_COUNTRY = {"USA": "US", "United States": "US", "Canada": "CA", "United Kingdom": "GB"}


def parse_locations(text: str) -> list[dict[str, str | None]]:
    """ "Chicago, IL, USA; Remote" -> [{"city": "Chicago", "region": "IL", "country": "US"}]."""
    out = []
    for part in (text or "").split(";"):
        bits = [b.strip() for b in part.split(",") if b.strip()]
        if len(bits) >= 3:
            out.append({"city": bits[0], "region": bits[1], "country": _COUNTRY.get(bits[-1], bits[-1])})
        elif len(bits) == 2 and re.fullmatch(r"[A-Z]{2}", bits[0]):
            # "CA, USA": a state with no city
            out.append({"city": None, "region": bits[0], "country": _COUNTRY.get(bits[1], bits[1])})
        elif len(bits) == 2:
            out.append({"city": bits[0], "region": None, "country": _COUNTRY.get(bits[1], bits[1])})
    return out


def batch_year(batch: str | None) -> int | None:
    m = re.search(r"(\d{4})", batch or "")
    return int(m.group(1)) if m else None


def _load(spec: dict[str, Any], fetcher) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError
    if spec.get("path"):
        path = Path(spec["path"])
        if not path.is_file():
            raise SourceError(f"yc directory file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetcher.get_json(spec.get("url", DEFAULT_URL), max_bytes=MAX_BYTES)
    if not isinstance(data, list):
        raise SourceError("yc directory: expected a JSON list of companies")
    return data


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import make_record

    industries = {i.lower() for i in spec.get("industries") or []}
    tags_any = {t.lower() for t in spec.get("tags_any") or []}
    statuses = {s.lower() for s in spec.get("statuses") or ["Active"]}
    size_range = list(spec.get("team_size") or [])
    low = size_range[0] if len(size_range) > 0 else None
    high = size_range[1] if len(size_range) > 1 else None
    since = spec.get("batch_since")

    records = []
    for c in _load(spec, fetcher):
        if statuses and (c.get("status") or "").lower() not in statuses:
            continue
        company_industries = {(c.get("industry") or "").lower(), *(i.lower() for i in c.get("industries") or [])}
        if industries and not industries & company_industries:
            continue
        if tags_any and not tags_any & {t.lower() for t in c.get("tags") or []}:
            continue
        size = c.get("team_size")
        if (low is not None and (size is None or size < low)) or (high is not None and (size is None or size > high)):
            continue
        if spec.get("hiring_only") and not c.get("isHiring"):
            continue
        # Prefer a listed location inside the region, then one that cannot be placed;
        # drop the company when every location is outside it.
        locations = parse_locations(c.get("all_locations", ""))
        ranked = {"in": [], "unknown": []}
        for loc in locations:
            st = mf.region_status(loc, region)
            if st in ranked:
                ranked[st].append(loc)
        location = (ranked["in"] or ranked["unknown"] or [None])[0]
        if location is None:
            continue

        profile_url = c.get("url") or ""
        evidence = [{"kind": "directory", "detail": f"Y Combinator {c.get('batch', '')}".strip(),
                     "url": profile_url, "snippet": (c.get("one_liner") or c.get("long_description") or "")[:300]}]
        year = batch_year(c.get("batch"))
        if since and year and year >= int(since):
            evidence.append({"kind": "funded", "date": f"{year}-01-01",
                             "detail": f"Funded by Y Combinator ({c.get('batch')})", "url": profile_url})
        if c.get("isHiring"):
            evidence.append({"kind": "hiring", "detail": "Hiring (YC directory)",
                             "url": f"{profile_url}/jobs" if profile_url else ""})
        records.append(make_record(
            "yc_directory", c.get("slug") or mf.slug(c.get("name", "")), c.get("name", ""),
            website=c.get("website"),
            address={"city": location["city"], "region": location["region"], "country": location["country"]},
            categories=[x for x in [c.get("industry"), *(c.get("industries") or []), *(c.get("tags") or [])] if x],
            evidence=evidence,
            employees=size,
        ))
    return records
