"""Tests for software-market sources and site tool detection: the YC directory, SEC
Form D filings, and chat, scheduler, and tag manager detection. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketmapper import filters as f
from marketmapper import pipeline as p
from marketmapper import report as r
from marketmapper import techdetect as t
from marketmapper.sources import SourceError, sec_form_d, yc_directory

MIDWEST = {"countries": ["US"], "regions": ["IL", "OH", "MI"]}


# --- YC directory ---------------------------------------------------------

def yc(name, locations, **kw):
    base = {"name": name, "slug": name.lower().replace(" ", "-"), "website": f"https://{name.lower()}.example",
            "all_locations": locations, "industry": "B2B", "industries": ["B2B", "Sales"], "tags": ["SaaS"],
            "team_size": 20, "status": "Active", "isHiring": True, "batch": "Winter 2024",
            "one_liner": "Invoices for plumbers", "url": f"https://www.ycombinator.com/companies/{name.lower()}"}
    return {**base, **kw}


@pytest.mark.parametrize("text,expected", [
    ("Chicago, IL, USA; Remote", [{"city": "Chicago", "region": "IL", "country": "US"}]),
    ("CA, USA", [{"city": None, "region": "CA", "country": "US"}]),          # a state with no city
    ("London, United Kingdom", [{"city": "London", "region": None, "country": "GB"}]),
])
def test_parse_locations(text, expected):
    assert yc_directory.parse_locations(text) == expected


def test_yc_filters_and_signals(tmp_path):
    companies = [
        yc("Ledgerly", "Chicago, IL, USA"),
        yc("Coastal", "San Francisco, CA, USA"),                               # out of region
        yc("Oldco", "Columbus, OH, USA", status="Inactive"),
        yc("Tiny", "Detroit, MI, USA", team_size=3),                           # below team_size
        yc("Quietco", "Detroit, MI, USA", isHiring=False, batch="Summer 2015"),
        yc("Twocity", "Austin, TX, USA; Ann Arbor, MI, USA"),                  # second location is in region
    ]
    path = tmp_path / "yc.json"
    path.write_text(json.dumps(companies), encoding="utf-8")
    spec = {"path": str(path), "industries": ["B2B"], "team_size": [10, 150], "batch_since": 2023}
    recs = {x["name"]: x for x in yc_directory.discover(spec, MIDWEST, None, {})}
    assert sorted(recs) == ["Ledgerly", "Quietco", "Twocity"]
    assert recs["Twocity"]["address"]["city"] == "Ann Arbor"
    assert {e["kind"] for e in recs["Ledgerly"]["evidence"]} == {"directory", "funded", "hiring"}
    assert {e["kind"] for e in recs["Quietco"]["evidence"]} == {"directory"}    # old batch, not hiring
    assert recs["Ledgerly"]["employees"] == 20


def test_yc_hiring_only(tmp_path):
    path = tmp_path / "yc.json"
    path.write_text(json.dumps([yc("A", "Chicago, IL, USA"), yc("B", "Chicago, IL, USA", isHiring=False)]),
                    encoding="utf-8")
    recs = yc_directory.discover({"path": str(path), "hiring_only": True}, MIDWEST, None, {})
    assert [x["name"] for x in recs] == ["A"]


# --- SEC Form D -----------------------------------------------------------

FORM_D_XML = """<?xml version="1.0"?>
<edgarSubmission>
  <primaryIssuer>
    <cik>0009999999</cik>
    <entityName>Ledgerly, Inc.</entityName>
    <issuerAddress><street1>1 Example Plaza</street1><city>CHICAGO</city>
      <stateOrCountry>IL</stateOrCountry><zipCode>60601</zipCode></issuerAddress>
  </primaryIssuer>
  <relatedPersonsList><relatedPersonInfo><relatedPersonName>
    <firstName>Marguerite</firstName><lastName>Quinnell</lastName></relatedPersonName></relatedPersonInfo>
  </relatedPersonsList>
  <offeringData>
    <industryGroup><industryGroupType>Other Technology</industryGroupType></industryGroup>
    <typeOfFiling><newOrAmendment><isAmendment>false</isAmendment></newOrAmendment>
      <dateOfFirstSale><value>2026-06-02</value></dateOfFirstSale></typeOfFiling>
    <offeringSalesAmounts><totalOfferingAmount>5000000</totalOfferingAmount>
      <totalAmountSold>3500000</totalAmountSold></offeringSalesAmounts>
  </offeringData>
