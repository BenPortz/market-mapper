"""Businesses from OpenStreetMap via the Overpass API.

Free, no API key, worldwide, and natively filtered by geography. Coverage is
strong for storefront and facility businesses (clinics, dentists, veterinarians,
hospitals, workshops, warehouses) and thin for office-park manufacturers, so it
pairs well with web search for industrial markets.

OSM often carries a `website` tag, which saves the enricher a lookup.

Spec:
    type: osm
    tags:                       # any one tag set matching is enough
      - {amenity: dentist}
      - {healthcare: dentist}
    max_results: 500

Region keys used:
    osm_areas: ["US-OR"]        # ISO 3166-2 codes, as tagged on OSM boundary relations

Data (c) OpenStreetMap contributors, ODbL. Keep attribution if you publish results.
"""

from __future__ import annotations

import re
from typing import Any

API = "https://overpass-api.de/api/interpreter"
_SAFE = re.compile(r"^[A-Za-z0-9:_\- .]+$")


def build_query(tags: list[dict[str, str]], areas: list[str], max_results: int) -> str:
    """Overpass QL for every tag set inside every area.

    Values are interpolated into a query language, so anything outside a plain
    character set is refused rather than escaped.
    """
    for value in [*areas, *(x for t in tags for kv in t.items() for x in kv)]:
        if not _SAFE.match(str(value)):
            raise ValueError(f"unsafe characters in OSM query value: {value!r}")
    area_sets = "".join(f'area["ISO3166-2"="{a}"]->.a{i};' for i, a in enumerate(areas))
    selectors = "".join(
        "nwr" + "".join(f'["{k}"="{v}"]' for k, v in t.items()) + f"(area.a{i});"
        for i in range(len(areas)) for t in tags
    )
    return f"[out:json][timeout:120];{area_sets}({selectors});out center tags {int(max_results)};"


def to_record(element: dict[str, Any]) -> dict[str, Any] | None:
    from marketmapper.sources import make_record

    tags = element.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None
    osm_id = f"{element.get('type', 'node')}/{element.get('id')}"
    categories = [f"{k}={tags[k]}" for k in ("amenity", "healthcare", "shop", "craft",
                                             "industrial", "office", "man_made") if k in tags]
    return make_record(
        "osm", osm_id, name,
        website=tags.get("website") or tags.get("contact:website"),
        address={"street": " ".join(filter(None, [tags.get("addr:housenumber"),
                                                  tags.get("addr:street")])),
                 "city": tags.get("addr:city"), "region": tags.get("addr:state"),
                 "postal_code": tags.get("addr:postcode"),
                 "country": tags.get("addr:country")},
        phone=tags.get("phone") or tags.get("contact:phone"),
        categories=categories,
        evidence=[{"kind": "map_listing", "detail": f"OpenStreetMap {osm_id}",
                   "url": f"https://www.openstreetmap.org/{osm_id}"}],
    )


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError

    areas = region.get("osm_areas") or []
    tags = spec.get("tags") or []
    if not areas or not tags:
        raise SourceError("osm needs region.osm_areas and at least one tag set")
    query = build_query(tags, areas, int(spec.get("max_results", 500)))
    data = fetcher.post_json(API, form={"data": query})
    records = [r for el in data.get("elements", []) if (r := to_record(el))]
    # OSM does not always tag the state; the area it was found in is known.
    if len(areas) == 1 and (regions := region.get("regions")) and len(regions) == 1:
        for r in records:
            r["address"]["region"] = r["address"]["region"] or regions[0].upper()
    return records
