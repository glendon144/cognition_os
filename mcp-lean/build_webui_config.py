#!/usr/bin/env python3
"""Regenerate webui-config.json from qwinn_system_prompt.md.

Edit the markdown file, run this script, then restart llama-server:
    ~/venvs/cognition-os/bin/python ~/src/cognition_os/mcp-lean/build_webui_config.py
    launchctl kickstart -k gui/$(id -u)/com.clonesvr.cognitionos.llama-server

llama-server loads the result via --webui-config-file (see
~/Library/Application Support/CognitionOS/bin/run-llama-server.sh). The web UI
(llama.cpp b12110+) syncs these defaults on load unless the browser has its own
override for a setting; Settings > Reset to default clears an override.

agenticMaxTurns caps tool-call cycles per question. The UI default is 100,
which lets a looping model fill the 32k context.
"""
import json
from pathlib import Path

here = Path(__file__).resolve().parent
prompt = (here / "qwinn_system_prompt.md").read_text(encoding="utf-8").strip()
config = {
    "systemMessage": prompt,
    "agenticMaxTurns": 6,
}
out = here / "webui-config.json"
out.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(prompt)} chars of system prompt, agenticMaxTurns={config['agenticMaxTurns']})")
