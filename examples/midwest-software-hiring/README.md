# Example: Midwest software companies that recently raised money or are hiring

The software version of a market map. The question was: which venture-backed
software companies in the Midwest (IL, IN, IA, MI, MN, MO, OH, WI) are growing
right now? For anyone selling to startups, or looking for a job at one, a recent
funding round and open roles are the two timing signals that matter most.

| File | What it is |
| --- | --- |
| [`report.md`](report.md) | The rendered list, strongest timing signals first |
| [`accounts.csv`](accounts.csv) | One row per company, including the tools detected on each site |
| [`profile.yaml`](profile.yaml) | The exact search configuration |

## What happened

1. **The YC directory source** returned 38 active YC software companies located in
   the eight states (B2B, fintech, healthcare, education, government, and real
   estate software).
2. **Enrich** read each company's website and recorded the tools its pages load.
3. **The filter** required at least one timing signal: a YC batch from 2022 on, or
   an open hiring flag. 18 companies passed; the other 20 had neither.
4. **Six companies have both signals**, so they rank first: AviaryAI, Letter AI,
   and Perspectives Health in Chicago, Healia in Columbus, Spot Health in
   Cincinnati, and Pylon in Bridgeton, Missouri.

Chicago has 7 of the 18, Ohio 4, and Michigan 3.

## Honest caveats

- **YC is one slice of Midwest software.** Most venture-backed companies in the
  region never went through YC. The `sec_form_d` source covers them by reading
  recent SEC Form D fundraising filings, but SEC refused requests carrying a
  declared contact user agent from the network used for this run, so it is
  commented out in `profile.yaml`. Its parsing is tested against the filing format.
- **"Funded" here means a YC batch year, not a round date.** A 2022 batch company
  may or may not have raised since. Form D filings would give actual dates and amounts.
- **Team sizes are self-reported on YC profiles and can be stale.** ShipBob, a
  large company, is listed with a team of 1.
- **Not judged.** This run stops at the filter (`judge.limit: 0`), so the list is
  ranked by signals, not by fit for a particular seller. Add context files and
  run `market-mapper judge` to have Claude weigh each company against an offer.
