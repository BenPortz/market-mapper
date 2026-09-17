"""BUYERS stage: who buys from the companies on the list, from public purchase records.

Most B2B sales leave no public trace. Government purchases are the exception:
federal contract awards are published on USAspending.gov, and many cities,
counties, and states publish their contracts on open data portals. For
industries that sell to utilities, public works, the military, or schools
(water valves, pipe, pumps, safety equipment), that record is a real map of who
buys what, from whom, for how much.

The stage answers three questions for each search:
- **For each company on the list:** which public buyers has it sold to?
- **For the market:** which buyers spend the most on this kind of product?
- **Who else those buyers use:** vendors that are not on the list, which are
  usually distributors or competitors, and both matter to a salesperson.

It reads the deliverable list (after the judge, if it ran) and never changes it.

Search keys used:
    buyers:
      years: 5
      keywords: ["valve", "backflow", "hydrant"]        # product words for market searches
      sources:
        - type: usaspending                              # federal contract awards, no key
          naics: ["332911", "332913", "332919"]
          states: ["IL", "IN"]                           # place of performance
          by_company: true                               # also look up each listed company
          max_awards: 200
        - type: socrata                                  # any Socrata open data portal
          label: "City of Chicago contracts"
          buyer: "City of Chicago"
          domain: data.cityofchicago.org
          dataset: rsxa-ify5
          fields: {vendor: vendor_name, unit: department, description: description,
                   amount: award_amount, date: start_date, id: purchase_order_contract_number}

Usage:
    python -m marketmapper.buyers
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from marketmapper import filters as mf
from marketmapper.config import Layout, Profile, ProfileError, load_profile
from marketmapper.net import DEFAULT_USER_AGENT, Fetcher, NetError

USASPENDING = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
CONTRACT_TYPES = ["A", "B", "C", "D"]   # definitive contracts, purchase orders, delivery orders, BPA calls
# Words too common in this kind of name to identify a company on their own. They
# still have to be present for a match; they just cannot be the whole match.
_GENERIC = {"and", "the", "of", "valve", "valves", "company", "manufacturing", "mfg", "products", "product",
            "industries", "industrial", "industry", "corp", "corporation", "co", "group", "systems", "system",
            "controls", "control", "usa", "america", "american", "international", "division", "llc", "inc",
            "technologies", "technology", "solutions", "equipment", "supply", "service", "services"}


class BuyerSourceError(RuntimeError):
    pass


# --- matching and analysis (pure) -----------------------------------------


_NOISE = {"and", "the", "of", "a", "an"}
_ABBREVIATIONS = {"mfg": "manufacturing", "mfr": "manufacturing", "mfrs": "manufacturing",
                  "intl": "international", "svc": "service", "svcs": "service", "sys": "system",
                  "tech": "technology", "technologies": "technology", "prods": "product", "assoc": "associates"}


def name_tokens(name: str) -> set[str]:
    """Comparable words of a company name: suffixes and filler dropped, abbreviations
    expanded, plurals folded ("VALVES" and "Valve" are the same word)."""
    words = mf.normalize_name(name).replace("-", " ").split()
    out = set()
    for w in words:
        if w in _NOISE:
            continue
        w = _ABBREVIATIONS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def vendor_pattern(name: str) -> str:
    """A LIKE pattern from a company's distinctive words, in order: "%VAL%MATIC%".

    Used only to *retrieve* candidate records. Procurement systems punctuate and
    abbreviate names differently, so the pattern is deliberately broad; the precise
    decision is made by `match_vendor`.
    """
    words = [w for w in mf.normalize_name(name).replace("-", " ").split() if w not in _GENERIC and len(w) >= 3]
    return "%" + "%".join(soql_literal(w) for w in words) + "%" if words else ""


def match_vendor(vendor: str, companies: dict[str, str]) -> str | None:
    """account_id of the listed company a purchase record's vendor refers to, or None.

    A vendor matches only when it contains *every* word of the company's name
    (after suffixes, filler, abbreviations, and plurals are normalized), so
    "VAL-MATIC VALVE & MFG CORP" matches "Val-Matic Valve & Mfg. Corp." but
    "DDB CHICAGO INC" does not match "Chicago Valves & Controls" and "MEMORIAL
    SLOAN-KETTERING" does not match "Sloan Valve". A name made only of generic
    words ("Valve Company") must match exactly. When several companies match,
    the one with the longest name wins.
    """
    v_tokens = name_tokens(vendor)
    best, best_len = None, 0
    for aid, name in companies.items():
        tokens = name_tokens(name)
        if not tokens:
            continue
        if not (tokens - _GENERIC):
            hit = tokens == v_tokens
        else:
            hit = tokens <= v_tokens
        if hit and len(tokens) > best_len:
            best, best_len = aid, len(tokens)
    return best


def buyer_label(p: dict[str, Any]) -> str:
    """"Department of Defense / Defense Logistics Agency": the unit is who actually buys."""
    unit = (p.get("buyer_unit") or "").strip()
    return p["buyer"] if not unit or unit.lower() == p["buyer"].lower() else f"{p['buyer']} / {unit}"


def summarize(purchases: list[dict[str, Any]], companies: dict[str, str]) -> dict[str, Any]:
    """Totals by listed company, by buyer, and for vendors not on the list."""
    by_company: dict[str, dict[str, Any]] = {}
    buyers: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0.0, "count": 0, "vendors": set()})
    others: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0.0, "count": 0, "buyers": set()})

    for p in purchases:
        amount = float(p.get("amount") or 0)
        buyer = buyer_label(p)
        b = buyers[buyer]
        b["total"] += amount
        b["count"] += 1
        aid = p.get("vendor_account_id")
        b["vendors"].add(companies[aid] if aid else p["vendor"])
        if aid:
            c = by_company.setdefault(aid, {"name": companies[aid], "total": 0.0, "count": 0, "buyers": defaultdict(float)})
            c["total"] += amount
            c["count"] += 1
            c["buyers"][buyer] += amount
        else:
            o = others[p["vendor"]]
            o["total"] += amount
            o["count"] += 1
            o["buyers"].add(buyer)

    def ranked(d: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
        rows = [{"name": k, "total": round(v["total"], 2), "count": v["count"],
                 key: sorted(v[key])} for k, v in d.items()]
        return sorted(rows, key=lambda r: (-r["total"], r["name"]))

    return {
        "by_company": {aid: {"name": c["name"], "total": round(c["total"], 2), "count": c["count"],
                             "buyers": sorted(({"buyer": k, "total": round(v, 2)} for k, v in c["buyers"].items()),
                                              key=lambda x: -x["total"])}
                       for aid, c in by_company.items()},
        "top_buyers": ranked(buyers, "vendors"),
        "other_vendors": ranked(others, "buyers"),
    }


# --- sources --------------------------------------------------------------


def _usaspending_rows(fetcher, filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows, page = [], 1
    while len(rows) < limit:
        body = {"filters": filters, "limit": min(100, limit - len(rows)), "page": page,
                "sort": "Award Amount", "order": "desc",
                "fields": ["Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
                           "Award Amount", "Description", "Start Date", "generated_internal_id"]}
        data = fetcher.post_json(USASPENDING, json_body=body)
        # `messages` is informational and present on every response; `detail` is an error.
        if data.get("detail"):
            raise BuyerSourceError(f"usaspending: {data['detail']}")
        batch = data.get("results") or []
        rows += batch
        if not batch or not (data.get("page_metadata") or {}).get("hasNext"):
            break
        page += 1
    return rows


def usaspending(spec: dict[str, Any], search: dict[str, Any], companies: dict[str, str], fetcher,
                today: dt.date) -> list[dict[str, Any]]:
    years = int((search.get("buyers") or {}).get("years", 5))
    base = {"award_type_codes": CONTRACT_TYPES,
            "time_period": [{"start_date": (today - dt.timedelta(days=365 * years)).isoformat(),
                             "end_date": today.isoformat()}]}
    limit = int(spec.get("max_awards", 200))
    queries: list[tuple[str, dict[str, Any]]] = []

    market = dict(base)
    if spec.get("naics"):
        market["naics_codes"] = {"require": [str(n) for n in spec["naics"]]}
    if spec.get("states"):
        market["place_of_performance_locations"] = [{"country": "USA", "state": s} for s in spec["states"]]
    if keywords := (search.get("buyers") or {}).get("keywords"):
        market["keywords"] = keywords
    if len(market) > len(base):
        queries.append(("market", market))
    if spec.get("by_company", True):
        for aid, name in companies.items():
            words = vendor_pattern(name).strip("%").replace("%", " ")
            if words:
                queries.append((aid, {**base, "recipient_search_text": [words]}))

    out, seen = [], set()
    for purpose, filters in queries:
        for row in _usaspending_rows(fetcher, filters, limit):
            key = row.get("generated_internal_id") or row.get("Award ID")
            vendor = row.get("Recipient Name") or ""
            matched = match_vendor(vendor, companies)
            # Name search is fuzzy ("Watts" also finds "Contrack Watts"). A company
            # lookup keeps only records that are really that company; the market
            # query keeps everything, because other vendors are the point there.
            if purpose != "market" and matched != purpose:
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source": "usaspending",
                "buyer": row.get("Awarding Agency") or "Unknown federal agency",
                "buyer_unit": row.get("Awarding Sub Agency") or "",
                "vendor": vendor,
                "vendor_account_id": matched,
                "description": (row.get("Description") or "")[:300],
                "amount": row.get("Award Amount"),
                "date": row.get("Start Date"),
                "award_id": row.get("Award ID"),
                "url": f"https://www.usaspending.gov/award/{key}" if row.get("generated_internal_id") else "",
            })
    return out


_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_DATASET = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
_FIELD = re.compile(r"^[a-z_][a-z0-9_]*$")


def soql_literal(text: str) -> str:
    """A safe SoQL string literal for a LIKE pattern: plain characters only, quotes doubled."""
    cleaned = re.sub(r"[^A-Za-z0-9 &.,/-]", "", text).strip().upper()
    return cleaned.replace("'", "''")


def socrata_query(spec: dict[str, Any], keywords: list[str], companies: dict[str, str],
                  since: dt.date) -> tuple[str, dict[str, str]]:
    domain, dataset, fields = spec.get("domain", ""), spec.get("dataset", ""), spec.get("fields") or {}
    if not _DOMAIN.match(domain) or not _DATASET.match(dataset):
        raise BuyerSourceError("socrata needs a plain domain and a dataset id like abcd-1234")
    for role in ("vendor", "description", "amount"):
        if not _FIELD.match(fields.get(role, "")):
            raise BuyerSourceError(f"socrata fields.{role} must be a plain column name")
    for role in ("unit", "date", "id"):
        if fields.get(role) and not _FIELD.match(fields[role]):
            raise BuyerSourceError(f"socrata fields.{role} must be a plain column name")

    terms = [f"upper({fields['description']}) like '%{soql_literal(k)}%'" for k in keywords if soql_literal(k)]
    terms += [f"upper({fields['vendor']}) like '{pattern}'"
              for pattern in sorted({vendor_pattern(n) for n in companies.values()} - {""})]
    if not terms:
        raise BuyerSourceError("socrata needs buyers.keywords or companies to search for")
    where = "(" + " OR ".join(terms) + ")"
    if fields.get("date"):
        where += f" AND {fields['date']} >= '{since.isoformat()}T00:00:00'"
    params = {"$where": where, "$limit": str(int(spec.get("max_rows", 1000)))}
    if fields.get("date"):
        params["$order"] = f"{fields['date']} DESC"
    return f"https://{domain}/resource/{dataset}.json", params


def socrata(spec: dict[str, Any], search: dict[str, Any], companies: dict[str, str], fetcher,
            today: dt.date) -> list[dict[str, Any]]:
    cfg = search.get("buyers") or {}
    since = today - dt.timedelta(days=365 * int(cfg.get("years", 5)))
    url, params = socrata_query(spec, cfg.get("keywords") or [], companies, since)
    rows = fetcher.get_json(url, params=params)
    if not isinstance(rows, list):
        raise BuyerSourceError(f"socrata: unexpected response from {spec['domain']}")
    f = spec["fields"]
    out = []
    for row in rows:
        vendor = str(row.get(f["vendor"], "")).strip()
        if not vendor:
            continue
        try:
            amount = float(row.get(f["amount"]) or 0)
        except (TypeError, ValueError):
            amount = 0.0
        out.append({
            "source": "socrata",
            "buyer": spec.get("buyer") or spec["domain"],
            "buyer_unit": str(row.get(f.get("unit", ""), "") or ""),
            "vendor": vendor,
            "vendor_account_id": match_vendor(vendor, companies),
            "description": str(row.get(f["description"], "") or "")[:300],
            "amount": amount,
            "date": str(row.get(f.get("date", ""), "") or "")[:10],
            "award_id": str(row.get(f.get("id", ""), "") or ""),
            "url": f"https://{spec['domain']}/d/{spec['dataset']}",
        })
    return out


SOURCES = {"usaspending": usaspending, "socrata": socrata}


# --- stage ----------------------------------------------------------------


def listed_companies(block: dict[str, Any], verdict: dict[str, Any] | None) -> dict[str, str]:
    from marketmapper.report import deliverable
    return {a["account_id"]: a["name"] for a, _ in deliverable(block, verdict, block.get("goal", "top_n"))}


def run_search(name: str, search: dict[str, Any], companies: dict[str, str], fetcher,
               today: dt.date) -> dict[str, Any]:
    purchases, log = [], []
    for spec in (search.get("buyers") or {}).get("sources", []):
        kind = spec.get("type")
        label = spec.get("label", kind)
        try:
            if kind not in SOURCES:
                raise BuyerSourceError(f"unknown buyer source '{kind}'")
            found = SOURCES[kind](spec, search, companies, fetcher, today)
            purchases += found
            log.append({"type": kind, "label": label, "status": "ok", "count": len(found)})
        except (BuyerSourceError, NetError, ValueError, KeyError) as e:
            log.append({"type": kind, "label": label, "status": "failed", "count": 0, "error": str(e)})
    ok = sum(s["status"] == "ok" for s in log)
    return {
        "status": "ok" if ok == len(log) else ("partial" if ok else "failed"),
        "sources": log,
        "companies_checked": len(companies),
        "purchases": sorted(purchases, key=lambda p: (-(float(p.get("amount") or 0)), p["vendor"])),
        "summary": summarize(purchases, companies),
    }


def main(argv: list[str] | None = None, fetcher=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--search", action="append")
    ap.add_argument("--data", default="data")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)

    try:
        profile: Profile = load_profile(args.profile)
    except ProfileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    layout = Layout(Path(args.data))
    apath = layout.for_date("accounts", args.date)
    if not apath.is_file():
        print(f"ERROR: accounts not found: {apath}", file=sys.stderr)
        return 1
    accounts = json.loads(apath.read_text(encoding="utf-8"))
    vpath = layout.for_date("verdicts", accounts["date"])
    verdicts = json.loads(vpath.read_text(encoding="utf-8")) if vpath.is_file() else {"searches": {}}

    names = [n for n in (args.search or accounts["searches"])
             if n in accounts["searches"] and profile.search(n).get("buyers")]
    if not names:
        print("No searches with a buyers section; nothing to do.", file=sys.stderr)
        return 0

    fetcher = fetcher or Fetcher(user_agent=profile.http.get("user_agent", DEFAULT_USER_AGENT),
                                 min_interval=float(profile.http.get("min_interval_seconds", 1.0)))
    today = dt.date.fromisoformat(accounts["date"])
    out = {"date": accounts["date"], "searches": {}}
    for n in names:
        companies = listed_companies(accounts["searches"][n], verdicts["searches"].get(n))
        block = run_search(n, profile.search(n), companies, fetcher, today)
        out["searches"][n] = block
        s = block["summary"]
        print(f"  {n}: {block['status']} | {len(block['purchases'])} purchases | "
              f"{len(s['by_company'])} of {len(companies)} listed companies have public buyers", file=sys.stderr)
        for src in block["sources"]:
            if src["status"] != "ok":
                print(f"    {src['label']}: {src['error']}", file=sys.stderr)

    path = layout.for_date("buyers", out["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
