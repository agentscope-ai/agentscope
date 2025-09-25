# -*- coding: utf-8 -*-
"""Module for refer node related functions."""
import time
from typing import Dict, Any
from collections.abc import Generator

from .node import Node
from .utils import NodeType
from ..utils.misc import get_model_instance
from ...core.node_caches.workflow_var import WorkflowVariable, DataType


class ReferNode(Node):
    """
    A node for referring to external applications or workflows through
    model management. It handles the configuration and execution of model
    based on application reference codes.
    """

    def _execute(self, **kwargs: Any) -> Generator:
        start_time = int(time.time() * 1000)
        node_param = self.sys_args["node_param"]
        user_prompt = self.sys_args["input_params"][0]["value"]
        app_refer_code = node_param["appReferCode"]

        model = get_model_instance(
            {
                "app_id": app_refer_code,
            },
        )

        messages = [{"role": "user", "content": user_prompt}]
        response = model(messages)

        node_exec_time = str(int(time.time() * 1000) - start_time) + "ms"
        yield [
            WorkflowVariable(
                name="output",
                content=response.text,
                source=self.node_id,
                data_type=DataType.STRING,
                input=self.sys_args.get("input_params"),
                node_type=self.node_type,
                node_name=self.node_name,
                node_exec_time=node_exec_time,
            ),
        ]

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
        Compiles the refer node, setting up model configurations and
        execution logic to interact with specified external resources.

        Returns:
            A dictionary with sections for imports, initializations, and execs.
        """
        model_str = self.build_graph_var_str("model")
        config = self.sys_args
        user_prompt = config["input_params"][0]["value"]
        config_str = self.node_kwargs["id"]

        node_param = config["node_param"]
        app_refer_code = node_param["appReferCode"]

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
            "model_type": "dashscope_application",
            "app_id": "{app_refer_code}",
            "stream": {"True" if self.stream else "False"},
        }}
    ]
)
{model_str} = ModelManager.get_instance().get_model_by_config_name(
    "{config_str}",
)
"""

        execs_str = f"""
messages = [
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
"""
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


class AppReferNode(ReferNode):
    """
    A specialized refer node for application reference,
    inheriting behavior from ReferNode.
    """

    node_type: str = NodeType.APP_REFER.value


class WorkflowReferNode(ReferNode):
    """
    A specialized refer node for workflow reference,
    inheriting behavior from ReferNode.
    """

    node_type: str = NodeType.WORKFLOW_REFER.value
