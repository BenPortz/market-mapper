"""Deterministic normalization, matching, and hard-filter logic.

Pure functions, no I/O, no network, no LLM. The same records always yield the
same accounts, filter results, and scores. Rules are applied here so the LLM
stage only sees accounts that have already cleared them.

All policy comes from the search definition in the profile (see
`config/profile.example.yaml`): the region, the keywords that make a company
relevant, the terms that disqualify it, and which signals count.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from typing import Any

# --- Names and domains ----------------------------------------------------

_SUFFIXES = {
    "llc", "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "pc", "pllc", "pa", "lp", "llp", "plc", "gmbh", "the",
}
_BATCH_SUFFIX_RE = re.compile(r"\s*\((?:[A-Z]\d{2}|[A-Z][a-z]+\s\d{4})\)\s*$")


def normalize_name(name: str) -> str:
    """Comparison key for a company name.

    Registries say "92ND TERRACE DENTAL LLC", maps say "92nd Terrace Dental",
    websites say "92nd Terrace Dental, Inc." They are the same company, and
    without this they become three accounts.
    """
    n = _BATCH_SUFFIX_RE.sub("", name or "").lower().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9]+", n)
    return " ".join(t for t in tokens if t not in _SUFFIXES)


_KEEP_UPPER = {"LLC", "PC", "PLLC", "PA", "LLP", "LP", "DDS", "DMD", "MD", "DVM", "II",
               "III", "IV", "USA", "US", "UK", "CNC", "HVAC"}


def display_name(name: str) -> str:
    """Readable name. Registry data is often ALL CAPS ("OSPREY POINT DENTAL CARE, PC")."""
    name = _BATCH_SUFFIX_RE.sub("", (name or "").strip())
    if not name.isupper():
        return name
    return re.sub(r"[A-Za-z']+", lambda m: m.group(0).upper()
                  if m.group(0).upper() in _KEEP_UPPER else m.group(0).capitalize(), name)


def domain_of(url: str | None) -> str | None:
    """Registrable-looking host without `www.`, or None."""
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host or None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# --- Region ---------------------------------------------------------------

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


def region_code(value: str | None) -> str | None:
    """"Oregon" and "OR" both become "OR". Unknown values pass through upper-cased."""
    if not value:
        return None
    v = value.strip()
    return US_STATES.get(v.lower(), v.upper())


def region_status(address: dict[str, Any] | None, region: dict[str, Any],
                  text: str = "") -> str:
    """"in", "out", or "unknown" for one account against a search region.

    An address is the strongest evidence. Without one (typical for web search
    results), text that names the region's places counts as "in". Anything else
    is "unknown", which the search's `region_strict` setting decides.
    """
    address = address or {}
    states = {region_code(s) for s in region.get("regions", [])}
    countries = {c.upper() for c in region.get("countries", [])}
    country = (address.get("country") or "").upper()
    code = region_code(address.get("region"))

    if countries and country and country not in countries:
        return "out"
    if states and code:
        return "in" if code in states else "out"
    if not states and countries and country:
        return "in"

    terms = region.get("site_terms", [])
    if text and terms and any(
        re.search(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])", text) for t in terms
    ):
        return "in"
    return "unknown"


# --- Relevance and signals ------------------------------------------------


def pattern_hits(text: str, patterns: list[str]) -> list[str]:
    """Patterns (regex, case-insensitive) that match somewhere in the text."""
    return [p for p in patterns if re.search(p, text or "", re.I)]


def signals_for(account: dict[str, Any], policy: dict[str, Any],
                run_date: str) -> list[dict[str, Any]]:
    """Timing signals worth mentioning in outreach, derived from evidence.

    `policy` accepts:
        registered_within_days: int  -> a registry entry this recent means a new business
        site_patterns: [regex, ...]  -> phrases on the site like "now open" or "new facility"
        hiring: true                 -> job postings imported from a board count
    """
    out: list[dict[str, Any]] = []
    today = dt.date.fromisoformat(run_date)

    if days := policy.get("registered_within_days"):
        for ev in account.get("evidence", []):
            if ev.get("kind") == "registered" and ev.get("date"):
                try:
                    age = (today - dt.date.fromisoformat(ev["date"][:10])).days
                except ValueError:
                    continue
                if 0 <= age <= int(days):
                    out.append({"kind": "recently_registered",
                                "detail": f"Registered {ev['date'][:10]} ({age} days ago)",
                                "url": ev.get("url", "")})

    site = account.get("site") or {}
    for pattern in policy.get("site_patterns", []):
        m = re.search(pattern, site.get("text", ""), re.I)
        if m:
            out.append({"kind": "site_mention", "detail": f'Site says "{m.group(0)}"',
                        "url": site.get("url", "")})

    if policy.get("hiring"):
        for ev in account.get("evidence", []):
            if ev.get("kind") == "hiring":
                out.append({"kind": "hiring", "detail": ev.get("detail", "Open posting"),
                            "url": ev.get("url", "")})

    # Two registry entries for one practice would otherwise count as two signals.
    unique: dict[str, dict[str, Any]] = {}
    for s in out:
        unique.setdefault(s["detail"], s)
    return list(unique.values())


# --- Filters --------------------------------------------------------------


def is_excluded(account: dict[str, Any], filter_cfg: dict[str, Any]) -> bool:
    """Current customers, competitors, and anyone else on the do-not-contact list."""
    names = {normalize_name(n) for n in filter_cfg.get("exclude_companies", [])}
    domains = {domain_of(d) for d in filter_cfg.get("exclude_domains", [])}
    return normalize_name(account.get("name", "")) in names or \
        (account.get("domain") is not None and account["domain"] in domains)


def relevance_text(account: dict[str, Any]) -> str:
    """Everything known about what a company does, in its own words or a source's.

    Search snippets are included because a site that blocks fetching often still
    has a snippet saying what the company makes and where.
    """
    site = account.get("site") or {}
    return "\n".join([
        account.get("name", ""),
        " ".join(account.get("categories", [])),
        site.get("title", ""), site.get("description", ""), site.get("text", ""),
        *(ev.get("snippet", "") for ev in account.get("evidence", [])),
    ])


def evaluate(account: dict[str, Any], search: dict[str, Any],
             filter_cfg: dict[str, Any]) -> dict[str, bool]:
    """Apply every hard filter to one merged account, one boolean per filter."""
    text = relevance_text(account)
    include = search.get("include_any", [])
    region = account.get("region_status", "unknown")
    return {
        "in_region": region == "in" or (region == "unknown" and not search.get("region_strict")),
        "relevant": not include or bool(pattern_hits(text, include)),
        "no_exclude_terms": not pattern_hits(text, search.get("exclude_any", [])),
        "not_excluded": not is_excluded(account, filter_cfg),
        "signal_ok": len(account.get("signals", [])) >= int(search.get("min_signals", 0)),
        "has_website": bool(account.get("website")) or not search.get("require_website"),
    }


def passed(filters: dict[str, bool], load_bearing: list[str]) -> bool:
    """True when every load-bearing filter passed.

    Filters outside `load_bearing` are still reported so the judge and the
    report can cite them, but they do not by themselves drop an account.
    """
    return all(filters.get(name, False) for name in load_bearing)


def score(account: dict[str, Any], search: dict[str, Any]) -> int:
    """Deterministic rank used to decide which accounts the judge reads first.

    Timing signals weigh most, then how strongly the company's own words match
    the search, then how much independent evidence exists for it.
    """
    site = account.get("site") or {}
    hits = len(pattern_hits(relevance_text(account), search.get("include_any", [])))
    return (3 * len(account.get("signals", []))
            + min(hits, 5)
            + (2 if site.get("text") else 0)
            + (1 if len(account.get("sources", [])) > 1 else 0)
            + (1 if account.get("region_status") == "in" else 0))
