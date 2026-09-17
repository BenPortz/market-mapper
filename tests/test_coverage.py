"""Tests for the coverage sources: NSF listings, OSHA filings, the Census benchmark,
and the size-aware ranking they feed. No network access; invented data only."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from marketmapper import benchmark as b
from marketmapper import filters as f
from marketmapper import pipeline as p
from marketmapper import report as r
from marketmapper.sources import SourceError, nsf, osha_ita

FIXTURES = Path(__file__).parent / "fixtures"
CHICAGO = {"regions": ["IL"], "postal_prefixes": ["600", "606"]}


# --- NSF listings ---------------------------------------------------------

@pytest.fixture
def listing() -> str:
    return (FIXTURES / "nsf_listing_sample.html").read_text(encoding="utf-8")


def test_nsf_parses_company_address_and_products(listing):
    recs = nsf.parse_listing(listing, "PwsComponents", "https://info.nsf.org/x")
    valve = next(x for x in recs if x["name"].startswith("Prairie"))
    assert valve["address"] == {"street": "100 Example Road", "city": "Elk Grove Village", "region": "IL",
                                "postal_code": "60007", "country": "US"}
    assert valve["phone"] == "847-555-0100"
    assert valve["website"] == "http://www.prairiebrass.example"
    assert "Valves" in valve["categories"]           # footnote mark [G] stripped
    assert valve["evidence"][0]["kind"] == "certification"
    assert "plant in Elk Grove Village, IL" in valve["evidence"][0]["detail"]


def test_nsf_uses_the_plant_not_the_foreign_head_office(listing):
    recs = nsf.parse_listing(listing, "PwsComponents", "u")
    foreign = next(x for x in recs if x["name"].startswith("Globex"))
    assert foreign["address"]["city"] == "Joliet" and foreign["address"]["region"] == "IL"
    assert foreign["address"]["street"] is None       # the street belongs to the Zurich office
    assert "Company office" not in foreign["evidence"][0]["snippet"]  # no US-style office line parsed


def test_nsf_product_filter_and_state_queries(listing):
    class Fake:
        def __init__(self):
            self.urls = []

        def get_page(self, url):
            self.urls.append(url)
            return {"html": listing}

    fake = Fake()
    recs = nsf.discover({"programs": ["PwsComponents", "Plumbing"], "product_match": ["valve"]},
                        {"regions": ["IL", "IN"]}, fake, {})
    assert [x["name"] for x in recs] == ["Prairie Brass Valve Works, Inc."]
    assert len(fake.urls) == 4                          # 2 programs x 2 states
    assert all("PlantState=" in u for u in fake.urls)
    assert len(recs[0]["evidence"]) == 4                # the same plant seen in every listing


def test_nsf_rejects_unknown_programs():
    with pytest.raises(SourceError, match="unknown program"):
        nsf.discover({"programs": ["Pools"]}, {"regions": ["IL"]}, None, {})


# --- OSHA filings ---------------------------------------------------------

OSHA_ROWS = [
    {"establishment_name": "PRAIRIE BRASS VALVE WORKS", "company_name": "Prairie Holdings LLC",
     "street_address": "100 Example Road", "city": "Elk Grove Village", "state": "IL", "zip_code": "60007-1234",
     "naics_code": "332911", "industry_description": "Industrial Valve Manufacturing", "size": "21",
     "establishment_type": "1", "annual_average_employees": "42", "year_filing_for": "2024"},
    {"establishment_name": "Big Pump Plant", "company_name": "", "street_address": "1 Main", "city": "Aurora",
     "state": "IL", "zip_code": "60506", "naics_code": "333914", "industry_description": "Pumps",
     "size": "3", "establishment_type": "1", "annual_average_employees": "900", "year_filing_for": "2024"},
    {"establishment_name": "Downstate Valve", "company_name": "", "street_address": "9 Elm", "city": "Decatur",
     "state": "IL", "zip_code": "62522", "naics_code": "332919", "industry_description": "Other Valves",
     "size": "21", "establishment_type": "1", "annual_average_employees": "60", "year_filing_for": "2024"},
    {"establishment_name": "City Water Dept", "company_name": "", "street_address": "2 Hall", "city": "Chicago",
     "state": "IL", "zip_code": "60601", "naics_code": "332911", "industry_description": "Valves",
     "size": "21", "establishment_type": "3", "annual_average_employees": "30", "year_filing_for": "2024"},
]


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_osha_filters_by_industry_region_and_entity_type(tmp_path):
    path = _write_csv(tmp_path / "ita.csv", OSHA_ROWS)
    recs = osha_ita.discover({"path": str(path), "naics": ["3329"]}, CHICAGO, None, {})
    assert [x["name"] for x in recs] == ["Prairie Brass Valve Works"]   # pumps, downstate, and government dropped
    rec = recs[0]
    assert rec["employees"] == 42
    assert rec["legal_name"] == "Prairie Holdings LLC"
    assert rec["address"]["postal_code"] == "60007"
    assert "42 average employees (size 20-99)" in rec["evidence"][0]["detail"]


def test_osha_reads_a_zip_download(tmp_path):
    csv_path = _write_csv(tmp_path / "ita.csv", OSHA_ROWS)
    zip_path = tmp_path / "ita.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(csv_path, "ITA_300A_Summary_Data_2024.csv")
    recs = osha_ita.discover({"path": str(zip_path), "naics": ["332911"]}, CHICAGO, None, {})
    assert len(recs) == 1


def test_osha_needs_a_file_and_codes(tmp_path):
    with pytest.raises(SourceError, match="not found"):
        osha_ita.discover({"path": str(tmp_path / "nope.csv"), "naics": ["3329"]}, CHICAGO, None, {})
    path = _write_csv(tmp_path / "ita.csv", OSHA_ROWS)
    with pytest.raises(SourceError, match="NAICS"):
        osha_ita.discover({"path": str(path)}, CHICAGO, None, {})


# --- Census benchmark -----------------------------------------------------

@pytest.mark.parametrize("label,bound", [
    ("All establishments", None),
    ("Establishments with less than 5 employees", 4),
    ("Establishments with 5 to 9 employees", 9),
    ("Establishments with 10 to 19 employees", 19),
    ("Establishments with 1,000 employees or more", 10**9),
])
def test_size_class_labels(label, bound):
    assert b.size_upper_bound(label) == bound


CBP_RESPONSE = [
    ["ESTAB", "EMPSZES_LABEL", "NAICS2017_LABEL", "NAICS2017", "state", "county"],
    ["12", "All establishments", "Industrial valve manufacturing", "332911", "17", "031"],
    ["5", "Establishments with less than 5 employees", "Industrial valve manufacturing", "332911", "17", "031"],
    ["3", "Establishments with 10 to 19 employees", "Industrial valve manufacturing", "332911", "17", "031"],
    ["4", "Establishments with 20 to 49 employees", "Industrial valve manufacturing", "332911", "17", "031"],
    ["6", "All establishments", "Industrial valve manufacturing", "332911", "17", "043"],
    ["2", "Establishments with 5 to 9 employees", "Industrial valve manufacturing", "332911", "17", "043"],
]


def test_benchmark_sums_counties_and_small_establishments():
    assert b.summarize(CBP_RESPONSE)["total"] == 18
    assert b.summarize(CBP_RESPONSE)["under_20"] == 10


def test_benchmark_fetch_builds_one_call_per_code_and_state():
    class Fake:
        def __init__(self):
            self.params = []

        def get_json(self, url, params=None, headers=None):
            self.params.append(params)
            return CBP_RESPONSE if params["NAICS2017"] == "332911" else [CBP_RESPONSE[0]]

    fake = Fake()
    out = b.fetch_counts({"naics": ["332911", "332913"], "counties": {"17": ["031", "043"]}, "year": 2022},
                         fake, "k")
    assert out["total"] == 18 and out["under_20"] == 10 and out["county_count"] == 2
    assert out["naics"]["332913"]["total"] == 0          # header-only response means none
    assert fake.params[0]["for"] == "county:031,043" and fake.params[0]["key"] == "k"


def test_benchmark_needs_codes_and_counties():
    with pytest.raises(b.BenchmarkError):
        b.fetch_counts({"naics": ["332911"]}, None, "k")


def test_benchmark_without_a_key_is_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    profile = tmp_path / "p.yaml"
    profile.write_text(
        "searches:\n  s:\n    goal: market_map\n    region: {regions: [IL]}\n"
        "    sources: [{type: csv, path: x.csv}]\n"
        "    coverage: {naics: ['332911'], counties: {'17': ['031']}}\n", encoding="utf-8")
    assert b.main(["--profile", str(profile), "--data", str(tmp_path), "--date", "2026-09-17"]) == 0
    doc = json.loads((tmp_path / "benchmark" / "2026-09-17.json").read_text(encoding="utf-8"))
    assert doc["searches"]["s"] == {"status": "failed", "error": "CENSUS_API_KEY is not set"}


# --- size-aware ranking and report ----------------------------------------

@pytest.mark.parametrize("employees,site,band", [
    (12, "", "under 20"), (42, "", "20-99"), (150, "", "100-249"), (900, "", "250+"),
    (None, "A family-owned shop since 1961", "small (site)"), (None, "Global leader", "unknown"),
])
def test_size_band(employees, site, band):
    assert f.size_band({"employees": employees, "site": {"text": site}}) == band


def test_prefer_small_lifts_small_companies_without_dropping_big_ones():
    base = {"name": "X", "site": {"text": "valve"}, "signals": [], "sources": ["osha_ita"],
            "region_status": "in", "evidence": [], "categories": []}
    small = {**base, "employees": 30, "size_band": "20-99"}
    big = {**base, "employees": 900, "size_band": "250+"}
    search = {"include_any": ["valve"]}
    assert f.score(small, search) == f.score(big, search)            # default mode: size is neutral
    assert f.score(small, {**search, "prefer": "small"}) > f.score(big, {**search, "prefer": "small"})
    assert f.passed(f.evaluate(big, {**search, "prefer": "small"}, {}),
                    ["in_region", "relevant"])                       # big still passes


def test_merged_account_keeps_the_largest_filing():
    recs = [
        {"record_id": "osha_ita:a", "source": "osha_ita", "name": "Prairie Brass", "website": None,
         "address": {"city": "Elk Grove Village"}, "categories": [], "evidence": [], "employees": 42},
        {"record_id": "nsf:a", "source": "nsf", "name": "Prairie Brass", "website": None,
         "address": {"city": "Elk Grove Village"}, "categories": [], "evidence": [], "employees": None},
    ]
    (acct,) = p.merge_records(recs)
    assert acct["employees"] == 42 and sorted(acct["sources"]) == ["nsf", "osha_ita"]


def test_coverage_line_states_the_gap_and_its_caveats():
    line = r.coverage_line({"status": "ok", "year": 2022, "naics": {"332911": {}, "332913": {}},
                            "total": 38, "under_20": 21, "county_count": 7}, 9)
    assert "**38** establishments in NAICS 332911, 332913 across 7 counties" in line
    assert "21 of them with fewer than 20 employees. This list has **9**." in line
    assert "locations rather than companies" in line


def test_coverage_line_reports_a_failed_check():
    assert r.coverage_line({"status": "failed", "error": "CENSUS_API_KEY is not set"}, 3) == \
        "- **Coverage check did not run:** CENSUS_API_KEY is not set."
    assert r.coverage_line(None, 3) is None


@pytest.mark.parametrize("name,city,expected", [
    ("Sloan Valve-Franklin Park", "Franklin Park", "Sloan Valve"),
    ("Chicago Faucets Company - Michigan City Plant", "Michigan City", "Chicago Faucets Company"),
    ("Motion Systems Lincolnshire", "Lincolnshire", "Motion Systems"),
    ("Chicago Valve Works", "Chicago", "Chicago Valve Works"),   # the city starts the name; kept
    ("Elgin", "Elgin", "Elgin"),                                   # never strip a name to nothing
])
def test_osha_plant_names_drop_the_location(name, city, expected):
    assert osha_ita.plant_name(name, city) == expected
