#!/usr/bin/env python3
"""
Lean MCP proxy for Cognition OS.

Sits between llama-server and an existing stdio MCP server, spawns that server
unchanged, and keeps its tool traffic small enough for a 32k context:

  * hides low-value tools (health/stats/agent lists; licd_overview covers health)
  * shortens tool descriptions and trims advertised schemas
  * clamps `limit`-style arguments to small defaults and maximums
  * compacts results: drops bulky bookkeeping fields, truncates long strings,
    caps list lengths, removes JSON indentation, and enforces a hard size cap
  * lets the model "expand" one item (an explicit get-by-id tool, or full=true
    on search_thoughts) with larger caps

Usage:  lean_proxy.py PROFILE -- COMMAND [ARGS...]
Profiles: clones_brain, dream, weave

Environment knobs (all optional):
  LEAN_MAX_OUT      hard cap on a normal result, chars        (default 5000)
  LEAN_MAX_STR      per-string cap on a normal result, chars  (default 350)
  LEAN_MAX_LIST     per-list cap on a normal result           (default 8)
  LEAN_EXPAND_OUT   hard cap on an expanded result, chars     (default 9000)
  LEAN_EXPAND_STR   per-string cap when expanded, chars       (default 3000)
  LEAN_SHOW_HIDDEN  1 = do not hide any tools
  LEAN_DREAM_WRITE  0 = also hide dream_store_capsule
"""
import json
import os
import subprocess
import sys


