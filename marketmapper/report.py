"""WRITE stage: accounts + verdicts -> report, company list CSV, outreach queue CSV, index row.

A deterministic render. The judge writes the judgment (what a company does,
why it fits, the risks, an optional draft) as data; this module formats it, so
report structure never drifts and a layout change costs no model calls.

Outputs per search, under `data/exports/<date>/`:
    <search>-accounts.csv   the deliverable list, ready for a CRM or a spreadsheet
    <search>-queue.csv      drafts only, every row status=draft

A top-N search exports the accounts the judge kept. A market map exports every
account that passed the filters, minus any the judge rejected as not actually
in the market.

Verdicts are optional: a market map with `judge.limit: 0` renders from the
accounts file alone.

Usage:
    python -m marketmapper.report
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path
from typing import Any

from marketmapper.config import Layout, Profile, ProfileError, load_profile

SCHEMA = Path(__file__).parent / "schemas" / "verdicts.schema.json"
MARKET_MAP_PREVIEW = 25
NONE_REASON_CAP = 50

INDEX_HEADER = (
    "# Market Mapper: Index\n\n"
    "One row per run. `Contacted:` tracks which accounts (if any) someone reached out to.\n\n"
    "| Date | Searches | Results | Contacted |\n"
    "|------|----------|---------|-----------|\n"
)

ACCOUNT_COLUMNS = [
    "rank", "account_id", "name", "website", "street", "city", "region", "postal_code",
    "country", "phone", "employees", "size_band", "categories", "sources", "signals", "score",
    "region_status",
    "fit", "fit_score", "what_they_do",
]
QUEUE_COLUMNS = [
    "date", "search", "account_id", "name", "fit", "fit_score", "contact_role",
    "channel", "subject", "body", "evidence_urls", "status",
]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [v for v in value if v]
    return [value] if value else []


def _location(address: dict[str, Any]) -> str:
    return ", ".join(filter(None, [address.get("city"), address.get("region"),
                                   address.get("country")]))


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# --- selection ------------------------------------------------------------


def deliverable(block: dict[str, Any], verdict: dict[str, Any] | None,
                goal: str) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """(account, judgment) pairs that make up this search's result, in order."""
    by_id = {a["account_id"]: a for a in block.get("accounts", [])}
    judged = {j["account_id"]: j for j in (verdict or {}).get("accounts", [])}
    rejected = {r["account_id"] for r in (verdict or {}).get("rejected", [])}

    if goal == "top_n":
        if verdict is not None:
            return [(by_id[j["account_id"]], j) for j in verdict.get("accounts", [])
                    if j["account_id"] in by_id]
        passed = [a for a in block.get("accounts", []) if a["passed"]]
        return [(a, None) for a in passed[: block.get("target_count") or len(passed)]]

    rows = [(a, judged.get(a["account_id"])) for a in block.get("accounts", [])
            if a["passed"] and a["account_id"] not in rejected]
    # Judged accounts first, best fit first; unjudged ones keep their filter-score order.
    # sorted() is stable, so ties keep the filter ranking.
    return sorted(rows, key=lambda r: (r[1] is None, -(r[1] or {}).get("fit_score", 0)))


# --- report ---------------------------------------------------------------


def funnel_line(block: dict[str, Any], verdict: dict[str, Any] | None) -> str:
    srcs = ", ".join(f"{s['type']} {s['count']}" if s["status"] == "ok" else f"{s['type']} FAILED"
                     for s in block.get("sources", []))
    n_pass = sum(1 for a in block.get("accounts", []) if a["passed"])
    parts = [f"{block.get('record_count', 0)} records", f"{len(block.get('accounts', []))} companies",
             f"{n_pass} passed filters"]
    if verdict is not None:
        parts += [f"{len(block.get('judge_queue', []))} judged",
                  f"{len(verdict.get('accounts', []))} kept",
                  f"{len(verdict.get('rejected', []))} rejected"]
    return f"Sources: {srcs or 'none'}. " + " → ".join(parts) + "."


