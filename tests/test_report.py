"""Tests for the WRITE stage: report, CSV exports, and index.

The renderer is a pure function of the accounts and verdicts files, so these
assert on rendered text directly.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from marketmapper import pipeline as p
from marketmapper import report as r
from marketmapper.config import load_profile

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = load_profile("config/profile.example.yaml")


@pytest.fixture
def accounts() -> dict:
    enriched = json.loads((FIXTURES / "enriched_sample.json").read_text(encoding="utf-8"))
    return p.filter_doc(enriched, PROFILE, "")


@pytest.fixture
def verdicts() -> dict:
    return json.loads((FIXTURES / "verdicts_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def rendered(accounts, verdicts) -> str:
    return r.render_report(accounts, verdicts, PROFILE)


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_verdicts_fixture_validates_against_the_schema(verdicts):
    pytest.importorskip("jsonschema")
    assert p.validate(verdicts, Path("schemas/verdicts.schema.json")) is None


def test_every_verdict_account_exists_in_the_accounts_fixture(accounts, verdicts):
    # Guards the fixtures drifting apart, which would silently drop rows.
    for name, v in verdicts["searches"].items():
        ids = {a["account_id"] for a in accounts["searches"][name]["accounts"]}
        assert {j["account_id"] for j in v["accounts"] + v["rejected"]} <= ids


# --- report ---------------------------------------------------------------

def test_report_says_nothing_was_sent(rendered):
    assert "Nothing in this report has been sent" in rendered


def test_summary_reports_progress_against_each_goal(rendered):
    assert "- **Dental practices likely to buy digital x-ray (Oregon):** 2 of 20" in rendered
    assert "- **Conveyor manufacturers (Great Lakes):** 3 companies mapped" in rendered


def test_funnel_line_shows_every_stage(rendered):
    assert "Sources: npi 5, osm 3. 8 records → 7 companies → 2 passed filters → 2 judged → 2 kept → 0 rejected." in rendered


def test_shortfall_is_stated_honestly(rendered):
    assert "**Delivered 2 of 20.** Only 2 in-region practices had a timing signal" in rendered


def test_account_brief_cites_context_ids(rendered):
    assert "### Pinecone Family Dental, **STRONG** (fit 5/5)" in rendered
    assert "(dental_new_practice)" in rendered


def test_timing_signals_link_to_evidence(rendered):
    assert "[Registered 2025-11-02 (132 days ago)](https://example.invalid/npi/1000000001)" in rendered


def test_draft_is_labelled_unsent(rendered):
    assert "- **Draft (email, not sent):**" in rendered
    assert "  > **Subject:** Imaging for your new Bend practice" in rendered


def test_multiple_risks_are_bullets_and_single_risk_is_inline(rendered):
    assert "- **Risks:**\n  - A practice that is already open" in rendered
    assert "- **Risks:** A new registry entry can mean" in rendered


def test_failed_source_is_named_in_the_section(rendered):
    assert "- **Source failed (osm):** request failed" in rendered


def test_market_map_links_to_the_full_csv_and_lists_rejections(rendered):
    assert "**3 companies** in `exports/2026-03-14/conveyor_manufacturers-accounts.csv`." in rendered
    assert "- GrainMove Bulk Handling: Bulk grain and mining conveyors" in rendered


def test_market_map_preview_is_truncated(accounts):
    block = accounts["searches"]["conveyor_manufacturers"]
    template = next(a for a in block["accounts"] if a["passed"])
    block["accounts"] = [{**template, "account_id": f"co-{i}", "name": f"Co {i}"} for i in range(40)]
    out = r.render_section("conveyor_manufacturers", block, None, PROFILE, "2026-03-14")
    assert out.count("| Co ") == r.MARKET_MAP_PREVIEW
    assert f"Showing {r.MARKET_MAP_PREVIEW} of 40" in out


def test_top_n_without_verdicts_renders_ranked_table(accounts):
    out = r.render_report(accounts, None, PROFILE)
    dental = out.split("## Dental")[1].split("## Conveyor")[0]
    assert "_Not judged: ranked by filter score only._" in dental
    assert "Pinecone Family Dental" in dental


def test_all_sources_failed_reads_as_not_run(accounts):
    accounts["searches"]["dental_xray_buyers"]["status"] = "failed"
    out = r.render_report(accounts, None, PROFILE)
    assert "_Search did not run: every source failed._" in out
    assert "search did not run" in out.split("---")[0]


# --- exports --------------------------------------------------------------

def test_top_n_export_is_the_judged_list_in_order(accounts, verdicts):
    rows = r.deliverable(accounts["searches"]["dental_xray_buyers"],
                         verdicts["searches"]["dental_xray_buyers"], "top_n")
    out = _rows(r._csv(r.ACCOUNT_COLUMNS, r.account_rows(rows)))
    assert [row["name"] for row in out] == ["Pinecone Family Dental", "High Desert Endodontics PLLC"]
    assert out[0]["fit"] == "strong" and out[0]["city"] == "Bend" and out[0]["rank"] == "1"


def test_market_map_export_drops_judge_rejections_but_keeps_unjudged(accounts, verdicts):
    block = accounts["searches"]["conveyor_manufacturers"]
    verdict = verdicts["searches"]["conveyor_manufacturers"]
    verdict["accounts"] = verdict["accounts"][:1]   # judge only read the top account
    names = [a["name"] for a, _ in r.deliverable(block, verdict, "market_map")]
    assert "GrainMove Bulk Handling" not in names
    assert "Packline Automation" in names           # passed filters, not judged, still mapped


def test_queue_contains_only_drafts(accounts, verdicts):
    rows = r.deliverable(accounts["searches"]["dental_xray_buyers"],
                         verdicts["searches"]["dental_xray_buyers"], "top_n")
    queue = _rows(r._csv(r.QUEUE_COLUMNS, r.queue_rows("2026-03-14", "dental_xray_buyers", rows)))
    assert [q["name"] for q in queue] == ["Pinecone Family Dental"]
    assert queue[0]["status"] == "draft"
    assert queue[0]["body"].startswith("Hello,\n\nCongratulations")


def test_main_writes_every_output(tmp_path, accounts, verdicts):
    data = tmp_path / "data"
    (data / "accounts").mkdir(parents=True)
    (data / "verdicts").mkdir()
    (data / "accounts" / "2026-03-14.json").write_text(json.dumps(accounts), encoding="utf-8")
    (data / "verdicts" / "2026-03-14.json").write_text(json.dumps(verdicts), encoding="utf-8")
    assert r.main(["--data", str(data), "--date", "2026-03-14",
                   "--profile", "config/profile.example.yaml"]) == 0
    exports = data / "exports" / "2026-03-14"
    assert (data / "reports" / "2026-03-14.md").is_file()
    assert (exports / "dental_xray_buyers-accounts.csv").is_file()
    assert (exports / "dental_xray_buyers-queue.csv").is_file()
    assert (exports / "conveyor_manufacturers-accounts.csv").is_file()
    assert not (exports / "conveyor_manufacturers-queue.csv").exists()   # no drafts, no file


# --- index ----------------------------------------------------------------

def test_index_row_names_results_per_search(accounts, verdicts):
    row = r.index_row(accounts, verdicts, PROFILE)
    assert row.startswith("| 2026-03-14 | 2 |")
    assert "dental_xray_buyers: Pinecone Family Dental, High Desert Endodontics PLLC" in row
    assert len(row.splitlines()) == 1


def test_upsert_replaces_rather_than_duplicates(tmp_path):
    index = tmp_path / "INDEX.md"
    r.upsert_index(index, "2026-03-14", "| 2026-03-14 | first |")
    r.upsert_index(index, "2026-03-14", "| 2026-03-14 | second |")
    text = index.read_text(encoding="utf-8")
    assert text.count("2026-03-14") == 1 and "second" in text
