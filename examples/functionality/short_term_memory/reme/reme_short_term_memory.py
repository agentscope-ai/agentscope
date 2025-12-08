# -*- coding: utf-8 -*-
"""ReMe-based short-term memory implementation for AgentScope."""
import json
from pathlib import Path
from typing import Any, List
from uuid import uuid4

from agentscope import logger
from agentscope._utils._common import _json_loads_with_repair
from agentscope.formatter import DashScopeChatFormatter, OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock, ToolUseBlock, ToolResultBlock
from agentscope.model import DashScopeChatModel, OpenAIChatModel
from agentscope.tool import write_text_file


class ReMeShortTermMemory(InMemoryMemory):
    """Short-term memory implementation using ReMe for message management.

    This class provides automatic working-memory management through a
    multi-stage pipeline that reduces token usage while preserving
    essential information:

    1. **Compaction**: Truncates large tool messages by storing full
       content in external files and keeping only short previews in the
       active context.
    2. **Compression**: Uses LLM to generate dense summaries of older
       conversation history, creating a compact state snapshot.
    3. **Offload**: Orchestrates compaction and optional compression
       based on the configured working_summary_mode (COMPACT, COMPRESS,
       or AUTO).

    The memory management is triggered automatically when `get_memory()`
    is called, ensuring the agent's context stays within token limits
    while maintaining access to detailed historical information through
    external storage.
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
        keep_recent_count: int = 10,
        store_dir: str = "inmemory",
        **kwargs: Any,
    ) -> None:
        """Initialize ReMe-based short-term memory.

        Args:
            model: Language model for compression operations. Must be
                either DashScopeChatModel or OpenAIChatModel.
            reme_config_path: Optional path to ReMe configuration file
                for custom settings.
            working_summary_mode: Strategy for working memory management.
                - "compact": Only compact verbose tool messages by
                  storing full content externally and keeping short
                  previews.
                - "compress": Only apply LLM-based compression to
                  generate compact state snapshots.
                - "auto": First run compaction, then optionally run
                  compression if the compaction ratio exceeds
                  compact_ratio_threshold.
                Defaults to "auto".
            compact_ratio_threshold: Threshold for compaction
                effectiveness in AUTO mode. If (compacted_tokens /
                original_tokens) > this threshold, compression is
                applied. Defaults to 0.75.
            max_total_tokens: Maximum token count threshold before
                compression is triggered. Does not include
                keep_recent_count messages or system messages.
                Defaults to 20000.
            max_tool_message_tokens: Maximum token count for individual
                tool messages before compaction. Tool messages exceeding
                this are stored externally. Defaults to 2000.
            group_token_threshold: Maximum token count per compression
                group when splitting messages for LLM compression. If
                None or 0, all messages are compressed in a single
                group. Defaults to None.
            keep_recent_count: Number of most recent messages to
                preserve without compression or compaction. These
                messages remain in full in the active context.
                Defaults to 1.
            store_dir: Directory path for storing offloaded message
                content and compressed history files. Defaults to
                "working_memory".
            **kwargs: Additional arguments passed to ReMeApp
                initialization.

        Raises:
            ValueError: If model is not a DashScopeChatModel or
                OpenAIChatModel.
            ImportError: If reme_ai library is not installed.
        """
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
        """Async context manager entry.

        Initializes the ReMe application for async operations.
        """
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
        """Async context manager exit.

        Cleans up the ReMe application resources.
        """
        if self.app is not None:
            await self.app.__aexit__(exc_type, exc_val, exc_tb)
        self._app_started = False

    async def get_memory(self) -> list[Msg]:
        """Retrieve and manage working memory with automatic summarization.

        This method performs the core working-memory management pipeline:

        1. **Format messages**: Converts internal Msg objects to standard
           message format using the appropriate formatter (DashScope or
           OpenAI).
        2. **Execute offload pipeline**: Calls ReMe's
           summary_working_memory_for_as operation which orchestrates:
           - Message compaction: Large tool messages are truncated and
             stored externally with only previews kept in context.
           - Message compression: If needed (based on
             working_summary_mode), older messages are compressed using
             LLM into dense summaries.
           - File storage: Offloaded content is written to external
             files for potential retrieval.
        3. **Update content**: Replaces the internal message list with
           the managed version, ensuring subsequent operations work with
           the optimized context.

        The operation respects configuration parameters like
        max_total_tokens, keep_recent_count, and working_summary_mode to
        balance context size with information preservation.

        Returns:
            List of Msg objects representing the managed working memory,
            with large tool messages compacted and/or older history
            compressed as needed.

        Note:
            This method automatically writes offloaded content to files
            in the configured store_dir. The write_file_dict metadata
            contains paths and content for all externally stored
            messages.
        """
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

        # Execute ReMe's working memory offload pipeline
        # This orchestrates compaction and/or compression based on
        # working_summary_mode
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
            "summary_working_memory_for_as.result=%s",
            json.dumps(result, ensure_ascii=False, indent=2),
        )

        # Extract managed messages and file write operations from result
        messages = result.get("answer", [])
        write_file_dict: dict = result.get("metadata", {}).get(
            "write_file_dict",
            {},
        )
        # Write offloaded content to external files
        # This includes full tool message content and compressed message
        # history
        if write_file_dict:
            for path, content_str in write_file_dict.items():
                file_dir = Path(path).parent
                if not file_dir.exists():
                    file_dir.mkdir(parents=True, exist_ok=True)
                await write_text_file(path, content_str)

        # Update internal content with managed messages
        self.content = self.list_to_msg(messages)
        return self.content

    @staticmethod
    def list_to_msg(messages: list[dict[str, Any]]) -> list[Msg]:
        """Convert a list of message dictionaries to Msg objects.

        This method handles the conversion from standard message format
        (used by ReMe and LLM APIs) back to AgentScope's Msg objects.
        It properly handles:
        - Text content for user, system, and assistant messages
        - Tool result blocks (converting role="tool" to role="system")
        - Tool use blocks from tool_calls in assistant messages

        Args:
            messages: List of message dictionaries with role, content,
                and optional tool_calls or tool-related fields.

        Returns:
            List of Msg objects with properly structured content blocks.
        """
        msg_list: list[Msg] = []
        for msg_dict in messages:
            role = msg_dict["role"]
            content_blocks: List[
                TextBlock | ToolUseBlock | ToolResultBlock
            ] = []
            content = msg_dict.get("content")

            # Convert text content to appropriate content blocks
            if content:
                if role in ["user", "system", "assistant"]:
                    content_blocks.append(TextBlock(type="text", text=content))
                elif role in ["tool"]:
                    # Tool messages are converted to system messages with
                    # ToolResultBlock
                    role = "system"
                    content_blocks.append(
                        ToolResultBlock(
                            type="tool_result",
                            name=msg_dict.get("name"),
                            id=msg_dict.get("tool_call_id"),
                            output=[TextBlock(type="text", text=content)],
                        ),
                    )

            # Convert tool_calls to ToolUseBlock content blocks
            if msg_dict.get("tool_calls"):
                for tool_call in msg_dict["tool_calls"]:
                    # Parse tool arguments with repair for malformed JSON
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
        """Retrieve operation is not implemented for ReMe short-term memory.

        ReMe focuses on working memory management (compaction and compression)
        rather than retrieval from long-term storage.

        Raises:
            NotImplementedError: This operation is not supported.
        """
        raise NotImplementedError
