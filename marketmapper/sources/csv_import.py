"""Companies from a CSV you already have.

Some of the best lists are not behind an API: a trade association's member
directory, a trade show's exhibitor list, a distributor's dealer locator, a
purchased list, or last quarter's CRM export. Save them as CSV and they join the
run like any other source: merged, enriched, filtered, and judged.

Columns (header row required; only `name` is mandatory):
    name, website, street, city, region, postal_code, country, phone, categories, note

`categories` may hold several values separated by semicolons.

Spec:
    type: csv
    path: data/imports/association-members.csv
    label: "Trade association member list"     # shown as the evidence detail
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def discover(spec: dict[str, Any], region: dict[str, Any], fetcher, ctx: dict[str, Any]
             ) -> list[dict[str, Any]]:
    from marketmapper.sources import SourceError, make_record

    path = Path(spec.get("path", ""))
    if not path.is_file():
        raise SourceError(f"csv file not found: {path}")
    label = spec.get("label", path.name)
    records = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=1):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("name"):
                continue
            evidence = {"kind": "list_member", "detail": label}
            if row.get("note"):
                evidence["snippet"] = row["note"][:300]
            records.append(make_record(
                "csv", f"{path.stem}:{i}", row["name"], website=row.get("website"),
                address={k: row.get(k) for k in ("street", "city", "region", "postal_code", "country")},
                phone=row.get("phone"),
                categories=[c.strip() for c in row.get("categories", "").split(";")],
                evidence=[evidence],
            ))
    return records