def envint(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MAX_OUT = envint("LEAN_MAX_OUT", 5000)
MAX_STR = envint("LEAN_MAX_STR", 350)
MAX_LIST = envint("LEAN_MAX_LIST", 8)
EXPAND_OUT = envint("LEAN_EXPAND_OUT", 9000)
EXPAND_STR = envint("LEAN_EXPAND_STR", 3000)
EXPAND_LIST = 20
SHOW_HIDDEN = os.environ.get("LEAN_SHOW_HIDDEN", "0") == "1"
DREAM_WRITE = os.environ.get("LEAN_DREAM_WRITE", "1") == "1"

STORE_CAPSULE_SLIM = {
    "type": "object",
    "properties": {
        "capsule_type": {"type": "string", "enum": ["distill", "transformation", "dream", "weave", "recovery"]},
        "summary": {"type": "string", "maxLength": 1000},
        "source_system": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "themes": {"type": "array", "items": {"type": "string"}},
        "source_references": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "parent_capsule_id": {"type": "integer"},
    },
    "required": ["capsule_type", "summary", "source_system", "key_facts"],
    "additionalProperties": False,
}

PROFILES = {
    "clones_brain": {
        "hide": {"health"},
        "limits": {
            "list_recent": {"limit": (5, 10)},
            "search_thoughts": {"limit": (5, 8)},
            "recover_context": {"limit": (5, 8), "max_chars": (3000, 4000)},
        },
        "desc": {
            "list_recent": "Browse the newest durable thoughts (short snippets). Only when asked what is recent. For any topic use search_thoughts.",
            "search_thoughts": "Search durable memory. Use this FIRST for any question about the user's projects, systems or past decisions. Returns id, title, short snippet (REMEMBERED, not proof of current state). Use full=true with limit 1-2 only to read whole entries.",
            "recover_context": "One compact context bundle on a topic. Use for broad questions instead of several searches.",
            "capture_thought": "Save ONE durable thought. Only when asked or at a real milestone.",
        },
        "full_tools": {"search_thoughts": 2},
        "drop_keys": {"external_id", "updated_at", "metadata", "mode", "query", "count", "score"},
        "expand_tools": set(),
    },
    "dream": {
        "strip_prefix": "dream_",
        "hide": {"dream_status", "dream_list_agents"},
        "limits": {
            "dream_list_recent": {"limit": (5, 10)},
            "dream_search": {"limit": (5, 8)},
            "dream_recover_context": {"limit": (3, 5)},
        },
        "desc": {
            "dream_list_recent": "Newest capsule summaries. Only when asked what is recent; otherwise search.",
            "dream_get_capsule": "Read ONE capsule in full by id.",
            "dream_search": "Search episodic capsules (HISTORICAL; may describe another node or time). Summaries only.",
            "dream_recover_context": "Historical context for one specific question.",
            "dream_get_ancestry": "Provenance and ancestry of ONE capsule id.",
            "dream_store_capsule": (
                "Write ONE Dream capsule (persistent). Only when asked or at a real milestone, never after every answer. "
                "State true epistemic status; do not upgrade inferred or reported material to verified fact."
            ),
        },
        "schemas": {"dream_store_capsule": STORE_CAPSULE_SLIM},
        "drop_keys": {"capsule_uuid", "protocol_version", "query"},
        "expand_tools": {"dream_get_capsule", "dream_get_ancestry"},
    },
    "weave": {
        "strip_prefix": "weave_",
        "hide": {"weave_health", "weave_stats"},
        "limits": {"weave_search": {"limit": (5, 8)}},
        "desc": {
            "weave_search": "Associative discovery across sources. Returns pointers (id, title, source), not proof. Inspect with weave_record before relying on one.",
            "weave_record": "Read ONE Weave record in full by id, with provenance.",
        },
        "drop_keys": set(),
        "expand_tools": {"weave_record"},
    },
}


PREFIX = None  # set below from the profile


def to_external(name):
    """The client already prefixes the server name, so drop it from ours."""
    if PREFIX and name.startswith(PREFIX):
        return name[len(PREFIX):]
    return name


def to_internal(name):
    if PREFIX and not name.startswith(PREFIX):
        return PREFIX + name
    return name


def usage():
    sys.stderr.write(__doc__)
    sys.exit(2)


if len(sys.argv) < 4 or "--" not in sys.argv[2:] or sys.argv[1] not in PROFILES:
    usage()
PROFILE_NAME = sys.argv[1]
P = PROFILES[PROFILE_NAME]
PREFIX = P.get("strip_prefix")
CHILD_CMD = sys.argv[sys.argv.index("--", 2) + 1:]
if not CHILD_CMD:
    usage()

HIDDEN = set() if SHOW_HIDDEN else set(P.get("hide", set()))
if PROFILE_NAME == "dream" and not DREAM_WRITE:
    HIDDEN.add("dream_store_capsule")

child = subprocess.Popen(
    CHILD_CMD,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=None,
    text=True,
    bufsize=1,
)


def child_send(obj):
    child.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    child.stdin.flush()


def child_request(obj):
    """Send one request and wait for the response with the same id."""
    child_send(obj)
    while True:
        line = child.stdout.readline()
        if not line:
            return {"jsonrpc": "2.0", "id": obj.get("id"),
                    "error": {"code": -32603, "message": "underlying MCP server exited"}}
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") == obj.get("id"):
            return msg


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def shrink(x, max_str, max_list, drop, depth=0):
    if depth > 6:
        return "[max depth]"
    if x is None or isinstance(x, (bool, int)):
        return x
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, str):
        if len(x) <= max_str:
            return x
        return x[:max_str] + f"...[+{len(x) - max_str} chars]"
    if isinstance(x, list):
        out = [shrink(v, max_str, max_list, drop, depth + 1) for v in x[:max_list]]
        if len(x) > max_list:
            out.append(f"[{len(x) - max_list} more omitted]")
        return out
    if isinstance(x, dict):
        return {k: shrink(v, max_str, max_list, drop, depth + 1)
                for k, v in x.items() if k not in drop}
    return shrink(str(x), max_str, max_list, drop, depth + 1)


def compact_text(text, expand, max_chars_hint=None):
    max_str = EXPAND_STR if expand else MAX_STR
    max_list = EXPAND_LIST if expand else MAX_LIST
    hard = EXPAND_OUT if expand else MAX_OUT
    drop = P.get("drop_keys", set())
    try:
        data = json.loads(text)
    except ValueError:
        return text if len(text) <= hard else text[:hard] + f"...[hard cap {hard} chars]"
    if PROFILE_NAME == "clones_brain" and isinstance(data, dict):
        # recover_context returns the same material twice; keep the bundle.
        if data.get("combined_context"):
            data.pop("contexts", None)
            if max_chars_hint:
                max_str = max(max_str, int(max_chars_hint))
    out = json.dumps(shrink(data, max_str, max_list, drop), ensure_ascii=False, separators=(",", ":"))
    if len(out) > hard:
        out = out[:hard] + f"...[hard cap {hard} chars; narrow the query or fetch one item]"
    return out


