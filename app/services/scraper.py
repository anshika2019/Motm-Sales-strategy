import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx2
from bs4 import BeautifulSoup

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 3
_MAX_EXTRA_PAGES = 3
_MAX_PAGE_TEXT_CHARS = 5000
_MAX_RESPONSE_BYTES = 2_000_000
_REQUEST_TIMEOUT = 10.0
_EXTRA_PAGE_KEYWORDS = ("about", "product", "solution", "service", "pricing")


class UnsafeUrlError(ValueError):
    """Raised when a URL resolves to a scheme/host this scraper refuses to fetch."""


def _assert_safe_url(url: str) -> None:
    """Blocks the classic SSRF targets: non-http(s) schemes, and hostnames
    that resolve to loopback/private/link-local addresses (the last of
    which covers cloud metadata endpoints like 169.254.169.254). Every hop
    -- including redirects -- must pass this before we connect to it."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise UnsafeUrlError(f"Refusing to fetch URL with scheme/host: {url}")

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except (socket.gaierror, UnicodeError, ValueError):
        # UnicodeError/ValueError cover a malformed hostname that fails
        # IDNA encoding before DNS resolution is even attempted (e.g. an
        # empty or oversized label) -- getaddrinfo() raises those, not
        # gaierror, for that case. Without this, a bad hostname crashes
        # the request instead of degrading to the same "can't fetch this
        # URL" outcome a genuine DNS failure already produces.
        raise UnsafeUrlError(f"Could not resolve host: {parsed.hostname}")

    for family, _, _, _, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeUrlError(f"Refusing to fetch URL resolving to a non-public address: {url}")


async def _safe_get(client: httpx2.AsyncClient, url: str) -> httpx2.Response | None:
    """GETs a URL, manually validating and following redirects one hop at a
    time (rather than letting httpx auto-follow them) so every hop -- not
    just the original URL -- passes the SSRF checks before we connect."""
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        _assert_safe_url(current_url)
        try:
            response = await client.get(
                current_url, timeout=_REQUEST_TIMEOUT, follow_redirects=False
            )
        except httpx2.RequestError:
            return None

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return None
            current_url = urljoin(current_url, location)
            continue

        if response.status_code >= 400:
            # A bot-block/error page (403 from Akamai/Cloudflare-style
            # protection, 404, 5xx, etc.) still returns a normal HTML body
            # -- without this check, _extract_text() happily pulls "Access
            # Denied..." out of it and summarize_company() treats that as
            # real site content instead of a failed fetch. Confirmed live
            # against parker.com, which 403s a plain non-browser GET.
            return None

        if len(response.content) > _MAX_RESPONSE_BYTES:
            return None
        return response

    return None  # too many redirects


def _extract_text(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:_MAX_PAGE_TEXT_CHARS]


def _find_extra_page_urls(homepage_url: str, homepage_html: bytes) -> list[str]:
    soup = BeautifulSoup(homepage_html, "html.parser")
    homepage_host = urlparse(homepage_url).hostname
    seen: set[str] = set()
    candidates: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        label = f"{a.get_text(strip=True)} {href}".lower()
        if not any(keyword in label for keyword in _EXTRA_PAGE_KEYWORDS):
            continue

        absolute = urljoin(homepage_url, href)
        parsed = urlparse(absolute)
        if parsed.hostname != homepage_host or absolute in seen:
            continue

        seen.add(absolute)
        candidates.append(absolute)
        if len(candidates) >= _MAX_EXTRA_PAGES:
            break

    return candidates


async def fetch_company_pages(url: str) -> list[tuple[str, str]]:
    """Fetches the given company homepage plus a few linked pages that look
    like About/Product/Pricing pages, returning [(label, text), ...]. Pages
    that fail to fetch are silently skipped rather than failing the whole
    call -- partial company context is still useful."""
    pages: list[tuple[str, str]] = []

    async with httpx2.AsyncClient() as client:
        homepage_response = await _safe_get(client, url)
        if homepage_response is None:
            return pages

        pages.append(("Homepage", _extract_text(homepage_response.content)))

        extra_urls = _find_extra_page_urls(str(homepage_response.url), homepage_response.content)
        for extra_url in extra_urls:
            try:
                extra_response = await _safe_get(client, extra_url)
            except UnsafeUrlError:
                # Only the caller's own URL should surface as a hard error --
                # a discovered link redirecting somewhere unsafe just gets skipped.
                continue
            if extra_response is None:
                continue
            label = urlparse(extra_url).path.strip("/") or extra_url
            pages.append((label, _extract_text(extra_response.content)))

    return pages
