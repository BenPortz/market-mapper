/* Y Combinator "Work at a Startup" DOM extractors: a worked example.
 *
 * Run verbatim by a browser agent. READ-ONLY: reads the DOM only, never
 * fetches, submits, or navigates. See README.md in this directory.
 *
 * Verified against the live site 2026-08-28. No login required.
 *
 * Flow:
 *   1) Navigate to https://www.workatastartup.com/jobs/l/<slug>
 *      (slugs: software-engineer, operations, sales-manager, marketing, ...)
 *      Slugs are path segments. A ?role= query string silently returns nothing.
 *   2) Run COLLECT; window.scrollBy(0, 1800) and re-run until the count stops growing.
 *   3) Sort ids descending (highest = most recent); take the top N.
 *   4) Open each /jobs/<id> and run EXTRACT_POSTING.
 *   5) Write postings with source:"yc" to data/postings/<date>.json.
 *
 * This board has no location filter in its URLs, so most of what it returns
 * will be out of region for a local search. It is here to show the pattern.
 * For regional searches, prefer a board whose URL takes a location.
 */

// ---- COLLECT: run on a /jobs/l/<slug> listing page ----
// Returns [{posting_id, url, title, company, card_text}].
//
// The card container is `div[class*="cursor-pointer"]`. The site uses utility
// classes, so guesses like [class*="card"] match nothing and silently fall back
// to the bare link, losing the company and location printed on the card.
(function () {
  const seen = {};
  const results = [];
  document.querySelectorAll('a[href*="/jobs/"]').forEach(function (a) {
    const m = a.href.match(/\/jobs\/(\d+)/);   // also excludes /jobs/l/<slug> nav links
    if (!m) return;
    const posting_id = 'yc_' + m[1];
    if (seen[posting_id]) return;
    seen[posting_id] = true;
    const card = a.closest('div[class*="cursor-pointer"]') || a;
    const txt = (card.innerText || '').trim();
    results.push({
      posting_id: posting_id,
      url: a.href.split('?')[0],
      title: a.textContent.trim(),
      company: (txt.split('•')[0] || '').trim(),   // "Hive (S14)"; the batch suffix is stripped later
      card_text: txt.slice(0, 600)
    });
  });
  return results;
})()

// ---- EXTRACT_POSTING: run on a /jobs/<id> detail page ----
// Returns {title, company, posting_text}.
//
// The wait matters. The page renders client-side, so reading it right after
// navigate returns an empty body. Written for top-level await: an async IIFE
// would return a Promise that serializes to {}.
//
// const deadline = Date.now() + 5000;
// while (document.body.innerText.length < 500 && Date.now() < deadline) {
//   await new Promise(r => setTimeout(r, 250));
// }
// (() => {
//   const title = ((document.querySelector('h1') || {}).innerText || '').trim()
//     || document.title.split(' at ')[0].trim();
//   const compEl = document.querySelector('a[href*="/company/"]');
//   const company = compEl ? compEl.textContent.trim()
//     : (document.title.split(' at ')[1] || '').split(' | ')[0].trim();
//   return {
//     title: title,
//     company: company,
//     posting_text: (document.body.innerText || '').slice(0, 4000)   // bounded on purpose
//   };
// })()
