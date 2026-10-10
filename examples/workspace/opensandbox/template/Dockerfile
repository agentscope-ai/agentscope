FROM alpine:3.19
RUN apk add --no-cache bash curl ca-certificates ripgrep python3 procps \
 && curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
RUN uv venv /root/.agentscope/.venv \
 && uv pip install --python /root/.agentscope/.venv/bin/python \
    'mcp<2.0.0' uvicorn fastapi httpx \
    docstring_parser jinja2 aiofiles python-frontmatter \
    'json_repair[schema]>=0.63.4' tree_sitter tree_sitter_bash
# Supply the same AgentScope wheel as the application using this template.
COPY agentscope-*.whl /tmp/
COPY prepare_gateway.py check_gateway.py start_gateway.sh /opt/agentscope-template/
COPY requirements.txt /tmp/requirements.txt
RUN uv pip install --python /root/.agentscope/.venv/bin/python \
    --no-deps /tmp/agentscope-*.whl \
 && ([ ! -s /tmp/requirements.txt ] || uv pip install \
       --python /root/.agentscope/.venv/bin/python -r /tmp/requirements.txt) \
 && /root/.agentscope/.venv/bin/python /opt/agentscope-template/prepare_gateway.py \
 && /root/.agentscope/.venv/bin/python -c \
    "from agentscope.tool import MCPTool" \
 && /root/.agentscope/.venv/bin/python \
    /root/.agentscope/_mcp_gateway_app.py --help \
 && uv pip freeze --python /root/.agentscope/.venv/bin/python \
    > /root/.agentscope/prebuilt-requirements.txt \
 && rm -f /tmp/agentscope-*.whl /tmp/requirements.txt \
 && rm -rf /root/.cache/uv
WORKDIR /workspace
ENTRYPOINT ["sh", "/opt/agentscope-template/start_gateway.sh"]
