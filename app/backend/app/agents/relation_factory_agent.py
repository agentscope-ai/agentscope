from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Attempt to import agentscope; fallback to local src for dev mode
try:
    from agentscope.agent import ReActAgent
    from agentscope.formatter import (
        OpenAIChatFormatter,
        DashScopeChatFormatter,
        AnthropicChatFormatter,
        GeminiChatFormatter,
        OllamaChatFormatter,
    )
    from agentscope.model import (
        OpenAIChatModel,
        DashScopeChatModel,
        AnthropicChatModel,
        GeminiChatModel,
        OllamaChatModel,
        ChatModelBase,
    )
    from agentscope.tool import Toolkit
    from agentscope.message import Msg
except ImportError:  # pragma: no cover
    ROOT = Path(__file__).resolve().parents[3]
    src_path = ROOT / "src"
    if src_path.exists():
        sys.path.append(str(src_path))
        from agentscope.agent import ReActAgent  # type: ignore
        from agentscope.formatter import (  # type: ignore
            OpenAIChatFormatter,
            DashScopeChatFormatter,
            AnthropicChatFormatter,
            GeminiChatFormatter,
            OllamaChatFormatter,
        )
        from agentscope.model import (  # type: ignore
            OpenAIChatModel,
            DashScopeChatModel,
            AnthropicChatModel,
            GeminiChatModel,
            OllamaChatModel,
            ChatModelBase,
        )
        from agentscope.tool import Toolkit  # type: ignore
        from agentscope.message import Msg  # type: ignore
    else:
        raise

from pydantic import BaseModel, Field

from ..config import get_settings
from ..ai_registry import get_relation_factory_prompt
from ..schemas.relations import EvidenceSnippet


@dataclass
class AgentBuildResult:
    agent: ReActAgent
    toolkit: Toolkit


class FuseOutput(BaseModel):
    claim: str = Field(min_length=5, max_length=240)
    reason: str = Field(min_length=5, max_length=240)
    evidence: list[EvidenceSnippet] = Field(min_items=2, max_items=4)


def _resolve_api_key(settings) -> Optional[str]:
    """Resolve API key from common env vars or settings."""
    return (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("ZHIPUAI_API_KEY")
        or os.getenv("GLM_API_KEY")
        or settings.llm_api_key
    )


def _build_model_and_formatter(provider: str, model_name: str) -> tuple[ChatModelBase, Any]:
    settings = get_settings()
    api_key = _resolve_api_key(settings)

    provider = (provider or "openai").lower()

    if provider == "openai":
        model = OpenAIChatModel(model_name=model_name, api_key=api_key, stream=False)
        fmt = OpenAIChatFormatter(system_prompt=get_relation_factory_prompt())
        return model, fmt
    if provider == "glm" or provider == "zhipuai":
        # Use OpenAI-compatible client with GLM base_url
        model = OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
            client_args={"base_url": settings.glm_base_url},
        )
        fmt = OpenAIChatFormatter(system_prompt=get_relation_factory_prompt())
        return model, fmt
    if provider == "dashscope":
        model = DashScopeChatModel(model_name=model_name, stream=False)
        fmt = DashScopeChatFormatter(system_prompt=get_relation_factory_prompt())
        return model, fmt
    if provider == "anthropic":
        model = AnthropicChatModel(model_name=model_name, api_key=api_key, stream=False)
        fmt = AnthropicChatFormatter(system_prompt=get_relation_factory_prompt())
        return model, fmt
    if provider == "gemini":
        model = GeminiChatModel(model_name=model_name, api_key=api_key, stream=False)
        fmt = GeminiChatFormatter(system_prompt=get_relation_factory_prompt())
        return model, fmt
    if provider == "ollama":
        # For local models via Ollama; assumes OLLAMA_BASE_URL env if needed
        model = OllamaChatModel(model_name=model_name, stream=False)
        fmt = OllamaChatFormatter(system_prompt=get_relation_factory_prompt())
        return model, fmt

    # Default to OpenAI
    model = OpenAIChatModel(model_name=model_name, api_key=api_key, stream=False)
    fmt = OpenAIChatFormatter(system_prompt=get_relation_factory_prompt())
    return model, fmt


def build_relation_factory_agent(evidence_provider) -> AgentBuildResult:
    settings = get_settings()
    model, fmt = _build_model_and_formatter(settings.llm_provider or "openai", settings.relation_factory_model)

    toolkit = Toolkit()

    # Register evidence provider tool
    toolkit.register_tool_function(evidence_provider)

    agent = ReActAgent(
        name="relation_factory",
        sys_prompt=get_relation_factory_prompt(),
        model=model,
        formatter=fmt,
        toolkit=toolkit,
        max_iters=6,
    )

    return AgentBuildResult(agent=agent, toolkit=toolkit)


async def run_relation_factory_once(
    subject: str,
    predicate: str,
    content: str,
    evidence_provider,
) -> Optional[FuseOutput]:
    # Build agent; if model init fails (e.g., missing key), degrade
    try:
        build = build_relation_factory_agent(evidence_provider)
    except Exception:
        return None

    user_msg = Msg(
        role="user",
        content=(
            f"目标关系: {predicate}\n"
            f"主题(subject): {subject}\n"
            f"新输入片段: {content[:400]}\n\n"
            "请使用工具 `evidence_provider` 获取两段证据后，调用 finish 函数"
            "generate_response，输出 claim/reason/evidence 三个字段。"
            "解释与关键短语必须逐字来自证据。"
        ),
    )

    try:
        res = await build.agent.reply(user_msg, structured_model=FuseOutput)
    except Exception:  # pragma: no cover - fallback in offline/no-key environments
        return None

    if res and isinstance(res.metadata, dict) and all(k in res.metadata for k in ("claim", "reason", "evidence")):
        try:
            return FuseOutput.model_validate(res.metadata)
        except Exception:
            return None
    return None
