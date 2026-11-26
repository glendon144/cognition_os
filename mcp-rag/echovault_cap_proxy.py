#!/usr/bin/env python3
"""Stdio MCP proxy that caps EchoVault searches at 3 results for Qwinn.

Why: in verify step 1 Qwinn asked echovault_search for top_k=5 (her prompt
says 3 or fewer). Result #5 came from a different conversation, and she
merged it into her account of the first one. Capping at 3 removes that path,
and shorter results also fit her 32k context better.

What it does, and nothing else:
  * spawns the real EchoVault MCP server (the command after "--") unchanged
  * tools/list: in echovault_search's schema, sets top_k to default 3 and
    maximum 3 and snippet_chars to maximum 900, and appends a note to the
    description
  * tools/call echovault_search: clamps params.top_k into 1..3 (default 3)
    and params.snippet_chars to at most 900 before forwarding
  * passes every other message through byte for byte

Usage: echovault_cap_proxy.py -- COMMAND [ARGS...]
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading

TOOL = "echovault_search"
TOP_K_MAX = 3
SNIPPET_MAX = 900
NOTE = (f" At most {TOP_K_MAX} results per call. Each result names its own conversation: "
        "never merge text from different conversations into one.")

_list_ids: set = set()
_lock = threading.Lock()


def clamp_args(args: dict) -> dict:
    params = args.get("params") if isinstance(args.get("params"), dict) else args
    try:
        top_k = int(params.get("top_k", TOP_K_MAX))
    except (TypeError, ValueError):
        top_k = TOP_K_MAX
    params["top_k"] = max(1, min(TOP_K_MAX, top_k))
    if "snippet_chars" in params:
        try:
            params["snippet_chars"] = min(SNIPPET_MAX, int(params["snippet_chars"]))
        except (TypeError, ValueError):
            del params["snippet_chars"]
    return args


def rewrite_tools(result: dict) -> dict:
    for tool in result.get("tools", []):
        if tool.get("name") != TOOL:
            continue
        tool["description"] = (tool.get("description") or "").rstrip() + NOTE
        schema = tool.get("inputSchema") or {}
        defs = list((schema.get("$defs") or {}).values()) + [schema]
        for d in defs:
            props = d.get("properties") or {}
            if "top_k" in props:
                props["top_k"].update({"default": TOP_K_MAX, "maximum": TOP_K_MAX,
                                       "description": f"Number of results (1-{TOP_K_MAX})."})
            if "snippet_chars" in props:
                props["snippet_chars"]["maximum"] = SNIPPET_MAX
    return result


def upstream(child: subprocess.Popen) -> None:
    """Client -> child, rewriting echovault_search calls."""
    for line in sys.stdin:
        out = line
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            msg = None
        if isinstance(msg, dict):
            if msg.get("method") == "tools/list" and "id" in msg:
                with _lock:
                    _list_ids.add(msg["id"])
            elif msg.get("method") == "tools/call":
                p = msg.get("params") or {}
                if p.get("name") == TOOL and isinstance(p.get("arguments"), dict):
                    p["arguments"] = clamp_args(p["arguments"])
                    out = json.dumps(msg) + "\n"
        child.stdin.write(out)
        child.stdin.flush()
    child.stdin.close()


def downstream(child: subprocess.Popen) -> None:
    """Child -> client, rewriting the tools/list answer."""
    for line in child.stdout:
        out = line
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            msg = None
        if isinstance(msg, dict) and "id" in msg and isinstance(msg.get("result"), dict):
            with _lock:
                hit = msg["id"] in _list_ids
                _list_ids.discard(msg["id"])
            if hit:
                msg["result"] = rewrite_tools(msg["result"])
                out = json.dumps(msg) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()


def main() -> int:
    if "--" not in sys.argv or sys.argv.index("--") == len(sys.argv) - 1:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = sys.argv[sys.argv.index("--") + 1:]
    child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, bufsize=1)
    t = threading.Thread(target=upstream, args=(child,), daemon=True)
    t.start()
    downstream(child)
    return child.wait()


if __name__ == "__main__":
    sys.exit(main())
