"""ENRICH stage: read each company's own website -> records with site text.

A registry says a company exists. Its website says what it does. The
judge needs the second, so this stage fetches the homepage plus a few pages
whose paths look like "about", "products", or "industries", and keeps bounded
plain text.

Records with no website can optionally be matched to one with a web search on
their name and city, accepted only when the result shares a distinctive word
with the company name.

Website text is untrusted third-party content. It is stored as data, email
addresses are stripped from it, and nothing in it is ever followed or executed.
Only same-domain links are fetched, and every request goes through the
guardrails in `net.py`.

Usage:
    python -m marketmapper.enrich
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from marketmapper import filters as mf
from marketmapper import techdetect
from marketmapper.config import Layout, Profile, ProfileError, load_profile
from marketmapper.net import DEFAULT_USER_AGENT, Fetcher, NetError

SITE_TEXT_CAP = 6000
SCRIPT_BYTES = 3_000_000   # one JavaScript bundle; larger files are skipped, not truncated
PAGE_TEXT_CAP = 2500
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_USEFUL_PATH = re.compile(r"/(about|company|who-we-are|products?|services?|solutions|"
                          r"industries|capabilities|markets|equipment)[\w-]*(/|$|\.)", re.I)
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
_NAME_STOPWORDS = {"the", "and", "company", "group", "services", "systems", "solutions",
                   "industries", "international", "dental", "clinic", "center", "medical"}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.links: list[str] = []
        self._chunks: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "description":
            self.description = (a.get("content") or "").strip()
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self._chunks.append(data.strip())

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._chunks)).strip()


def parse_page(html: str, base_url: str) -> dict[str, Any]:
    """Title, meta description, visible text, and same-domain useful links."""
    p = _PageParser()
    p.feed(html)
    domain = mf.domain_of(base_url)
    links = []
    for href in p.links:
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        if mf.domain_of(url) == domain and _USEFUL_PATH.search(urllib.parse.urlsplit(url).path):
            if url not in links:
                links.append(url)
    return {"title": p.title.strip(), "description": p.description,
            "text": _EMAIL.sub("[email removed]", p.text), "links": links}


def read_site(website: str, fetcher, max_pages: int, max_scripts: int = 0) -> dict[str, Any]:
    site: dict[str, Any] = {"url": website, "title": "", "description": "", "text": "",
                            "pages": [], "error": None}
    try:
        home = fetcher.get_page(website)
    except NetError as e:
        site["error"] = str(e)
        return site
    first = parse_page(home["html"], home["url"])
    site.update(url=home["url"], title=first["title"], description=first["description"])
    texts = [first["text"][:PAGE_TEXT_CAP]]
    raw_pages = [home["html"]]
    site["pages"].append(home["url"])
    for link in first["links"][: max(0, max_pages - 1)]:
        try:
            page = fetcher.get_page(link)
        except NetError:
            continue  # a missing about page is not worth failing the company over
        texts.append(parse_page(page["html"], page["url"])["text"][:PAGE_TEXT_CAP])
        raw_pages.append(page["html"])
        site["pages"].append(page["url"])
    site["text"] = "\n\n".join(texts)[:SITE_TEXT_CAP]
    # Tools are read from the embed code of the pages fetched; the raw HTML
    # itself is not stored.
    tech = set(techdetect.detect(raw_pages))
    scanned = 0
    for script_url in techdetect.first_party_scripts(home["html"], home["url"], max_scripts):
        try:
            tech.update(techdetect.detect_in_script(fetcher.get_asset(script_url, max_bytes=SCRIPT_BYTES)))
            scanned += 1
        except NetError:
            continue
    site["tech"] = sorted(tech)
    site["scripts_scanned"] = scanned
    site["client_rendered"] = techdetect.looks_client_rendered(home["html"])
    return site


def distinctive_tokens(name: str) -> set[str]:
    return {t for t in mf.normalize_name(name).split() if len(t) >= 4 and t not in _NAME_STOPWORDS}


def find_website(record: dict[str, Any], provider: str, fetcher) -> str | None:
    """Look up a website for a record that has none. Conservative on purpose.

    A wrong website is worse than none: the judge would describe the wrong
    company. A result is accepted only if its domain or title shares a
    distinctive word with the company name.
    """
    from marketmapper.sources import SourceError
    from marketmapper.sources.web_search import is_skipped, search

    tokens = distinctive_tokens(record["name"])
    if not tokens:
        return None
    addr = record.get("address") or {}
    query = " ".join(filter(None, [f'"{record["name"]}"', addr.get("city"), addr.get("region")]))
    try:
        results = search(fetcher, provider, query)
    except (SourceError, NetError):
        return None
    for res in results[:5]:
        domain = mf.domain_of(res["url"])
        if not domain or is_skipped(domain, set()):
            continue
        haystack = domain.replace("-", "") + " " + res.get("title", "").lower()
        if any(t in haystack for t in tokens):
            return f"https://{domain}/"
    return None


def enrich_search(block: dict[str, Any], search: dict[str, Any], fetcher) -> dict[str, Any]:
    cfg = search.get("enrich") or {}
    max_sites = int(cfg.get("max_sites", 300))
    max_pages = int(cfg.get("max_pages", 3))
    max_scripts = int(cfg.get("max_scripts", 0))   # site bundles to scan for tools; 0 turns it off
    lookup_provider = cfg.get("find_websites")  # e.g. "brave"; unset means no lookups
    cache: dict[str, dict[str, Any]] = {}
    fetched = 0

    region = search.get("region") or {}
    for rec in block.get("records", []):
        # Registry and certification sources return whole states; a plant whose own
        # address is outside the region would be filtered out later, so reading its
        # website only spends the fetch budget.
        if mf.region_status(rec.get("address"), region, "", rec.get("coords")) == "out":
            continue
        if not rec.get("website") and lookup_provider:
            if found := find_website(rec, lookup_provider, fetcher):
                rec["website"] = found
                rec["evidence"].append({"kind": "website_lookup",
                                        "detail": "Website matched by name search", "url": found})
        domain = mf.domain_of(rec.get("website"))
        if not domain:
            continue
        if domain not in cache:
            if fetched >= max_sites:
                continue
            cache[domain] = read_site(rec["website"], fetcher, max_pages, max_scripts)
            fetched += 1
        rec["site"] = cache[domain]

    block["enrich"] = {"sites_fetched": fetched,
                       "sites_failed": sum(1 for s in cache.values() if s["error"])}
    return block


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile")
    ap.add_argument("--data", default="data")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)

    try:
        profile: Profile = load_profile(args.profile)
    except ProfileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    layout = Layout(Path(args.data))
    src = layout.for_date("discovered", args.date)
    if not src.is_file():
        print(f"ERROR: discovered records not found: {src}", file=sys.stderr)
        return 1
    doc = json.loads(src.read_text(encoding="utf-8"))
    fetcher = Fetcher(user_agent=profile.http.get("user_agent", DEFAULT_USER_AGENT),
                      min_interval=float(profile.http.get("min_interval_seconds", 1.0)))

    for name, block in doc["searches"].items():
        enrich_search(block, profile.search(name), fetcher)
        e = block["enrich"]
        print(f"  {name}: fetched {e['sites_fetched']} sites, {e['sites_failed']} failed",
              file=sys.stderr)

    out = layout.for_date("enriched", doc["date"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