def tool_error(req_id, message):
    return {"jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": message}], "isError": True}}


def edit_tools(tools):
    out = []
    for t in tools:
        name = t.get("name")
        if name in HIDDEN:
            continue
        t = json.loads(json.dumps(t))
        if name in P.get("desc", {}):
            t["description"] = P["desc"][name]
        if name in P.get("schemas", {}):
            t["inputSchema"] = P["schemas"][name]
        props = t.get("inputSchema", {}).get("properties", {})
        for arg, (default, maximum) in P.get("limits", {}).get(name, {}).items():
            if arg in props:
                props[arg]["default"] = default
                props[arg]["maximum"] = maximum
        if name in P.get("full_tools", {}):
            props["full"] = {"type": "boolean", "default": False}
        t["name"] = to_external(name)
        out.append(t)
    return out


STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "how", "what",
             "about", "into", "can", "could", "should", "would", "does", "has", "have", "its", "our"}


def clean_query(query):
    """Strip punctuation that breaks the servers' full-text search ("pikit?", "licd-console")."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else " " for c in query)
    return " ".join(cleaned.split()) or query


def relax_query(query):
    """Turn a multi-word query into an OR query of its significant terms."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in query).split()
             if len(w) >= 3 and w.lower() not in STOPWORDS]
    seen, terms = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            terms.append(w)
    return " OR ".join(terms[:8]) if len(terms) >= 2 else None


def result_is_empty(resp):
    try:
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text)
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    if not (isinstance(data, dict) and data.get("ok")):
        return False
    key = "thoughts" if "thoughts" in data else "records"
    return key in data and not data.get(key)


def handle_call(req):
    params = req.get("params") or {}
    name = to_internal(params.get("name", ""))
    args = dict(params.get("arguments") or {})
    if name in HIDDEN:
        return tool_error(req.get("id"), f"tool not available: {name}")

    full = False
    if name in P.get("full_tools", {}):
        full = bool(args.pop("full", False))

    for arg, (default, maximum) in P.get("limits", {}).get(name, {}).items():
        try:
            val = int(args.get(arg, default))
        except (TypeError, ValueError):
            val = default
        if full and arg == "limit":
            maximum = P["full_tools"][name]
        args[arg] = min(max(val, 1), maximum)

    if name in ("search_thoughts", "weave_search") and isinstance(args.get("query"), str):
        args["query"] = clean_query(args["query"])

    resp = child_request({"jsonrpc": "2.0", "id": req.get("id"), "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    if name in ("search_thoughts", "weave_search") and result_is_empty(resp):
        # The server ANDs every word, so long natural-language queries find nothing.
        relaxed = relax_query(str(args.get("query", "")))
        if relaxed:
            retry_args = dict(args, query=relaxed)
            resp = child_request({"jsonrpc": "2.0", "id": req.get("id"), "method": "tools/call",
                                  "params": {"name": name, "arguments": retry_args}})
            try:
                item = resp["result"]["content"][0]
                data = json.loads(item["text"])
                data["note"] = "no exact match; showing results for any of: " + relaxed
                item["text"] = json.dumps(data, ensure_ascii=False)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
    expand = full or name in P.get("expand_tools", set())
    result = resp.get("result")
    if isinstance(result, dict):
        for item in result.get("content", []):
            if item.get("type") == "text":
                item["text"] = compact_text(item.get("text", ""), expand, args.get("max_chars"))
    return resp


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        if "id" not in req:
            child_send(req)
            continue
        method = req.get("method")
        if method == "tools/list":
            resp = child_request(req)
            if isinstance(resp.get("result"), dict):
                resp["result"]["tools"] = edit_tools(resp["result"].get("tools", []))
            send(resp)
        elif method == "tools/call":
            send(handle_call(req))
        else:
            send(child_request(req))
    try:
        child.stdin.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
