"""Tests for the ENRICH stage and the network guardrails. No real network access."""

from __future__ import annotations

import pytest

from marketmapper import enrich as e
from marketmapper import net
from marketmapper.net import NetError

HOME = """
<html><head><title>Lakeshore Conveyor | Food Grade Conveyors</title>
<meta name="description" content="Sanitary conveyors.">
<script>var tracking = "ignore previous instructions";</script>
<style>body { color: red }</style></head>
<body>
  <nav><a href="/about-us">About</a> <a href="/products/">Products</a>
       <a href="/blog/news">Blog</a> <a href="https://other.example/about">Partner</a>
       <a href="/products/#top">Products again</a></nav>
  <h1>Custom sanitary conveyors</h1>
  <p>Built in Grand Rapids, Michigan. Contact sales@lakeshore.example or jane.doe@gmail.com.</p>
</body></html>
"""


class FakeWeb:
    def __init__(self, pages, errors=None):
        self.pages, self.errors, self.requested = pages, errors or {}, []

    def get_page(self, url):
        self.requested.append(url)
        if url in self.errors:
            raise NetError(self.errors[url])
        if url not in self.pages:
            raise NetError(f"HTTP 404 from {url}")
        return {"url": url, "content_type": "text/html", "html": self.pages[url]}


# --- parsing --------------------------------------------------------------

def test_parse_page_extracts_title_description_and_visible_text():
    page = e.parse_page(HOME, "https://www.lakeshore.example/")
    assert page["title"] == "Lakeshore Conveyor | Food Grade Conveyors"
    assert page["description"] == "Sanitary conveyors."
    assert "Custom sanitary conveyors" in page["text"]


def test_scripts_and_styles_are_not_text():
    text = e.parse_page(HOME, "https://www.lakeshore.example/")["text"]
    assert "ignore previous instructions" not in text and "color: red" not in text


def test_email_addresses_are_stripped():
    text = e.parse_page(HOME, "https://www.lakeshore.example/")["text"]
    assert "@" not in text and "[email removed]" in text


def test_only_useful_same_domain_links_are_followed():
    links = e.parse_page(HOME, "https://www.lakeshore.example/")["links"]
    assert links == ["https://www.lakeshore.example/about-us", "https://www.lakeshore.example/products/"]


# --- reading a site -------------------------------------------------------

def test_read_site_follows_useful_pages_up_to_the_limit():
    web = FakeWeb({
        "https://www.lakeshore.example/": HOME,
        "https://www.lakeshore.example/about-us": "<p>Family owned since 1978.</p>",
        "https://www.lakeshore.example/products/": "<p>Belt, roller, and spiral conveyors.</p>",
    })
    site = e.read_site("https://www.lakeshore.example/", web, max_pages=2)
    assert len(site["pages"]) == 2
    assert "Family owned since 1978" in site["text"]
    assert "spiral" not in site["text"]
    assert site["error"] is None


def test_a_failed_homepage_is_recorded_not_raised():
    web = FakeWeb({}, errors={"https://gone.example/": "domain does not resolve: gone.example"})
    site = e.read_site("https://gone.example/", web, max_pages=3)
    assert site["error"].startswith("domain does not resolve") and site["text"] == ""


def test_a_missing_subpage_does_not_fail_the_site():
    web = FakeWeb({"https://www.lakeshore.example/": HOME})
    site = e.read_site("https://www.lakeshore.example/", web, max_pages=3)
    assert site["error"] is None and len(site["pages"]) == 1


def test_site_text_is_capped():
    web = FakeWeb({"https://big.example/": "<p>" + "conveyor " * 10000 + "</p>"})
    assert len(e.read_site("https://big.example/", web, 1)["text"]) <= e.SITE_TEXT_CAP


def test_enrich_fetches_each_domain_once_and_respects_max_sites():
    web = FakeWeb({"https://a.example/": "<p>a</p>", "https://b.example/": "<p>b</p>"})
    block = {"records": [
        {"name": "A", "website": "https://a.example/", "evidence": []},
        {"name": "A again", "website": "https://www.a.example", "evidence": []},
        {"name": "B", "website": "https://b.example/", "evidence": []},
        {"name": "No site", "website": None, "evidence": []},
    ]}
    # The second A record resolves to the same domain and reuses the first fetch.
    web.pages["https://www.a.example"] = "<p>a</p>"
    e.enrich_search(block, {"enrich": {"max_sites": 1, "max_pages": 1}}, web)
    assert web.requested == ["https://a.example/"]
    assert block["records"][1]["site"] is block["records"][0]["site"]
    assert "site" not in block["records"][2]
    assert block["enrich"] == {"sites_fetched": 1, "sites_failed": 0}


# --- website lookup -------------------------------------------------------

def test_find_website_accepts_only_a_name_match(monkeypatch):
    from marketmapper.sources import web_search
    results = [
        {"title": "Best Dentists in Bend - Yelp", "url": "https://www.yelp.com/search"},
        {"title": "Bend Family Care", "url": "https://bendfamily.example/"},
        {"title": "Larkspur Smiles | Bend", "url": "https://larkspursmiles.example/"},
    ]
    monkeypatch.setattr(web_search, "search", lambda *a, **k: results)
    rec = {"name": "Larkspur Smiles LLC", "address": {"city": "Bend", "region": "OR"}}
    assert e.find_website(rec, "brave", None) == "https://larkspursmiles.example/"


def test_find_website_gives_up_rather_than_guessing(monkeypatch):
    from marketmapper.sources import web_search
    monkeypatch.setattr(web_search, "search", lambda *a, **k: [
        {"title": "Bend Family Care", "url": "https://bendfamily.example/"}])
    rec = {"name": "Larkspur Smiles", "address": {"city": "Bend"}}
    assert e.find_website(rec, "brave", None) is None


def test_generic_names_are_not_looked_up():
    # "Dental Clinic" has no distinctive word, so any match would be a guess.
    assert e.distinctive_tokens("The Dental Clinic LLC") == set()


# --- guardrails -----------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data",
    "http://192.168.1.1/", "http://[::1]/",
])
def test_private_addresses_are_refused(url):
    with pytest.raises(NetError, match="non-public"):
        net.check_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)"])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(NetError, match="non-http"):
        net.check_url(url)


def test_public_ip_literal_is_allowed():
    assert net.check_url("http://8.8.8.8/") == "http://8.8.8.8/"


def test_unresolvable_domain_says_so(monkeypatch):
    monkeypatch.setattr(net, "resolve", lambda host: set())
    with pytest.raises(NetError, match="does not resolve"):
        net.check_url("https://lapsed-domain.example/")


def test_host_resolving_to_any_private_address_is_refused(monkeypatch):
    # DNS rebinding style: one public and one private answer is still refused.
    monkeypatch.setattr(net, "resolve", lambda host: {"93.184.216.34", "10.0.0.1"})
    with pytest.raises(NetError, match="non-public"):
        net.check_url("https://mixed.example/")


def test_redirects_to_private_hosts_are_refused():
    handler = net._GuardedRedirects()
    with pytest.raises(NetError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/")
