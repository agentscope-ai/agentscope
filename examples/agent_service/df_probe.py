# -*- coding: utf-8 -*-
"""探针：确认 deerflow run 端点在无前置准备时的行为（agent/session 解析）。"""
import os

os.environ.setdefault("AGENTSCOPE_LOG_DIR", "/tmp/agentscope-log")
os.environ.setdefault("ADP_K8S_ENABLED", "false")

from fastapi.testclient import TestClient

import main

with TestClient(main.root_app) as client:
    # 1. 无任何前置：直接 stream（agent_id=customer_service，session 不存在）
    r = client.post(
        "/api/deerflow/threads/probe1/runs/stream",
        json={"agent_id": "customer_service", "input": {"name": "user", "role": "user", "content": [{"type": "text", "text": "hi"}]}},
        headers={"x-user-id": "u1"},
    )
    print("probe1 direct stream:", r.status_code)
    print("  body:", r.text[:300].replace("\n", "\\n"))

    # 2. 缺 x-user-id header
    r2 = client.post(
        "/api/deerflow/threads/probe2/runs/stream",
        json={"agent_id": "customer_service", "input": "hi"},
    )
    print("probe2 no x-user-id:", r2.status_code)

    # 3. 带 SDK 风格 payload（assistant_id + input.messages，无 agent_id）
    r3 = client.post(
        "/api/deerflow/threads/probe3/runs/stream",
        json={
            "assistant_id": "lead_agent",
            "input": {"messages": [{"type": "human", "content": [{"type": "text", "text": "hi"}]}]},
            "stream_mode": ["values", "messages", "custom"],
            "stream_subgraphs": True,
            "config": {"recursion_limit": 1000},
        },
        headers={"x-user-id": "u1"},
    )
    print("probe3 sdk-style payload:", r3.status_code)
    print("  body:", r3.text[:300].replace("\n", "\\n"))

    # 4. 原生 /agent/ 创建 agent 后 stream
    r4a = client.post(
        "/api/agent/",
        json={
            "type": "reusable",
            "agent_id": "probe_agent",
            "name": "probe",
            "system_prompt": "You are a helpful assistant.",
        },
        headers={"x-user-id": "u1"},
    )
    print("probe4 create agent:", r4a.status_code, r4a.text[:200])
    r4b = client.post(
        "/api/deerflow/threads/probe4/runs/stream",
        json={"agent_id": "probe_agent", "input": "hi"},
        headers={"x-user-id": "u1"},
    )
    print("probe4 stream with created agent:", r4b.status_code)
    print("  body:", r4b.text[:300].replace("\n", "\\n"))