</edgarSubmission>"""

FUND_XML = FORM_D_XML.replace("Ledgerly, Inc.", "Example Growth Fund LP") \
    .replace("Other Technology", "Pooled Investment Fund").replace("0009999999", "0008888888")


def test_parse_form_d():
    d = sec_form_d.parse_form_d(FORM_D_XML)
    assert d == {"name": "Ledgerly, Inc.", "street": "1 Example Plaza", "city": "CHICAGO", "region": "IL",
                 "postal_code": "60601", "industry_group": "Other Technology", "is_amendment": False,
                 "first_sale": "2026-06-02", "total_offering": 5000000.0, "amount_sold": 3500000.0}


def test_indefinite_offering_amount_is_none():
    d = sec_form_d.parse_form_d(FORM_D_XML.replace("5000000", "Indefinite"))
    assert d["total_offering"] is None


class SecFake:
    user_agent = "Example Research research@example.com"

    def __init__(self):
        self.urls = []

    def get_json(self, url, params=None, headers=None, max_bytes=None):
        self.params = params
        hit = lambda cik, adsh, form="D": {"_source": {"ciks": [cik], "adsh": adsh, "form": form,  # noqa: E731
                                                       "file_date": "2026-06-10"}}
        return {"hits": {"hits": [hit("0009999999", "0009999999-26-000001"),
                                  hit("0008888888", "0008888888-26-000001"),
                                  hit("0007777777", "0007777777-26-000001", form="D/A")]}}

    def get_text(self, url, headers=None, max_bytes=None):
        self.urls.append(url)
        return FUND_XML if "8888888" in url else FORM_D_XML


def test_form_d_keeps_technology_issuers_and_never_reads_people():
    fake = SecFake()
    recs = sec_form_d.discover({"days": 365}, {"regions": ["IL"]}, fake, {"date": "2026-09-17"})
    assert [x["name"] for x in recs] == ["Ledgerly, Inc."]                   # fund dropped, amendment skipped
    assert fake.params["locationCodes"] == "IL" and fake.params["startdt"] == "2025-09-17"
    assert fake.urls[0] == "https://www.sec.gov/Archives/edgar/data/9999999/000999999926000001/primary_doc.xml"
    ev = recs[0]["evidence"][0]
    assert ev["kind"] == "funded" and ev["date"] == "2026-06-02"
    assert "sold $3,500,000 of a $5,000,000 offering" in ev["detail"]
    assert "Quinnell" not in json.dumps(recs)


def test_form_d_requires_a_declared_contact():
    fake = SecFake()
    fake.user_agent = "market-mapper/0.1"
    with pytest.raises(SourceError, match="contact email"):
        sec_form_d.discover({}, {"regions": ["IL"]}, fake, {"date": "2026-09-17"})


def test_funded_signal_respects_the_window():
    acct = {"evidence": [{"kind": "funded", "date": "2026-06-02", "detail": "Form D"},
                         {"kind": "funded", "date": "2023-01-01", "detail": "Old round"}], "site": None}
    assert [s["detail"] for s in f.signals_for(acct, {"funded_within_days": 180}, "2026-09-17")] == ["Form D"]
    assert len(f.signals_for(acct, {"funded": True}, "2026-09-17")) == 2


# --- tool detection -------------------------------------------------------

def page(*scripts, body="<h1>Invoices for plumbers</h1>" + "<p>word</p>" * 60):
    return f"<html><head>{''.join(scripts)}</head><body>{body}</body></html>"


INTERCOM = '<script>window.intercomSettings = {app_id: "x"};</script><script src="https://widget.intercom.io/widget/x"></script>'
GTM = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-X"></script>'
CHILI = '<script src="https://js.chilipiper.com/marketing.js"></script>'


def test_detects_vendors_from_embed_code():
    assert t.detect([page(INTERCOM, GTM), page(CHILI)]) == [
        "chat:intercom", "scheduler:chili_piper", "tag_manager:google_tag_manager"]


def test_visible_text_mentioning_a_vendor_is_not_detection():
    body = "<p>We moved our support off Intercom and Drift last year.</p><img src='/logos/zendesk.png'>" * 20
    assert t.detect([page(body=body)]) == []


@pytest.mark.parametrize("site,expected", [
    ({"tech": ["chat:intercom"], "error": None}, "present"),
    ({"tech": [], "error": None}, "absent"),
    ({"tech": ["tag_manager:google_tag_manager"], "error": None}, "absent_unverified"),
    ({"tech": [], "error": None, "client_rendered": True}, "unknown"),
    ({"tech": None, "error": "HTTP 403"}, "unknown"),
    (None, "unknown"),
])
def test_status(site, expected):
    assert t.status(site, "chat") == expected


def test_client_rendered_shell():
    shell = "<html><head><script src='a.js'></script><script src='b.js'></script><script>x</script></head>" \
            "<body><div id='root'></div></body></html>"
    assert t.looks_client_rendered(shell) is True
    assert t.looks_client_rendered(page(INTERCOM)) is False


@pytest.mark.parametrize("tech,unverified_ok,passes", [
    ([], False, True),
    (["chat:drift"], False, False),
    (["tag_manager:segment"], False, False),
    (["tag_manager:segment"], True, True),
])
def test_tech_absent_filter(tech, unverified_ok, passes):
    acct = {"site": {"tech": tech, "error": None}}
    assert f.tech_ok(acct, {"tech_absent": ["chat"], "tech_unverified_ok": unverified_ok}) is passes


def test_unreadable_site_never_passes_an_absence_rule():
    assert f.tech_ok({"site": {"tech": None, "error": "HTTP 403"}}, {"tech_absent": ["chat"],
                                                                      "tech_unverified_ok": True}) is False


def test_tech_present_filter():
    acct = {"site": {"tech": ["chat:drift"], "error": None}}
    assert f.tech_ok(acct, {"tech_present": ["chat"]}) is True
    assert f.tech_ok(acct, {"tech_present": ["scheduler"]}) is False


def test_tech_rules_bind_even_with_an_explicit_knockouts_list():
    from marketmapper.config import Profile
    profile = Profile(context=[], dedup={}, http={}, filters={"knockouts": ["in_region"]},
                      searches={"s": {"goal": "market_map", "region": {"countries": ["US"]},
                                      "tech_absent": ["chat"]}})
    rec = {"record_id": "yc:a", "source": "yc_directory", "name": "Ledgerly", "website": "https://l.example",
           "address": {"country": "US"}, "categories": [], "evidence": [],
           "site": {"url": "https://l.example", "title": "", "description": "", "text": "",
                    "tech": ["chat:intercom"], "error": None}}
    out = p.filter_search("s", {"records": [rec]}, profile, "2026-09-17", "")
    acct = out["accounts"][0]
    assert acct["tech_status"] == {"chat": "present"} and acct["passed"] is False


def test_report_summarizes_the_tool_check():
    block = {"accounts": [{"tech_status": {"chat": "present"}}, {"tech_status": {"chat": "absent"}},
                          {"tech_status": {"chat": "absent_unverified"}}, {"tech_status": {"chat": "unknown"}}]}
    line = r.tool_check_line(block)
    assert line.startswith("- **Tool check** across 4 companies: chat: 1 present, 1 absent, ")
    assert "1 not found, but a tag manager or HubSpot tracking could switch it on" in line
    assert r.tool_check_line({"accounts": [{}]}) is None


def test_widget_markup_counts_but_only_in_ids_and_classes():
    markup = page(body='<div class="intercom-lightweight-app"><div class="intercom-lightweight-app-launcher"></div></div>'
                       + "<p>word</p>" * 60)
    assert t.detect([markup]) == ["chat:intercom"]
    text_only = page(body="<p>Our intercom-lightweight-app replacement is faster.</p>" * 30)
    assert t.detect([text_only]) == []


def test_company_specific_scheduler_subdomain():
    html = page('<script src="https://acme.chilipiper.com/concierge-js/cjs/concierge.js"></script>')
    assert t.detect([html]) == ["scheduler:chili_piper"]


def test_first_party_scripts_skip_vendor_files_and_respect_the_limit():
    html = page('<script src="/_next/static/chunks/app.js"></script>',
                '<script src="https://app-preview.vercel.app/_next/static/chunks/b.js"></script>',
                '<script src="https://widget.intercom.io/widget/x"></script>',
                '<script src="https://www.googletagmanager.com/gtm.js?id=G"></script>',
                '<script src="/_next/static/chunks/app.js"></script>')
    assert t.first_party_scripts(html, "https://acme.example/", 5) == [
        "https://acme.example/_next/static/chunks/app.js",
        "https://app-preview.vercel.app/_next/static/chunks/b.js"]
    assert len(t.first_party_scripts(html, "https://acme.example/", 1)) == 1


def test_bundle_scan_uses_host_signatures_only():
    bundle = 'var e="https://widget.intercom.io/widget/"+t;window.intercomSettings={app_id:t};'
    assert t.detect_in_script(bundle) == ["chat:intercom"]
    # A short markup marker inside minified code is not trusted on its own.
    assert t.detect_in_script('className:"intercom-frame-like-card"') == []


def test_read_site_merges_bundle_detections():
    from marketmapper.enrich import read_site

    class Web:
        def __init__(self):
            self.assets = []

        def get_page(self, url):
            return {"url": url, "content_type": "text/html",
                    "html": page('<script src="/static/app.js"></script>')}

        def get_asset(self, url, max_bytes=None):
            self.assets.append(url)
            return 'load("https://widget.intercom.io/widget/abc")'

    web = Web()
    site = read_site("https://acme.example/", web, max_pages=1, max_scripts=5)
    assert site["tech"] == ["chat:intercom"] and site["scripts_scanned"] == 1
    assert web.assets == ["https://acme.example/static/app.js"]
    off = read_site("https://acme.example/", Web(), max_pages=1, max_scripts=0)
    assert off["tech"] == [] and off["scripts_scanned"] == 0


def test_hubspot_tracking_makes_an_absence_unverifiable():
    html = page('<script id="hs-script-loader" async defer src="//js-na1.hs-scripts.com/123456.js"></script>')
    tech = t.detect([html])
    assert tech == ["chat_loader:hubspot_tracking"]
    assert t.status({"tech": tech, "error": None}, "chat") == "absent_unverified"
