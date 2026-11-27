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
  * Scripts, styles, inline SVG and comments are stripped while the body
    streams in (CodeStripper), so MAX_BYTES (4 MB) counts page content rather
    than code; MAX_DOWNLOAD_BYTES (12 MB) bounds the raw download.
  * html_to_outline drops page chrome (nav, aside, form, dialog, buttons, site
    header/footer, chrome ARIA roles, aria-hidden) but keeps a header inside an
    <article>. Text in plain blocks (div, section, td, blockquote, ...) is read
    when they hold no other blocks, loose text beside blocks is read too, table
    rows become "cell | cell" lines (rows of layout tables are not), and a line
    repeated within one section is kept once.
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
MAX_BYTES = 4_000_000        # page bytes kept, after scripts/styles are stripped
MAX_DOWNLOAD_BYTES = 12_000_000  # raw bytes downloaded, scripts and styles included
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
    bytes_read: int              # page bytes kept (scripts, styles, comments stripped)
    truncated: bool
    truncated_reason: str | None
    content_type: str
    redirects: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    backend: str = "requests"
    downloaded: int = 0          # raw bytes received


# --------------------------------------------------------------------------- code stripping
# Scripts, styles, inline SVG and comments are dropped as the body streams in, so
# MAX_BYTES counts page content. Many sites send megabytes of inline script ahead
# of the text (CNN's front page: 7.3 MB raw, under 1 MB without code).
_JUNK_OPEN = re.compile(rb"<(script|style|svg|noscript|template)\b|<!--", re.I)
_JUNK_CLOSE: dict[bytes, re.Pattern] = {}


def _junk_close(name: bytes) -> re.Pattern:
    if name not in _JUNK_CLOSE:
        _JUNK_CLOSE[name] = re.compile(rb"</" + name + rb"\s*>", re.I)
    return _JUNK_CLOSE[name]


class CodeStripper:
    """Incremental remover of <script>, <style>, <svg>, <noscript>, <template>
    blocks and <!-- comments -->. feed() returns the bytes that are safe to keep;
    an unclosed block, or a '<' that may start one, waits for more input."""

    def __init__(self):
        self.pending = b""

    def feed(self, data: bytes, final: bool = False) -> bytes:
        buf = self.pending + data
        out = bytearray()
        pos = 0
        while True:
            m = _JUNK_OPEN.search(buf, pos)
            if not m:
                cut = len(buf)
                lt = buf.rfind(b"<", pos)
                if not final and lt != -1 and b">" not in buf[lt:] and len(buf) - lt < 16:
                    cut = lt                      # may be the start of "<script"
                out += buf[pos:cut]
                self.pending = buf[cut:]
                return bytes(out)
            out += buf[pos:m.start()]
            if m.group(1):
                close = _junk_close(m.group(1).lower()).search(buf, m.end())
                end = close.end() if close else -1
            else:
                i = buf.find(b"-->", m.end())
                end = i + 3 if i != -1 else -1
            if end == -1:                         # block still open
                self.pending = b"" if final else buf[m.start():]
                return bytes(out)
            pos = end


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
            stripper = CodeStripper()
            downloaded = 0
            truncated, reason = False, None
            for chunk in hop.chunks:
                if chunk:
                    downloaded += len(chunk)
                    raw.extend(stripper.feed(chunk))
                if len(raw) > MAX_BYTES:
                    del raw[MAX_BYTES:]
                    truncated, reason = True, f"byte cap {MAX_BYTES:,} reached"
                    break
                if downloaded >= MAX_DOWNLOAD_BYTES:
                    truncated, reason = True, f"download cap {MAX_DOWNLOAD_BYTES:,} reached"
                    break
                if time.monotonic() - start > BUDGET_S:
                    truncated, reason = True, f"time budget {BUDGET_S:.0f}s reached"
                    break
            else:
                raw.extend(stripper.feed(b"", final=True))
                if len(raw) > MAX_BYTES:
                    del raw[MAX_BYTES:]
                    truncated, reason = True, f"byte cap {MAX_BYTES:,} reached"
            html = decode_bytes_best(bytes(raw), truncated=truncated)
            return FetchResult(current, html, len(raw), truncated, reason, hop.ctype,
                               redirects, round(time.monotonic() - start, 2), backend,
                               downloaded)
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


