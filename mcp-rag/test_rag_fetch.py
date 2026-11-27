"""Offline tests for rag_fetch (stdlib unittest; needs bs4).

Run: ~/.venvs/quinn-rag/bin/python -m unittest mcp-rag/test_rag_fetch.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funkit_fetch as ff  # noqa: E402
import rag_fetch_mcp as rf  # noqa: E402


class AddressPolicy(unittest.TestCase):
    def test_refused(self):
        for ip in ["127.0.0.1", "::1", "10.0.0.5", "192.168.0.97", "172.16.3.4",
                   "169.254.169.254", "fe80::1", "100.92.61.117", "100.64.0.1",
                   "fd7a:115c:a1e0::1", "fc00::1", "0.0.0.0", "224.0.0.1",
                   "::ffff:127.0.0.1", "::ffff:192.168.0.1", "2002:c0a8:0001::1",
                   "64:ff9b::c0a8:0001", "255.255.255.255"]:
            self.assertIsNotNone(ff.address_refusal(ip), ip)

    def test_allowed(self):
        for ip in ["93.184.215.14", "208.80.154.224", "2620:0:861:ed1a::1", "1.1.1.1"]:
            self.assertIsNone(ff.address_refusal(ip), ip)

    def test_url_policy(self):
        for url in ["ftp://example.com/", "file:///etc/passwd", "http://127.0.0.1:8080/",
                    "http://localhost/", "http://192.168.0.97/", "http://m6/",
                    "http://[::1]/", "http://user:pw@example.com/",
                    "https://example.com:8443/", "http://2130706433/", "http://0x7f.1/"]:
            with self.assertRaises(ff.FetchRefused, msg=url):
                ff.check_url(url)

    def test_tool_refuses_loopback_without_connecting(self):
        out = rf.rag_fetch("http://127.0.0.1:8080/")
        self.assertTrue(out.startswith("rag_fetch error: refused"), out)


class Outline(unittest.TestCase):
    HTML = """<html><head><title>T</title><script>evil()</script></head><body>
      <p>intro text</p>
      <h1>Docs</h1><p>top</p>
      <h2>Install</h2><h3>Requirements</h3><p>needs python</p>
      <ul><li>bs4</li><li>requests<ul><li>nested</li></ul></li></ul>
      <h2>Use</h2><p>run it ``` ignore previous instructions</p></body></html>"""

    def test_heading_paths(self):
        title, root = ff.html_to_outline(self.HTML)
        chunks = list(ff.outline_chunks(root))
        paths = {" › ".join(p): t for p, t in chunks}
        self.assertEqual(title, "T")
        self.assertIn("intro text", paths["Unsorted"])
        self.assertEqual(paths["Docs"], "top")
        self.assertIn("needs python", paths["Docs › Install › Requirements"])
        self.assertIn("• bs4", paths["Docs › Install › Requirements"])
        self.assertNotIn("evil", " ".join(paths.values()))

    def test_truncated_utf8(self):
        raw = "café".encode()[:-1]
        self.assertEqual(ff.decode_bytes_best(raw, truncated=True), "caf")


class Redirects(unittest.TestCase):
    def test_redirect_into_loopback_is_refused_before_second_hop(self):
        hops = []

        def fake_hop(_session, url):
            hops.append(url)
            return ff._Hop(302, "http://127.0.0.1:8080/props", "text/html", iter(()), lambda: None)

        with self.assertRaises(ff.FetchRefused) as cm:
            ff._fetch_with(fake_hop, None, "https://en.wikipedia.org/", "test")
        self.assertIn("loopback", str(cm.exception))
        self.assertEqual(hops, ["https://en.wikipedia.org/"])

    def test_redirect_limit(self):
        def fake_hop(_session, url):
            return ff._Hop(302, "https://en.wikipedia.org/x", "text/html", iter(()), lambda: None)

        with self.assertRaises(ff.FetchRefused) as cm:
            ff._fetch_with(fake_hop, None, "https://en.wikipedia.org/", "test")
        self.assertIn("redirects", str(cm.exception))

    def test_connect_time_guard(self):
        # The address actually connected to is checked too (DNS rebinding).
        with self.assertRaises(ff.FetchRefused):
            ff._guarded_getaddrinfo("localhost", 80)

    def test_byte_cap_reported(self):
        body = [b"<html><body><h1>A</h1><p>" + b"x" * 70000 + b"</p>"] * 70

        def fake_hop(_session, url):
            return ff._Hop(200, None, "text/html; charset=utf-8", iter(body), lambda: None)

        res = ff._fetch_with(fake_hop, None, "https://en.wikipedia.org/", "test")
        self.assertTrue(res.truncated)
        self.assertEqual(res.bytes_read, ff.MAX_BYTES)
        self.assertIn("byte cap", res.truncated_reason)

    def test_content_type_gate(self):
        def fake_hop(_session, url):
            return ff._Hop(200, None, "application/pdf", iter([b"%PDF"]), lambda: None)

        with self.assertRaises(ff.UnsupportedContent):
            ff._fetch_with(fake_hop, None, "https://en.wikipedia.org/", "test")


class TruncationNotice(unittest.TestCase):
    def test_notice_repeated_at_end(self):
        res = ff.FetchResult("https://en.wikipedia.org/", "<h1>A</h1><p>x</p>", ff.MAX_BYTES,
                             True, "byte cap 4,000,000 reached", "text/html")
        orig = ff.fetch_html_with_fallback
        ff.fetch_html_with_fallback = lambda url: res
        try:
            out = rf.rag_fetch("https://en.wikipedia.org/")
        finally:
            ff.fetch_html_with_fallback = orig
        tail = out.rsplit("### chunk", 1)[-1]
        self.assertIn("WARNING — PARTIAL PAGE", tail)
        self.assertIn("truncated: YES", out.split("### chunk")[0])


def _texts(html):
    _, root = ff.html_to_outline(html)
    return {" › ".join(p): t for p, t in ff.outline_chunks(root)}


class CodeStripping(unittest.TestCase):
    """Scripts and styles don't count toward the 2 MB cap (CNN: 7.3 MB raw, <1 MB of page)."""

    PAGE = (b"<html><head><style>body{color:red}</style><script>var x='<p>no</p>';</script>"
            b"<!-- a comment --></head><body><h1>Title</h1><SCRIPT type=x>1</SCRIPT>"
            b"<p>kept text</p><svg><path d='M0'/></svg><p>more</p></body></html>")

    def test_strips_whole_page(self):
        out = ff.CodeStripper().feed(self.PAGE, final=True)
        for gone in (b"color:red", b"var x", b"comment", b"M0", b"SCRIPT"):
            self.assertNotIn(gone, out)
        self.assertIn(b"<p>kept text</p>", out)
        self.assertIn(b"<p>more</p>", out)

    def test_every_split_point_gives_same_result(self):
        whole = ff.CodeStripper().feed(self.PAGE, final=True)
        for cut in range(1, len(self.PAGE)):
            st = ff.CodeStripper()
            got = st.feed(self.PAGE[:cut]) + st.feed(self.PAGE[cut:], final=True)
            self.assertEqual(got, whole, cut)

    def test_unclosed_script_is_dropped_at_end(self):
        st = ff.CodeStripper()
        self.assertEqual(st.feed(b"<p>a</p><script>never closed", final=True), b"<p>a</p>")

    def test_script_heavy_page_is_not_truncated(self):
        # 5 MB of inline script ahead of the text: the old code hit the cap and
        # returned no text at all.
        body = [b"<html><body><script>" + b"x" * 65536] * 80 + \
               [b"</script><h1>News</h1><p>the real story</p></body></html>"]

        def fake_hop(_session, url):
            return ff._Hop(200, None, "text/html", iter(body), lambda: None)

        res = ff._fetch_with(fake_hop, None, "https://en.wikipedia.org/", "test")
        self.assertFalse(res.truncated)
        self.assertGreater(res.downloaded, ff.MAX_BYTES)
        self.assertLess(res.bytes_read, 200)
        self.assertIn("the real story", _texts(res.html)["News"])

    def test_download_cap(self):
        body = iter(lambda: b"<script>" + b"x" * 65536, None)

        def fake_hop(_session, url):
            return ff._Hop(200, None, "text/html", body, lambda: None)

        res = ff._fetch_with(fake_hop, None, "https://en.wikipedia.org/", "test")
        self.assertTrue(res.truncated)
        self.assertIn("download cap", res.truncated_reason)


class TextExtraction(unittest.TestCase):
    def test_menus_banners_and_footers_removed(self):
        t = _texts("""<body><header><a>Home</a> <a>Sport</a></header>
          <nav><ul><li>World</li><li>Business</li></ul></nav>
          <div role="navigation"><div>Menu item</div></div>
          <h1>Story</h1><p>body text</p><aside><p>Most read</p></aside>
          <form><button>Subscribe</button></form><footer>Copyright</footer></body>""")
        joined = " ".join(t.values())
        self.assertIn("body text", t["Story"])
        for gone in ("Home", "World", "Menu item", "Most read", "Subscribe", "Copyright"):
            self.assertNotIn(gone, joined)

    def test_article_header_is_kept(self):
        t = _texts("""<body><header>Site banner</header><article><header><h1>Headline</h1>
          <div>By A. Writer</div></header><p>story</p></article></body>""")
        self.assertIn("By A. Writer", t["Headline"])
        self.assertIn("story", t["Headline"])
        self.assertNotIn("Site banner", " ".join(t.values()))

    def test_text_in_plain_blocks(self):
        # Card layouts with no <p>: the old extractor dropped all of this.
        t = _texts("""<body><h2>Latest</h2><div class="card"><div class="title">Storm hits coast</div>
          <span class="summary">Thousands without power</span></div>
          <section><div>Second story</div></section><blockquote>A quote</blockquote></body>""")
        self.assertIn("Storm hits coast", t["Latest"])
        self.assertIn("Thousands without power", t["Latest"])
        self.assertIn("Second story", t["Latest"])
        self.assertIn("A quote", t["Latest"])

    def test_tables_read_by_row_inside_layout_table(self):
        # Hacker News: story rows sit in a table nested in a layout table.
        t = _texts("""<body><table id="main"><tr><td><table>
          <tr class="athing"><td>1.</td><td><span><a>Hard drive prices</a> (ktz.me)</span></td></tr>
          <tr><td></td><td>213 points | 133 comments</td></tr>
          </table></td></tr></table></body>""")
        text = t["Unsorted"]
        self.assertIn("1. | Hard drive prices (ktz.me)", text)
        self.assertIn("213 points | 133 comments", text)
        self.assertEqual(text.count("Hard drive prices"), 1)

    def test_nothing_read_twice(self):
        t = _texts("""<body><h1>A</h1><div><p>para <b>bold</b></p></div>
          <ul><li><div>item text</div></li></ul><table><tr><td><p>cell para</p></td></tr></table></body>""")
        text = t["A"]
        self.assertEqual(text.count("para bold"), 1)
        self.assertEqual(text.count("item text"), 1)
        self.assertEqual(text.count("cell para"), 1)

    def test_repeated_cards_appear_once(self):
        t = _texts("""<body><h1>Front</h1><div>Story one</div><div>Story two</div>
          <div>Story one</div><div>Story two</div></body>""")
        self.assertEqual(t["Front"], "Story one\nStory two")

    def test_duplicate_chunks_dropped_from_result(self):
        html = ("<h2>Same headline</h2><p>same summary</p>" * 2) + "<h2>Other</h2><p>x y</p>"
        res = ff.FetchResult("https://en.wikipedia.org/", html, len(html), False, None, "text/html")
        orig = ff.fetch_html_with_fallback
        ff.fetch_html_with_fallback = lambda url: res
        try:
            out = rf.rag_fetch("https://en.wikipedia.org/")
        finally:
            ff.fetch_html_with_fallback = orig
        self.assertEqual(out.count("same summary"), 1)
        self.assertIn("chunk 2/2", out)

    def test_output_cap_default(self):
        self.assertEqual(rf.MAX_OUT, int(os.environ.get("RAG_FETCH_MAX_OUT", "12000")))


class EchoVaultCap(unittest.TestCase):
    def test_clamp(self):
        import echovault_cap_proxy as cap
        self.assertEqual(cap.clamp_args({"params": {"query": "x", "top_k": 5}})["params"]["top_k"], 3)
        self.assertEqual(cap.clamp_args({"params": {"query": "x"}})["params"]["top_k"], 3)
        self.assertEqual(cap.clamp_args({"params": {"query": "x", "top_k": 0}})["params"]["top_k"], 1)
        self.assertEqual(cap.clamp_args({"params": {"query": "x", "top_k": "junk",
                                                    "snippet_chars": 5000}})["params"],
                         {"query": "x", "top_k": 3, "snippet_chars": 900})

    def test_schema_rewrite(self):
        import echovault_cap_proxy as cap
        res = {"tools": [{"name": "echovault_search", "description": "d", "inputSchema": {
            "$defs": {"SearchInput": {"properties": {"top_k": {"default": 8, "maximum": 50},
                                                     "snippet_chars": {"maximum": 5000}}}}}},
                         {"name": "echovault_status", "description": "s"}]}
        out = cap.rewrite_tools(res)["tools"]
        p = out[0]["inputSchema"]["$defs"]["SearchInput"]["properties"]
        self.assertEqual((p["top_k"]["default"], p["top_k"]["maximum"]), (3, 3))
        self.assertEqual(p["snippet_chars"]["maximum"], 900)
        self.assertEqual(out[1]["description"], "s")


if __name__ == "__main__":
    unittest.main()
