# -*- coding: utf-8 -*-
"""Set up an A2A server with a ReAct agent to handle the input query"""
import os
import uuid
from typing import AsyncGenerator, Any

from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import Event
from a2a.types import (
    Task,
    TaskStatus,
    TaskState,
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
)
from agentscope._logging import logger
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter, A2AChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.session import JSONSession
from agentscope.tool import Toolkit, view_text_file


class SimpleStreamHandler:
    """A simple request handler that handles the input query by an
    ReAct agent."""

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
        toolkit = Toolkit()
        # toolkit.register_tool_function(execute_python_code)
        # toolkit.register_tool_function(execute_shell_command)
        toolkit.register_tool_function(view_text_file)
        toolkit.register_tool_function(get_restaurants)
        toolkit.register_tool_function(book_restaurants)

        use_ui = check_a2ui_extension()

        if use_ui:
            system_prompt = get_ui_prompt()
            logger.info("--- Using UI prompt ---")
        else:
            system_prompt = get_text_prompt()
            logger.info("--- Using text-only prompt ---")

        # Create the agent instance
        agent = ReActAgent(
            name="Friday",
            sys_prompt=system_prompt,
            model=DashScopeChatModel(
                model_name="qwen-plus",
                # model_name = "gemini-3-flash-preview",
                api_key=os.getenv("DASHSCOPE_API_KEY"),
            ),
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
        )

        session = JSONSession(save_dir="./sessions")
        session_id = params.message.task_id or "test-a2ui-agent"
        await session.load_session_state(
            session_id=session_id,
            agent=agent,
        )

        # Convert the A2A message to AgentScope Msg objects
        pre_processed_message = pre_process_request_with_ui_event(
            params.message,
        )
        formatter = A2AChatFormatter()
        as_msg = await formatter.format_a2a_message(
            name="Friday",
            message=pre_processed_message,
        )

        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.working),
            final=False,
        )

        async for msg, last in stream_printing_messages(
            agents=[agent],
            coroutine_task=agent(as_msg),
        ):
            # The A2A streaming response is one complete Message object rather
            # than accumulated or incremental text
            if last:
                a2a_message = await formatter.format([msg])
                # post process the message to extract the a2ui JSON parts
                # and add them to the message
                a2a_message = post_process_a2a_message_for_ui(a2a_message)

                yield TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(
                        state=TaskState.working,
                        message=a2a_message,
                    ),
                    final=False,
                )

            # Save session state before yielding final event
            # This ensures the state is saved even if the caller stops
            # iterating after final=True
        await session.save_session_state(
            session_id=session_id,
            agent=agent,
        )

        # Finish the task
        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.input_required,
                message=a2a_message,
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
