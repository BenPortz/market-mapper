"""US healthcare organizations from the CMS NPI Registry (NPPES).

Free, no API key, and filterable by provider type and state, which makes it the
best source for "who buys medical equipment in this region": imaging centers,
dental practices, veterinary groups, urgent care, orthopedics.

Only organization records (`NPI-2`) are requested. Individual providers are
people, and the registry's authorized-official fields (a named person with a
phone number) are deliberately never copied into a record.

The registry's enumeration date becomes `registered` evidence, which the FILTER
stage can turn into a "recently opened" signal. A practice that registered last
year is still buying equipment.

Spec:
    type: npi
    taxonomy_descriptions: ["Dentist", "Radiology"]   # NPPES taxonomy text
    cities: ["Portland"]                               # optional, narrows each query
    max_results: 600                                   # per taxonomy per state; API caps at 1200
"""

from __future__ import annotations

from typing import Any

API = "https://npiregistry.cms.hhs.gov/api/"
PAGE = 200
API_SKIP_CAP = 1000  # the registry refuses skip values above this


def _location(entry: dict[str, Any]) -> dict[str, Any]:
    addrs = entry.get("addresses") or []
    return next((a for a in addrs if a.get("address_purpose") == "LOCATION"),
                addrs[0] if addrs else {})


def to_record(entry: dict[str, Any]) -> dict[str, Any] | None:
    from marketmapper.sources import make_record

    basic = entry.get("basic") or {}
    legal = basic.get("organization_name")
    if not legal or entry.get("enumeration_type") != "NPI-2":
        return None  # never turn an individual into a company record
    dba = next((o.get("organization_name") for o in entry.get("other_names") or []
                if o.get("type") == "Doing Business As" and o.get("organization_name")), None)
    loc = _location(entry)
    number = str(entry.get("number", ""))
    url = f"https://npiregistry.cms.hhs.gov/provider-view/{number}"
    evidence = [{"kind": "registry", "detail": f"NPI {number}", "url": url}]
    if basic.get("enumeration_date"):
        evidence.append({"kind": "registered", "date": basic["enumeration_date"],
                         "detail": f"NPI enumerated {basic['enumeration_date']}", "url": url})
    return make_record(
        "npi", number, dba or legal, legal_name=legal if dba else None,
        address={"street": loc.get("address_1"), "city": loc.get("city"),
                 "region": loc.get("state"), "postal_code": loc.get("postal_code"),
                 "country": loc.get("country_code")},
        phone=loc.get("telephone_number"),
        categories=[t.get("desc") for t in entry.get("taxonomies") or []],
        evidence=evidence,
    )


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError

    states = region.get("regions") or []
    if not states:
        raise SourceError("npi needs region.regions (US state codes)")
    max_results = int(spec.get("max_results", 600))
    out: dict[str, dict[str, Any]] = {}

    for taxonomy in spec.get("taxonomy_descriptions") or []:
        for state in states:
            for city in spec.get("cities") or [None]:
                fetched, skip = 0, 0
                while fetched < max_results and skip <= API_SKIP_CAP:
                    params = {"version": "2.1", "enumeration_type": "NPI-2",
                              "taxonomy_description": taxonomy, "state": state,
                              "limit": PAGE, "skip": skip}
                    if city:
                        params["city"] = city
                    data = fetcher.get_json(API, params=params)
                    if data.get("Errors"):
                        raise SourceError(f"npi: {data['Errors']}")
                    results = data.get("results") or []
                    for entry in results:
                        if (rec := to_record(entry)) is not None:
                            out.setdefault(rec["record_id"], rec)
                    fetched += len(results)
                    if len(results) < PAGE:
                        break
                    skip += PAGE
    return list(out.values())
