"""Tests for the DISCOVER sources, against a fake fetcher.

Payloads are trimmed copies of real API response shapes with invented values.
Nothing here touches the network.
"""

from __future__ import annotations

import json

import pytest

from marketmapper import discover
from marketmapper.config import Profile
from marketmapper.net import NetError
from marketmapper.sources import SourceError, csv_import, npi, osm, postings, web_search


class FakeFetcher:
    def __init__(self, responses=None, error=None):
        self.responses = responses or []
        self.error = error
        self.calls = []

    def _next(self, call):
        self.calls.append(call)
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else {}

    def get_json(self, url, params=None, headers=None):
        return self._next(("GET", url, params, headers))

    def post_json(self, url, form=None, json_body=None, headers=None):
        return self._next(("POST", url, form or json_body, headers))


OREGON = {"regions": ["OR"], "countries": ["US"], "osm_areas": ["US-OR"],
          "search_places": ["Oregon", "Bend Oregon"]}


# --- npi ------------------------------------------------------------------

def npi_entry(number="1234567890", kind="NPI-2", dba=None):
    return {
        "number": number, "enumeration_type": kind,
        "basic": {"organization_name": "EXAMPLE DENTAL LLC", "enumeration_date": "2025-11-02",
                  "authorized_official_first_name": "MARGUERITE", "authorized_official_last_name": "QUINNELL",
                  "authorized_official_telephone_number": "5415550199"},
        "other_names": [{"type": "Doing Business As", "organization_name": dba}] if dba else [],
        "addresses": [
            {"address_purpose": "MAILING", "city": "PO BOX TOWN", "state": "OR"},
            {"address_purpose": "LOCATION", "address_1": "1 EXAMPLE ST", "city": "BEND",
             "state": "OR", "postal_code": "977010000", "country_code": "US",
             "telephone_number": "541-555-0100"},
        ],
        "taxonomies": [{"desc": "Dentist", "primary": True}],
    }


def test_npi_record_uses_the_location_address():
    rec = npi.to_record(npi_entry())
    assert rec["address"] == {"street": "1 EXAMPLE ST", "city": "Bend", "region": "OR",
                              "postal_code": "97701", "country": "US"}
    assert rec["phone"] == "541-555-0100"


def test_npi_prefers_the_trade_name_and_keeps_the_legal_one():
    rec = npi.to_record(npi_entry(dba="LARKSPUR SMILES"))
    assert rec["name"] == "Larkspur Smiles"
    assert rec["legal_name"] == "Example Dental LLC"


def test_npi_never_copies_the_authorized_official():
    blob = json.dumps(npi.to_record(npi_entry())).upper()
    assert "MARGUERITE" not in blob and "QUINNELL" not in blob
    assert "5415550199" not in blob


def test_npi_skips_individual_providers():
    assert npi.to_record(npi_entry(kind="NPI-1")) is None


def test_npi_registration_becomes_evidence():
    rec = npi.to_record(npi_entry())
    assert {"kind": "registered", "date": "2025-11-02"}.items() <= rec["evidence"][1].items()


def test_npi_pages_until_a_short_page():
    full = {"results": [npi_entry(str(i)) for i in range(npi.PAGE)]}
    short = {"results": [npi_entry("9999999999")]}
    fetcher = FakeFetcher([full, short])
    recs = npi.discover({"taxonomy_descriptions": ["Dentist"], "max_results": 1000}, OREGON, fetcher, {})
    assert len(recs) == npi.PAGE + 1
    assert [c[2]["skip"] for c in fetcher.calls] == [0, npi.PAGE]
    assert fetcher.calls[0][2]["enumeration_type"] == "NPI-2"


def test_npi_requires_states():
    with pytest.raises(SourceError, match="regions"):
        npi.discover({"taxonomy_descriptions": ["Dentist"]}, {}, FakeFetcher(), {})


def test_npi_api_errors_surface():
    fetcher = FakeFetcher([{"Errors": [{"description": "bad taxonomy"}]}])
    with pytest.raises(SourceError, match="bad taxonomy"):
        npi.discover({"taxonomy_descriptions": ["Nope"]}, OREGON, fetcher, {})


