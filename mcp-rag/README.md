# mcp-rag: `rag_fetch` for Qwinn

A stdio MCP server that gives Qwinn one public web page per call, distilled to heading-path chunks. Each chunk carries the source URL, page title and heading path. llama-server registers it as server `rag`, so the model sees the tool as `rag_rag_fetch(url, query?)`.

- `funkit_fetch.py` holds the fetch and outline code, lifted from FunKit (`ai_dream_communicator_deluxe` main @ 883efd6). Its docstring lists every change from the originals.
- `rag_fetch_mcp.py` is the MCP server. It can also be run from the shell: `rag_fetch_mcp.py URL [query]`.
- `echovault_cap_proxy.py` wraps the existing EchoVault MCP server. It caps `echovault_search` at 3 results and 900-char snippets, in both the advertised schema and the actual call, and passes every other message through unchanged. This came out of verify step 1: with top_k=5, result #5 from another conversation got merged into her answer.
- Run the tests with `~/.venvs/quinn-rag/bin/python -m unittest mcp-rag/test_rag_fetch.py -v`.

## Policy and budgets as shipped

| | |
|---|---|
| Schemes / method | http, https / GET only |
| Ports | 80, 443 |
| Refused addresses | loopback, private, link-local, 100.64.0.0/10, fd7a:115c:a1e0::/48, multicast, reserved, unspecified, plus IPv4 hidden inside mapped, 6to4, NAT64 or Teredo IPv6 |
| When checked | before connecting, inside `getaddrinfo` (the connect-time address), and on every redirect hop |
| Redirects | 3 at most, followed by hand |
| Proxies | ignored (env proxies and netrc are not used) |
| Body cap | 2,000,000 bytes; truncation is reported in the result header |
| Time | 5 s connect, 10 s read, 20 s total budget |
| Output cap | 7,000 chars per result (`RAG_FETCH_MAX_OUT`), chunks up to 1,200 chars |
| User-Agent | `FunKit-OPML-Crawler/1.0 (Qwinn rag_fetch; read-only, one page per call)` |

Page text comes back inside fenced blocks, under a header that marks it as untrusted data. When the page was truncated, a `WARNING — PARTIAL PAGE` line is also added at the end of the result, so the model sees it right before answering.

## Setup (m6)

```sh
python3.14 -m venv ~/.venvs/quinn-rag
~/.venvs/quinn-rag/bin/pip install beautifulsoup4 requests
```

Entry for llama-server's `--mcp-servers-config`:

```json
"rag": {
  "command": "/Users/gross/.venvs/quinn-rag/bin/python",
  "args": ["/Users/gross/src/cognition_os-rag-fetch/mcp-rag/rag_fetch_mcp.py"]
}
```

Wrapping EchoVault (the server name stays `echovault`, so the tool is still `echovault_echovault_search`):

```json
"echovault": {
  "command": "/Users/gross/.venvs/quinn-rag/bin/python",
  "args": ["/Users/gross/src/cognition_os-rag-fetch/mcp-rag/echovault_cap_proxy.py", "--",
           "/Users/gross/src/echovault/.venv/bin/echovault-mcp"],
  "env": {"ECHOVAULT_URL": "http://127.0.0.1:5052",
          "ECHOVAULT_MODEL": "sentence-transformers/all-MiniLM-L6-v2"}
}
```
