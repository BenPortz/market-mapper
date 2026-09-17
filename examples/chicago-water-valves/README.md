# Example: water valve manufacturers in the Chicago metro

A real run, not a mock-up. The question was: which companies in the Chicago
metro *manufacture* valves whose main use is water (waterworks, plumbing,
hydronic heating), as opposed to reselling them or making valves for hydraulics
or oil and gas?

| File | What it is |
| --- | --- |
| [`report.md`](report.md) | The rendered report: funnel counts, the 14 companies kept with sizes, and the 14 rejected with reasons |
| [`accounts.csv`](accounts.csv) | The deliverable list, one row per company, ranked by fit |
| [`profile.yaml`](profile.yaml) | The exact search configuration |
| [`ideal_customer.md`](ideal_customer.md) | The market definition the judge applied |
| [`web-search-candidates.csv`](web-search-candidates.csv) | The starting list from ordinary web searches |

## What happened

1. **A first pass used web search alone** and found 9 manufacturers, mostly the
   well-known brands that rank for "water valve manufacturer Chicago."
2. **Coverage mode added two public record sources.** NSF certification listings
   returned 78 certified plants in Illinois and Indiana. OSHA injury filings
   returned 18 valve-industry plants in the metro, each with a headcount.
3. **The filter stage** merged 115 records into 108 companies and passed 28: in
   the metro, mentioning valves, not excluded. Most NSF plants were cut here
   because they make pipe, coatings, or sealants.
4. **The judge** kept 14 and rejected 14. The rejections are the useful part: six
   local fluid power and hydraulic valve plants, three distributors, a diesel
   accessories maker, a railcar valve maker, a solenoid maker, an asphalt coatings
   maker, and a waterworks valve brand whose local plant has closed.

The result went from 9 companies to 14. The five new ones, including a 180-person
flush valve plant in Chicago and two valve makers under 40 employees, never
appeared in a search result. OSHA filings also confirmed which brand offices
really have local manufacturing, and attached a headcount to six of the 14.

## Honest caveats

- **Starting candidates came from an agent's web searches**, saved as a CSV,
  because no search API key was configured. With a Brave or Tavily key, the
  `web_search` source does the same job inside the pipeline.
- **This run predates the automated judge stage.** Decisions were written by
  Claude following the same rules file (`marketmapper/prompts/judge_rules.md`)
  against the same accounts file; `market-mapper judge` now does this with
  structured output and the checks described in the main README.
- **Many results are marked Unverified.** Six company sites refused automated
  reading, and OSHA-only plants have no website on record. The report says which
  facts came from which source, so a person knows what to check first.
- **Company names are real businesses**, taken from public records. Fit scores
  and risks are research judgments for a sales list, not statements about the
  companies' quality.
