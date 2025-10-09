# -*- coding: utf-8 -*-
"""Module for agent group node related functions."""
import time
from typing import Dict, Any, Generator

from agentscope.message import Msg

from .scheduling import scheduling_pipeline, get_language_dependent_resources
from ..subgraph import WorkflowNode
from ...utils.misc import get_model_instance, get_module_source_content
from ....core.node_caches.workflow_var import WorkflowVariable, DataType
from ..utils import NodeType


class AgentGroupNode(WorkflowNode):
    """Class for agent group node."""

    node_type: str = NodeType.AGENT_GROUP.value

    def _execute(self, **kwargs: Any) -> Generator:
        start_time = int(time.time() * 1000)
        from ...interface.run import exec_runner

        node_param = self.sys_args["node_param"]
        query = self.sys_args["input_params"][0]["value"]

        scheduler_model_instance = get_model_instance(
            node_param["modelConfig"],
        )

        app_list = node_param["appList"]
        agent_group_name = self.node_kwargs.get("name")

        short_memory = node_param["shortMemory"]
        short_memory_enable = short_memory.get("enable", False)
        if short_memory_enable:
            short_memory_type = short_memory.get("type", "custom")
            if short_memory_type == "custom":
                # TODO: fix history_list
                # history_list = short_memory["param"]["value"]
                messages = Msg(
                    role="user",
                    name="user",
                    content=f"User Query：{query}",
                )
            elif short_memory_type == "self":
                short_memory_window = short_memory.get("window", 10)
                short_memory_messages = kwargs.get("global_cache", {}).get(
                    "memory",
                    [],
                )
                used_short_messages = short_memory_messages[
                    -short_memory_window:
                ]
                messages = []
                for short_message in used_short_messages:
                    self.logger.query_info(
                        request_id=self.request_id,
                        message=short_message["status"]["inter_results"][
                            self.node_id
                        ]["results"][-1],
                    )
                    messages.extend(
                        [
                            Msg(
                                role="user",
                                name="user",
                                content=short_message["status"][
                                    "inter_results"
                                ][self.node_id]["results"][-1]["input"],
                            ),
                            Msg(
                                role="assistant",
                                name=agent_group_name,
                                content=short_message["status"][
                                    "inter_results"
                                ][self.node_id]["results"][-1]["content"][
                                    "agProgress"
                                ],
                            ),
                        ],
                    )
                messages.append(
                    Msg(
                        role="user",
                        name="user",
                        content=f"User Query：{query}",
                    ),
                )
        else:
            messages = Msg(
                role="user",
                name="user",
                content=f"User Query：{query}",
            )

        dependency, subgraph_config = scheduling_pipeline(
            scheduler_model_instance,
            app_list,
            messages,
        )

        # TODO: check language
        (
            _,
            _,
            _,
            _,
            scheduling_progress_format,
            generation_result_desc,
        ) = get_language_dependent_resources(language="en")

        global_cache = kwargs.get("global_cache", {})
        # Use raw config to avoid var being replaced
        subgraph_dsl_config = self._build_dsl_config(subgraph_config)
        node_instance = kwargs.get("graph").node_instance

        for _, messages in exec_runner(
            params=self.params,
            dsl_config=subgraph_dsl_config,
            request_id=self.request_id,
            node_instance=node_instance,
            disable_pause=True,
            inter_results=global_cache,
            subgraph_mode=True,
        ):
            inter_results = messages[-1]["status"]["inter_results"]

            subgraph_var = WorkflowVariable(
                name="subgraph",
                content=inter_results,
                source=self.node_id,
                data_type=DataType.OBJECT,
            )
            output_var = [subgraph_var]
            yield output_var

        ag_progress = []
        ag_result = []

        for node_id in inter_results:
            if node_id == "memory":
                continue
            for node_config in subgraph_config["nodes"]:
                if node_id == node_config["id"] and "ag_p_or_r" in node_config:
                    ag_progress.append(
                        f"""## {node_config["appName"]}
### {generation_result_desc}
{inter_results[node_id]["results"][0]["content"]}""",
                    )
                    if node_config["ag_p_or_r"] == "r":
                        ag_result.append(
                            inter_results[node_id]["results"][0]["content"],
                        )

        ag_results = {
            "agProgress": scheduling_progress_format.format(
                content=query,
                dependence=dependency,
                details="\n".join(ag_progress),
                result="\n".join(ag_result),
            ),
            "agResult": "\n".join(ag_result),
        }

        node_exec_time = str(int(time.time() * 1000) - start_time) + "ms"
        output_var.append(
            WorkflowVariable(
                name="output",
                content=ag_results,
                source=self.node_id,
                data_type=DataType.OBJECT,
                input=query,
                node_type=self.node_type,
                node_name=self.node_name,
                node_exec_time=node_exec_time,
            ),
        )
        yield output_var

    def _mock_execute(self, **kwargs: Any) -> Generator:
        yield [
            WorkflowVariable(
                name="output",
                content={
                    "agProgress": f"This is a mock progress of "
                    f"{self.node_type}.",
                    "agResult": f"This is a mock result of "
                    f"{self.node_type}.",
                },
                source=self.node_id,
                data_type=DataType.OBJECT,
                input=self.sys_args.get("input_params"),
                node_type=self.node_type,
                node_name=self.node_name,
            ),
        ]

    def compile(self) -> Dict[str, Any]:
        scheduler_model_str = self.build_graph_var_str(
            "scheduler_model_config",
        )
        config = self.sys_args
        input_ = config["input_params"][0]["value"]

        node_param = config["node_param"]

        group_model_name = node_param["modelConfig"]["modelId"]
        group_generate_args = node_param["modelConfig"]["params"]

        app_list = node_param["appList"]

        basic_agent_name = "basic_agent"
        basic_agent_sys_prompt = (
            "You are a basic intelligent agent based on"
            " Chat LLM, capable of performing fundamental"
            " natural language generation tasks."
        )
        basic_agent_desc = (
            "A basic intelligent agent based on Chat LLM, "
            "capable of performing fundamental natural language generation "
            "tasks."
        )

        import_list = [
            "import os",
            "from agentscope.manager import ModelManager",
            "from agentscope.message.msg import Msg",
            "from agentscope.agent import DialogAgent",
            "from scheduling import scheduling_pipeline",
        ]

        init_str = f"""
ModelManager.get_instance().load_model_configs(
    [
        {{
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
            "config_name": "{self.node_kwargs["id"]}",
            "model_name": "{group_model_name}",
            "model_type": "dashscope_chat",
            "generate_args": {group_generate_args},
            "stream": {"True" if self.stream else "False"},
        }}
    ]
)
{scheduler_model_str} = "{self.node_kwargs["id"]}"

{basic_agent_name} = DialogAgent(
    name="{basic_agent_name}",
    sys_prompt=\"\"\"{basic_agent_sys_prompt}\"\"\",
    model_config_name={scheduler_model_str},
)
"""

        agent_list = [basic_agent_name]
        agent_desc_list = [basic_agent_desc]
        for _, app in enumerate(app_list):
            if app["appCreateType"] == "AppCustom":
                app_str = f"agent_{app['id'].replace('-', '_')}"
                app_agent_name = app["appName"]
                app_desc = app["appDesc"]
                app_model_name = app["appConfig"]["modelId"]
                app_model_config_name = app["id"]
                app_generate_args = app["appConfig"]["appConfig"][
                    "parameterVO"
                ]
                app_instructions = app["appConfig"]["appConfig"].get(
                    "instructions",
                    "",
                )
                init_str += f"""
ModelManager.get_instance().load_model_configs(
    [
        {{
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
            "config_name": "{app_model_config_name}",
            "model_name": "{app_model_name}",
            "model_type": "dashscope_chat",
            "generate_args": {app_generate_args},
            "stream": {"True" if self.stream else "False"},
        }}
    ]
)
{app_str} = DialogAgent(
    name="{app_agent_name}",
    sys_prompt=\"\"\"{app_instructions}\"\"\",
    model_config_name="{app_model_config_name}",
)
"""
                agent_list.append(app_str)
                agent_desc_list.append(app_desc)

            elif app["appCreateType"] == "AppRefer":
                app_id = app["id"]
                app_name = app["appName"]
                app_desc = app["appDesc"]

                init_str += f"""
ModelManager.get_instance().load_model_configs(
    [
        {{
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
            "config_name": "{app_id}",
            "app_id": "{app_id}",
            "model_type": "dashscope_application",
            "stream": {"True" if self.stream else "False"},
        }}
    ]
)
{app_name} = DialogAgent(
    name="{app_name}",
    sys_prompt="",
    model_config_name="{app_id}",
)
"""
                agent_list.append(app_name)
                agent_desc_list.append(app_desc)

        agent_list_str = ",\n    ".join(agent_list)

        execs_str = f"""
msg = Msg(role="user", name="user", content="User Query：{input_}")

{self.build_graph_var_str("result")} = scheduling_pipeline(
    {scheduler_model_str},
    [{agent_list_str}],
    {agent_desc_list},
    msg,
)
{self.build_node_output_str(self.build_graph_var_str("result"), str)}
"""

        current_module = __name__.rsplit(".", 1)[0]

        scheduling_module_name = f"{current_module}.scheduling_compile"
        scheduling_contents = get_module_source_content(
            scheduling_module_name,
        )

        prompt_module_name = f"{current_module}._prompt"
        prompt_contents = get_module_source_content(prompt_module_name)

        return {
            "imports": import_list,
            "inits": [init_str],
            "execs": [execs_str],
            "increase_indent": False,
            "dependency": [
                {
                    "name": "scheduling_pipeline",
                    "path": ".",
                    "script": scheduling_contents,
                },
                {
                    "name": "_prompt",
                    "path": ".",
                    "script": prompt_contents,
                },
            ],
        }
