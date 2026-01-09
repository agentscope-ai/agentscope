# -*- coding: utf-8 -*-
"""Set up an A2A server with a ReAct agent to handle the input query"""
import os
import uuid
import copy
from typing import AsyncGenerator, Any

from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import Event
from a2a.types import (
    Task,
    TaskStatus,
    TaskState,
    Message,
    MessageSendParams,
    TaskStatusUpdateEvent,
)
from starlette.middleware.cors import CORSMiddleware

from agent_card import agent_card
from prompt_builder import get_ui_prompt, get_text_prompt
from tools import get_restaurants, book_restaurants
from a2ui_utils import (
    check_a2ui_extension,
    pre_process_request_with_ui_event,
    post_process_a2a_message_for_ui,
    A2UI_SKILL_INSTRUCTION,
    A2UI_SKILL_TEMPLATE_MINIMAL,
    A2UIResponse,
)
from agentscope._logging import logger
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter, A2AChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.session import JSONSession
from agentscope.tool import (
    Toolkit,
    view_text_file,
    execute_python_code,
    execute_shell_command,
)
from agentscope.message import Msg


def get_final_structured_output(message: Msg) -> None | str:
    """Get the final structured output from the message."""
    if isinstance(message.content, list):
        for block in message.content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "generate_response"
            ):
                input_data = block.get("input")
                if input_data is not None and isinstance(input_data, dict):
                    return input_data.get("response_with_a2ui")
    return None


