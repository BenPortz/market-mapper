# Example: US B2B software companies with no chat widget

A market map filtered on a tool a website does not run. The question was: which
hiring US B2B software companies have no chat or AI assistant on their website?
For anyone selling chat, support automation, or conversational AI, the absence
of a widget is the buying signal.

| File | What it is |
| --- | --- |
| [`report.md`](report.md) | The rendered list, with the tool check summarized |
| [`accounts.csv`](accounts.csv) | One row per company, with its detection status |
| [`profile.yaml`](profile.yaml) | The exact search configuration |

## What happened

1. The YC directory source returned 298 active US B2B software companies with
   10 to 150 employees and an open hiring flag.
2. Enrich fetched all 298 sites, 8 of which failed, and recorded which tools
   each page loads from its script tags and embed URLs.
3. The filter required `chat` to be absent and verified. 126 companies passed.

The tool check across all 298:

| Status | Count | Meaning |
| --- | --- | --- |
| `absent` | 126 | No chat script, and no tag manager that could be hiding one |
| `absent_unverified` | 132 | No chat script, but a tag manager could inject one |
| `present` | 25 | A chat or assistant widget loads directly |
| `unknown` | 15 | Site unreadable: 7 build themselves in JavaScript, 8 refused the request |

Of the 25 that run chat, Intercom accounts for 16, then Zendesk with 4, HubSpot
chat and Inkeep with 2 each, and Chatwoot with 1. Separately, 13 companies run a
meeting scheduler (Calendly 6, HubSpot meetings 1, Chili Piper 1, plus 5 generic),
which is a useful cross-check for anyone selling chat: those companies already
book demos from their site.

The 126 skew small and coastal. California has 88 and New York 27, leaving 11
across the other seven states. Sixty-five have under 20 employees, 56 have 20 to
99, and 5 have 100 to 249. The median is 19.

## The 132 unverified companies

More companies landed in `absent_unverified` than passed the filter. A tag
manager can inject a chat widget at runtime, so it never appears in the page
HTML, and this run does not count that as "no chat."

The real count of companies with no chat is between 126 and 258. This search
reports the lower bound. Setting `tech_unverified_ok: true` would return all 258
and be wrong about roughly half of them. Confirming the other 132 needs a
headless browser that executes page scripts, which this pipeline does not do.

## Caveats

- YC is one slice of US B2B software. Every company here went through Y
  Combinator, which skews young, small, and concentrated in San Francisco and
  New York. A full market map would add SEC Form D filings and web search.
- The detector knows about 20 or so chat vendors, so it covers the common ones.
  A company running something obscure or self-hosted may read as `absent`, and
  the 15 `unknown` sites were never assessed at all.
- Hiring comes from the YC profile flag, not from job boards. It reflects what
  the company last told YC, which can be stale.
- Team sizes are self-reported on YC profiles and carry the same staleness.
- This run stops at the filter (`judge.limit: 0`), so the list is ranked by
  signal rather than by fit for a particular seller. Add context files and run
  `market-mapper judge` to have Claude weigh each company against an offer.
- The list is a snapshot. Every company here could install Intercom tomorrow,
  so it is only meaningful on the date it was run.
