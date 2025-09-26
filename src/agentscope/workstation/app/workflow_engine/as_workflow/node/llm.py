# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches, too-many-statements
"""Module for LLM node related functions."""
import time
from typing import Dict, Any
from collections.abc import Generator
from agentscope.model import ModelWrapperBase

from .node import Node
from .utils import NodeType
from ..utils.misc import get_model_instance
from ...core.node_caches.workflow_var import WorkflowVariable, DataType
from ...core.utils.misc import format_output


class LLMNode(Node):
    """
    Represents a Language Model Node configured to interact with a language
    model using specified parameters and prompts. Supports context switching
    to incorporate conversation history.
    """

    node_type: str = NodeType.LLM.value

    def _execute(self, **kwargs: Any) -> Generator:
        start_time = int(time.time() * 1000)
        node_param = self.sys_args["node_param"]
        sys_prompt = node_param["sys_prompt_content"]

        model_invocations = []

        def node_model_invocation_hook(
            model_wrapper: ModelWrapperBase,
            model_invocation_id: str,
            timestamp: str,
            arguments: Dict,  # pylint: disable=unused-argument
            response: Dict,  # pylint: disable=unused-argument
            usage: Dict,
        ) -> None:
            self.logger.query_info(
                request_id=self.request_id,
                message=f"Model usage structure: {usage}",
            )
            invocation_info = {
                "id": model_invocation_id,
                "node_id": self.node_id,
                "timestamp": timestamp,
                "model_name": model_wrapper.model_name,
                "usage": usage,
            }
            model_invocations.append(invocation_info)

        hook_name = f"{self.node_id}_{start_time}_hook"
        ModelWrapperBase.register_save_model_invocation_hook(
            hook_name,
            node_model_invocation_hook,
        )

        user_prompt = node_param["prompt_content"]
        model_config = node_param["model_config"]
        model = get_model_instance(model_config, stream=True)

        # context_switch = node_param.get("contextSwitch", False)
        short_memory = node_param["short_memory"]
        short_memory_enable = short_memory.get("enable", False)

        try_catch_config = node_param.get("try_catch_config", {})
        strategy = try_catch_config.get("strategy", "noop")
        input_dict = {
            "input": {
                "provider": model_config.get("provider"),
                "modelId": model_config.get("model_id"),
                "params": model_config.get("params"),
            },
        }

        try:
            if short_memory_enable:
                short_memory_type = short_memory.get("type", "custom")
                if short_memory_type == "custom":
                    history_list = short_memory["param"]["value"]
                    messages = (
                        [{"role": "system", "content": sys_prompt}]
                        + history_list
                        + [{"role": "user", "content": user_prompt}]
                    )
                    response = model(messages)
                elif short_memory_type == "self":
                    short_memory_window = short_memory.get("window", 10)
                    short_memory_messages = kwargs.get("global_cache", {}).get(
                        "memory",
                        [],
                    )
                    used_short_messages = short_memory_messages[
                        -short_memory_window:
                    ]
                    messages = [
                        {"role": "system", "content": sys_prompt},
                    ]
                    for short_message in used_short_messages:
                        messages.extend(
                            [
                                {
                                    "role": "user",
                                    "content": short_message["status"][
                                        "inter_results"
                                    ][self.node_id]["results"][0]["input"][-1][
                                        "content"
                                    ],
                                },
                                {
                                    "role": "assistant",
                                    "content": short_message["status"][
                                        "inter_results"
                                    ][self.node_id]["results"][0]["content"],
                                },
                            ],
                        )
                    messages.append(
                        {"role": "user", "content": user_prompt},
                    )
                    response = model(messages)

            else:
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                response = model(messages)

            usages = []
            input_dict["input"]["messages"] = messages
            node_exec_time = str(int(time.time() * 1000) - start_time) + "ms"

            for _, text_chunk in response.stream:
                yield [
                    WorkflowVariable(
                        name="output",
                        content=text_chunk,
                        source=self.node_id,
                        data_type=DataType.STRING,
                        input=input_dict,
                        output={"output": text_chunk},
                        output_type="json",
                        node_type=self.node_type,
                        node_name=self.node_name,
                        node_exec_time=node_exec_time,
                        usages=usages,
                    ),
                ]

            if model_invocations:
                try:
                    usages = [
                        {
                            "prompt_tokens": model_invocations[0]["usage"][
                                "usage"
                            ]["prompt_tokens"],
                            "completion_tokens": model_invocations[0]["usage"][
                                "usage"
                            ]["completion_tokens"],
                            "total_tokens": model_invocations[0]["usage"][
                                "usage"
                            ]["total_tokens"],
                        },
                    ]
                except Exception:
                    self.logger.query_error(
                        request_id=self.request_id,
                        message=f"Error when get usage from model_invocations:"
                        f" {model_invocations}",
                    )
                    usages = [
                        {
                            "prompt_tokens": None,
                            "completion_tokens": None,
                            "total_tokens": None,
                        },
                    ]

            yield [
                WorkflowVariable(
                    name="output",
                    content=response.text,
                    source=self.node_id,
                    data_type=DataType.STRING,
                    input=input_dict,
                    output={"output": response.text},
                    output_type="json",
                    node_type=self.node_type,
                    node_name=self.node_name,
                    node_exec_time=node_exec_time,
                    usages=usages,
                ),
            ]
        except Exception as e:
            usages = []
            if strategy == "noop":
                raise e
            if strategy == "defaultValue":
                response = {
                    item["key"]: self.build_var_str(item)
                    for item in try_catch_config.get("default_values")
                }
                input_dict["input"]["messages"] = messages
                node_exec_time = (
                    str(
                        int(time.time() * 1000) - start_time,
                    )
                    + "ms"
                )

                yield [
                    WorkflowVariable(
                        name="output",
                        content=response["output"],
                        source=self.node_id,
                        data_type=DataType.STRING,
                        input=input_dict,
                        output=response,
                        output_type="json",
                        node_type=self.node_type,
                        node_name=self.node_name,
                        node_exec_time=node_exec_time,
                        usages=usages,
                        try_catch={
                            "happened": True,
                            "strategy": "failBranch",
                        },
                    ),
                ]
            elif strategy == "failBranch":
                targets = []
                for source, adjacency in kwargs["graph"].adj.items():
                    if source != self.node_id:
                        continue
                    for target, edges in adjacency.items():
                        for _, data in edges.items():
                            source_handle = self.node_id + "_fail"
                            if data.get("source_handle") != source_handle:
                                continue
                            target = data.get("target")
                            targets.append(target)

                node_exec_time = (
                    str(
                        int(time.time() * 1000) - start_time,
                    )
                    + "ms"
                )
                yield [
                    WorkflowVariable(
                        name="output",
                        content=format_output(e),
                        source=self.node_id,
                        targets=targets,
                        data_type=DataType.STRING,
                        input=input_dict,
                        node_type=self.node_type,
                        output=format_output(e),
                        node_name=self.node_name,
                        node_exec_time=node_exec_time,
                        usages=usages,
                        try_catch={
                            "happened": True,
                            "strategy": "failBranch",
                        },
                    ),
                ]
            else:
                raise ValueError(
                    f"Invalid try_catch_config strategy: {strategy}",
                ) from e
        finally:
            ModelWrapperBase.remove_save_model_invocation_hook(hook_name)

    def _mock_execute(self, **kwargs: Any) -> Generator:
        yield [
            WorkflowVariable(
                name="output",
                content=f"This is a mock response of {self.node_type} node.",
                source=self.node_id,
                data_type=DataType.STRING,
                input=self.sys_args.get("input_params"),
                node_type=self.node_type,
                node_name=self.node_name,
            ),
        ]

    def compile(self) -> Dict[str, Any]:
        """
        Compiles the language model node into a structured dictionary with
        setup and execution logic for processing messages with the language
        model.

        Returns:
            A dictionary with sections for imports, initializations, and execs.
        """
        model_str = self.build_graph_var_str("model")

        node_param = self.sys_args["node_param"]
        config_str = self.node_kwargs["id"]
        model_config = node_param["modelConfig"]
        sys_prompt = node_param["sysPromptContent"]
        user_prompt = node_param["promptContent"]

        context_switch = node_param.get("contextSwitch", False)
        if context_switch:
            history_list = node_param["contextParam"][0]["value"]

        import_list = [
            "import os",
            "from agentscope.manager import ModelManager",
        ]

        init_str = f"""
ModelManager.get_instance().load_model_configs(
    [
        {{
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
            "config_name": "{config_str}",
            "model_type": "dashscope_chat",
            "model_name": "{model_config["modelId"]}",
            "generate_args": {{
                "temperature": {model_config["params"]["temperature"]},
                "max_tokens": {model_config["params"]["maxTokens"]},
                "enable_search": {model_config["params"]["enable_search"]},
            }},
            "stream": {"True" if self.stream else "False"},
        }}
    ]
)
{model_str} = ModelManager.get_instance().get_model_by_config_name(
    "{config_str}",
)
"""
        if context_switch:
            execs_str = f"""
messages = [dict(role="system", content="{sys_prompt}")]
            + {history_list}
            + [dict(role="user", content="{user_prompt}")]
{self.build_graph_var_str("response")} = {model_str}(messages).text
"""
        else:
            execs_str = f"""
messages = [
    dict(role="system", content="{sys_prompt}"),
    dict(role="user", content="{user_prompt}"),
]
{self.build_graph_var_str("response")} = {model_str}(messages)
"""

        stream_resp = (
            f"getattr({self.build_graph_var_str('response')}, 'stream')"
        )

        if self.stream:
            execs_str += f"""
{self.build_node_output_str(stream_resp, Generator, sub_attr="[1]")}
{self.build_graph_var_str("result")} = res[1]
"""  # noqa E501
        else:
            execs_str += f"""
{self.build_graph_var_str("result")} = {self.build_graph_var_str("response")}.text
{self.build_node_output_str(self.build_graph_var_str("result"), str)}
"""  # noqa E501

        return {
            "imports": import_list,
            "inits": [init_str],
            "execs": [execs_str],
            "increase_indent": False,
        }
