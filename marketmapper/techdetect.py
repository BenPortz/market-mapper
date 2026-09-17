"""Which web tools a company's site loads: chat widgets, meeting schedulers, tag managers.

Pure functions over raw HTML. A company that sells software and has no chat or
booking tool on its site is a different prospect from one already running
Intercom with a Chili Piper router, and that difference is visible in the page
source for anyone who looks.

Detection is by signature, in three places no visitor sees as text: script tags
and embed URLs, the ids and classes a widget leaves in the markup, and the site's
own JavaScript bundles, where frameworks such as Next.js compile a chat loader.
The bundle scan was added after checking a random sample of "no chat" sites in a
real browser: two of eight were running Intercom, one via markup and one via a
bundle, and both are now caught.

Nothing is executed, so two blind spots remain, and both are carried into every
result instead of being hidden:
- **Tag managers and vendor loaders.** A widget configured inside Google Tag
  Manager or Segment, or switched on in HubSpot's settings behind its standard
  tracking script, is fetched at runtime and never appears in the page code. When
  one of these is present and no chat is found, the status is
  `absent_unverified`, not `absent`. (The browser check found HubSpot chat on two
  of six such sites.)
- **Client-rendered sites.** When the HTML is a near-empty application shell, the
  status is `unknown`.
"""

from __future__ import annotations

import re
from typing import Any

# category -> vendor -> signatures (case-insensitive substrings of the raw HTML)
SIGNATURES: dict[str, dict[str, list[str]]] = {
    "chat": {
        "intercom": ["widget.intercom.io", "js.intercomcdn.com", "intercomsettings", "api-iam.intercom.io",
                     "intercom-lightweight-app", "intercom-container", "intercom-frame"],
        "drift": ["js.driftt.com", "drift.com/include", "drift-widget", "drift-frame-controller"],
        "qualified": ["js.qualified.com"],
        "hubspot_chat": ["js.usemessages.com", "hubspotconversations", "hubspot-messages-iframe-container"],
        "zendesk": ["static.zdassets.com/ekr/snippet.js", "zopim"],
        "livechat": ["cdn.livechatinc.com"],
        "tidio": ["code.tidio.co", "tidio-chat"],
        "crisp": ["client.crisp.chat", "crisp-client"],
        "zoho_salesiq": ["salesiq.zoho", "zsiq_float"],
        "olark": ["static.olark.com"],
        "freshchat": ["wchat.freshchat.com", "fw-cdn.com"],
        "tawk": ["embed.tawk.to"],
        "helpscout_beacon": ["beacon-v2.helpscout.net"],
        "front_chat": ["chat-assets.frontapp.com"],
        "gorgias": ["config.gorgias.chat"],
        "ada": ["static.ada.support"],
        "salesforce_messaging": ["embeddedservice", "salesforce-scrt.com"],
        "chatbase": ["chatbase.co/embed"],
        "voiceflow": ["cdn.voiceflow.com"],
        "botpress": ["cdn.botpress.cloud"],
        "landbot": ["static.landbot.io"],
        "chatwoot": ["chatwoot"],
        "userlike": ["userlike-cdn-widgets"],
        "kapa_ai": ["widget.kapa.ai"],
        "inkeep": ["unpkg.com/@inkeep", "inkeep.com"],
    },
    "scheduler": {
        "chili_piper": ["js.chilipiper.com", ".chilipiper.com/concierge", "chilipiper.com/marketing"],
        "calendly": ["assets.calendly.com"],
        "hubspot_meetings": ["meetings.hubspot.com"],
        "default": ["import-cdn.default.com"],
        "revenuehero": ["assets.revenuehero.io"],
        "savvycal": ["embed.savvycal.com"],
    },
    "tag_manager": {
        "google_tag_manager": ["googletagmanager.com/gtm.js", "googletagmanager.com/ns.html"],
        "segment": ["cdn.segment.com/analytics.js"],
        "tealium": ["tags.tiqcdn.com"],
    },
    # Scripts that switch a chat widget on from the vendor's own settings, so the
    # page code cannot show whether chat is enabled. HubSpot's standard tracking
    # code does this: turning on chat in HubSpot adds no new embed code.
    "chat_loader": {
        "hubspot_tracking": ["hs-scripts.com", "js.hs-analytics.net"],
    },
}

