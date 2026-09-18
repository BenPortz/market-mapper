"""Profile loading and the on-disk layout of a run.

A profile holds two things, and nothing about any individual person:

- `context`: files describing the company doing the selling. What it sells,
  who buys it, results it can prove, and what it will not say. Only the judge
  reads these.
- `searches`: what to look for. Each search names a market (an industry, a
  kind of buyer), a region, the sources to pull from, and the rules a company
  must pass.

`config/profile.yaml` is gitignored, so a fork of this repo carries the example
and never a real target list or customer names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROFILE = Path("config/profile.yaml")
EXAMPLE_PROFILE = Path("config/profile.example.yaml")

GOALS = ("top_n", "market_map")
DEFAULT_KNOCKOUTS = ["in_region", "relevant", "no_exclude_terms", "not_excluded",
                        "signal_ok", "has_website", "tech_ok"]


class ProfileError(RuntimeError):
    """Raised when a profile is missing or structurally unusable."""


@dataclass(frozen=True)
class Profile:
    context: list[str]
    searches: dict[str, dict[str, Any]]
    filters: dict[str, Any]
    http: dict[str, Any]
    dedup: dict[str, Any]

    def search(self, name: str) -> dict[str, Any]:
        """Policy for one search, or an empty policy for an unknown one.

        Tolerated instead of fatal: a data file may carry a search the profile no
        longer defines, and default rules are better than failing the whole run.
        """
        return self.searches.get(name, {})

    def label(self, name: str) -> str:
        return self.search(name).get("label", name.replace("_", " ").title())

    def goal(self, name: str) -> str:
        return self.search(name).get("goal", "top_n")

    def target_count(self, name: str) -> int | None:
        value = self.search(name).get("target_count")
        return int(value) if value else None

    def judge_limit(self, name: str) -> int:
        """How many passed accounts the judge reads for this search.

        A top-N search reads twice the target, so there is room to reject weak
        accounts and still deliver the number asked for. A market map judges
        nothing by default: its output is the full filtered list.
        """
        judge = self.search(name).get("judge") or {}
        if "limit" in judge:
            return int(judge["limit"])
        target = self.target_count(name)
        return 2 * target if self.goal(name) == "top_n" and target else 0

    def wants_drafts(self, name: str) -> bool:
        return bool((self.search(name).get("judge") or {}).get("drafts", False))

    def dedup_enabled(self, name: str) -> bool:
        """Re-running a market map should return the whole market, not only what is new."""
        return bool(self.search(name).get("dedup", self.goal(name) == "top_n"))

    @property
    def knockouts(self) -> list[str]:
        return self.filters.get("knockouts", DEFAULT_KNOCKOUTS)

    @property
    def dedup_days(self) -> int:
        return int(self.dedup.get("window_days", 30))


def load_profile(path: Path | str | None = None) -> Profile:
    """Load and validate a profile, falling back to the committed example.

    The fallback keeps a fresh clone runnable: `pytest` and a demo run work
    before anyone has written their own profile.
    """
    from marketmapper.sources import SOURCES  # local import: sources import config

    p = Path(path) if path else DEFAULT_PROFILE
    if not p.is_file():
        if path is None and EXAMPLE_PROFILE.is_file():
            p = EXAMPLE_PROFILE
        else:
            raise ProfileError(
                f"No profile at {p}. Copy {EXAMPLE_PROFILE} to {DEFAULT_PROFILE} and edit it."
            )

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ProfileError(f"{p} did not parse to a mapping.")

    searches = data.get("searches") or {}
    if not searches:
        raise ProfileError(f"{p} defines no searches; there is nothing to look for.")
    for name, s in searches.items():
        s = s or {}
        goal = s.get("goal", "top_n")
        if goal not in GOALS:
            raise ProfileError(f"Search '{name}': goal must be one of {GOALS}, not '{goal}'.")
        if goal == "top_n" and not s.get("target_count"):
            raise ProfileError(f"Search '{name}' is top_n but sets no target_count.")
        if not s.get("region"):
            raise ProfileError(f"Search '{name}' has no region.")
        sources = s.get("sources") or []
        if not sources:
            raise ProfileError(f"Search '{name}' has no sources, so nothing could be found.")
        for src in sources:
            if src.get("type") not in SOURCES:
                raise ProfileError(
                    f"Search '{name}': unknown source type '{src.get('type')}'. "
                    f"Known: {', '.join(sorted(SOURCES))}."
                )

    return Profile(
        context=list(data.get("context") or []),
        searches=searches,
        filters=data.get("filters") or {},
        http=data.get("http") or {},
        dedup=data.get("dedup") or {},
    )


@dataclass(frozen=True)
class Layout:
    """Where a run reads and writes.

    One dated file per stage, so any stage can be re-run against the previous
    stage's output without repeating the slow network steps before it.
    """

    root: Path

    def for_date(self, stage: str, date: str, suffix: str = ".json") -> Path:
        return self.root / stage / f"{date}{suffix}"

    def exports(self, date: str) -> Path:
        return self.root / "exports" / date

    @property
    def index(self) -> Path:
        return self.root / "reports" / "INDEX.md"
