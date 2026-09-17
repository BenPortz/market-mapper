"""Tests for the JUDGE stage, against a fake Claude client. No API calls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from marketmapper import judge as j
from marketmapper import pipeline as p
from marketmapper import report as r
from marketmapper.config import load_profile

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = load_profile("config/profile.example.yaml")
DOCS = {
    "ideal_customer.md": "# Ideal customer\n\n## Dental practices\n\n**Good fit**\n",
    "proof.md": "# Proof\n\n## dental_new_practice\nEquipped 14 new practices.\n",
}


def keep(aid, score=4, fit="strong", cite="dental_new_practice", draft_body=""):
    return {"account_id": aid, "decision": "keep", "reject_reason": "", "fit": fit, "fit_score": score,
            "what_they_do": "A dental practice.", "why_fit": [{"text": "New practice", "context_id": cite}],
            "signals": [{"detail": "Registered recently", "url": "https://example.invalid/npi"}],
            "risks": ["May already own imaging."], "contact_role": "Practice owner",
            "draft": {"channel": "email" if draft_body else "none", "subject": "Hi", "body": draft_body}}


def reject(aid, reason="Not in the market."):
    return {"account_id": aid, "decision": "reject", "reject_reason": reason, "fit": "none", "fit_score": 0,
            "what_they_do": "", "why_fit": [], "signals": [], "risks": [], "contact_role": "",
            "draft": {"channel": "none", "subject": "", "body": ""}}


class FakeClient:
    """Mimics client.beta.messages.create; answers from a list of payloads or a function."""

    def __init__(self, respond, stop_reason="end_turn"):
        self.calls = []
        self.respond = respond
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.respond(kwargs) if callable(self.respond) else self.respond[len(self.calls) - 1]
        return SimpleNamespace(
            stop_reason=self.stop_reason, stop_details=SimpleNamespace(category="cyber"),
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=800),
            model="claude-opus-5")


@pytest.fixture
def accounts() -> dict:
    enriched = json.loads((FIXTURES / "enriched_sample.json").read_text(encoding="utf-8"))
    return p.filter_doc(enriched, PROFILE, "")


# --- request shape --------------------------------------------------------

def test_request_uses_structured_output_fallbacks_and_a_cached_context(accounts):
    block = accounts["searches"]["dental_xray_buyers"]
    client = FakeClient(lambda kw: {"decisions": [], "none_reason": ""})
    j.judge_search(client, "dental_xray_buyers", block, PROFILE, "RULES", DOCS, j.DEFAULT_MODEL, "high")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["effort"] == "high"
    assert call["fallbacks"] == "default" and call["betas"] == [j.FALLBACK_BETA]
    assert call["system"][0]["text"] == "RULES"
    assert "cache_control" in call["system"][1] and "dental_new_practice" in call["system"][1]["text"]
    assert "temperature" not in call


def test_records_are_framed_as_untrusted_data(accounts):
    block = accounts["searches"]["dental_xray_buyers"]
    client = FakeClient(lambda kw: {"decisions": [], "none_reason": ""})
    j.judge_search(client, "dental_xray_buyers", block, PROFILE, "R", DOCS, "m", "high")
    user = client.calls[0]["messages"][0]["content"]
    assert "untrusted third-party data" in user
    assert "<company_records>" in user and "pineconefamilydental-example" in user
    assert "Goal: top_n: keep at most 20" in user


def test_queue_is_split_into_batches():
    queue = [f"co-{i}" for i in range(45)]
    block = {"goal": "market_map", "judge_queue": queue,
             "accounts": [{"account_id": a, "name": a.upper()} for a in queue]}
    client = FakeClient(lambda kw: {"decisions": [], "none_reason": ""})
    j.judge_search(client, "conveyor_manufacturers", block, PROFILE, "R", DOCS, "m", "high")
    assert len(client.calls) == 3                                  # 20 + 20 + 5
    assert "Batch 3 of 3" in client.calls[2]["messages"][0]["content"]


# --- enforcement ----------------------------------------------------------

NAMES = {"a": "Alpha", "b": "Beta", "c": "Gamma"}
HEADINGS = j.context_headings(DOCS)


def test_every_queued_company_gets_exactly_one_decision():
    verdict, stats = j.to_verdict([keep("a"), keep("a", score=1), keep("zzz")], ["a", "b"], NAMES,
                                  "market_map", None, False, HEADINGS, "")
    assert [x["account_id"] for x in verdict["accounts"]] == ["a"]
    assert verdict["accounts"][0]["fit_score"] == 4               # first decision wins
    assert verdict["rejected"] == [{"account_id": "b", "name": "Beta",
                                    "reason": "No decision returned by the judge; review by hand."}]
    assert stats["missing_decisions"] == 1 and stats["unknown_ids_ignored"] == 1


@pytest.mark.parametrize("cite,kept", [
    ("dental_new_practice", True),
    ("Dental practices / Good fit", True),        # section heading with a subsection label
    ("guaranteed_roi", False),                     # not a heading anywhere in the context
    ("", False),
])
def test_points_without_a_real_citation_are_removed(cite, kept):
    verdict, stats = j.to_verdict([keep("a", cite=cite)], ["a"], NAMES, "market_map", None, False, HEADINGS, "")
    assert bool(verdict["accounts"][0]["why_fit"]) is kept
    assert stats["uncited_points_removed"] == (0 if kept else 1)


def test_top_n_keeps_the_best_and_states_cuts_and_shortfalls():
    decisions = [keep("a", score=2), keep("b", score=5), keep("c", score=4)]
    verdict, _ = j.to_verdict(decisions, ["a", "b", "c"], NAMES, "top_n", 2, False, HEADINGS, "")
    assert [x["account_id"] for x in verdict["accounts"]] == ["b", "c"]
    assert verdict["rejected"][0]["reason"] == "Qualified, but ranked below the top 2."
    short, _ = j.to_verdict([keep("a")], ["a"], NAMES, "top_n", 5, False, HEADINGS, "Few practices are new.")
    assert short["none_reason"] == "Few practices are new."


def test_drafts_only_when_enabled_and_strong():
    strong = keep("a", draft_body="Hello")
    possible = keep("b", fit="possible", draft_body="Hello")
    on, _ = j.to_verdict([strong, possible], ["a", "b"], NAMES, "market_map", None, True, HEADINGS, "")
    assert "draft" in on["accounts"][0] and "draft" not in on["accounts"][1]
    off, _ = j.to_verdict([keep("a", draft_body="Hello")], ["a"], NAMES, "market_map", None, False, HEADINGS, "")
    assert "draft" not in off["accounts"][0]


def test_scores_are_clamped_and_empty_risks_are_flagged():
    d = keep("a", score=9)
    d["risks"] = ["  "]
    verdict, _ = j.to_verdict([d], ["a"], NAMES, "market_map", None, False, HEADINGS, "")
    assert verdict["accounts"][0]["fit_score"] == 5
    assert verdict["accounts"][0]["risks"] == ["The judge stated no risks; review before any outreach."]


def test_reject_without_reason_is_labelled():
    verdict, _ = j.to_verdict([reject("a", reason="")], ["a"], NAMES, "market_map", None, False, HEADINGS, "")
    assert verdict["rejected"][0]["reason"] == "Rejected without a stated reason."


# --- failure modes --------------------------------------------------------

@pytest.mark.parametrize("stop,message", [("refusal", "declined"), ("max_tokens", "max_tokens")])
def test_unusable_responses_raise(stop, message):
    client = FakeClient(lambda kw: {"decisions": [], "none_reason": ""}, stop_reason=stop)
    with pytest.raises(j.JudgeError, match=message):
        j.call_model(client, "m", "high", [], "u")


def test_invalid_json_raises():
    client = FakeClient(lambda kw: {})
    client.beta.messages.create = lambda **kw: SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{not json")], usage=None, model="m")
    with pytest.raises(j.JudgeError, match="not valid JSON"):
        j.call_model(client, "m", "high", [], "u")


# --- end to end -----------------------------------------------------------

def _run_dir(tmp_path, accounts, monkeypatch):
    data = tmp_path / "data"
    (data / "accounts").mkdir(parents=True)
    (data / "accounts" / "2026-03-14.json").write_text(json.dumps(accounts), encoding="utf-8")
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    for name, text in DOCS.items():
        (ctx / name).write_text(text, encoding="utf-8")
    profile = tmp_path / "profile.yaml"
    body = Path("config/profile.example.yaml").read_text(encoding="utf-8")
    body = body.replace("context/", f"{ctx.as_posix()}/").replace("company.md", "proof.md") \
               .replace("constraints.md", "proof.md")
    profile.write_text(body, encoding="utf-8")
    return data, profile


def test_main_writes_schema_valid_verdicts_the_report_can_render(tmp_path, accounts, monkeypatch):
    data, profile = _run_dir(tmp_path, accounts, monkeypatch)

    def respond(kw):
        user = kw["messages"][0]["content"]
        ids = user.split("account_ids: ")[1].split("\n")[0].split(", ")
        return {"decisions": [keep(ids[0], draft_body="Hello there")] + [reject(i) for i in ids[1:]],
                "none_reason": "Only one practice had a timing signal."}

    client = FakeClient(respond)
    code = j.main(["--profile", str(profile), "--data", str(data), "--date", "2026-03-14"], client=client)
    assert code == 0
    verdicts = json.loads((data / "verdicts" / "2026-03-14.json").read_text(encoding="utf-8"))
    assert p.validate(verdicts, r.SCHEMA) is None
    dental = verdicts["searches"]["dental_xray_buyers"]
    assert len(dental["accounts"]) == 1 and dental["none_reason"].startswith("Only one")
    assert "2 requests" in verdicts["run_cost_note"] and "cache reads" in verdicts["run_cost_note"]
    assert "Delivered 1 of 20" in r.render_report(accounts, verdicts, PROFILE)


def test_dry_run_makes_no_calls(tmp_path, accounts, monkeypatch, capsys):
    data, profile = _run_dir(tmp_path, accounts, monkeypatch)
    client = FakeClient(lambda kw: pytest.fail("the model was called during a dry run"))
    assert j.main(["--profile", str(profile), "--data", str(data), "--date", "2026-03-14", "--dry-run"],
                  client=client) == 0
    assert "dental_xray_buyers: 2 companies" in capsys.readouterr().out


def test_missing_context_file_is_an_error(tmp_path, accounts, monkeypatch):
    data, profile = _run_dir(tmp_path, accounts, monkeypatch)
    profile.write_text(profile.read_text(encoding="utf-8").replace("proof.md", "missing.md"), encoding="utf-8")
    assert j.main(["--profile", str(profile), "--data", str(data), "--date", "2026-03-14"],
                  client=FakeClient([])) == 1


def test_rules_file_ships_with_the_package():
    assert "No citation" in j.__doc__ and j.RULES_PATH.is_file()


def test_cli_dispatches_and_run_passes_shared_flags(monkeypatch):
    from marketmapper import __main__ as cli
    seen = []
    for stage in cli.RUN_ORDER:
        monkeypatch.setitem(cli.STAGES, stage, lambda argv, s=stage: seen.append((s, argv)) or 0)
    assert cli.main(["run", "--data", "d", "--search", "x", "--no-judge"]) == 0
    assert [s for s, _ in seen] == ["discover", "enrich", "filter", "buyers", "report"]
    assert dict(seen)["discover"] == ["--data", "d", "--search", "x"]
    assert dict(seen)["filter"] == ["--data", "d"]              # filter has no --search flag
    assert cli.main(["nonsense"]) == 2