# Any of these present means an absence cannot be confirmed from the page code.
HIDES_CHAT = ("tag_manager:", "chat_loader:")

_BODY_TEXT = re.compile(r"<body[^>]*>(.*)</body>", re.I | re.S)
_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.I | re.S)
_SCRIPTS = re.compile(r"<script\b[^>]*>.*?</script>|<script\b[^>]*/>", re.I | re.S)
_EMBED_URLS = re.compile(r"<(?:iframe|link|img|noscript)\b[^>]*?(?:src|href)\s*=\s*[\"']([^\"']+)", re.I)
_MARKUP_IDS = re.compile(r"\b(?:id|class)\s*=\s*[\"']([^\"']+)", re.I)
_SCRIPT_SRC = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)", re.I)
_VENDOR_HOSTS = ("intercom", "drift", "hubspot", "hs-scripts", "hs-analytics", "googletagmanager", "segment.com",
                 "google-analytics", "doubleclick", "facebook", "linkedin", "hotjar", "cloudflare", "gstatic",
                 "googleapis", "jsdelivr", "unpkg", "cdnjs", "jquery", "chilipiper", "calendly", "zdassets")


def embed_code(html: str) -> str:
    """Script tags, embed URLs, and element ids and classes; never visible text.

    A widget leaves its container in the markup ("intercom-lightweight-app"), so ids
    and classes count. Visible text does not: a customer logo wall or a blog post
    that says "we moved off Intercom" must not count as running Intercom.
    """
    html = html or ""
    return "\n".join(_SCRIPTS.findall(html) + _EMBED_URLS.findall(html) + _MARKUP_IDS.findall(html))


def first_party_scripts(html: str, base_url: str, limit: int) -> list[str]:
    """Script bundles worth scanning: the site's own JavaScript, not known vendor files.

    Frameworks such as Next.js compile a chat loader into the site's own bundles,
    where it never appears in the page HTML. Vendor scripts are skipped because their
    URL is already a detection, and analytics libraries never contain a chat loader.
    """
    import urllib.parse
    out = []
    for src in _SCRIPT_SRC.findall(html or ""):
        url = urllib.parse.urljoin(base_url, src)
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not url.startswith(("http://", "https://")) or any(v in host for v in _VENDOR_HOSTS):
            continue
        if url not in out:
            out.append(url)
    return out[:limit]


def detect_in_script(source: str) -> list[str]:
    """Vendors whose loader appears in a JavaScript file. Host signatures only (the
    markup markers are too short to trust inside minified code)."""
    blob = (source or "").lower()
    return sorted(f"{cat}:{vendor}" for cat, vendors in SIGNATURES.items()
                  for vendor, sigs in vendors.items()
                  if any(s in blob for s in sigs if "." in s))


def detect(html_pages: list[str]) -> list[str]:
    """Sorted "category:vendor" labels found in the embed code of any of the pages."""
    blob = "\n".join(embed_code(h) for h in html_pages).lower()
    return sorted(f"{cat}:{vendor}" for cat, vendors in SIGNATURES.items()
                  for vendor, sigs in vendors.items() if any(s in blob for s in sigs))


def looks_client_rendered(html: str) -> bool:
    """True for an application shell: script-heavy with almost no visible text."""
    m = _BODY_TEXT.search(html or "")
    body = m.group(1) if m else (html or "")
    text = re.sub(r"\s+", " ", _TAGS.sub(" ", body)).strip()
    return len(text) < 200 and html.lower().count("<script") >= 3


def status(site: dict[str, Any] | None, category: str) -> str:
    """"present", "absent", "absent_unverified", or "unknown" for one category on one site."""
    if not site or site.get("error") or site.get("tech") is None:
        return "unknown"
    tech = site["tech"]
    if any(t.startswith(f"{category}:") for t in tech):
        return "present"
    if site.get("client_rendered"):
        return "unknown"
    if any(t.startswith(HIDES_CHAT) for t in tech):
        return "absent_unverified"
    return "absent"
