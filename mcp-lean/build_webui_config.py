#!/usr/bin/env python3
"""Regenerate webui-config.json from qwinn_system_prompt.md.

Edit the markdown file, run this script, then restart llama-server:
    python3 ~/src/cognition_os/mcp-lean/build_webui_config.py

llama-server loads the result via --webui-config-file. The WebUI applies these
defaults once per browser; a browser with saved settings keeps its own system
message until you paste the new one or use Reset to Default.
"""
import json
from pathlib import Path

here = Path(__file__).resolve().parent
prompt = (here / "qwinn_system_prompt.md").read_text(encoding="utf-8").strip()
out = here / "webui-config.json"
out.write_text(json.dumps({"systemMessage": prompt}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(prompt)} chars of system prompt)")
