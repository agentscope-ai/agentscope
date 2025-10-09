# -*- coding: utf-8 -*-
"""Module for agent node related functions."""
import os
import time
from typing import Dict, Any, Generator

import dashscope
from llama_index.indices.managed.dashscope.retriever import (
    DashScopeCloudRetriever,
)
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank

from agentscope.message import Msg

from .node import Node
from .utils import NodeType
from .common.agent import WorkflowAgent
from ..utils.misc import get_model_instance
from ...core.node_caches.workflow_var import WorkflowVariable, DataType


class AgentNode(Node):
    """Class for agent node related functions."""

    node_type: str = NodeType.AGENT.value

    def create_instance(self) -> None:
        node_param = self.sys_args["node_param"]
        agent_name = node_param["appName"]

        app_config = node_param["appConfig"]
        sys_prompt = app_config.get("instructions", "")
        model_name = node_param["modelId"]
        generate_args = app_config["parameterVO"]
        model_config = {"modelId": model_name, "params": generate_args}
        model_instance = get_model_instance(model_config)

        self._persistent_instance = WorkflowAgent(
            name=agent_name,
            sys_prompt=sys_prompt,
        )
        self._persistent_instance.model = model_instance

    def _execute(self, **kwargs: Any) -> Generator:
        start_time = int(time.time() * 1000)
        query = self.sys_args["input_params"][0].get("value")

        msg = Msg(role="user", name="user", content=query)

        node_param = self.sys_args["node_param"]
        app_config = node_param["appConfig"]
        rag_config = app_config.get("ragConfig", {})
        enable_rag = rag_config.get("enableSearch", False)
        retriever_text = ""
        if enable_rag:
            knowledge_base_ids = rag_config.get(
                "knowledge_base_ids",
                [],
            )
            top_k = rag_config.get("top_k", 3)
            # enable_citation = rag_config.get("enableCitation", False)
            if len(knowledge_base_ids) > 1:
                dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

                nodes_list = []

                for idx in knowledge_base_ids:
                    # TODO: add workspace id and fix index_name
                    retriever = DashScopeCloudRetriever(
                        index_name=idx,
                        rerank_top_n=top_k,
                        enable_rewrite=True,
                        api_key=os.getenv("DASHSCOPE_API_KEY"),
                    )

                    nodes_list.extend(retriever.retrieve(query))

                dashscope_rerank = DashScopeRerank(top_n=top_k)

                nodes = dashscope_rerank.postprocess_nodes(
                    nodes_list,
                    query_str=query,
                )
                retriever_text = "".join(_.node.get_content() for _ in nodes)
            else:
                # TODO: add workspace id and fix index_name
                retriever = DashScopeCloudRetriever(
                    index_name=knowledge_base_ids[0],
                    rerank_top_n=top_k,
                    enable_rewrite=True,
                    api_key=os.getenv("DASHSCOPE_API_KEY"),
                )
                nodes = retriever.retrieve(query)
                retriever_text = "".join(_.node.get_content() for _ in nodes)

        persistent_instance = self.persistent_instance

        original_sys_prompt = persistent_instance.sys_prompt
        if "${documents}" in persistent_instance.sys_prompt:
            persistent_instance.sys_prompt = (
                persistent_instance.sys_prompt.replace(
                    "${documents}",
                    retriever_text,
                )
            )

        result = persistent_instance(msg).content
        persistent_instance.sys_prompt = original_sys_prompt

        node_exec_time = str(int(time.time() * 1000) - start_time) + "ms"
        yield [
            WorkflowVariable(
                name="output",
                content=result,
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
                content=f"This is a mock result of {self.node_type} node.",
                source=self.node_id,
                data_type=DataType.STRING,
                input=self.sys_args.get("input_params"),
                node_type=self.node_type,
                node_name=self.node_name,
            ),
        ]

    def compile(self) -> Dict[str, Any]:
        agent_str = self.build_graph_var_str("agent")
        config = self.sys_args
        query = config["input_params"][0]["value"]

        node_param = config["node_param"]
        agent_name = node_param["appName"]
        app_config = node_param["appConfig"]
        sys_prompt = app_config.get("instructions", "")
        generate_args = app_config.get("parameterVO", {})

        # tools = app_config.get("tools", [])
        # workflows = app_config.get("workflows", [])
        # app_refer_params = app_config.get("appReferParams", [])
        rag_config = app_config.get("ragConfig", {})
        enable_rag = rag_config.get("enableSearch", False)

        import_list = [
            "import os",
            "from agentscope.manager import ModelManager",
            "from agentscope.message.msg import Msg",
            "from agentscope.agent import DialogAgent",
        ]

        init_str = ""

        if enable_rag:
            import_list.extend(
                [
                    "import dashscope",
                    "from "
                    "llama_index.indices.managed.dashscope.retriever"
                    " import DashScopeCloudRetriever",
                ],
            )
            retriever_str = self.build_graph_var_str("retriever")
            nodes_list_name = self.build_graph_var_str("node_list")
            dashscope_rerank_str = self.build_graph_var_str("dashscope_rerank")
            retriever_text = self.build_graph_var_str("retriever_text")
            # TODO: get the knowledge list from the config (TBD by bailian)
            # TODO: get workspace id
            knowledge_base_ids = rag_config.get(
                "knowledge_base_ids",
                [],
            )
            top_k = rag_config.get("top_k", 3)
            # enable_citation = rag_config.get("enableCitation", False)
            if len(knowledge_base_ids) > 1:
                import_list.append(
                    "from llama_index.postprocessor.dashscope_rerank import"
                    " DashScopeRerank",
                )
                init_str = f"""
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

{nodes_list_name} = []

for idx in {knowledge_base_ids}:
    # TODO: add workspace id
    {retriever_str} = DashScopeCloudRetriever(
        index_name=idx,
        rerank_top_n={top_k},
        enable_rewrite=True,
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )

    {nodes_list_name}.extend({retriever_str}.retrieve("{query}"))

{dashscope_rerank_str} = DashScopeRerank(
    top_n={top_k},
)

nodes = dashscope_rerank.postprocess_nodes(
    {nodes_list_name},
    query_str="{query}"
)
{retriever_text} = "".join(_.node.get_content() for _ in nodes)
"""
            else:
                init_str = f"""
{retriever_str} = DashScopeCloudRetriever(
    index_name="{knowledge_base_ids[0]}",
    rerank_top_n={top_k},
    enable_rewrite=True,
    api_key=os.getenv("DASHSCOPE_API_KEY"),
)
nodes = {retriever_str}.retrieve({query})
{retriever_text} = "".join(_.node.get_content() for _ in nodes)
"""
            if "${documents}" in sys_prompt:
                sys_prompt = sys_prompt.replace("${documents}", retriever_text)
        if "${documents}" in sys_prompt:
            sys_prompt = sys_prompt.replace("${documents}", "")

        init_str += f"""
ModelManager.get_instance().load_model_configs(
    [
        {{
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
            "config_name": "{self.node_kwargs["id"]}",
            "model_name": "{node_param["modelId"]}",
            "model_type": "dashscope_chat",
            "generate_args": {generate_args},
            "stream": {"True" if self.stream else "False"},
        }}
    ]
)
{agent_str} = DialogAgent(
    name="{agent_name}",
    sys_prompt=\"\"\"{sys_prompt}\"\"\",
    model_config_name="{self.node_kwargs["id"]}",
)
"""

        execs_str = f"""
msg = Msg(role="user", name="user", content={query})

{self.build_graph_var_str("result")} = {agent_str}(msg).content
# TODO: make the result a generator
{self.build_node_output_str(self.build_graph_var_str("result"), str)}
"""

        return {
            "imports": import_list,
            "inits": [init_str],
            "execs": [execs_str],
            "increase_indent": False,
        }
