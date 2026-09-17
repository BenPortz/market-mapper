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
    osm_bbox: [38.2, -91.0, 39.0, -89.7]   # or a metro box: south, west, north, east

Data (c) OpenStreetMap contributors, ODbL. Keep attribution if you publish results.
"""

from __future__ import annotations

import re
from typing import Any

API = "https://overpass-api.de/api/interpreter"
_SAFE = re.compile(r"^[A-Za-z0-9:_\- .]+$")


def build_query(tags: list[dict[str, str]], areas: list[str], max_results: int,
                bbox: list[float] | None = None) -> str:
    """Overpass QL for every tag set inside every area, or inside one bounding box.

    Values are interpolated into a query language, so anything outside a plain
    character set is refused rather than escaped. A bounding box is
    [south, west, north, east] and must be four numbers.
    """
    for value in [*areas, *(x for t in tags for kv in t.items() for x in kv)]:
        if not _SAFE.match(str(value)):
            raise ValueError(f"unsafe characters in OSM query value: {value!r}")
    if bbox is not None:
        if len(bbox) != 4 or not all(isinstance(v, (int, float)) for v in bbox):
            raise ValueError(f"osm_bbox must be four numbers [south, west, north, east]: {bbox!r}")
        box = ",".join(str(float(v)) for v in bbox)
        selectors = "".join("nwr" + "".join(f'["{k}"="{v}"]' for k, v in t.items()) + f"({box});"
                            for t in tags)
        return f"[out:json][timeout:120];({selectors});out center tags {int(max_results)};"
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
    point = element.get("center") or element
    coords = (point["lat"], point["lon"]) if "lat" in point and "lon" in point else None
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
        coords=coords,
    )


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError

    areas = region.get("osm_areas") or []
    bbox = region.get("osm_bbox")
    tags = spec.get("tags") or []
    if not (areas or bbox) or not tags:
        raise SourceError("osm needs region.osm_areas or region.osm_bbox, and at least one tag set")
    query = build_query(tags, [] if bbox else areas, int(spec.get("max_results", 500)), bbox)
    data = fetcher.post_json(API, form={"data": query})
    records = [r for el in data.get("elements", []) if (r := to_record(el))]
    # OSM does not always tag the state; the area it was found in is known.
    if len(areas) == 1 and (regions := region.get("regions")) and len(regions) == 1:
        for r in records:
            r["address"]["region"] = r["address"]["region"] or regions[0].upper()
    return records
