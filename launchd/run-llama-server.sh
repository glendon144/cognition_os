#!/bin/sh
# llama-server bound to all interfaces (0.0.0.0): answers on the LAN as well as Tailscale.
# Still waits for the tailnet to come up before starting.
#
# Qwinn's system prompt lives in ~/src/cognition_os/mcp-lean/qwinn_system_prompt.md and reaches
# the web UI through --webui-config-file. Edit the .md, run build_webui_config.py, then restart.
# KEEP the --webui-config-file line when editing this script (a 2026-11-09 rewrite dropped it
# and Qwinn lost his prompt). Earlier versions: run-llama-server.sh.bak-*
set -eu
UI_CONFIG="$HOME/src/cognition_os/mcp-lean/webui-config.json"
MCP_CONFIG="$HOME/src/cognition_os/mcp-cognition-os.recovery.json"
TS_IP=""
i=0
while [ -z "$TS_IP" ] && [ "$i" -lt 120 ]; do
  TS_IP=$(/sbin/ifconfig | /usr/bin/awk '/inet 100\./ {print $2; exit}')
  [ -n "$TS_IP" ] || { sleep 2; i=$((i+1)); }
done
[ -n "$TS_IP" ] || { echo "no Tailscale address after 240s" >&2; exit 1; }
[ -f "$UI_CONFIG" ] || echo "WARNING: $UI_CONFIG missing; Qwinn will start with no system prompt" >&2
cd "$HOME"
exec /usr/bin/caffeinate -is /opt/homebrew/bin/llama-server \
  -m "$HOME/Models/Qwen/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf" \
  -ngl 99 -c 32768 --host 0.0.0.0 --port 8080 --ui \
  --webui-config-file "$UI_CONFIG" \
  --mcp-servers-config "$MCP_CONFIG" \
  --api-key-file "$HOME/qwen.key"
