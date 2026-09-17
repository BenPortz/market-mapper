"""Pluggable company sources for the DISCOVER stage.

Every source is a function `discover(spec, region, fetcher, ctx) -> list[record]`
where `spec` is the source block from the profile, `region` is the search's
region block, and `ctx` carries run-level values (date, data dir). Each returns
records in one shared shape, built with `make_record`, so the FILTER stage never
needs to know where a company came from.

Sources describe organizations only. None of them may return a person's name,
personal email, or personal phone number as a company record.

To add a source: write a module with a `discover` function, register it in
`SOURCES` below, and document its spec keys in the module docstring.
"""

from __future__ import annotations

from typing import Any, Callable

from marketmapper import filters as mf


class SourceError(RuntimeError):
    """A source could not run (missing key, API error). Recorded, never swallowed."""


def make_record(source: str, source_id: str, name: str, *, website: str | None = None,
                address: dict[str, Any] | None = None, phone: str | None = None,
                categories: list[str] | None = None,
                evidence: list[dict[str, Any]] | None = None,
                legal_name: str | None = None,
                coords: tuple[float, float] | None = None,
                employees: int | None = None) -> dict[str, Any]:
    address = address or {}
    return {
        "record_id": f"{source}:{source_id}",
        "source": source,
        "name": mf.display_name(name),
        "legal_name": mf.display_name(legal_name) if legal_name else None,
        "website": website or None,
        "address": {
            "street": address.get("street") or None,
            "city": mf.display_name(address["city"]) if address.get("city") else None,
            "region": mf.region_code(address.get("region")),
            "postal_code": (address.get("postal_code") or "")[:5] or None,
            "country": (address.get("country") or "").upper() or None,
        },
        "phone": phone or None,
        "categories": [c for c in (categories or []) if c],
        "evidence": evidence or [],
        "coords": {"lat": coords[0], "lon": coords[1]} if coords else None,
        "employees": employees,
    }


from marketmapper.sources import csv_import, npi, nsf, osha_ita, osm, postings, web_search  # noqa: E402

SOURCES: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "npi": npi.discover,
    "nsf": nsf.discover,
    "osha_ita": osha_ita.discover,
    "osm": osm.discover,
    "web_search": web_search.discover,
    "csv": csv_import.discover,
    "postings": postings.discover,
}
