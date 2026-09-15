"""The only module that touches the network.

Every source and the website enricher go through a `Fetcher`, which gives each
of them the same guardrails:

- **Public hosts only.** Company websites come from third-party data, so a URL
  is untrusted input. Before any request (and on every redirect) the host is
  resolved and rejected if any address is private, loopback, or link-local. A
  poisoned record pointing at `http://169.254.169.254/` or a router admin page
  goes nowhere.
- **robots.txt is honored** for website fetches.
- **Polite by default.** A minimum interval between requests to the same host,
  an identifying User-Agent, and a timeout.
- **Bounded reads.** Responses are capped, so one enormous page cannot exhaust
  memory or the judge's context.

Tests replace the Fetcher with a fake, so nothing in the test suite hits the network.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Any

DEFAULT_USER_AGENT = "market-mapper/0.1 (company research; respects robots.txt)"


class NetError(RuntimeError):
    """A request failed or was refused by a guardrail."""


def resolve(host: str) -> set[str]:
    """Every address a host resolves to. Empty when it does not resolve."""
    try:
        return {info[4][0].split("%")[0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return set()


def is_public_host(host: str) -> bool:
    """True only when every address the host resolves to is globally routable."""
    addrs = resolve(host) if host else set()
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


def check_url(url: str) -> str:
    """Return the URL if it is safe to request, else raise NetError."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https"):
        raise NetError(f"refused non-http URL: {url}")
    if not host or not resolve(host):
        # Stale listings point at lapsed domains all the time; say so plainly.
        raise NetError(f"domain does not resolve: {host or url}")
    if not is_public_host(host):
        raise NetError(f"refused non-public host: {host}")
    return url


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect target, so a public URL cannot bounce to a private one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout: float = 20.0,
                 min_interval: float = 1.0, max_bytes: int = 2_000_000):
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_bytes = max_bytes
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._opener = urllib.request.build_opener(_GuardedRedirects)

    # -- plumbing ----------------------------------------------------------

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or ""
        wait = self._last_hit.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _open(self, req: urllib.request.Request) -> tuple[bytes, str, str]:
        req.add_header("User-Agent", self.user_agent)
        self._throttle(req.full_url)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read(self.max_bytes + 1)[: self.max_bytes]
                return body, resp.geturl(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            raise NetError(f"HTTP {e.code} from {req.full_url}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NetError(f"request failed for {req.full_url}: {e}") from e

    # -- APIs --------------------------------------------------------------

    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        body, _, _ = self._open(urllib.request.Request(url, headers=headers or {}))
        return json.loads(body.decode("utf-8"))

    def post_json(self, url: str, form: dict[str, str] | None = None,
                  json_body: Any = None, headers: dict[str, str] | None = None) -> Any:
        hdrs = dict(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        else:
            data = urllib.parse.urlencode(form or {}).encode("utf-8")
        body, _, _ = self._open(urllib.request.Request(url, data=data, headers=hdrs))
        return json.loads(body.decode("utf-8"))

    # -- websites ----------------------------------------------------------

    def allowed_by_robots(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                body, _, _ = self._open(urllib.request.Request(f"{origin}/robots.txt"))
                parser.parse(body.decode("utf-8", "replace").splitlines())
            except NetError:
                parser = None  # no readable robots.txt: the convention is "allowed"
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self.user_agent, url)

    def get_page(self, url: str) -> dict[str, str]:
        """Fetch one public web page. Returns {url, content_type, html}."""
        check_url(url)
        if not self.allowed_by_robots(url):
            raise NetError(f"blocked by robots.txt: {url}")
        body, final_url, ctype = self._open(urllib.request.Request(url))
        if "html" not in ctype.lower():
            raise NetError(f"not an HTML page ({ctype or 'unknown type'}): {url}")
        return {"url": final_url, "content_type": ctype, "html": body.decode("utf-8", "replace")}
