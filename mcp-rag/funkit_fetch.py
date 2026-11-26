"""Guarded single-page fetch and HTML->outline distillation for Qwinn's rag_fetch.

Lifted from Glendon's FunKit (github.com/glendon144/ai_dream_communicator_deluxe,
main @ 883efd6, FunKit/modules/):

  * fetch_html_with_fallback  <- network_utils.py   (envelope kept: HTML/XML
    content-type gate, max_bytes cap, total time budget, connect/read timeouts,
    requests -> urllib fallback)
  * decode_bytes_best         <- opml_extras_plugin._decode_bytes_best
  * Outline / html_to_outline <- aopml_engine.py

Changes from the originals, all deliberate:

  * Honest User-Agent (FunKit-OPML-Crawler/1.0 house style) instead of the
    browser-spoofing string.
  * Address guard: only http/https on ports 80/443; the host is resolved and
    every address must be globally routable (no private, loopback, link-local,
    CGNAT/tailnet 100.64.0.0/10, Tailscale fd7a:115c:a1e0::/48, multicast,
    reserved, or IPv4 embedded in mapped/6to4/NAT64/Teredo forms). The same
    check runs inside socket.getaddrinfo for this process, so the address that
    is checked is the address that is connected to (no DNS-rebinding window),
    and environment proxies are ignored.
  * Redirects are followed by hand (at most MAX_REDIRECTS), each hop re-checked.
  * Truncation is reported (FetchResult.truncated / reason), never silent, and
    a cut mid UTF-8 sequence is trimmed instead of falling back to cp1252.
  * Policy refusals never fall through to the urllib path.
  * html_to_outline: paragraphs attach to the heading they follow (the original
    put every <p> under "Unsorted", which loses the heading path); only text
    before the first heading goes to "Unsorted". <main>/role=main is preferred
    as the body when present; lists nested in list items are not repeated.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

USER_AGENT = "FunKit-OPML-Crawler/1.0 (Qwinn rag_fetch; read-only, one page per call)"

# Shipped budgets (reported in every result header).
MAX_BYTES = 2_000_000        # decoded body bytes read before truncating
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 10.0
BUDGET_S = 20.0              # total wall time for all hops of one fetch
MAX_REDIRECTS = 3
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}

_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class FetchRefused(Exception):
    """Policy refusal (scheme, port, address, method). Never retried."""


class UnsupportedContent(Exception):
    """Content-type gate. Never retried."""


@dataclass
class FetchResult:
    url: str                     # final URL after redirects
    html: str
    bytes_read: int
    truncated: bool
    truncated_reason: str | None
    content_type: str
    redirects: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    backend: str = "requests"


# --------------------------------------------------------------------------- address policy
def address_refusal(ip_text: str) -> str | None:
    """Return a reason string if this address must not be contacted, else None."""
    try:
        ip = ipaddress.ip_address(ip_text.split("%", 1)[0])
    except ValueError:
        return f"unparseable address {ip_text!r}"
    candidates = [ip]
    if ip.version == 6:
        if ip.ipv4_mapped:
            candidates.append(ip.ipv4_mapped)
        if ip.sixtofour:
            candidates.append(ip.sixtofour)
        if ip.teredo:
            candidates.extend(ip.teredo)
        if ip in _NAT64:
            candidates.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    for c in candidates:
        if c.is_loopback:
            return f"{ip} is a loopback address"
        if c.is_link_local:
            return f"{ip} is a link-local address"
        if (c.version == 4 and c in _TAILNET_V4) or (c.version == 6 and c in _TAILNET_V6):
            return f"{ip} is a tailnet/CGNAT address"
        if c.is_private:
            return f"{ip} is a private (LAN) address"
        if c.is_multicast or c.is_reserved or c.is_unspecified or not c.is_global:
            return f"{ip} is not a public address"
    return None


_real_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, *args, **kwargs):
    infos = _real_getaddrinfo(host, *args, **kwargs)
    for info in infos:
        reason = address_refusal(info[4][0])
        if reason:
            raise FetchRefused(f"refused: {host} resolves to {reason}")
    return infos


def install_address_guard() -> None:
    """Make every connection this process opens pass the address policy."""
    socket.getaddrinfo = _guarded_getaddrinfo


def check_url(url: str) -> urllib.parse.SplitResult:
    """Scheme/port/host policy plus a resolve-and-check, before any connection."""
    parts = urllib.parse.urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchRefused(f"refused: scheme {scheme or '(none)'!r} (only http/https)")
    if parts.username or parts.password:
        raise FetchRefused("refused: credentials in URL")
    host = parts.hostname
    if not host:
        raise FetchRefused("refused: URL has no host")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise FetchRefused("refused: invalid port")
    try:
        infos = _real_getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchRefused(f"refused: cannot resolve {host}: {e}")
    for info in infos:
        reason = address_refusal(info[4][0])
        if reason:
            raise FetchRefused(f"refused: {host} resolves to {reason}")
    if port not in ALLOWED_PORTS:
        raise FetchRefused(f"refused: port {port} (only 80/443)")
    return parts


# --------------------------------------------------------------------------- decoding
def decode_bytes_best(raw: bytes, default: str = "utf-8", truncated: bool = False) -> str:
    """FunKit's _decode_bytes_best, plus: a truncated body may end mid UTF-8 sequence."""
    if truncated:
        for cut in range(0, 4):
            try:
                return raw[: len(raw) - cut].decode("utf-8")
            except UnicodeDecodeError:
                continue
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    head = raw[:4096].decode("latin-1", errors="replace")
    m = re.search(r'charset=["\']?([\w\-\.:]+)', head, re.I) or \
        re.search(r"<\?xml[^>]*encoding=['\"]([\w\-\.]+)['\"]", head, re.I)
    if m:
        enc = (m.group(1) or "").strip().lower()
        for candidate in (enc, enc.replace("-", ""), enc.replace("_", "-")):
            try:
                return raw.decode(candidate)
            except Exception:
                continue
    for enc in ("utf-8-sig", "windows-1252", "latin-1", default):
        try:
            return raw.decode(enc, errors="strict")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- fetch