def render_account(acct: dict[str, Any], j: dict[str, Any]) -> str:
    lines = [f"### {acct['name']}, **{j['fit'].upper()}** (fit {j['fit_score']}/5)\n"]
    meta = [m for m in (_location(acct.get("address") or {}), acct.get("website")) if m]
    if meta:
        lines.append(f"- {' · '.join(meta)}")
    lines.append(f"- **What they do:** {j['what_they_do']}")
    if why := j.get("why_fit"):
        lines.append("- **Why they fit:**")
        for item in why:
            ref = f" ({item['context_id']})" if item.get("context_id") else ""
            lines.append(f"  - {item['text']}{ref}")
    if signals := j.get("signals"):
        lines.append("- **Timing:**")
        lines.extend(f"  - [{s['detail']}]({s['url']})" if s.get("url") else f"  - {s['detail']}"
                     for s in signals)
    # Risks are full sentences; more than one gets its own bullets.
    if risks := _as_list(j.get("risks")):
        if len(risks) == 1:
            lines.append(f"- **Risks:** {risks[0]}")
        else:
            lines.append("- **Risks:**")
            lines.extend(f"  - {r}" for r in risks)
    if role := j.get("contact_role"):
        lines.append(f"- **Reach:** {role}")
    if draft := j.get("draft"):
        lines.append(f"- **Draft ({draft.get('channel', 'email')}, not sent):**\n")
        if subject := draft.get("subject"):
            lines.append(f"  > **Subject:** {subject}\n  >")
        lines.extend(f"  > {ln}" if ln else "  >" for ln in draft["body"].splitlines())
    lines.append("")
    return "\n".join(lines)


