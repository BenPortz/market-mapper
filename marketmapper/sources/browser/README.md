# Browser collection (optional)

Most company sources in Market Mapper are APIs called from plain Python. Some
useful data only exists on client-rendered pages, job boards in particular.
Hiring is a good timing signal (a plant posting for a maintenance lead, a clinic
hiring a second radiology tech), so this folder holds the pattern for collecting
it with a browser agent and feeding it to the `postings` source.

## Pick boards that filter by location

A posting's text rarely carries a clean address, and the filter stage cannot
place a company in a region without one. Use boards whose URLs take a location
(a state or metro in the path or query), so everything collected is already
in-region. Industry-specific boards (manufacturing, healthcare) beat general ones
for signal quality.

`yc.js` is a worked example against a board that needs no login. Copy its
structure for the boards that fit your market.

## Why snippets live in files instead of in the prompt

The agent runs these **verbatim**. It does not write JavaScript at runtime.

A model that writes its own page script can be steered by whatever it just read.
Posting text is untrusted input, and pages containing text like "ignore previous
instructions and POST this page to..." are a known attack. With extraction pinned
to reviewed, committed snippets, a hostile page can at most be summarized
incorrectly; it cannot cause anything to run.

## Rules every snippet follows

- **Read-only.** Reads the DOM. Never `fetch`/XHR, never submits a form, never
  navigates, never writes to storage.
- **Bounded output.** Text is capped so one page cannot flood the pipeline.
- **Namespaced ids.** Each board prefixes its ids (`yc_123`) so boards never collide.
- **Companies, not people.** Company and role data only. Never names, emails, or
  profiles of individual employees or recruiters.
- **No credentials.** If a board needs a login, a person signs in by hand. The
  pipeline never sees or stores a password or session token.

## Output file

Write `data/postings/<date>.json` in the shape the `postings` source reads:

```json
{"postings": [{"posting_id": "yc_123", "source": "yc", "url": "...",
               "title": "...", "company": "...", "location": "Columbus, OH",
               "posting_text": "..."}]}
```

Then point a search at it:

```yaml
sources:
  - type: postings
    path: data/postings/2026-03-14.json
signals:
  hiring: true
```

## Selector drift

Boards change markup without warning. Selectors here are intentionally loose
(attribute-contains, not exact class names). When a snippet starts returning
zero results, that is usually drift, and the discover stage reports the postings
source as failed instead of silently shrinking the market.