_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "close",
}


def _gate_ctype(ctype: str) -> None:
    c = (ctype or "").lower()
    if "text/html" not in c and "xml" not in c:
        raise UnsupportedContent(f"unsupported Content-Type: {c or 'unknown'} (HTML/XML only)")


class _Hop:
    """One response: status, headers, and a chunk iterator."""

    def __init__(self, status, location, ctype, chunks, close):
        self.status, self.location, self.ctype = status, location, ctype
        self.chunks, self.close = chunks, close


def _hop_requests(session, url):
    r = session.get(url, headers=_HEADERS, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
                    stream=True, allow_redirects=False)
    return _Hop(r.status_code, r.headers.get("Location"), r.headers.get("Content-Type") or "",
                r.iter_content(chunk_size=65536), r.close)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_URLLIB_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _hop_urllib(_session, url):
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(READ_TIMEOUT_S)
    try:
        try:
            resp = _URLLIB_OPENER.open(req, timeout=CONNECT_TIMEOUT_S)
        except urllib.error.HTTPError as e:   # 3xx/4xx/5xx arrive here with no-redirect
            resp = e
    finally:
        socket.setdefaulttimeout(old)

    def chunks():
        while True:
            b = resp.read(65536)
            if not b:
                return
            yield b

    return _Hop(resp.status if hasattr(resp, "status") else resp.code,
                resp.headers.get("Location"), resp.headers.get("Content-Type") or "",
                chunks(), resp.close)


