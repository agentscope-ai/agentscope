# -*- coding: utf-8 -*-
"""E2E (direct-call mode) for SearchSubReactAgent.

Construct the subagent directly (no make_subagent_tool), inject host.model and
the full search toolset, then await subagent.reply(SearchInput(...)) to obtain
an agent Msg. This validates the "直呼模式（独立 Agent）"运行路径。
"""

from __future__ import annotations

import asyncio
import logging

from src.agentscope.agent._subagent_base import PermissionBundle
from src.agentscope.tool import Toolkit
from src.agentscope._logging import setup_logger

from examples.agent_search_subagent.search_subreact_agent import (
    SearchSubReactAgent,
    SearchInput,
    get_all_search_tools,
)


def _load_env_from_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file without overriding existing env.

    - Lines starting with '#' or blank lines are ignored.
    - Values can be quoted with single or double quotes.
    - Existing environment variables are not overwritten.
    """
    import os
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


async def main() -> None:
    # 1) Host model（示例用 OpenAIChatModel；实际按你的环境替换/注入）
    import os
    from src.agentscope.model import OpenAIChatModel

    # Auto load env (AGENTS.md: E2E scripts must autoload)
    _load_env_from_dotenv(".env")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "Missing OPENAI_API_KEY (see AGENTS.md: E2E env loading)",
        )
    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.environ.get("OPENAI_BASE_URL")

    client_args = {"base_url": base_url} if base_url else None
    host_model = OpenAIChatModel(
        model_name=model_name,
        stream=False,
        client_args=client_args,
    )

    # 2) 子代理权限束（最小必需字段）
    setup_logger(level="INFO", filepath=None)
    logger = logging.getLogger("search-subreact-e2e")
    permissions = PermissionBundle(
        logger=logger,
        tracer=None,
        filesystem_service=None,
        session=None,
        long_term_memory=None,
        safety_limits={},
        supervisor_name="Host",
    )

    # 3) 工具集（硬注册所有稳定 search_* 工具；说明与 Schema 由函数 __doc__ 与签名提供）
    toolkit = Toolkit()
    for f in get_all_search_tools():
        toolkit.register_tool_function(f)

    # 4) 构造子代理（直接注入 host.model 作为 model_override）
    agent = SearchSubReactAgent(
        permissions=permissions,
        spec_name="search_web",
        toolkit=toolkit,
        tools=get_all_search_tools(),
        model_override=host_model,
    )

    # 5) 直呼模式：直接调用子代理的 reply（内部组合 ReActAgent 自主使用工具）
    q = "machine learning transformers"
    msg = await agent.reply(SearchInput(query=q))

    # 6) 输出结果
    text = getattr(msg, "content", "")
    print("=== SearchSubReactAgent (direct) ===")
    print(text if isinstance(text, str) else msg.get_text_content())
    print("=== END ===")


if __name__ == "__main__":
    asyncio.run(main())
