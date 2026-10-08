#!/bin/sh
set -eu
# Configure guest DNS at template build time for the target network.
if [ -n "${SANDBOX_NAMESERVERS:-}" ]; then
    : > /etc/resolv.conf
    for address in $SANDBOX_NAMESERVERS; do
        printf 'nameserver %s\n' "$address" >> /etc/resolv.conf
    done
fi
# Preload the MCP tool adapters without connecting any MCP clients.
/root/.agentscope/.venv/bin/python -u -c \
    'from agentscope.tool import MCPTool; import runpy; runpy.run_path("/root/.agentscope/_mcp_gateway_app.py", run_name="__main__")' \
    --port 5600 > /root/.agentscope/gateway.log 2>&1 &
# Gateway reset on resume must not exit the bootstrap main command.
exec tail -f /dev/null
