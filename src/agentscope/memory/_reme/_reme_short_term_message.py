# -*- coding: utf-8 -*-
"""ReMe-based short-term memory implementation for AgentScope."""
import json
from pathlib import Path
from typing import Any, List
from uuid import uuid4

from .._in_memory_memory import InMemoryMemory
from ..._logging import logger
from ..._utils._common import _json_loads_with_repair
from ...formatter import DashScopeChatFormatter, OpenAIChatFormatter
from ...message import Msg, TextBlock, ToolUseBlock, ToolResultBlock
from ...model import DashScopeChatModel, OpenAIChatModel
from ...tool import write_text_file


class ReMeShortTermMemory(InMemoryMemory):
    """Short-term memory implementation using ReMe for message management.

    This class provides automatic memory management with working memory
    summarization and compaction capabilities.
    """

    def __init__(
        self,
        model: DashScopeChatModel | OpenAIChatModel | None = None,
        reme_config_path: str | None = None,
        working_summary_mode: str = "auto",
        compact_ratio_threshold: float = 0.75,
        max_total_tokens: int = 20000,
        max_tool_message_tokens: int = 2000,
        group_token_threshold: int | None = None,
        keep_recent_count: int = 1,
        store_dir: str = "working_memory",
        **kwargs: Any,
    ) -> None:
        super().__init__()

        # Store working memory parameters
        self.working_summary_mode = working_summary_mode
        self.compact_ratio_threshold = compact_ratio_threshold
        self.max_total_tokens = max_total_tokens
        self.max_tool_message_tokens = max_tool_message_tokens
        self.group_token_threshold = group_token_threshold
        self.keep_recent_count = keep_recent_count
        self.store_dir = store_dir

        config_args = []

        if isinstance(model, DashScopeChatModel):
            llm_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            llm_api_key = model.api_key
            self.formatter = DashScopeChatFormatter()

        elif isinstance(model, OpenAIChatModel):
            llm_api_base = str(getattr(model.client, "base_url", None))
            llm_api_key = str(getattr(model.client, "api_key", None))
            self.formatter = OpenAIChatFormatter()

        else:
            raise ValueError(
                "model must be a DashScopeChatModel or "
                "OpenAIChatModel instance. "
                f"Got {type(model).__name__} instead.",
            )

        llm_model_name = model.model_name

        if llm_model_name:
            config_args.append(f"llm.default.model_name={llm_model_name}")

        try:
            from reme_ai import ReMeApp
        except ImportError as e:
            raise ImportError(
                "The 'reme_ai' library is required for ReMe-based "
                "short-term memory. Please try `pip install reme-ai`,"
                "and visit: https://github.com/agentscope-ai/ReMe for more "
                "information.",
            ) from e

        self.app = ReMeApp(
            *config_args,
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
            embedding_api_key=llm_api_key,  # fake api key
            embedding_api_base=llm_api_base,  # fake api base
            config_path=reme_config_path,
            **kwargs,
        )

        self._app_started = False

    async def __aenter__(self) -> "ReMeShortTermMemory":
        if self.app is not None:
            await self.app.__aenter__()
            self._app_started = True
        return self

    async def __aexit__(
        self,
        exc_type: Any = None,
        exc_val: Any = None,
        exc_tb: Any = None,
    ) -> None:
        if self.app is not None:
            await self.app.__aexit__(exc_type, exc_val, exc_tb)
        self._app_started = False

    async def get_memory(self) -> list[Msg]:
        messages: list[dict[str, Any]] = await self.formatter.format(
            msgs=self.content,  # type: ignore[has-type]
        )
        for message in messages:
            if isinstance(message.get("content"), list):
                msg_content = message.get("content")
                logger.warning(
                    "Skipping message with content as list. content=%s",
                    msg_content,
                )
                message["content"] = ""

        result: dict = await self.app.async_execute(
            name="summary_working_memory_for_as",
            messages=messages,
            working_summary_mode=self.working_summary_mode,
            compact_ratio_threshold=self.compact_ratio_threshold,
            max_total_tokens=self.max_total_tokens,
            max_tool_message_tokens=self.max_tool_message_tokens,
            group_token_threshold=self.group_token_threshold,
            keep_recent_count=self.keep_recent_count,
            store_dir=self.store_dir,
            chat_id=uuid4().hex,
        )
        logger.info(
            "result123=%s",
            json.dumps(result, ensure_ascii=False, indent=2),
        )

        messages = result.get("answer", [])
        write_file_dict: dict = result.get("metadata", {}).get(
            "write_file_dict",
            {},
        )
        if write_file_dict:
            for path, content_str in write_file_dict.items():
                file_dir = Path(path).parent
                if not file_dir.exists():
                    file_dir.mkdir(parents=True, exist_ok=True)
                await write_text_file(path, content_str)

        self.content = self.list_to_msg(messages)
        return self.content

    @staticmethod
    def list_to_msg(messages: list[dict[str, Any]]) -> list[Msg]:
        """Convert a list of message dictionaries to Msg objects.

        Args:
            messages: List of message dictionaries with role and content.

        Returns:
            List of Msg objects.
        """
        msg_list: list[Msg] = []
        for msg_dict in messages:
            role = msg_dict["role"]
            content_blocks: List[
                TextBlock | ToolUseBlock | ToolResultBlock
            ] = []
            content = msg_dict.get("content")

            if content:
                if role in ["user", "system", "assistant"]:
                    content_blocks.append(TextBlock(type="text", text=content))
                elif role in ["tool"]:
                    role = "system"
                    content_blocks.append(
                        ToolResultBlock(
                            type="tool_result",
                            name=msg_dict.get("name"),
                            id=msg_dict.get("tool_call_id"),
                            output=[TextBlock(type="text", text=content)],
                        ),
                    )

            if msg_dict.get("tool_calls"):
                for tool_call in msg_dict["tool_calls"]:
                    input_ = _json_loads_with_repair(
                        tool_call["function"].get(
                            "arguments",
                            "{}",
                        )
                        or "{}",
                    )
                    content_blocks.append(
                        ToolUseBlock(
                            type="tool_use",
                            name=tool_call["function"]["name"],
                            input=input_,
                            id=tool_call["id"],
                        ),
                    )

            msg_obj = Msg(
                name=role,
                content=content_blocks,
                role=role,
                metadata=msg_dict.get("metadata"),
            )
            msg_list.append(msg_obj)
        return msg_list

    async def retrieve(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError
