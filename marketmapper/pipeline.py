"""FILTER stage: enriched records -> merged, filtered, ranked accounts.

The same company routinely arrives from several sources: a registry entry in
capitals with a legal suffix, a map listing with a website, a search result
under a trade name. This stage merges them into one account, derives timing
signals, applies the search's hard filters, scores what passed, and marks the
top accounts for the judge.

No browser, no network, no model. The same enriched file always produces the
same accounts file, so the judge can be re-run against a fixed set while the
prompt is being tuned.

Usage:
    python -m marketmapper.pipeline
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from marketmapper import filters as mf
from marketmapper.config import Layout, Profile, ProfileError, load_profile

SCHEMA = Path(__file__).parent / "schemas" / "accounts.schema.json"
JUDGE_TEXT_CAP = 3000  # per account, keeps the judge's context bounded


# --- merge ----------------------------------------------------------------


def _name_keys(rec: dict[str, Any]) -> list[tuple[str, str]]:
    city = mf.normalize_name((rec.get("address") or {}).get("city") or "")
    names = [rec.get("name"), rec.get("legal_name")]
    return [(mf.normalize_name(n), city) for n in names if n and mf.normalize_name(n)]


def _is_plainer(candidate: str, current: str) -> bool:
    """Same company name, minus legal suffixes and punctuation."""
    same = mf.normalize_name(candidate) == mf.normalize_name(current)
    return same and len(candidate) < len(current)


def _new_account(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": rec["name"],
        "legal_name": rec.get("legal_name"),
        "website": rec.get("website"),
        "domain": mf.domain_of(rec.get("website")),
        "address": dict(rec.get("address") or {}),
        "phone": rec.get("phone"),
        "categories": [],
        "sources": [],
        "record_ids": [],
        "evidence": [],
        "site": None,
        "coords": rec.get("coords"),
        "employees": rec.get("employees"),
    }


def _absorb(acct: dict[str, Any], rec: dict[str, Any]) -> None:
    """Fold one record into an account without losing what either knew."""
    acct["record_ids"].append(rec["record_id"])
    # "Lakeshore Conveyor Systems" reads better than "Lakeshore Conveyor Systems, Inc."
    if _is_plainer(rec["name"], acct["name"]):
        acct["legal_name"] = acct["legal_name"] or acct["name"]
        acct["name"] = rec["name"]
    if rec["source"] not in acct["sources"]:
        acct["sources"].append(rec["source"])
    acct["categories"] += [c for c in rec.get("categories", []) if c not in acct["categories"]]
    acct["evidence"] += rec.get("evidence", [])
    acct["legal_name"] = acct["legal_name"] or rec.get("legal_name")
    acct["phone"] = acct["phone"] or rec.get("phone")
    acct["coords"] = acct.get("coords") or rec.get("coords")
    # Several plants can merge into one account; the largest filing is the best size signal.
    if rec.get("employees") is not None:
        acct["employees"] = max(acct.get("employees") or 0, rec["employees"])
    if not acct["website"] and rec.get("website"):
        acct["website"], acct["domain"] = rec["website"], mf.domain_of(rec["website"])
    for k, v in (rec.get("address") or {}).items():
        acct["address"][k] = acct["address"].get(k) or v
    if rec.get("site") and not (acct["site"] and acct["site"].get("text")):
        acct["site"] = rec["site"]


def merge_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group records into accounts that share a website domain or a name in the same city.

    Grouping is transitive and order-independent: a map listing with a website,
    a registry entry carrying both a trade name and a legal name, and a list
    entry under the legal name all end up as one account, whichever arrives
    first. One rule blocks a merge: two groups with different websites are two
    companies, even when their names and cities match.
    """
    records = sorted(records, key=lambda r: r["record_id"])
    parent = list(range(len(records)))
    domains: dict[int, str | None] = {i: mf.domain_of(r.get("website")) for i, r in enumerate(records)}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri == rj or (domains[ri] and domains[rj] and domains[ri] != domains[rj]):
            return
        parent[rj] = ri
        domains[ri] = domains[ri] or domains[rj]

    # Domains first: a shared website is the strongest identity evidence, and
    # settling it first lets the different-website rule see the full picture.
    for keyer in (lambda r: [mf.domain_of(r.get("website"))] if r.get("website") else [],
                  _name_keys):
        first_seen: dict[Any, int] = {}
        for i, rec in enumerate(records):
            for key in keyer(rec):
                if key in first_seen:
                    union(first_seen[key], i)
                else:
                    first_seen[key] = i

    groups: dict[int, list[dict[str, Any]]] = {}
    for i, rec in enumerate(records):
        groups.setdefault(find(i), []).append(rec)

    accounts = []
    for members in groups.values():
        # Start from a record with a website so the account's name and URL come from it.
        members.sort(key=lambda r: (not r.get("website"), r["record_id"]))
        acct = _new_account(members[0])
        for rec in members:
            _absorb(acct, rec)
        accounts.append(acct)

    for acct in accounts:
        base = acct["domain"] or "-".join(filter(None, [
            mf.slug(mf.normalize_name(acct["name"])), mf.slug(acct["address"].get("city") or ""),
            (acct["address"].get("region") or "").lower()]))
        acct["account_id"] = mf.slug(base)
    return accounts


# --- dedup ----------------------------------------------------------------


def recent_index_text(index_path: Path, days: int, exclude_date: str = "") -> str:
    """Text of the last `days` index rows, for suppressing recently surfaced accounts.

    `exclude_date` drops that date's own row. Without it a re-run deduplicates
    against itself and silently empties its own report.
    """
    if not index_path.is_file():
        return ""
    rows = [ln for ln in index_path.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| 20") and not (exclude_date and ln.startswith(f"| {exclude_date} "))]
    return "\n".join(rows[-days:]).lower()


def seen_in(name: str, blob: str) -> bool:
    """Whole-name match, so "Hive" is not flagged by an earlier "Archive Labs"."""
    return bool(name) and re.search(
        rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", blob) is not None


# --- filter ---------------------------------------------------------------


def _for_judge(acct: dict[str, Any]) -> dict[str, Any]:
    """Trim bulky fields. The accounts file is the judge's whole view of a company."""
    site = acct.get("site")
    if site:
        acct["site"] = {k: site.get(k) for k in ("url", "title", "description", "error", "tech", "client_rendered")}
        acct["site"]["text"] = (site.get("text") or "")[:JUDGE_TEXT_CAP]
    acct["evidence"] = acct["evidence"][:10]
    return acct


def filter_search(name: str, block: dict[str, Any], profile: Profile, run_date: str,
                  recent_blob: str) -> dict[str, Any]:
    search = profile.search(name)
    accounts = merge_records(block.get("records", []))
    use_dedup = profile.dedup_enabled(name)

    for acct in accounts:
        acct["region_status"] = mf.region_status(acct["address"], search.get("region", {}),
                                                 mf.relevance_text(acct), acct.get("coords"))
        acct["signals"] = mf.signals_for(acct, search.get("signals", {}), run_date)
        acct["keyword_hits"] = mf.pattern_hits(mf.relevance_text(acct), search.get("include_any", []))
        acct["size_band"] = mf.size_band(acct)
        acct["filters"] = mf.evaluate(acct, search, profile.filters)
        acct["tech_status"] = mf.tech_statuses(acct, search)
        acct["seen_recent"] = use_dedup and seen_in(acct["name"], recent_blob)
        knockouts = profile.knockouts + (["tech_ok"] if (search.get("tech_absent") or search.get("tech_present"))
                                               and "tech_ok" not in profile.knockouts else [])
        acct["passed"] = mf.passed(acct["filters"], knockouts) and not acct["seen_recent"]
        acct["score"] = mf.score(acct, search)
        _for_judge(acct)

    accounts.sort(key=lambda a: (not a["passed"], -a["score"], a["name"].lower()))
    limit = profile.judge_limit(name)
    return {
        "status": block.get("status", "ok"),
        "goal": profile.goal(name),
        "target_count": profile.target_count(name),
        "sources": block.get("sources", []),
        "record_count": len(block.get("records", [])),
        "judge_queue": [a["account_id"] for a in accounts if a["passed"]][:limit],
        "accounts": accounts,
    }


def filter_doc(doc: dict[str, Any], profile: Profile, recent_blob: str) -> dict[str, Any]:
    """Filter every search in an enriched file. Pure, no I/O."""
    return {"date": doc["date"], "searches": {
        name: filter_search(name, block, profile, doc["date"], recent_blob)
        for name, block in doc.get("searches", {}).items()
    }}


def validate(doc: dict[str, Any], schema_path: Path) -> str | None:
    """Validate against a JSON schema. Returns an error string, or None."""
    try:
        import jsonschema
    except ImportError:
        return None  # optional dependency; absence is not a failure
    if not schema_path.is_file():
        return None
    try:
        jsonschema.validate(doc, json.loads(schema_path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001 - surfaced to the caller as text
        return str(e).split("\n")[0]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enriched", help="Enriched JSON (default: <data>/enriched/<date>.json)")
    ap.add_argument("--profile")
    ap.add_argument("--data", default="data")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args(argv)

    try:
        profile = load_profile(args.profile)
    except ProfileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    layout = Layout(Path(args.data))
    src = Path(args.enriched) if args.enriched else layout.for_date("enriched", args.date)
    if not src.is_file():
        print(f"ERROR: enriched records not found: {src}", file=sys.stderr)
        return 1
    doc = json.loads(src.read_text(encoding="utf-8"))
    blob = recent_index_text(layout.index, profile.dedup_days, exclude_date=doc["date"])
    out = filter_doc(doc, profile, blob)

    if not args.no_validate and (err := validate(out, SCHEMA)):
        print(f"ERROR: accounts failed schema validation: {err}", file=sys.stderr)
        return 2

    path = layout.for_date("accounts", out["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, block in out["searches"].items():
        n_pass = sum(a["passed"] for a in block["accounts"])
        print(f"  {name}: {block['record_count']} records -> {len(block['accounts'])} accounts "
              f"-> {n_pass} passed -> {len(block['judge_queue'])} queued for judge", file=sys.stderr)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
