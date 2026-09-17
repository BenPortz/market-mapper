"""Manufacturers from NSF International certification listings.

Products that touch drinking water in North America are certified to NSF/ANSI 61
(health effects) and usually NSF/ANSI 372 (lead content), and plumbing products
to a family of related standards. The public listings name every certified
company and, importantly, the *plant* where the product is made. A 15-person
valve shop with no website to speak of still has to appear here to sell into
potable water, so this finds small manufacturers that web search misses.

Listings are filtered by plant state, so a result means manufacturing happens in
the region, not just a sales office.

Spec:
    type: nsf
    programs: [PwsComponents, Plumbing]   # drinking water components; plumbing products
    product_match: ["valve", "backflow", "faucet"]   # optional regex filter on product types

Region keys used:
    regions: ["IL"]    # US state codes, one listing request per program per state

NSF notes that its listings change and should be confirmed at the listing link;
every record carries that link as evidence.
"""

from __future__ import annotations

import html as htmllib
import re
import urllib.parse
from typing import Any

BASE = "https://info.nsf.org/Certified/{program}/Listings.asp"
PROGRAMS = {
    "PwsComponents": {"label": "NSF/ANSI/CAN 61 drinking water components", "params": {"Standard": "061"}},
    "Plumbing": {"label": "NSF plumbing system components", "params": {}},
}

_COMPANY_SPLIT = re.compile(r"<hr noshade>\s*<br>\s*<table", re.I)
_NAME = re.compile(r"<font size='\+2'>(.*?)</font>", re.I | re.S)
_ROW_CELL = re.compile(r"<td align=['\"]?left['\"]?[^>]*>(.*?)</td>", re.I | re.S)
_WEBSITE = re.compile(r"<a href=\"([^\"]+)\"[^>]*>\s*Visit this company", re.I)
_FACILITY = re.compile(r"Facility\s*:\s*</strong>\s*([^<]+)", re.I)
_GROUP = re.compile(r"<font size=\"3\">\s*<strong>(.*?)</strong>", re.I | re.S)
_TYPE = re.compile(r"colspan=\"\d+\">\s*(?:<br>)?\s*<font size=\"2\">\s*<strong>(.*?)</strong>", re.I | re.S)
_CITY_STATE_ZIP = re.compile(r"^(.+?),\s*([A-Z]{2})\s+(\d{5})(?:-?\d{4})?$")
_PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")


def _text(fragment: str) -> str:
    """Plain text of an HTML fragment, with entities decoded and footnote marks removed."""
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    t = re.sub(r"\[[A-Z0-9]{1,2}\]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def listing_url(program: str, state: str) -> str:
    params = {**PROGRAMS[program]["params"], "PlantState": state, "PlantCountry": "UNITED STATES"}
    return BASE.format(program=program) + "?" + urllib.parse.urlencode(params)


def parse_listing(page: str, program: str, url: str) -> list[dict[str, Any]]:
    """One record per company facility on a listing page."""
    from marketmapper.sources import make_record
    from marketmapper import filters as mf

    records = []
    for block in _COMPANY_SPLIT.split(page)[1:]:
        m = _NAME.search(block)
        if not m:
            continue
        name = _text(m.group(1))
        header = block[: block.find("</table>")]
        lines = [t for t in (_text(c) for c in _ROW_CELL.findall(header)) if t and t != name]

        street = city = state = postal = phone = None
        for line in lines:
            if (csz := _CITY_STATE_ZIP.match(line)):
                city, state, postal = csz.groups()
            elif _PHONE.match(line):
                phone = line
            elif line not in ("United States", "Visit this company's website") and street is None and city is None:
                street = line
        site = _WEBSITE.search(header)

        groups = [_text(g) for g in _GROUP.findall(block)]
        types = sorted({_text(t) for t in _TYPE.findall(block)} - {"Trade Designation", "Size", "Temp",
                                                                     "Material", "Water", "Contact"})
        for facility in {_text(f) for f in _FACILITY.findall(block)} or {None}:
            fac_city, _, fac_state = (facility or "").rpartition(", ")
            same_site = bool(facility) and fac_city.lower() == (city or "").lower() and fac_state == state
            evidence = {
                "kind": "certification",
                "detail": f"{PROGRAMS[program]['label']}: plant in {facility or 'unlisted location'}",
                "url": url,
                "snippet": ("Certified products: " + "; ".join(types or groups))[:300],
            }
            if not same_site and city:
                evidence["snippet"] += f" | Company office: {city}, {state}"
            records.append(make_record(
                "nsf", f"{mf.slug(name)}:{mf.slug(facility or 'none')}", name,
                website=site.group(1) if site else None,
                address={"street": street if same_site else None,
                         "city": fac_city or city, "region": fac_state or state,
                         "postal_code": postal if same_site else None, "country": "US"},
                phone=phone,
                categories=groups + types,
                evidence=[evidence],
            ))
    return records


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError
    from marketmapper import filters as mf

    states = region.get("regions") or []
    if not states:
        raise SourceError("nsf needs region.regions (US state codes)")
    programs = spec.get("programs") or list(PROGRAMS)
    unknown = [p for p in programs if p not in PROGRAMS]
    if unknown:
        raise SourceError(f"nsf: unknown program(s) {unknown}; known: {sorted(PROGRAMS)}")
    match = spec.get("product_match") or []

    out: dict[str, dict[str, Any]] = {}
    for program in programs:
        for state in states:
            url = listing_url(program, state)
            page = fetcher.get_page(url)["html"]
            for rec in parse_listing(page, program, url):
                if match and not mf.pattern_hits(" ".join([rec["name"], *rec["categories"]]), match):
                    continue
                if rec["record_id"] in out:
                    existing = out[rec["record_id"]]
                    existing["categories"] += [c for c in rec["categories"] if c not in existing["categories"]]
                    existing["evidence"] += rec["evidence"]
                else:
                    out[rec["record_id"]] = rec
    return list(out.values())
