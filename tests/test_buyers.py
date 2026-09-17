"""Tests for the BUYERS stage: vendor matching, public record sources, and the report
section. Fake fetchers only; no network."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path

import pytest

from marketmapper import buyers as b
from marketmapper import pipeline as p
from marketmapper import report as r
from marketmapper.config import load_profile

TODAY = dt.date(2026, 9, 17)
COMPANIES = {
    "cv": "Chicago Valves & Controls",
    "sl": "Sloan Valve",
    "vm": "Val-Matic Valve & Mfg. Corp.",
    "gv": "Valve Company",
}


# --- matching -------------------------------------------------------------

@pytest.mark.parametrize("vendor,expected", [
    ("VAL-MATIC VALVE & MFG CORP", "vm"),
    ("VAL MATIC VALVE AND MANUFACTURING CORPORATION", "vm"),   # abbreviation expanded
    ("SLOAN VALVE CO", "sl"),
    ("CHICAGO VALVES AND CONTROLS LLC", "cv"),
    ("VALVE CO", "gv"),                                         # generic name, exact match only
    # Real false positives from a live run: a shared city or surname is not a match.
    ("DDB CHICAGO INC.", None),
    ("THE UNIVERSITY OF CHICAGO", None),
    ("MEMORIAL SLOAN-KETTERING CANCER CENTER", None),
    ("SLOAN CONSULTING SERVICES, LLC", None),
    ("ACME VALVE COMPANY", None),                               # does not borrow the generic name
])
def test_match_vendor(vendor, expected):
    assert b.match_vendor(vendor, COMPANIES) == expected


def test_longest_matching_name_wins():
    companies = {"short": "Sloan Valve", "long": "Sloan Valve Water Technologies"}
    assert b.match_vendor("SLOAN VALVE WATER TECHNOLOGIES INC", companies) == "long"


def test_name_tokens_fold_abbreviations_and_plurals():
    assert b.name_tokens("Chicago Valves & Controls, Inc.") == b.name_tokens("CHICAGO VALVE AND CONTROL")
    assert "manufacturing" in b.name_tokens("Acme Mfg")


def test_vendor_pattern_is_broad_retrieval():
    assert b.vendor_pattern("Val-Matic Valve & Mfg. Corp.") == "%VAL%MATIC%"
    assert b.vendor_pattern("Valve Company") == ""


# --- socrata --------------------------------------------------------------

SOCRATA = {"type": "socrata", "label": "City contracts", "buyer": "City of Example",
           "domain": "data.example.gov", "dataset": "abcd-1234",
           "fields": {"vendor": "vendor_name", "unit": "department", "description": "description",
                      "amount": "award_amount", "date": "start_date", "id": "contract_number"}}


def test_soql_literal_cannot_break_out_of_the_string():
    assert b.soql_literal("valve') OR 1=1 --") == "VALVE OR 11 --"
    assert "'" not in b.soql_literal("O'Brien")


def test_socrata_query_shape():
    url, params = b.socrata_query(SOCRATA, ["valve"], {"sl": "Sloan Valve"}, dt.date(2021, 9, 18))
    assert url == "https://data.example.gov/resource/abcd-1234.json"
    assert "upper(description) like '%VALVE%'" in params["$where"]
    assert "upper(vendor_name) like '%SLOAN%'" in params["$where"]
    assert "start_date >= '2021-09-18T00:00:00'" in params["$where"]
    assert params["$order"] == "start_date DESC"


@pytest.mark.parametrize("bad", [
    {"domain": "data.example.gov/../x"},
    {"dataset": "abcd-1234.json?x"},
    {"fields": {**SOCRATA["fields"], "vendor": "vendor_name) OR (1"}},
])
def test_socrata_rejects_unsafe_config(bad):
    with pytest.raises(b.BuyerSourceError):
        b.socrata_query({**SOCRATA, **bad}, ["valve"], {}, TODAY)


class FakeFetcher:
    def __init__(self, json_rows=None, post=None):
        self.json_rows, self.post, self.bodies = json_rows, post, []

    def get_json(self, url, params=None, headers=None):
        return self.json_rows

    def post_json(self, url, form=None, json_body=None, headers=None):
        self.bodies.append(json_body)
        return self.post(json_body)


def test_socrata_rows_become_purchases():
    rows = [{"vendor_name": "SLOAN VALVE CO", "department": "DEPT OF WATER MANAGEMENT",
             "description": "FLUSH VALVES", "award_amount": "12500.50", "start_date": "2025-03-01T00:00:00.000",
             "contract_number": "PO-1"},
            {"vendor_name": "", "award_amount": "1"},
            {"vendor_name": "CORE AND MAIN LP", "department": "DEPT OF WATER MANAGEMENT",
             "description": "HYDRANT PARTS", "award_amount": "not a number"}]
    search = {"buyers": {"keywords": ["valve"], "years": 5}}
    out = b.socrata(SOCRATA, search, {"sl": "Sloan Valve"}, FakeFetcher(json_rows=rows), TODAY)
    assert [x["vendor"] for x in out] == ["SLOAN VALVE CO", "CORE AND MAIN LP"]
    assert out[0]["vendor_account_id"] == "sl" and out[0]["amount"] == 12500.5 and out[0]["date"] == "2025-03-01"
    assert out[1]["amount"] == 0.0 and out[1]["buyer"] == "City of Example"


# --- usaspending ----------------------------------------------------------

def award(recipient, amount, uid, agency="Department of Defense", sub="Defense Logistics Agency"):
    return {"Recipient Name": recipient, "Awarding Agency": agency, "Awarding Sub Agency": sub,
            "Award Amount": amount, "Description": "VALVE,GATE", "Start Date": "2025-01-01",
            "Award ID": uid, "generated_internal_id": f"CONT_AWD_{uid}"}


def test_usaspending_market_and_company_queries():
    def post(body):
        f = body["filters"]
        if "recipient_search_text" in f:
            # Name search is fuzzy: the real company plus a lookalike.
            return {"results": [award("SLOAN VALVE CO", 900, "S1", "Department of Veterans Affairs", ""),
                                award("SLOAN SECURITY GROUP, LLC", 5000, "X1")],
                    "page_metadata": {"hasNext": False}, "messages": ["informational"]}
        return {"results": [award("HOOSIER INDUSTRIAL SUPPLY, INC", 700, "H1"), award("SLOAN VALVE CO", 900, "S1")],
                "page_metadata": {"hasNext": False}, "messages": ["informational"]}

    fetcher = FakeFetcher(post=post)
    spec = {"naics": ["332911"], "states": ["IL"], "by_company": True, "max_awards": 50}
    search = {"buyers": {"years": 5, "keywords": ["valve"]}}
    out = b.usaspending(spec, search, {"sl": "Sloan Valve"}, fetcher, TODAY)

    vendors = sorted(x["vendor"] for x in out)
    assert vendors == ["HOOSIER INDUSTRIAL SUPPLY, INC", "SLOAN VALVE CO"]    # lookalike dropped, S1 deduped
    market = fetcher.bodies[0]["filters"]
    assert market["naics_codes"] == {"require": ["332911"]} and market["keywords"] == ["valve"]
    assert market["time_period"][0]["start_date"] == "2021-09-18"
    assert fetcher.bodies[1]["filters"]["recipient_search_text"] == ["SLOAN"]
    assert next(x for x in out if x["vendor"] == "SLOAN VALVE CO")["url"].endswith("CONT_AWD_S1")


def test_usaspending_pages_until_done_and_reports_errors():
    pages = iter([{"results": [award("A", 1, "1")], "page_metadata": {"hasNext": True}},
                  {"results": [award("B", 1, "2")], "page_metadata": {"hasNext": False}}])
    fetcher = FakeFetcher(post=lambda body: next(pages))
    assert len(b._usaspending_rows(fetcher, {}, 10)) == 2
    bad = FakeFetcher(post=lambda body: {"detail": "Invalid filter"})
    with pytest.raises(b.BuyerSourceError, match="Invalid filter"):
        b._usaspending_rows(bad, {}, 10)


# --- summary and stage ----------------------------------------------------

PURCHASES = [
    {"buyer": "Department of Defense", "buyer_unit": "Defense Logistics Agency", "vendor": "SLOAN VALVE CO",
     "vendor_account_id": "sl", "amount": 900},
    {"buyer": "Department of Defense", "buyer_unit": "Defense Logistics Agency", "vendor": "HOOSIER INDUSTRIAL SUPPLY",
     "vendor_account_id": None, "amount": 700},
    {"buyer": "City of Example", "buyer_unit": "", "vendor": "HOOSIER INDUSTRIAL SUPPLY",
     "vendor_account_id": None, "amount": 300},
]


def test_summarize():
    s = b.summarize(PURCHASES, {"sl": "Sloan Valve"})
    assert s["by_company"]["sl"]["buyers"] == [{"buyer": "Department of Defense / Defense Logistics Agency", "total": 900}]
    top = s["top_buyers"][0]
    assert top["name"] == "Department of Defense / Defense Logistics Agency" and top["total"] == 1600
    assert top["vendors"] == ["HOOSIER INDUSTRIAL SUPPLY", "Sloan Valve"]
    assert s["other_vendors"][0] == {"name": "HOOSIER INDUSTRIAL SUPPLY", "total": 1000, "count": 2,
                                     "buyers": ["City of Example", "Department of Defense / Defense Logistics Agency"]}


def test_failed_source_makes_the_stage_partial():
    search = {"buyers": {"sources": [{"type": "socrata", "label": "Broken", "domain": "bad domain"},
                                     {"type": "usaspending", "by_company": False, "naics": ["332911"]}]}}
    fetcher = FakeFetcher(post=lambda body: {"results": [], "page_metadata": {"hasNext": False}})
    block = b.run_search("s", search, {}, fetcher, TODAY)
    assert block["status"] == "partial"
    assert block["sources"][0]["status"] == "failed" and block["sources"][1]["status"] == "ok"


def test_main_skips_searches_without_a_buyers_section(tmp_path):
    enriched = json.loads((Path(__file__).parent / "fixtures" / "enriched_sample.json").read_text(encoding="utf-8"))
    accounts = p.filter_doc(enriched, load_profile("config/profile.example.yaml"), "")
    (tmp_path / "accounts").mkdir()
    (tmp_path / "accounts" / "2026-03-14.json").write_text(json.dumps(accounts), encoding="utf-8")
    assert b.main(["--profile", "config/profile.example.yaml", "--data", str(tmp_path), "--date", "2026-03-14"],
                  fetcher=FakeFetcher()) == 0
    assert not (tmp_path / "buyers").exists()


def test_report_renders_who_buys_and_exports_purchases(tmp_path):
    block = {"status": "ok", "companies_checked": 3,
             "sources": [{"type": "usaspending", "label": "usaspending", "status": "ok", "count": 3},
                         {"type": "socrata", "label": "City contracts", "status": "failed", "count": 0,
                          "error": "HTTP 403 from https://data.example.gov"}],
             "purchases": PURCHASES, "summary": b.summarize(PURCHASES, {"sl": "Sloan Valve"})}
    text = r.render_buyers(block)
    assert "**Listed companies seen as a direct vendor: 1 of 3.**" in text
    assert "- **Sloan Valve:** $900 across 1 purchases." in text
    assert "| Department of Defense / Defense Logistics Agency | $1,600 | 2 |" in text
    assert "| HOOSIER INDUSTRIAL SUPPLY | $1,000 | 2 |" in text
    assert "- **Source failed (City contracts):** HTTP 403" in text
    assert "absence here does not mean a company has no customers" in text

    rows = list(csv.DictReader(io.StringIO(r._csv(r.PURCHASE_COLUMNS, [
        {**p_, "listed_company": {"sl": "Sloan Valve"}.get(p_["vendor_account_id"], "")} for p_ in PURCHASES]))))
    assert rows[0]["listed_company"] == "Sloan Valve" and rows[1]["listed_company"] == ""
