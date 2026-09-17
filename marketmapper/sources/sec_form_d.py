"""Recently funded companies from SEC Form D filings.

A company raising money under Regulation D files a Form D within 15 days of the
first sale. The filing is public and names the issuer, its business address, its
industry group, and how much it is raising. For software markets that is a timing
signal with a date and a dollar figure: a company that just raised is hiring,
buying tools, and setting up processes.

Two steps: EDGAR full-text search finds Form D filings by business location and
date, then each filing's XML gives the industry group and amounts. Pooled
investment funds and SPVs, which dominate Form D volume, are dropped by industry
group. The filing's list of related persons (named individuals) is never read.

SEC's fair access policy requires a user agent that identifies you with a contact
email, and limits automated traffic to 10 requests per second. Set it in the
profile:

    http:
      user_agent: "Your Company contact@yourcompany.com"

Spec:
    type: sec_form_d
    days: 365
    industry_groups: ["Computers", "Telecommunications", "Other Technology"]
    new_filings_only: true          # skip amendments
    max_filings: 300                # search hits examined, before the industry filter

Region keys used:
    regions: ["IL"]                 # state codes for EDGAR location search
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any

SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}"
PAGE = 100
DEFAULT_GROUPS = ["Computers", "Telecommunications", "Other Technology"]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node: ET.Element | None, *path: str) -> ET.Element | None:
    """Namespace-agnostic child lookup by local tag names."""
    for name in path:
        if node is None:
            return None
        node = next((c for c in node if _local(c.tag) == name), None)
    return node


def _text(node: ET.Element | None, *path: str) -> str:
    found = _find(node, *path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_form_d(xml_text: str) -> dict[str, Any]:
    """The issuer, industry group, and offering amounts from a Form D primary document."""
    root = ET.fromstring(xml_text)
    issuer = _find(root, "primaryIssuer")
    offering = _find(root, "offeringData")

    def amount(name: str) -> float | None:
        raw = _text(offering, "offeringSalesAmounts", name)
        try:
            return float(raw)
        except ValueError:
            return None  # "Indefinite" or blank

    return {
        "name": _text(issuer, "entityName"),
        "street": _text(issuer, "issuerAddress", "street1"),
        "city": _text(issuer, "issuerAddress", "city"),
        "region": _text(issuer, "issuerAddress", "stateOrCountry"),
        "postal_code": _text(issuer, "issuerAddress", "zipCode"),
        "industry_group": _text(offering, "industryGroup", "industryGroupType"),
        "is_amendment": _text(offering, "typeOfFiling", "newOrAmendment", "isAmendment").lower() == "true",
        "first_sale": _text(offering, "typeOfFiling", "dateOfFirstSale", "value"),
        "total_offering": amount("totalOfferingAmount"),
        "amount_sold": amount("totalAmountSold"),
    }


def _money(value: float | None) -> str:
    return "an undisclosed amount" if value is None else f"${value:,.0f}"


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError, make_record
    from marketmapper import filters as mf

    if "@" not in getattr(fetcher, "user_agent", "@"):
        raise SourceError("sec_form_d: SEC requires a user agent with a contact email; set http.user_agent")
    states = region.get("regions") or []
    if not states:
        raise SourceError("sec_form_d needs region.regions (US state codes)")
    groups = {g.lower() for g in spec.get("industry_groups") or DEFAULT_GROUPS}
    today = dt.date.fromisoformat(ctx.get("date") or dt.date.today().isoformat())
    start = today - dt.timedelta(days=int(spec.get("days", 365)))
    limit = int(spec.get("max_filings", 300))

    hits, offset = [], 0
    while len(hits) < limit:
        data = fetcher.get_json(SEARCH, params={
            "forms": "D", "startdt": start.isoformat(), "enddt": today.isoformat(),
            "locationCodes": ",".join(states), "from": offset})
        batch = (data.get("hits") or {}).get("hits") or []
        hits += batch
        if len(batch) < PAGE:
            break
        offset += PAGE

    records: dict[str, dict[str, Any]] = {}
    for hit in hits[:limit]:
        src = hit.get("_source") or {}
        if src.get("form") != "D" and spec.get("new_filings_only", True):
            continue
        ciks, adsh = src.get("ciks") or [], src.get("adsh", "")
        if not ciks or not adsh:
            continue
        cik = str(int(ciks[0]))
        folder = ARCHIVE.format(cik=cik, folder=adsh.replace("-", ""))
        try:
            filing = parse_form_d(fetcher.get_text(f"{folder}/primary_doc.xml", max_bytes=2_000_000))
        except ET.ParseError:
            continue
        if filing["industry_group"].lower() not in groups or not filing["name"]:
            continue
        if filing["is_amendment"] and spec.get("new_filings_only", True):
            continue

        date = filing["first_sale"] or src.get("file_date", "")
        record = make_record(
            "sec_form_d", cik, filing["name"],
            address={"street": filing["street"], "city": filing["city"], "region": filing["region"],
                     "postal_code": filing["postal_code"], "country": "US"},
            categories=[filing["industry_group"]],
            evidence=[{"kind": "funded", "date": date,
                       "detail": f"Form D filed {src.get('file_date', '')}: sold {_money(filing['amount_sold'])} "
                                 f"of a {_money(filing['total_offering'])} offering",
                       "url": f"{folder}/"}],
        )
        # One company can file several times in a window; keep the most recent sale.
        key = mf.normalize_name(filing["name"])
        if key not in records or date > records[key]["evidence"][0]["date"]:
            records[key] = record
    return list(records.values())