def render_market_table(rows: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> str:
    lines = ["| # | Company | Location | Size | Website | Signals |", "|---|---|---|---|---|---|"]
    for i, (a, _) in enumerate(rows[:MARKET_MAP_PREVIEW], start=1):
        sig = "; ".join(s["detail"] for s in a.get("signals", [])) or ""
        site = a.get("domain") or ""
        size = a.get("size_band") if a.get("size_band") not in (None, "unknown") else ""
        if a.get("employees") is not None:
            size = f"{a['employees']} employees"
        lines.append(f"| {i} | {a['name']} | {_location(a.get('address') or {})} | {size} | {site} | {sig} |")
    if len(rows) > MARKET_MAP_PREVIEW:
        lines.append(f"\n_Showing {MARKET_MAP_PREVIEW} of {len(rows)}. The full list is in the CSV._")
    return "\n".join(lines) + "\n"


def coverage_line(bench: dict[str, Any] | None, found: int) -> str | None:
    """How the list compares with the Census count of establishments, or why it cannot."""
    if not bench:
        return None
    if bench.get("status") != "ok":
        return f"- **Coverage check did not run:** {bench.get('error', 'unknown error').rstrip('.')}."
    codes = ", ".join(bench.get("naics", {}))
    total = bench.get("total", 0)
    return (f"- **Coverage check:** Census County Business Patterns ({bench.get('year')}) counts "
            f"**{total}** establishments in NAICS {codes} across {bench.get('county_count')} counties, "
            f"{bench.get('under_20', 0)} of them with fewer than 20 employees. This list has **{found}**. "
            f"Census counts locations rather than companies and covers every product in those codes, "
            f"so the real target is smaller than {total}.")


def render_section(name: str, block: dict[str, Any], verdict: dict[str, Any] | None,
                   profile: Profile, date: str, bench: dict[str, Any] | None = None) -> str:
    goal = block.get("goal", "top_n")
    target = block.get("target_count")
    lines = [f"## {profile.label(name)}\n",
             f"_Goal: {'find the best ' + str(target) if goal == 'top_n' else 'map the whole market'}_\n",
             funnel_line(block, verdict), ""]
    if (cov := coverage_line(bench, len(deliverable(block, verdict, goal)))):
        lines += [cov, ""]

    failed = [s for s in block.get("sources", []) if s["status"] != "ok"]
    for s in failed:
        lines.append(f"- **Source failed ({s['type']}):** {s.get('error', 'unknown error').rstrip('.')}.")
    if failed:
        lines.append("")
    if block.get("status") == "failed":
        lines.append("_Search did not run: every source failed._\n")
        return "\n".join(lines)

    rows = deliverable(block, verdict, goal)
    csv_path = f"exports/{date}/{name}-accounts.csv"

    if goal == "top_n":
        if target and len(rows) < target:
            reason = (verdict or {}).get("none_reason") or "not enough companies cleared the filters and the judge"
            lines.append(f"**Delivered {len(rows)} of {target}.** {reason.rstrip('.')}.\n")
        if not rows:
            return "\n".join(lines)
        if verdict is None:
            lines.append("_Not judged: ranked by filter score only._\n")
            lines.append(render_market_table(rows))
        else:
            lines.extend(render_account(a, j) for a, j in rows)
    else:
        lines.append(f"**{len(rows)} companies** in `{csv_path}`.\n")
        if rows:
            lines.append(render_market_table(rows))
        if verdict and verdict.get("rejected"):
            n = len(verdict["rejected"])
            lines.append(f"_Removed by the judge as not actually in this market ({n}):_")
            lines.extend(f"- {r.get('name', r['account_id'])}: {r['reason']}" for r in verdict["rejected"])
            lines.append("")
    return "\n".join(lines)


def render_report(accounts: dict[str, Any], verdicts: dict[str, Any] | None,
                  profile: Profile, benchmark: dict[str, Any] | None = None) -> str:
    date = accounts["date"]
    weekday = dt.date.fromisoformat(date).strftime("%A")
    vsearch = (verdicts or {}).get("searches", {})
    order = [s for s in profile.searches if s in accounts["searches"]] + \
            [s for s in accounts["searches"] if s not in profile.searches]

    lines = [f"# Market Mapper: {date} ({weekday})\n",
             "Nothing in this report has been sent to anyone. Drafts are for a person to edit.\n",
             "## Summary", ""]
    for name in order:
        rows = deliverable(accounts["searches"][name], vsearch.get(name), accounts["searches"][name].get("goal", "top_n"))
        block = accounts["searches"][name]
        if block.get("status") == "failed":
            lines.append(f"- **{profile.label(name)}:** search did not run")
        elif block.get("goal") == "top_n":
            lines.append(f"- **{profile.label(name)}:** {len(rows)} of {block.get('target_count')}")
        else:
            lines.append(f"- **{profile.label(name)}:** {len(rows)} companies mapped")
    lines += ["", "---\n"]
    bsearch = (benchmark or {}).get("searches", {})
    lines += [render_section(n, accounts["searches"][n], vsearch.get(n), profile, date, bsearch.get(n))
              for n in order]
    lines.append("---")
    footer = f"**Run cost:** {(verdicts or {}).get('run_cost_note', 'not recorded')}"
    if model := (verdicts or {}).get("model"):
        footer += f" · model: {model}"
    lines.append(footer)
    return "\n".join(lines) + "\n"


# --- exports --------------------------------------------------------------


def account_rows(rows: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> list[dict[str, Any]]:
    out = []
    for i, (a, j) in enumerate(rows, start=1):
        addr = a.get("address") or {}
        out.append({
            "rank": i, "account_id": a["account_id"], "name": a["name"],
            "website": a.get("website") or "", **{k: addr.get(k) or "" for k in
                                                  ("street", "city", "region", "postal_code", "country")},
            "phone": a.get("phone") or "",
            "employees": a.get("employees") if a.get("employees") is not None else "",
            "size_band": a.get("size_band", ""),
            "categories": "; ".join(a.get("categories", [])),
            "sources": "; ".join(a.get("sources", [])),
            "signals": "; ".join(s["detail"] for s in a.get("signals", [])),
            "score": a.get("score", ""), "region_status": a.get("region_status", ""),
            "fit": (j or {}).get("fit", ""), "fit_score": (j or {}).get("fit_score", ""),
            "what_they_do": (j or {}).get("what_they_do", ""),
        })
    return out


def queue_rows(date: str, name: str, rows) -> list[dict[str, Any]]:
    out = []
    for a, j in rows:
        if not j or not j.get("draft"):
            continue
        draft = j["draft"]
        out.append({
            "date": date, "search": name, "account_id": a["account_id"], "name": a["name"],
            "fit": j["fit"], "fit_score": j["fit_score"], "contact_role": j.get("contact_role", ""),
            "channel": draft.get("channel", "email"), "subject": draft.get("subject", ""),
            "body": draft["body"], "evidence_urls": " ".join(s["url"] for s in j.get("signals", []) if s.get("url")),
            "status": "draft",
        })
    return out


def index_row(accounts: dict[str, Any], verdicts: dict[str, Any] | None, profile: Profile) -> str:
    vsearch = (verdicts or {}).get("searches", {})
    parts = []
    for name, block in accounts["searches"].items():
        rows = deliverable(block, vsearch.get(name), block.get("goal", "top_n"))
        names = ", ".join(a["name"] for a, _ in rows[:5])
        more = f" +{len(rows) - 5}" if len(rows) > 5 else ""
        if rows:
            parts.append(f"{name}: {names}{more}")
        else:
            reason = (vsearch.get(name) or {}).get("none_reason", "none")
            if len(reason) > NONE_REASON_CAP:
                reason = reason[:NONE_REASON_CAP - 2].rstrip() + "…"
            parts.append(f"{name}: none ({reason})")
    return f"| {accounts['date']} | {len(accounts['searches'])} | {' · '.join(parts)} | none |"


def upsert_index(index_path: Path, date: str, row: str) -> None:
    """Insert or replace this date's row, so re-runs never duplicate."""
    if not index_path.is_file():
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(INDEX_HEADER, encoding="utf-8")
    lines = index_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"| {date} "):
            lines[i] = row
            break
    else:
        lines.append(row)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--accounts", help="Accounts JSON (default: <data>/accounts/<date>.json)")
    ap.add_argument("--verdicts", help="Verdicts JSON (default: <data>/verdicts/<date>.json, optional)")
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
    apath = Path(args.accounts) if args.accounts else layout.for_date("accounts", args.date)
    if not apath.is_file():
        print(f"ERROR: accounts not found: {apath}", file=sys.stderr)
        return 1
    accounts = json.loads(apath.read_text(encoding="utf-8"))
    date = accounts["date"]

    vpath = Path(args.verdicts) if args.verdicts else layout.for_date("verdicts", date)
    verdicts = json.loads(vpath.read_text(encoding="utf-8")) if vpath.is_file() else None
    if verdicts is not None and not args.no_validate:
        from marketmapper.pipeline import validate
        if err := validate(verdicts, SCHEMA):
            print(f"ERROR: verdicts failed schema validation: {err}", file=sys.stderr)
            return 2

    bpath = layout.for_date("benchmark", date)
    benchmark = json.loads(bpath.read_text(encoding="utf-8")) if bpath.is_file() else None

    report_path = layout.for_date("reports", date, ".md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(accounts, verdicts, profile, benchmark), encoding="utf-8")

    export_dir = layout.exports(date)
    export_dir.mkdir(parents=True, exist_ok=True)
    vsearch = (verdicts or {}).get("searches", {})
    for name, block in accounts["searches"].items():
        rows = deliverable(block, vsearch.get(name), block.get("goal", "top_n"))
        (export_dir / f"{name}-accounts.csv").write_text(
            _csv(ACCOUNT_COLUMNS, account_rows(rows)), encoding="utf-8", newline="")
        if queue := queue_rows(date, name, rows):
            (export_dir / f"{name}-queue.csv").write_text(
                _csv(QUEUE_COLUMNS, queue), encoding="utf-8", newline="")

    upsert_index(layout.index, date, index_row(accounts, verdicts, profile))
    print(f"Wrote {report_path}, exports in {export_dir}, and updated {layout.index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
