"""Tests for the FILTER stage: merging, filtering, ranking, and judge queueing.

Runs the real fixture through the real stage, asserting on which companies
survive and why each rejected one was rejected. That catches a filter that drops
the right companies for the wrong reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketmapper import pipeline as p
from marketmapper.config import ProfileError, load_profile

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = load_profile("config/profile.example.yaml")


@pytest.fixture
def enriched() -> dict:
    return json.loads((FIXTURES / "enriched_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def out(enriched) -> dict:
    return p.filter_doc(enriched, PROFILE, recent_blob="")


def _acct(out, search, account_id):
    return next(a for a in out["searches"][search]["accounts"] if a["account_id"] == account_id)


def _rec(rid, source, name, website=None, city=None, region=None, **kw):
    return {"record_id": rid, "source": source, "name": name, "legal_name": kw.get("legal_name"),
            "website": website, "address": {"city": city, "region": region},
            "phone": None, "categories": [], "evidence": []}


# --- merging --------------------------------------------------------------

def test_registry_and_map_listing_merge_into_one_account(out):
    a = _acct(out, "dental_xray_buyers", "pineconefamilydental-example")
    assert sorted(a["sources"]) == ["npi", "osm"]
    assert a["phone"] == "541-555-0100"                # from the registry
    assert a["website"].startswith("https://www.pinecone")  # from the map


def test_merged_account_collects_signals_from_both_sources(out):
    a = _acct(out, "dental_xray_buyers", "pineconefamilydental-example")
    assert {s["kind"] for s in a["signals"]} == {"recently_registered", "site_mention"}


def test_same_domain_merges_regardless_of_www_and_name_style(out):
    a = _acct(out, "conveyor_manufacturers", "lakeshoreconveyor-example")
    assert sorted(a["sources"]) == ["csv", "web_search"]
    assert a["name"] == "Lakeshore Conveyor Systems"   # the plainer name wins
    assert a["address"]["region"] == "MI"              # the list supplied the address


def test_same_name_different_city_stays_separate():
    accts = p.merge_records([_rec("npi:1", "npi", "Smile Dental", city="Bend", region="OR"),
                             _rec("npi:2", "npi", "Smile Dental", city="Salem", region="OR")])
    assert len(accts) == 2


def test_same_name_and_city_but_different_websites_stay_separate():
    accts = p.merge_records([
        _rec("osm:1", "osm", "Acme Conveyor", website="https://acme-one.example", city="Dayton"),
        _rec("csv:1", "csv", "Acme Conveyor", website="https://acme-two.example", city="Dayton"),
    ])
    assert len(accts) == 2


def test_legal_name_is_used_for_matching():
    accts = p.merge_records([
        _rec("osm:1", "osm", "Marrowstone Dental Group", website="https://marrowstone.example", city="Tillamook"),
        _rec("npi:1", "npi", "Marrowstone Dental Group", city="Tillamook", legal_name="92nd Terrace Dental LLC"),
        _rec("csv:1", "csv", "92ND TERRACE DENTAL", city="Tillamook"),
    ])
    assert len(accts) == 1


def test_merge_does_not_depend_on_record_order(enriched):
    import random
    records = enriched["searches"]["conveyor_manufacturers"]["records"]
    expected = sorted(a["account_id"] for a in p.merge_records(records))
    for seed in range(5):
        shuffled = records[:]
        random.Random(seed).shuffle(shuffled)
        assert sorted(a["account_id"] for a in p.merge_records(shuffled)) == expected


def test_account_ids_are_stable_slugs(out):
    ids = [a["account_id"] for b in out["searches"].values() for a in b["accounts"]]
    assert "high-desert-endodontics-redmond-or" in ids  # suffix dropped, city and state kept
    assert len(ids) == len(set(ids))


# --- filtering ------------------------------------------------------------

def test_dental_search_passes_only_the_qualified(out):
    passed = [a["account_id"] for a in out["searches"]["dental_xray_buyers"]["accounts"] if a["passed"]]
    assert passed == ["pineconefamilydental-example", "high-desert-endodontics-redmond-or"]


@pytest.mark.parametrize("account_id,failed_filter", [
    ("pacificsmiles-example", "in_region"),                          # Washington address
    ("harbordental-example", "in_region"),                           # no address, strict region
    ("example-customer-dental-group-salem-or", "not_excluded"),      # existing customer
    ("willamette-dental-school-clinic-portland-or", "no_exclude_terms"),
    ("riverbend-orthodontics-eugene-or", "signal_ok"),               # registered in 2012
])
def test_each_rejection_fails_for_its_own_reason(out, account_id, failed_filter):
    a = _acct(out, "dental_xray_buyers", account_id)
    assert [k for k, v in a["filters"].items() if not v] == [failed_filter]


def test_market_map_keeps_unknown_region_when_not_strict(out):
    a = _acct(out, "conveyor_manufacturers", "grainmove-example")
    assert a["region_status"] == "unknown" and a["passed"] is True


def test_market_map_rejections(out):
    fails = {a["account_id"]: [k for k, v in a["filters"].items() if not v]
             for a in out["searches"]["conveyor_manufacturers"]["accounts"] if not a["passed"]}
    assert fails == {
        "beltworks-example": ["no_exclude_terms"],
        "ohio-valley-conveyor-dayton-oh": ["has_website"],
        "lonestarconveyor-example": ["in_region"],
    }


def test_blocked_site_can_still_pass_on_its_search_snippet(out):
    a = _acct(out, "conveyor_manufacturers", "rollerpro-example")
    assert a["site"]["error"].startswith("blocked by robots.txt")
    assert a["passed"] is True and a["region_status"] == "in"


# --- ranking and queue ----------------------------------------------------

def test_passed_accounts_sort_first_by_score(out):
    accts = out["searches"]["conveyor_manufacturers"]["accounts"]
    flags = [a["passed"] for a in accts]
    assert flags == sorted(flags, reverse=True)
    passed_scores = [a["score"] for a in accts if a["passed"]]
    assert passed_scores == sorted(passed_scores, reverse=True)


def test_judge_queue_holds_passed_accounts_in_rank_order(out):
    block = out["searches"]["dental_xray_buyers"]
    assert block["judge_queue"] == [a["account_id"] for a in block["accounts"] if a["passed"]]


def test_judge_limit_bounds_the_queue(enriched):
    profile = load_profile("config/profile.example.yaml")
    profile.searches["conveyor_manufacturers"]["judge"] = {"limit": 2}
    doc = p.filter_doc(enriched, profile, "")
    assert len(doc["searches"]["conveyor_manufacturers"]["judge_queue"]) == 2


def test_source_failures_are_carried_forward(out):
    block = out["searches"]["conveyor_manufacturers"]
    assert block["status"] == "partial"
    assert any(s["status"] == "failed" for s in block["sources"])


def test_site_text_is_capped_for_the_judge(enriched):
    enriched["searches"]["conveyor_manufacturers"]["records"][0]["site"]["text"] = "conveyor " * 5000
    doc = p.filter_doc(enriched, PROFILE, "")
    for a in doc["searches"]["conveyor_manufacturers"]["accounts"]:
        assert len((a.get("site") or {}).get("text", "")) <= p.JUDGE_TEXT_CAP


def test_output_validates_against_the_schema(out):
    pytest.importorskip("jsonschema")
    assert p.validate(out, Path("schemas/accounts.schema.json")) is None


# --- dedup ----------------------------------------------------------------

def test_top_n_search_suppresses_recently_surfaced_accounts(enriched):
    doc = p.filter_doc(enriched, PROFILE, "| 2026-03-01 | 2 | dental_xray_buyers: pinecone family dental |")
    a = _acct(doc, "dental_xray_buyers", "pineconefamilydental-example")
    assert a["seen_recent"] is True and a["passed"] is False


def test_market_map_ignores_dedup(enriched):
    doc = p.filter_doc(enriched, PROFILE, "| 2026-03-01 | 2 | conveyor_manufacturers: lakeshore conveyor systems |")
    assert _acct(doc, "conveyor_manufacturers", "lakeshoreconveyor-example")["passed"] is True


def test_dedup_matches_whole_names_only():
    assert p.seen_in("Hive", "archive labs") is False
    assert p.seen_in("Hive", "dental: hive, acme") is True


def test_a_rerun_does_not_dedup_against_its_own_row(tmp_path):
    index = tmp_path / "INDEX.md"
    index.write_text("| Date | x |\n|---|---|\n| 2026-03-13 | old co |\n| 2026-03-14 | today co |\n",
                     encoding="utf-8")
    blob = p.recent_index_text(index, days=30, exclude_date="2026-03-14")
    assert "old co" in blob and "today co" not in blob


def test_missing_index_is_not_an_error(tmp_path):
    assert p.recent_index_text(tmp_path / "nope.md", days=30) == ""


# --- profile validation ---------------------------------------------------

@pytest.mark.parametrize("yaml_text,message", [
    ("searches: {}\n", "no searches"),
    ("searches:\n  s:\n    goal: top_n\n    region: {regions: [OR]}\n    sources: [{type: npi}]\n", "target_count"),
    ("searches:\n  s:\n    goal: market_map\n    sources: [{type: npi}]\n", "no region"),
    ("searches:\n  s:\n    goal: market_map\n    region: {regions: [OR]}\n    sources: []\n", "no sources"),
    ("searches:\n  s:\n    goal: market_map\n    region: {regions: [OR]}\n    sources: [{type: telepathy}]\n", "unknown source"),
    ("searches:\n  s:\n    goal: everything\n    region: {regions: [OR]}\n    sources: [{type: npi}]\n", "goal must be"),
])
def test_bad_profiles_are_rejected_with_a_clear_message(tmp_path, yaml_text, message):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ProfileError, match=message):
        load_profile(path)


def test_judge_limit_defaults():
    assert PROFILE.judge_limit("dental_xray_buyers") == 40      # explicit
    PROFILE.searches["tmp"] = {"goal": "top_n", "target_count": 10}
    PROFILE.searches["tmp_map"] = {"goal": "market_map"}
    try:
        assert PROFILE.judge_limit("tmp") == 20                  # twice the target
        assert PROFILE.judge_limit("tmp_map") == 0               # maps are not judged by default
    finally:
        del PROFILE.searches["tmp"], PROFILE.searches["tmp_map"]
