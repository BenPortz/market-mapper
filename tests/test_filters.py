"""Tests for the pure normalization, region, signal, and filter logic."""

from __future__ import annotations

import pytest

from marketmapper import filters as f


# --- names and domains ----------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("92ND TERRACE DENTAL LLC", "92nd Terrace Dental"),
    ("Lakeshore Conveyor Systems, Inc.", "Lakeshore Conveyor Systems"),
    ("Smith & Sons Co.", "Smith and Sons"),
    ("Hive (S14)", "Hive"),
])
def test_normalize_name_makes_variants_equal(a, b):
    assert f.normalize_name(a) == f.normalize_name(b)


def test_normalize_name_keeps_meaningful_words():
    assert f.normalize_name("Acme Conveyor") != f.normalize_name("Acme Dental")


@pytest.mark.parametrize("raw,expected", [
    ("OSPREY POINT DENTAL CARE, PC", "Osprey Point Dental Care, PC"),
    ("BRAMBLEWOOD ENDODONTICS PLLC", "Bramblewood Endodontics PLLC"),
    ("Quillon Street Dental", "Quillon Street Dental"),   # mixed case is left alone
    ("OHIO CNC WORKS II", "Ohio CNC Works II"),
])
def test_display_name_fixes_all_caps(raw, expected):
    assert f.display_name(raw) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.acme.example/about", "acme.example"),
    ("acme.example", "acme.example"),
    ("HTTP://Sub.Acme.Example:8080/", "sub.acme.example"),
    ("", None),
    (None, None),
])
def test_domain_of(url, expected):
    assert f.domain_of(url) == expected


# --- region ---------------------------------------------------------------

OREGON = {"countries": ["US"], "regions": ["OR"], "site_terms": ["Oregon", "OR 97"]}


def test_address_in_region():
    assert f.region_status({"region": "OR", "country": "US"}, OREGON) == "in"


def test_full_state_name_is_understood():
    assert f.region_status({"region": "Oregon"}, OREGON) == "in"


def test_address_out_of_region():
    assert f.region_status({"region": "WA"}, OREGON) == "out"


def test_wrong_country_is_out_even_without_a_state():
    assert f.region_status({"country": "CA"}, OREGON) == "out"


def test_site_text_can_place_a_company_without_an_address():
    assert f.region_status({}, OREGON, "Visit our shop in Bend, Oregon.") == "in"


def test_site_terms_match_whole_words_and_case():
    # "OR 97" must not fire on "or 97 other products"; "Oregon" must not fire inside "Oregonian".
    assert f.region_status({}, OREGON, "choose from 12 or 97 other sizes") == "unknown"
    assert f.region_status({}, OREGON, "as seen in The Oregonian") == "unknown"


def test_address_beats_site_text():
    # A Washington address wins over a site that mentions serving Oregon.
    assert f.region_status({"region": "WA"}, OREGON, "Serving Oregon too") == "out"


def test_no_evidence_is_unknown():
    assert f.region_status({}, OREGON, "") == "unknown"


def test_country_only_region():
    assert f.region_status({"country": "us"}, {"countries": ["US"]}) == "in"


# --- signals --------------------------------------------------------------

def _acct(**kw):
    base = {"evidence": [], "site": None}
    base.update(kw)
    return base


def test_recent_registration_is_a_signal():
    acct = _acct(evidence=[{"kind": "registered", "date": "2026-01-15"}])
    out = f.signals_for(acct, {"registered_within_days": 90}, "2026-03-14")
    assert [s["kind"] for s in out] == ["recently_registered"]
    assert "58 days ago" in out[0]["detail"]


def test_old_registration_is_not_a_signal():
    acct = _acct(evidence=[{"kind": "registered", "date": "2012-05-01"}])
    assert f.signals_for(acct, {"registered_within_days": 90}, "2026-03-14") == []


def test_malformed_date_is_ignored_not_fatal():
    acct = _acct(evidence=[{"kind": "registered", "date": "sometime"}])
    assert f.signals_for(acct, {"registered_within_days": 90}, "2026-03-14") == []


def test_site_pattern_signal_quotes_the_matched_words():
    acct = _acct(site={"text": "Now Open in Bend!", "url": "https://x.example/"})
    out = f.signals_for(acct, {"site_patterns": ["now open"]}, "2026-03-14")
    assert out[0]["detail"] == 'Site says "Now Open"'


