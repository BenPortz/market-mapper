# Market Mapper: 2026-03-14 (Saturday)

Nothing in this report has been sent to anyone. Drafts are for a person to edit.

## Summary

- **Dental practices likely to buy digital x-ray (Oregon):** 2 of 20
- **Conveyor manufacturers (Great Lakes):** 3 companies mapped

---

## Dental practices likely to buy digital x-ray (Oregon)

_Goal: find the best 20_

Sources: npi 5, osm 3. 8 records → 7 companies → 2 passed filters → 2 judged → 2 kept → 0 rejected.

**Delivered 2 of 20.** Only 2 in-region practices had a timing signal this run; the rest registered years ago or were excluded.

### Pinecone Family Dental, **STRONG** (fit 5/5)

- Bend, OR, US · https://www.pineconefamilydental.example/
- **What they do:** New family dental practice in Bend with four operatories offering general and emergency care.
- **Why they fit:**
  - Independent single-location practice that just opened, which is when imaging gets bought (Dental practices / Good fit)
  - Equipping newly opened practices, including build-out coordination, is an established service (dental_new_practice)
- **Timing:**
  - [Registered 2025-11-02 (132 days ago)](https://example.invalid/npi/1000000001)
  - [Site says "Now open"](https://www.pineconefamilydental.example/)
- **Risks:**
  - A practice that is already open may have bought imaging during build-out. The site does not say what equipment is installed.
  - Four operatories is small; a single sensor may be the whole order.
- **Reach:** Practice owner or office manager
- **Draft (email, not sent):**

  > **Subject:** Imaging for your new Bend practice
  >
  > Hello,
  >
  > Congratulations on opening Pinecone Family Dental. New practices often put imaging off until the schedule fills, then find the gap costs them referrals.
  >
  > We install digital sensors and panoramic units for dental practices, with a median of 9 business days from order to first patient image.
  >
  > If imaging is still on your list, would a short call next week be useful?

### High Desert Endodontics PLLC, **POSSIBLE** (fit 3/5)

- Redmond, OR, US
- **What they do:** Unverified: endodontic practice in Redmond, per its registry listing. No website was found to confirm.
- **Why they fit:**
  - Endodontics is a listed good-fit specialty and the practice registered two months ago (Dental practices / Good fit)
- **Timing:**
  - [Registered 2026-01-15 (58 days ago)](https://example.invalid/npi/1000000005)
- **Risks:** A new registry entry can mean an existing practice changed ownership or structure, not a new office. Confirm before pitching.
- **Reach:** Practice owner

## Conveyor manufacturers (Great Lakes)

_Goal: map the whole market_

Sources: web_search 5, csv 3, osm FAILED. 8 records → 7 companies → 4 passed filters → 4 judged → 3 kept → 1 rejected.

- **Source failed (osm):** request failed for https://overpass-api.de/api/interpreter: timed out.

**3 companies** in `exports/2026-03-14/conveyor_manufacturers-accounts.csv`.

| # | Company | Location | Website | Signals |
|---|---|---|---|---|
| 1 | Lakeshore Conveyor Systems | Grand Rapids, MI, US | lakeshoreconveyor.example |  |
| 2 | Packline Automation |  | packline.example |  |
| 3 | RollerPro |  | rollerpro.example |  |

_Removed by the judge as not actually in this market (1):_
- GrainMove Bulk Handling: Bulk grain and mining conveyors, listed as a poor fit; no inspection requirement

---
**Run cost:** ~21K tokens (est.), 2 searches, 6 accounts judged · model: example-model
