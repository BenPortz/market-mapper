"""Company websites from a web search API.

The most general source: "conveyor belt manufacturer Ohio" finds manufacturers
no registry or map lists. Each query template is expanded once per place in the
search region, every result is reduced to its domain, and domains that are
directories, marketplaces, or social networks are dropped. What is left is a
list of candidate company websites for the enricher to read.

Providers (set the key in the environment, never in the profile):
    brave   -> BRAVE_API_KEY
    tavily  -> TAVILY_API_KEY

Spec:
    type: web_search
    provider: brave
    queries: ["conveyor belt manufacturer {place}", "belt conveyor systems {place}"]
    pages: 2                      # result pages per query (brave only)
    skip_domains: ["example-directory.com"]   # added to the built-in list

Region keys used:
    search_places: ["Ohio", "Michigan"]       # substituted for {place}
"""

from __future__ import annotations

import os
import re
from typing import Any

from marketmapper import filters as mf

# Aggregators rank well for every industry query and are never the company itself.
# Directories are still useful: export their listings to CSV and use the csv source.
DEFAULT_SKIP_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "yelp.com", "yellowpages.com", "bbb.org", "mapquest.com",
    "thomasnet.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "zoominfo.com",
    "dnb.com", "manta.com", "crunchbase.com", "amazon.com", "ebay.com", "alibaba.com",
    "made-in-china.com", "reddit.com", "quora.com", "pinterest.com", "google.com",
}

_TITLE_SPLIT = re.compile(r"\s+[|\-–—:]\s+")


def name_from_title(title: str, domain: str) -> str:
    """Best guess at a company name from a result title.

    Titles look like "Belt Conveyors | Acme Conveyor Co." or "Acme - Home". The
    segment sharing the most characters with the domain is usually the name.
    """
    parts = [p.strip() for p in _TITLE_SPLIT.split(title or "") if p.strip()]
    stem = domain.split(".")[0].replace("-", "")
    if not parts:
        return stem.title()
    return max(parts, key=lambda p: (sum(1 for t in re.findall(r"[a-z0-9]+", p.lower())
                                         if t in stem), -len(p)))


def is_skipped(domain: str, extra: set[str]) -> bool:
    skip = DEFAULT_SKIP_DOMAINS | extra
    return any(domain == d or domain.endswith("." + d) for d in skip)


def _brave(fetcher, query: str, page: int) -> list[dict[str, str]]:
    from marketmapper.sources import SourceError
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        raise SourceError("BRAVE_API_KEY is not set")
    data = fetcher.get_json("https://api.search.brave.com/res/v1/web/search",
                            params={"q": query, "count": 20, "offset": page},
                            headers={"X-Subscription-Token": key, "Accept": "application/json"})
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("description", "")} for r in (data.get("web") or {}).get("results", [])]


def _tavily(fetcher, query: str, page: int) -> list[dict[str, str]]:
    from marketmapper.sources import SourceError
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise SourceError("TAVILY_API_KEY is not set")
    if page > 0:
        return []  # no paging; one call returns up to max_results
    data = fetcher.post_json("https://api.tavily.com/search",
                             json_body={"query": query, "max_results": 20},
                             headers={"Authorization": f"Bearer {key}"})
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("content", "")} for r in data.get("results", [])]


PROVIDERS = {"brave": _brave, "tavily": _tavily}


def search(fetcher, provider: str, query: str, page: int = 0) -> list[dict[str, str]]:
    from marketmapper.sources import SourceError
    if provider not in PROVIDERS:
        raise SourceError(f"unknown search provider '{provider}'")
    return PROVIDERS[provider](fetcher, query, page)


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError, make_record

    queries = spec.get("queries") or []
    if not queries:
        raise SourceError("web_search needs at least one query")
    places = region.get("search_places") or [region.get("label", "")]
    provider = spec.get("provider", "brave")
    extra = {d.lower() for d in spec.get("skip_domains", [])}
    out: dict[str, dict[str, Any]] = {}

    for template in queries:
        for place in places if "{place}" in template else [None]:
            query = template.replace("{place}", place or "").strip()
            for page in range(int(spec.get("pages", 1))):
                results = search(fetcher, provider, query, page)
                for res in results:
                    domain = mf.domain_of(res["url"])
                    if not domain or is_skipped(domain, extra):
                        continue
                    evidence = {"kind": "search_result", "detail": f'"{query}"',
                                "url": res["url"], "snippet": res.get("snippet", "")[:300]}
                    if domain in out:
                        out[domain]["evidence"].append(evidence)
                        continue
                    out[domain] = make_record(
                        "web_search", domain, name_from_title(res["title"], domain),
                        website=f"https://{domain}/", evidence=[evidence],
                    )
                if not results:
                    break
    return list(out.values())