def test_hiring_counts_only_when_enabled():
    acct = _acct(evidence=[{"kind": "hiring", "detail": "Hiring: Maintenance Lead"}])
    assert f.signals_for(acct, {}, "2026-03-14") == []
    assert f.signals_for(acct, {"hiring": True}, "2026-03-14")[0]["kind"] == "hiring"


def test_duplicate_registry_entries_do_not_double_count():
    ev = {"kind": "registered", "date": "2026-01-15"}
    acct = _acct(evidence=[ev, dict(ev)])
    assert len(f.signals_for(acct, {"registered_within_days": 90}, "2026-03-14")) == 1


# --- filters --------------------------------------------------------------

LOAD_BEARING = ["in_region", "relevant", "no_exclude_terms", "not_excluded", "signal_ok", "has_website"]
SEARCH = {"include_any": ["conveyor"], "exclude_any": ["used conveyor"], "min_signals": 0}
GOOD = {"name": "Lakeshore Conveyor", "domain": "lakeshore.example",
        "website": "https://lakeshore.example/", "categories": [], "evidence": [],
        "site": {"text": "Custom conveyor systems."}, "signals": [], "region_status": "in"}


def test_clean_account_passes():
    result = f.evaluate(GOOD, SEARCH, {})
    assert all(result.values())
    assert f.passed(result, LOAD_BEARING)


def test_irrelevant_account_fails():
    acct = {**GOOD, "name": "Lakeshore Dental", "site": {"text": "Family dentistry."}}
    assert f.evaluate(acct, SEARCH, {})["relevant"] is False


def test_search_snippet_counts_toward_relevance():
    # The site blocked fetching, but the search snippet says what they make.
    acct = {**GOOD, "name": "RollerPro", "site": {"text": ""},
            "evidence": [{"kind": "search_result", "snippet": "roller conveyors in Toledo"}]}
    assert f.evaluate(acct, SEARCH, {})["relevant"] is True


def test_exclude_term_fails():
    acct = {**GOOD, "site": {"text": "We buy and sell used conveyor equipment."}}
    assert f.evaluate(acct, SEARCH, {})["no_exclude_terms"] is False


def test_unknown_region_passes_unless_strict():
    acct = {**GOOD, "region_status": "unknown"}
    assert f.evaluate(acct, SEARCH, {})["in_region"] is True
    assert f.evaluate(acct, {**SEARCH, "region_strict": True}, {})["in_region"] is False


def test_out_of_region_always_fails():
    assert f.evaluate({**GOOD, "region_status": "out"}, SEARCH, {})["in_region"] is False


def test_exclusion_by_name_ignores_suffixes():
    cfg = {"exclude_companies": ["Lakeshore Conveyor, LLC"]}
    assert f.evaluate(GOOD, SEARCH, cfg)["not_excluded"] is False


def test_exclusion_by_domain():
    cfg = {"exclude_domains": ["https://www.lakeshore.example"]}
    assert f.evaluate(GOOD, SEARCH, cfg)["not_excluded"] is False


def test_exclusion_is_whole_name_not_substring():
    cfg = {"exclude_companies": ["Lakeshore"]}
    assert f.evaluate(GOOD, SEARCH, cfg)["not_excluded"] is True


def test_min_signals_and_require_website():
    strict = {**SEARCH, "min_signals": 1, "require_website": True}
    no_site = {**GOOD, "website": None}
    result = f.evaluate(no_site, strict, {})
    assert result["signal_ok"] is False and result["has_website"] is False


def test_filter_outside_load_bearing_is_reported_not_fatal():
    result = f.evaluate({**GOOD, "website": None}, {**SEARCH, "require_website": True}, {})
    assert f.passed(result, [k for k in LOAD_BEARING if k != "has_website"])


def test_score_ranks_signals_above_keywords():
    with_signal = {**GOOD, "signals": [{"kind": "hiring", "detail": "x"}], "site": {"text": ""}}
    keywords_only = {**GOOD, "site": {"text": "conveyor " * 20}}
    assert f.score(with_signal, SEARCH) > f.score(keywords_only, SEARCH)