# --- osm ------------------------------------------------------------------

def test_osm_query_covers_every_tag_set_and_area():
    q = osm.build_query([{"amenity": "dentist"}, {"healthcare": "dentist"}], ["US-OR", "US-WA"], 50)
    assert q.count('["amenity"="dentist"]') == 2 and q.count('["healthcare"="dentist"]') == 2
    assert 'area["ISO3166-2"="US-WA"]->.a1;' in q
    assert q.endswith("out center tags 50;")


@pytest.mark.parametrize("bad", ['dentist"];out;', "x\ny", "a(b)"])
def test_osm_query_refuses_injection(bad):
    with pytest.raises(ValueError):
        osm.build_query([{"amenity": bad}], ["US-OR"], 10)


def test_osm_records_fill_region_from_the_single_area():
    data = {"elements": [
        {"type": "node", "id": 1, "tags": {"name": "Fernleaf Dentistry", "amenity": "dentist",
                                           "addr:city": "West Linn", "website": "https://fernleaf.example/"}},
        {"type": "node", "id": 2, "tags": {"amenity": "dentist"}},   # unnamed: skipped
    ]}
    recs = osm.discover({"tags": [{"amenity": "dentist"}]}, OREGON, FakeFetcher([data]), {})
    assert len(recs) == 1
    assert recs[0]["address"]["region"] == "OR"
    assert recs[0]["website"] == "https://fernleaf.example/"
    assert recs[0]["categories"] == ["amenity=dentist"]


# --- web search -----------------------------------------------------------

def brave(*results):
    return {"web": {"results": [{"title": t, "url": u, "description": d} for t, u, d in results]}}


