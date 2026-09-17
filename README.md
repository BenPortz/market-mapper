# market-mapper

Find the companies in a market, check that they really belong there, and hand a sales team a clean list with the reasoning attached.

Tell it "every conveyor manufacturer in the Great Lakes states" or "20 dental practices in Oregon likely to buy digital x-ray this year." It pulls companies from public registries, maps, web search, and lists you already have. It merges the duplicates, reads each company's own website, and filters the results in code against your rules. Then an LLM judge weighs the survivors against what *your* company sells. The output is a report, a CSV ready for a CRM, and optional outreach drafts. Nothing is ever sent.

```bash
market-mapper run --search chicago_water_valves
# = discover -> enrich -> filter -> judge -> report, each stage also runnable on its own
```

[![tests](https://github.com/BenPortz/market-mapper/actions/workflows/tests.yml/badge.svg)](https://github.com/BenPortz/market-mapper/actions/workflows/tests.yml)

**See it on real data:** [`examples/chicago-water-valves/`](examples/chicago-water-valves/) maps the water valve manufacturers in the Chicago metro. Web search alone found 9; adding certification and OSHA records found 14, with headcounts, and the judge explains each of the 14 companies it rejected. Federal contract records then show who buys valves in the region, and through which distributors.

---

## Two kinds of search

| Goal | Example | What you get |
| --- | --- | --- |
| `market_map` | "Find as many conveyor belt manufacturers as you can in OH, MI, IN, WI" | Every company that passes the filters, with the judge removing false positives. Completeness matters most. |
| `top_n` | "Find 20 dental practices in Oregon likely to buy x-ray equipment" | The best `target_count` accounts, each with why it fits, timing signals, risks, and an optional draft. If fewer qualify, the report says so instead of padding the list. |

## Where companies come from

| Source | Good for | Key needed |
| --- | --- | --- |
| `npi` | US healthcare organizations by specialty and state (the federal NPI Registry). Includes registration date, which makes "recently opened" a signal. | No |
| `osm` | Storefront and facility businesses anywhere, filtered by map tags and region (OpenStreetMap). Often carries a website. | No |
| `web_search` | Anything with a website: manufacturers, integrators, service firms. Query templates are expanded once per place in the region, and directories and social sites are dropped. | Brave or Tavily |
| `csv` | Lists you already have: trade association members, trade show exhibitors, dealer locators, CRM exports. Often the most complete list of an industry that exists. | No |
| `nsf` | Manufacturers of products certified for drinking water and plumbing (NSF/ANSI 61 and related listings), filtered by the state of the **plant**. Certification is required to sell into potable water, so small makers appear whether or not they rank in search. | No |
| `osha_ita` | Plants that file OSHA injury summaries (every manufacturing establishment with 20+ employees), filtered by NAICS code and ZIP. The only source that gives **headcount**. Reads the public CSV from osha.gov/itadata. | No |
| `postings` | Companies that are hiring, collected by a browser agent from job boards that filter by location. A timing signal, not the backbone. See [`sources/browser/`](marketmapper/sources/browser/). | No |

Adding a source is one module with a `discover()` function that returns records in the shared shape. Obvious next candidates are SAM.gov (by NAICS code), ASSE and IAPMO product listings, and national company registries like UK Companies House.

## Coverage mode: finding the smaller companies

Search engines rank by traffic, so the same well-known brands fill every results page. A default run is good at finding the leaders in a market and bad at finding the 40-person shop two exits down the highway. Coverage mode is a set of settings on a search that fixes that without throwing the leaders away:

- **Sources that ignore popularity.** `nsf` and `osha_ita` list companies because they are certified or because they employ people, not because they rank well.
- **Company size on every account.** `size_band` comes from OSHA headcount when there is a filing, or from a site describing itself as family owned. It is exported to the CSV and shown in the report.
- **`prefer: small`** lifts known and likely small companies in the ranking, so the judge reads them first. Large companies still pass the filters and still appear; they just stop crowding the top.
- **A coverage check.** `market-mapper benchmark` pulls Census County Business Patterns counts for the search's NAICS codes and counties (free key in `CENSUS_API_KEY`), and the report states the gap between the Census count and the list. It names nobody, so it can only measure the list, never pad it.

On a Chicago water valve search, adding the two coverage sources to the same web search candidates took the list from 9 to 14 manufacturers. The new names included a 180-person flush valve plant and two valve makers with under 40 employees, none of which appeared in any search result. It also confirmed which brand offices actually have local manufacturing.

## Who buys: public purchase records

A list of companies answers "who could I sell to." A salesperson also wants to know who those companies sell to, and who buys this kind of product at all. Most business-to-business sales leave no public record, but government purchasing does. `market-mapper buyers` reads the finished list and looks up:

- **Each listed company as a vendor** in federal contract awards (USAspending.gov, no key) and in city, county, or state contracts published on any Socrata open data portal.
- **The market as a whole:** awards for the search's NAICS codes and product keywords, delivered in the search region.

The report gains a "Who buys" section with the listed companies that sell to public buyers, the largest buyers, and the vendors those buyers pay who are **not** on the list, which are usually the distributors and competitors a salesperson needs to know about. Every purchase is exported to `<search>-purchases.csv` with a link to its source record.

Matching purchase records to companies is where this goes wrong quietly, so it is strict: a vendor must contain every word of the company's name after legal suffixes, abbreviations ("MFG"), and plurals are normalized. An early version matched on distinctive words only and credited a Chicago valve company with $2.9 billion that belonged to an ad agency and a university; those names are now regression tests.

---

## Architecture

```mermaid
flowchart LR
    A[DISCOVER<br/><i>APIs, lists</i>] -->|discovered.json| B[ENRICH<br/><i>read websites</i>]
    B -->|enriched.json| C[FILTER<br/><i>plain Python</i>]
    C -->|accounts.json| D[JUDGE<br/><i>LLM, no tools</i>]
    D -->|verdicts.json| B2[BUYERS<br/><i>public purchases</i>]
    B2 -->|buyers.json| E[WRITE<br/><i>plain Python</i>]
    E --> F[report.md]
    E --> G[accounts.csv]
    E --> H[queue.csv]
    E --> I[purchases.csv]
```

| Stage | Runs as | Why it is separate |
| --- | --- | --- |
| **DISCOVER** | Python against public APIs | Structured sources are cheaper, faster, and more complete than a model browsing |
| **ENRICH** | Python, bounded fetches | A registry says a company exists; its website says what it actually does |
| **FILTER** | Pure Python | Merging, region checks, keywords, exclusions, and ranking are rules. Rules belong in code, where they are reproducible and testable. |
| **JUDGE** | Claude API, no tools | Deciding whether a company really fits *your* offer is the only step that needs judgment |
| **BUYERS** | Python against public APIs | Purchase records are facts to look up, not judgments; the list is never changed by them |
| **WRITE** | Pure Python | The report and CSVs render from data, so their structure never drifts |

### Why the stages are separate

The model never both gathers the evidence and grades it. If one agent searched and judged in a single pass, whatever it happened to find would become the evidence for its own conclusion, and every company would start to look like a fit. Here, code decides which companies qualify, and the judge only reads companies that already cleared the rules. The judge has no network access at all.

Separation also keeps failures visible. Each source reports its own status, so an expired API key shows up as "web_search FAILED" in the report and not as a suspiciously small market.

### Design notes

**One company, many records.** The same dental practice shows up as `92ND TERRACE DENTAL LLC` in the registry, "Marrowstone Dental Group" on the map, and a website under a third spelling. FILTER groups records that share a website domain, or a normalized name in the same city. Grouping is transitive and does not depend on record order (there is a test that shuffles the input). Two records with different websites never merge, even when the names match.

**The company's own words decide relevance.** Keyword rules run against the name, categories, website text, and search snippets. A belt repair shop that ranks for "conveyor manufacturer" is caught by `exclude_any` when its own site says "repair and used equipment."

**Honest about region.** An address is the strongest evidence, a site that names in-region places counts, and anything else is `unknown`. Each search decides whether unknown passes (`region_strict`). Registry searches set it strict, and web search market maps usually don't.

**Timing signals are data, not vibes.** A recent registry date, "now open" or "new facility" on the site, or open job postings become signals with a link to their source. `top_n` searches can require them (`min_signals`), and ranking weighs them most.

**Every claim cites the seller's context.** The judge can only argue fit using the seller's context files. Each point names the proof id or ideal customer section it came from. A result that is not written in `proof.md` cannot appear in a report or a draft.

**The judge is boxed in by code.** `market-mapper judge` sends Claude the rules ([`judge_rules.md`](marketmapper/prompts/judge_rules.md)), the seller's context, and the company records, with no tools, and constrains the reply to a JSON schema. Code then enforces what a prompt can only ask for: every queued company gets exactly one decision (a skipped company is recorded as "no decision returned", never dropped), ids the model invents are ignored, any `why_fit` point that does not cite a real heading in the context files is removed, a top-N list is cut to the target with the shortfall stated, and drafts appear only when enabled. The rules and context sit in a cached prefix, so batches after the first mostly pay for the company records. Refusals fall back server-side; truncated or invalid output fails loudly instead of writing a partial file.

**Schema-validated handoffs.** [`marketmapper/schemas/`](marketmapper/schemas/) defines each contract. When the judge's output drifts, it fails as a schema error instead of producing a broken CSV.

**Rerunnable stages.** Each stage writes a dated file. Re-run the judge against frozen accounts while tuning the prompt, or re-render without calling the model.

---

## Security and conduct

The pipeline reads a lot of untrusted third-party content, and its output is used to contact real businesses.

- **Website text is data.** A site containing *"ignore previous instructions"* is stored as text, and the judge is told to reject that company, not obey it. Scripts and styles are dropped during parsing.
- **Guarded fetching.** Every request goes through [`net.py`](marketmapper/net.py). It allows only http(s), resolves the host and refuses private, loopback, and link-local addresses (checked again on every redirect). It honors robots.txt, rate-limits per host, sends an identifying User-Agent, and caps response size. A poisoned listing pointing at an internal address goes nowhere.
- **No injection into queries.** Values that go into the OpenStreetMap query language are checked against a safe character set, not escaped.
- **Organizations, not people.** The NPI source requests organization records only and never copies the registry's named official. Email addresses are stripped from website text. The judge names a role to contact, never a person.
- **Nothing is sent.** No stage can send email or messages. Drafts go to a CSV with `status=draft`.
- **Keys stay in the environment.** Search API keys are read from environment variables, never from the profile.

---

## Your company's context

Everything company-specific lives in two gitignored places:

- `config/profile.yaml`: the searches (market, region, sources, rules) and the do-not-contact list.
- `context/`: what the judge knows about the seller.
  - `company.md`: what you sell and how
  - `ideal_customer.md`: who buys, and just as important, who does not
  - `proof.md`: results you can back up, each under an id heading
  - `constraints.md`: what must never be claimed or promised

`config/profile.example.yaml` and `context.example/` belong to a fictional seller, Acme Radiography, and every company in `tests/fixtures/` is invented. The run in `examples/` uses real public records.

---

## Quickstart

```bash
pip install -e ".[judge]"          # Python 3.10+
cp config/profile.example.yaml config/profile.yaml
cp -r context.example context
export ANTHROPIC_API_KEY=...        # the judge stage (or run `ant auth login`)
export BRAVE_API_KEY=...            # only if a search uses web_search
export CENSUS_API_KEY=...           # only for the coverage check
```

Edit the profile and the context files, then run everything, or one stage at a time:

```bash
market-mapper run --search dental_xray_buyers
market-mapper discover --search dental_xray_buyers
market-mapper enrich
market-mapper filter
market-mapper judge --dry-run       # shows how much would be sent, calls nothing
market-mapper judge
market-mapper buyers                # needs a buyers: section on the search
market-mapper report
```

The judge uses `claude-opus-5` at `high` effort by default; set `judge.model`, `judge.effort`, or `judge.chunk_size` per search. `market-mapper run --no-judge` stops before any model call, and a market map with `judge.limit: 0` renders from the accounts file alone. [`prompts/judge.md`](prompts/judge.md) describes the same stage for an interactive agent instead of the API.

```bash
pip install -e ".[dev]" && pytest -q     # no network, no API keys needed
```

---

## Layout

```
marketmapper/
  net.py           The only module that touches the network, with its guardrails
  sources/         One module per company source, plus browser snippets for job boards
  discover.py      DISCOVER: run each search's sources
  enrich.py        ENRICH: read company websites into bounded text
  filters.py       Pure normalization, region, signal, and filter logic
  pipeline.py      FILTER: merge records into accounts, filter, rank, queue for the judge
  report.py        WRITE: report, accounts CSV, drafts CSV, index
  judge.py         JUDGE: Claude API call, structured output, and the checks around it
  buyers.py        BUYERS: federal and city purchase records, vendor matching, summaries
  benchmark.py     COVERAGE: Census establishment counts to measure a list against
  config.py        Profile loading and validation
  __main__.py      The market-mapper command
  prompts/         The judge's rules
  schemas/         JSON Schema contracts between stages
config/            Example profile (real one gitignored)
context.example/   Example seller context for a fictional company
prompts/judge.md   The judge stage as instructions for an interactive agent
tests/             240 tests, no network or API keys, fabricated fixtures
examples/          A real run: Chicago water valve manufacturers
```

Map data from OpenStreetMap is (c) OpenStreetMap contributors under the ODbL. Keep that attribution if you publish results built on it.

## License

Copyright (c) 2026 Ben Portz. All rights reserved.

This repository is public so the code can be read and evaluated. No license is granted to copy, modify, or distribute it. If you would like to use it, open an issue and ask.