def _fetch_with(hop_fn, session, url, backend) -> FetchResult:
    start = time.monotonic()
    redirects: list[str] = []
    current = url
    while True:
        check_url(current)
        if time.monotonic() - start > BUDGET_S:
            raise TimeoutError(f"time budget {BUDGET_S:.0f}s spent before connecting")
        hop = hop_fn(session, current)
        try:
            if hop.status in (301, 302, 303, 307, 308) and hop.location:
                if len(redirects) >= MAX_REDIRECTS:
                    raise FetchRefused(f"refused: more than {MAX_REDIRECTS} redirects")
                current = urllib.parse.urljoin(current, hop.location)
                redirects.append(current)
                continue
            if hop.status >= 400:
                raise RuntimeError(f"HTTP {hop.status} from {current}")
            _gate_ctype(hop.ctype)
            raw = bytearray()
            truncated, reason = False, None
            for chunk in hop.chunks:
                if chunk:
                    raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    del raw[MAX_BYTES:]
                    truncated, reason = True, f"byte cap {MAX_BYTES:,} reached"
                    break
                if time.monotonic() - start > BUDGET_S:
                    truncated, reason = True, f"time budget {BUDGET_S:.0f}s reached"
                    break
            html = decode_bytes_best(bytes(raw), truncated=truncated)
            return FetchResult(current, html, len(raw), truncated, reason, hop.ctype,
                               redirects, round(time.monotonic() - start, 2), backend)
        finally:
            hop.close()


def fetch_html_with_fallback(url: str) -> FetchResult:
    """GET one page under the policy. requests first, urllib if requests is
    missing or fails at the transport level. Policy errors propagate."""
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        with requests.Session() as s:
            s.trust_env = False          # ignore HTTP(S)_PROXY / netrc
            s.max_redirects = MAX_REDIRECTS
            try:
                return _fetch_with(_hop_requests, s, url, "requests")
            except (FetchRefused, UnsupportedContent):
                raise
            except requests.exceptions.RequestException as e:
                # The guard raises inside urllib3; requests may wrap it.
                inner = e
                while inner is not None:
                    if isinstance(inner, (FetchRefused, UnsupportedContent)):
                        raise inner
                    inner = inner.__cause__ or inner.__context__
    return _fetch_with(_hop_urllib, None, url, "urllib")


# --------------------------------------------------------------------------- outline
@dataclass
class Outline:
    text: str
    children: list["Outline"] = field(default_factory=list)
    is_heading: bool = False

    def add(self, child: "Outline") -> None:
        self.children.append(child)


def html_to_outline(html: str):
    """Heading tree (h1-h6); paragraphs and lists attach under the heading they
    follow; text before any heading goes to "Unsorted". Returns (title, root)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for junk in soup(["script", "style", "noscript", "template", "svg"]):
        junk.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    root = Outline(title or "Document", is_heading=True)
    unsorted = Outline("Unsorted", is_heading=True)
    body = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    stack: list[tuple[int, Outline]] = [(0, root)]
    for el in body.descendants:
        name = getattr(el, "name", None)
        if not name:
            continue
        parent = stack[-1][1] if len(stack) > 1 else unsorted
        if re.fullmatch(r"h[1-6]", name, re.I):
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            lvl = int(name[1])
            node = Outline(text, is_heading=True)
            while len(stack) > 1 and lvl <= stack[-1][0]:
                stack.pop()
            stack[-1][1].add(node)
            stack.append((lvl, node))
        elif name == "p":
            if el.find_parent("li"):
                continue
            txt = el.get_text(" ", strip=True)
            if txt:
                parent.add(Outline(txt))
        elif name in ("ul", "ol"):
            if el.find_parent("li"):
                continue
            items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
            items = [t for t in items if t]
            if items:
                lst = Outline("List")
                for t in items:
                    lst.add(Outline(t))
                parent.add(lst)
    if unsorted.children:
        root.children.insert(0, unsorted)
    return title, root


def outline_chunks(root: Outline, max_chars: int = 1200):
    """Chunk along heading boundaries. Yields (heading_path list, text)."""
    def walk(node: Outline, path: list[str]):
        here = path + [node.text] if node is not root else []
        texts: list[str] = []
        for c in node.children:
            if c.is_heading:
                continue
            if c.text == "List":
                texts.extend("• " + li.text for li in c.children)
            else:
                texts.append(c.text)
        buf = ""
        for t in texts:
            if buf and len(buf) + len(t) + 1 > max_chars:
                yield here, buf
                buf = ""
            while len(t) > max_chars:
                yield here, t[:max_chars]
                t = t[max_chars:]
            buf = f"{buf}\n{t}" if buf else t
        if buf:
            yield here, buf
        for c in node.children:
            if c.is_heading:
                yield from walk(c, here)

    yield from walk(root, [])