class SimpleStreamHandler:
    """A simple request handler that handles the input query by an
    ReAct agent."""

    async def _prepare_final_message(
        self,
        formatter: A2AChatFormatter,
        final_msg_text: str | None,
        last_complete_msg: Msg | None,
    ) -> Message:
        """Prepare the final message for response.

        Args:
            formatter: The A2AChatFormatter instance.
            final_msg_text: The structured output text if available.
            last_complete_msg: The last complete message if available.

        Returns:
            The prepared final message.
        """
        logger.info(
            "--- Processing final response, final_msg_text: %s ---",
            final_msg_text is not None,
        )

        if final_msg_text is not None:
            logger.info("--- Using structured output for final message ---")
            final_a2a_message = await formatter.format(
                [Msg(name="Friday", content=final_msg_text, role="assistant")],
            )
        else:
            logger.info(
                "--- Using last complete message for final message ---",
            )
            final_a2a_message = await formatter.format(
                [copy.deepcopy(last_complete_msg)],
            )

        logger.info(
            "--- Post-processing message for UI: %s ---",
            final_a2a_message,
        )
        final_a2a_message = post_process_a2a_message_for_ui(
            final_a2a_message,
            is_final=True,
        )
        return final_a2a_message

    async def on_message_send(
        self,  # pylint: disable=unused-argument
        params: MessageSendParams,
        *args: Any,
        **kwargs: Any,
    ) -> Task:
        """Handles non-streaming message_send requests by collecting
        events from the stream and returning the final Task.

        Args:
            params (`MessageSendParams`):
                The parameters for sending the message.

        Returns:
            The final Task object.
        """
        logger.info("--- params: %s ---", params)
        logger.info("args: %s ---", args)
        logger.info("kwargs: %s ---", kwargs)
        # Collect all events from the stream
        final_event = None
        task_id = params.message.task_id or uuid.uuid4().hex
        context_id = params.message.context_id or "default-context"

        async for event in self.on_message_send_stream(
            params,
            *args,
            **kwargs,
        ):
            if event.final:
                final_event = event
                break

        # Ensure we always return a valid Task
        if final_event is None:
            # If no final event was found, create one with completed state
            logger.warning(
                "No final event found in stream, "
                "creating default completed event",
            )
            final_event = TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.failed),
                final=True,
            )

        # Convert TaskStatusUpdateEvent to Task
        # A2A protocol expects on_message_send to return a Task,
        # not TaskStatusUpdateEvent
        return Task(
            id=final_event.task_id,
            context_id=final_event.context_id,
            status=final_event.status,
            artifacts=[],
        )

    async def on_message_send_stream(
        self,  # pylint: disable=unused-argument
        params: MessageSendParams,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[Event, None]:
        """Handles the message_send method by the agent

        Args:
            params (`MessageSendParams`):
                The parameters for sending the message.

        Returns:
            `AsyncGenerator[Event, None]`:
                An asynchronous generator that yields task status update
                events.
        """

        task_id = params.message.task_id or uuid.uuid4().hex
        context_id = params.message.context_id or "default-context"
        # ============ Agent Logic ============

        # Register the tool functions
        toolkit = Toolkit(
            agent_skill_instruction=A2UI_SKILL_INSTRUCTION,
            agent_skill_template=A2UI_SKILL_TEMPLATE_MINIMAL,
        )
        toolkit.register_tool_function(execute_python_code)
        toolkit.register_tool_function(execute_shell_command)
        toolkit.register_tool_function(view_text_file)
        toolkit.register_tool_function(get_restaurants)
        toolkit.register_tool_function(book_restaurants)
        # Get the skill path relative to this file
        # From restaurant_finder/ to a2ui_agent/skills/A2UI_response_generator
        skill_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "..",
                "skills",
                "A2UI_response_generator",
            ),
        )
        toolkit.register_agent_skill(skill_path)
        logger.info("Agent skill prompt: %s", toolkit.get_agent_skill_prompt())
        use_ui = check_a2ui_extension(*args)

        if use_ui:
            system_prompt = get_ui_prompt()
        else:
            system_prompt = get_text_prompt()

        # Create the agent instance
        agent = ReActAgent(
            name="Friday",
            sys_prompt=system_prompt,
            model=DashScopeChatModel(
                model_name="qwen-max",
                api_key=os.getenv("DASHSCOPE_API_KEY"),
            ),
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
            max_iters=10,
        )
        logger.info("Agent system prompt: %s", agent.sys_prompt)

        session = JSONSession(save_dir="./sessions")
        session_id = params.message.task_id or "test-a2ui-agent"
        await session.load_session_state(
            session_id=session_id,
            agent=agent,
        )

        # pre-process the A2A message with UI event,
        # and then convert to AgentScope Msg objects
        formatter = A2AChatFormatter()
        as_msg = await formatter.format_a2a_message(
            name="Friday",
            message=pre_process_request_with_ui_event(
                params.message,
            ),
        )

        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.working),
            final=False,
        )

        # Collect all messages from the stream
        # The 'last' flag indicates the last chunk of a streaming message,
        # not the last message from the agent
        final_msg_text = None
        last_complete_msg = None  # Track the last complete message
        message_count = 0
        try:
            async for msg, last in stream_printing_messages(
                agents=[agent],
                coroutine_task=agent(as_msg, structured_model=A2UIResponse),
            ):
                message_count += 1
                if last:
                    # Print message content preview (first 100 characters)
                    log_text = f"----{msg}"
                    logger.info(log_text[0 : min(len(log_text), 500)])
                    # Track the last complete message
                    # (only keep reference, no expensive ops)
                    last_complete_msg = copy.deepcopy(msg)
                    if isinstance(msg.content, list):
                        structured_output = get_final_structured_output(msg)
                        if structured_output:
                            final_msg_text = structured_output
                            break
        except Exception as e:
            logger.error(
                "--- Error in message stream: %s ---",
                e,
                exc_info=True,
            )
            raise
        finally:
            logger.info(
                "--- Message stream collection completed. "
                "Total messages: %s, "
                "Last message: %s ---",
                message_count,
                last_complete_msg,
            )

        # Save session state (move before final message processing
        # to avoid blocking yield)
        await session.save_session_state(
            session_id=session_id,
            agent=agent,
        )

        final_a2a_message = await self._prepare_final_message(
            formatter,
            final_msg_text,
            last_complete_msg,
        )

        logger.info("--- Yielding final TaskStatusUpdateEvent ---")
        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.input_required,
                message=final_a2a_message,
            ),
            final=True,
        )


handler = SimpleStreamHandler()
app_instance = A2AStarletteApplication(
    agent_card,
    handler,
)
app = app_instance.build()

# Add CORS middleware to handle OPTIONS requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=False,  # Cannot use "*" with credentials=True
    allow_methods=["*"],  # Allow all HTTP methods including OPTIONS
    allow_headers=["*"],  # Allow all headers
)