# Elements that hold text when they have no block children ("leaf blocks").
_LEAF_BLOCKS = {"div", "section", "article", "td", "th", "dd", "dt", "blockquote",
                "figcaption", "pre", "address", "summary"}
_BLOCK_TAGS = ["p", "div", "section", "article", "ul", "ol", "li", "table", "tr", "td",
               "th", "dl", "dd", "dt", "blockquote", "figcaption", "pre", "address",
               "summary", "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer",
               "main", "aside", "nav", "figure", "details", "form"]
_CAPTURING = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]
_CHROME_ROLES = {"navigation", "banner", "contentinfo", "search", "complementary",
                 "menu", "menubar", "dialog", "alertdialog"}


def _inside_captured(el) -> bool:
    """True when an ancestor is read as a whole: a paragraph, list item or heading,
    or a table row with no table inside it (rows of layout tables are not read)."""
    if el.find_parent(_CAPTURING) is not None:
        return True
    return any(row.find("table") is None for row in el.find_parents("tr"))


def _loose_text_runs(el):
    """Text of el's inline children (strings and inline tags with no block inside),
    one run per stretch between block children; runs shorter than 2 chars skipped."""
    runs, cur = [], []
    for child in el.children:
        name = getattr(child, "name", None)
        if name is None:
            if type(child).__name__ == "NavigableString":   # not comments or doctypes
                cur.append(str(child))
        elif name in _BLOCK_TAGS or child.find(_BLOCK_TAGS):
            runs.append(cur)
            cur = []
        else:
            cur.append(child.get_text(" "))
    runs.append(cur)
    out = []
    for r in runs:
        txt = " ".join(" ".join(r).split())
        if len(txt) >= 2:
            out.append(txt)
    return out


def _drop_page_chrome(soup) -> None:
    """Remove site menus, banners, footers, sidebars, forms and dialogs. A header
    or footer inside an <article> is kept: that is where an article's own title
    and byline live."""
    for el in soup.find_all(["nav", "aside", "form", "dialog", "button", "select"]):
        if not el.decomposed:
            el.decompose()
    for el in soup.find_all(["header", "footer"]):
        if not el.decomposed and el.find_parent("article") is None:
            el.decompose()
    for el in soup.find_all(attrs={"role": True}):
        if not el.decomposed and str(el.get("role", "")).lower() in _CHROME_ROLES:
            el.decompose()
    for el in soup.find_all(attrs={"aria-hidden": "true"}):
        if not el.decomposed:
            el.decompose()


def html_to_outline(html: str):
    """Heading tree (h1-h6); paragraphs and lists attach under the heading they
    follow; text before any heading goes to "Unsorted". Returns (title, root)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for junk in soup(["script", "style", "noscript", "template", "svg"]):
        junk.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    _drop_page_chrome(soup)
    root = Outline(title or "Document", is_heading=True)
    unsorted = Outline("Unsorted", is_heading=True)
    body = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    stack: list[tuple[int, Outline]] = [(0, root)]
    seen: dict[int, set] = {}

    def add_text(parent: Outline, txt: str) -> None:
        texts = seen.setdefault(id(parent), set())
        if txt not in texts:                      # carousels and cards repeat lines
            texts.add(txt)
            parent.add(Outline(txt))

    for el in body.descendants:
        name = getattr(el, "name", None)
        if not name:
            continue
        parent = stack[-1][1] if len(stack) > 1 else unsorted
        if name in _LEAF_BLOCKS or name == "tr":
            if _inside_captured(el):
                continue
            if name == "tr":
                if el.find("table"):
                    continue                      # layout table: rows inside are read
                cells = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])]
                txt = " | ".join(c for c in cells if c)
            else:
                if el.find(_BLOCK_TAGS):
                    # Not a leaf: block children are read in turn; text sitting
                    # loose beside them (a card's summary <span>) is read here.
                    for run in _loose_text_runs(el):
                        add_text(parent, run)
                    continue
                txt = el.get_text(" ", strip=True)
            if len(txt) >= 2:
                add_text(parent, txt)
            continue
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
            if _inside_captured(el):
                continue
            txt = el.get_text(" ", strip=True)
            if txt:
                add_text(parent, txt)
        elif name in ("ul", "ol"):
            if _inside_captured(el):
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
