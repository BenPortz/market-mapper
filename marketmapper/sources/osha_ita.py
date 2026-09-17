"""Establishments from OSHA Injury Tracking Application (Form 300A) filings.

Manufacturing establishments with 20 or more employees file an annual injury
summary with OSHA, and the public dataset lists each one with its name, street
address, NAICS industry code, and average employee count. That makes it two
things at once: a source of mid-size and small plants that never rank in web
search, and the only source here that gives a company's size.

Establishments under 20 employees are not required to file, so the smallest
shops are missing; pair it with certification listings or trade lists.

The dataset is a large CSV (or a zip holding one) published at
https://www.osha.gov/itadata. Download it once and point the spec at the file;
rows are streamed, so a multi-hundred-megabyte file is fine.

Spec:
    type: osha_ita
    path: data/imports/osha/ITA_300A_Summary_Data_2024.zip
    naics: ["332911", "332913", "332919"]   # prefixes; "3329" matches the whole group

Region keys used:
    regions: ["IL", "IN"]            # state filter
    postal_prefixes: ["606", "600"]  # optional ZIP prefix filter
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Iterator

SIZE_CODES = {"1": "under 20", "2": "20-249", "21": "20-99", "22": "100-249", "3": "250+"}


def _rows(path: Path) -> Iterator[dict[str, str]]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            member = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
            if member is None:
                raise ValueError(f"no CSV inside {path}")
            with zf.open(member) as raw:
                yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace"))
    else:
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            yield from csv.DictReader(fh)


def plant_name(name: str, city: str) -> str:
    """Drop the location that filers append to a plant's name.

    OSHA filings name establishments like "Sloan Valve-Franklin Park" or "Chicago
    Faucets Company - Michigan City Plant". Without the suffix the name matches the
    company's other records and merges into one account.
    """
    if not city:
        return name
    pattern = rf"[\s\-–,]*\b{re.escape(city)}\b(\s+(plant|facility|site))?\s*$"
    cleaned = re.sub(pattern, "", name, flags=re.I).strip(" -–,")
    return cleaned or name


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError, make_record
    from marketmapper import filters as mf

    path = Path(spec.get("path", ""))
    if not path.is_file():
        raise SourceError(f"OSHA ITA file not found: {path} (download it from https://www.osha.gov/itadata)")
    naics = tuple(str(n) for n in spec.get("naics") or [])
    if not naics:
        raise SourceError("osha_ita needs at least one NAICS code or prefix")
    states = {mf.region_code(s) for s in region.get("regions", [])}
    prefixes = tuple(str(p) for p in region.get("postal_prefixes", []))

    out: dict[str, dict[str, Any]] = {}
    for row in _rows(path):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        code = row.get("naics_code", "")
        if not code.startswith(naics):
            continue
        state = mf.region_code(row.get("state"))
        postal = row.get("zip_code", "")[:5]
        if states and state not in states:
            continue
        if prefixes and not postal.startswith(prefixes):
            continue
        name = plant_name(row.get("establishment_name") or row.get("company_name") or "", row.get("city", ""))
        if not name or row.get("establishment_type", "1") not in ("", "1"):
            continue  # government entities are not companies

        employees = _int(row.get("annual_average_employees"))
        size = SIZE_CODES.get(row.get("size", ""), "")
        year = row.get("year_filing_for") or (row.get("created_timestamp", "")[:4])
        key = f"{mf.slug(name)}:{postal}"
        company = row.get("company_name")
        rec = make_record(
            "osha_ita", key, name,
            legal_name=company if company and mf.normalize_name(company) != mf.normalize_name(name) else None,
            address={"street": row.get("street_address"), "city": row.get("city"), "region": state,
                     "postal_code": postal, "country": "US"},
            categories=[c for c in (row.get("industry_description"), f"NAICS {code}") if c],
            evidence=[{"kind": "osha_filing",
                       "detail": f"OSHA 300A filing{' ' + year if year else ''}: "
                                 f"{employees if employees is not None else 'unknown'} average employees"
                                 f"{' (size ' + size + ')' if size else ''}",
                       "url": "https://www.osha.gov/itadata"}],
            employees=employees,
        )
        # Several yearly filings for one plant: keep the most recent row read.
        out[key] = rec
    return list(out.values())