def test_web_search_expands_places_and_dedups_by_domain(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    fetcher = FakeFetcher([
        brave(("Belt Conveyors | Acme Conveyor Co", "https://www.acmeconveyor.example/belts", "Acme builds belts"),
              ("Acme on LinkedIn", "https://www.linkedin.com/company/acme", "")),
        brave(("Acme Conveyor - Home", "https://acmeconveyor.example/", "")),
    ])
    recs = web_search.discover({"queries": ["conveyor manufacturer {place}"]}, OREGON, fetcher, {})
    assert [c[2]["q"] for c in fetcher.calls] == ["conveyor manufacturer Oregon",
                                                  "conveyor manufacturer Bend Oregon"]
    assert len(recs) == 1                                   # one domain, LinkedIn dropped
    assert recs[0]["name"] == "Acme Conveyor Co"
    assert len(recs[0]["evidence"]) == 2                    # found by both queries
    assert fetcher.calls[0][3]["X-Subscription-Token"] == "test-key"


def test_web_search_without_a_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with pytest.raises(SourceError, match="BRAVE_API_KEY"):
        web_search.discover({"queries": ["x"]}, OREGON, FakeFetcher(), {})


@pytest.mark.parametrize("domain,skipped", [
    ("linkedin.com", True), ("de.linkedin.com", True), ("thomasnet.com", True),
    ("notlinkedin.com", False), ("acme.example", False),
])
def test_skip_domains_match_subdomains_not_lookalikes(domain, skipped):
    assert web_search.is_skipped(domain, set()) is skipped


@pytest.mark.parametrize("title,domain,expected", [
    ("Custom Conveyors | Lakeshore Conveyor", "lakeshoreconveyor.example", "Lakeshore Conveyor"),
    ("Packline Automation - Packaging Lines", "packline.example", "Packline Automation"),
    ("", "rollerpro.example", "Rollerpro"),
])
def test_name_from_title(title, domain, expected):
    assert web_search.name_from_title(title, domain) == expected


def test_tavily_provider(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    fetcher = FakeFetcher([{"results": [{"title": "Acme", "url": "https://acme.example/", "content": "x"}]}])
    recs = web_search.discover({"provider": "tavily", "queries": ["acme"], "pages": 3}, OREGON, fetcher, {})
    assert len(recs) == 1 and len(fetcher.calls) == 1        # no paging on tavily
    assert fetcher.calls[0][3]["Authorization"] == "Bearer t"


# --- csv ------------------------------------------------------------------

def test_csv_import(tmp_path):
    path = tmp_path / "members.csv"
    path.write_text("﻿Name,Website,City,Region,Categories,Note\n"
                    "Lakeshore Conveyor,https://lakeshore.example,Grand Rapids,Michigan,Food; Packaging,Booth 12\n"
                    ",,,,,\n", encoding="utf-8")
    recs = csv_import.discover({"path": str(path), "label": "Expo exhibitors"}, {}, None, {})
    assert len(recs) == 1
    r = recs[0]
    assert r["address"]["region"] == "MI"
    assert r["categories"] == ["Food", "Packaging"]
    assert r["evidence"][0] == {"kind": "list_member", "detail": "Expo exhibitors", "snippet": "Booth 12"}


def test_csv_missing_file(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        csv_import.discover({"path": str(tmp_path / "nope.csv")}, {}, None, {})


# --- postings -------------------------------------------------------------

def test_postings_group_by_company_and_become_hiring_evidence(tmp_path):
    path = tmp_path / "postings.json"
    path.write_text(json.dumps({"postings": [
        {"posting_id": "b_1", "url": "https://jobs.example/1", "title": "Maintenance Lead",
         "company": "Acme Foods", "location": "Columbus, OH"},
        {"posting_id": "b_2", "url": "https://jobs.example/2", "title": "Line Supervisor",
         "company": "ACME FOODS", "posting_text": "Line Supervisor\nColumbus, OH"},
    ]}), encoding="utf-8")
    recs = postings.discover({"path": str(path)}, {}, None, {})
    assert len(recs) == 1
    assert recs[0]["address"]["region"] == "OH"
    assert [e["detail"] for e in recs[0]["evidence"]] == ["Hiring: Maintenance Lead", "Hiring: Line Supervisor"]


# --- discover orchestration -----------------------------------------------

def test_one_failed_source_makes_the_search_partial_not_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    csv_path = tmp_path / "list.csv"
    csv_path.write_text("name,region\nAcme,OR\n", encoding="utf-8")
    profile = Profile(context=[], filters={}, http={}, dedup={}, searches={"s": {
        "region": OREGON,
        "sources": [{"type": "csv", "path": str(csv_path)}, {"type": "web_search", "queries": ["x"]}],
    }})
    block = discover.run_search("s", profile, FakeFetcher(), {"date": "2026-03-14"})
    assert block["status"] == "partial"
    assert len(block["records"]) == 1
    assert block["sources"][1] == {"type": "web_search", "status": "failed", "count": 0,
                                   "error": "BRAVE_API_KEY is not set"}


def test_network_errors_are_recorded_per_source():
    profile = Profile(context=[], filters={}, http={}, dedup={}, searches={"s": {
        "region": OREGON, "sources": [{"type": "osm", "tags": [{"amenity": "dentist"}]}]}})
    block = discover.run_search("s", profile, FakeFetcher(error=NetError("timed out")), {})
    assert block["status"] == "failed"
    assert block["sources"][0]["error"] == "timed out"


def test_osm_bounding_box_query():
    q = osm.build_query([{"landuse": "quarry"}], [], 100, [38.2, -91.0, 39.0, -89.7])
    assert '["landuse"="quarry"](38.2,-91.0,39.0,-89.7);' in q
    assert "area[" not in q


@pytest.mark.parametrize("bad", [[1, 2, 3], ["38.2", -91, 39, -89.7], [38.2, -91, 39, "0);out;"]])
def test_osm_bounding_box_must_be_four_numbers(bad):
    with pytest.raises(ValueError):
        osm.build_query([{"landuse": "quarry"}], [], 10, bad)


def test_osm_records_keep_coordinates_from_nodes_and_way_centers():
    node = osm.to_record({"type": "node", "id": 1, "lat": 38.5, "lon": -90.3, "tags": {"name": "A Quarry"}})
    way = osm.to_record({"type": "way", "id": 2, "center": {"lat": 38.6, "lon": -90.2}, "tags": {"name": "B Quarry"}})
    assert node["coords"] == {"lat": 38.5, "lon": -90.3}
    assert way["coords"] == {"lat": 38.6, "lon": -90.2}
