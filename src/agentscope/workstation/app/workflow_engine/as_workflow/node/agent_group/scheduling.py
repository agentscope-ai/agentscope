# -*- coding: utf-8 -*-
"""scheduling function"""
# pylint: disable=too-many-branches, too-many-statements

import re
from collections import defaultdict
from typing import Optional, Pattern, Union, Sequence

import networkx as nx
from agentscope.message import Msg
from agentscope.model import ModelWrapperBase

from ._prompt import (
    agent_sys_prompt_without_context_en,
    agent_sys_prompt_without_context_zh,
    agent_sys_prompt_with_context_en,
    agent_sys_prompt_with_context_zh,
    scheduler_sys_prompt_en,
    scheduler_sys_prompt_zh,
    scheduling_progress_format_zh,
    scheduling_progress_format_en,
)
from ..common.dialog_agent import WorkflowDialogAgent


def scheduling_pipeline(
    model_instance: ModelWrapperBase,
    app_list: list[dict],
    msg: Optional[Union[Sequence[Msg], Msg]],
    language: str = "en",
) -> tuple:
    """scheduling procedure.

    Args:
        model_instance (`ModelWrapperBase`): The model wrapper instance for
            Scheduler agent.
        app_list (`list[str]`): app list in group.
        msg (`Optional[Msg]`, defaults to `None`):
            The input Msg.
        language (`str`): The language

    Returns:
        `Msg`: the output message.
    """

    (
        step_pattern,
        _,
        scheduler_sys_prompt,
        _,
        _,
        _,
    ) = get_language_dependent_resources(language)

    app_list_id_mapping = {app["id"]: app for app in app_list}

    agent_name_list = []
    agent_id_list = []
    agent_desc_list = []
    for _, app in enumerate(app_list):
        app_agent_name = app["appName"]
        agent_name_list.append(app_agent_name)
        app_id = app["id"]
        agent_id_list.append(app_id)
        app_desc = app["appDesc"]
        agent_desc_list.append(app_desc)

    # desc_dict = {}
    candidate_parts = []
    for i, agent_id in enumerate(agent_id_list):
        candidate_parts.append(
            f"""
### {agent_id}
{agent_desc_list[i]}""",
        )

    candidates = "".join(candidate_parts).strip()

    candidates = candidates.strip()

    scheduler_agent = WorkflowDialogAgent(
        name="scheduler",
        sys_prompt=scheduler_sys_prompt.format(candidates=candidates),
    )
    scheduler_agent.model = model_instance

    scheduler_result = scheduler_agent(msg)
    matches = step_pattern.findall(scheduler_result.content)

    dependence = format_dependency(matches, language=language)
    for app in app_list:
        dependence = dependence.replace(app["id"], app["appName"])

    subgraph_config = generate_subgraph_config(
        matches,
        app_list_id_mapping,
        msg=msg,
        language=language,
    )

    return dependence, subgraph_config


def format_dependency(steps: list[tuple], language: str = "en") -> str:
    """
        Formats a list of step tuples into a string that describes the
        dependency relationships among steps.

        Each step tuple is expected to contain:
        - step_number: The identifier for the step.
        - _: (Unused in this function) Can be used to pass additional
            information.
        - agent: The name of the agent responsible for the step.
        - dependency: The identifier(s) of the step(s) this step depends
            on, or "None" if there are no dependencies.

    Parameters:
        steps (list of tuples): A list of tuples where each tuple
            represents a step. Each tuple is formatted as (step_number, _,
            agent, dependency).
        language (str): The language

    Returns:
        str: A formatted string where each line represents a step and
            its dependencies. Each line is of the format
            "step_number.agent (dependent on dependency)" if there are
            dependencies, or "step_number.agent" if there are no
            dependencies.

    Example:
        Input: [(1, _, "AgentA", "None"), (2, _, "AgentB", "1")]
        Output:
            "1.AgentA
             2.AgentB (dependent on 1)"
    """

    no_dependency_label = "无" if language == "zh" else "None"

    dependency_description = "依赖" if language == "zh" else "depends on"

    formatted_dependency = []
    for step in steps:
        step_number, _, agent, dependency = step
        if dependency.strip() != no_dependency_label:
            dependency_str = f"({dependency_description} {dependency})"
        else:
            dependency_str = ""
        formatted_dependence = f"{step_number}.{agent}{dependency_str}"
        formatted_dependency.append(formatted_dependence)
    return "\n".join(formatted_dependency)


def generate_subgraph_config(
    task_list: list[tuple],
    app_list_id_mapping: dict,
    msg: Optional[Union[Sequence[Msg], Msg]],
    language: str = "en",
) -> dict:
    """
    Performs a topological sort on a list of tasks to determine a
        feasible sequence of execution based on dependencies.

    Each task in the task_list is expected to be a tuple containing:
    - step_number: A unique identifier for the task.
    - _: (Unused in this function) Placeholder for additional information.
    - agent: The name of the agent responsible for executing the task.
    - dependencies: A string containing step_numbers this task depends
        on, separated by commas, or "None" if no dependencies.

    Parameters:
        task_list (List[Tuple[str, str, str, str]]): A list of tuples,
            where each tuple represents a task with its associated details.
        app_list_id_mapping (dict): The mapping with unique keys (i.e. ids).
        msg: The input message.
        language (str): The language

    Returns:
        dict:
            A dictionary of the subgraph config

    Raises:
        ValueError: If there is a circular dependency that prevents
            topological sorting or if a task has undefined dependencies.

    Example:
        Input: [
            ("1", "subtask", "AgentA", "None"),
            ("2", "subtask", "AgentB", "1"),
            ("3", "subtask", "AgentC", "2,1")
        ]
        Output: {{"nodes": [...], "edges": [...]}
    """
    if isinstance(msg, list):
        msg = msg[-1]

    (
        _,
        agent_sys_prompt_with_context,
        _,
        agent_sys_prompt_without_context,
        _,
        _,
    ) = get_language_dependent_resources(language)

    subgraph_config = {"nodes": [], "edges": []}

    subgraph_start_node_id = "WorkflowStart_ag"
    subgraph_end_node_id = "WorkflowEnd_ag"

    subgraph_config["nodes"].append(
        {
            "id": subgraph_start_node_id,
            "name": "Start",
            "type": "Start",
            "config": {
                "input_params": [],
                "outputParams": [],
                "node_param": {
                    "outputType": "text",
                    "textTemplate": "",
                    "jsonParams": [],
                },
            },
        },
    )
    subgraph_config["nodes"].append(
        {
            "id": subgraph_end_node_id,
            "name": "End",
            "type": "End",
            "config": {
                "input_params": [],
                "outputParams": [],
                "node_param": {
                    "outputType": "text",
                    "textTemplate": "",
                    "jsonParams": [],
                },
            },
        },
    )

    task_list_order_mapping = {task[0]: task for task in task_list}

    no_dependency_label = "无" if language == "zh" else "None"

    G = nx.DiGraph()

    task_map = {task[0]: task for task in task_list}
    dependencies_by_task = defaultdict(list)

    # Populate the graph and map dependencies with unique identifiers
    for step_number, subtask, agent, dependencies in task_list:
        G.add_node(step_number)

        node_param = app_list_id_mapping[agent]["appConfig"]

        node_config = app_list_id_mapping[agent]
        node_config["config"] = {"node_param": node_param}
        node_config["type"] = node_config["appCreateType"]
        del node_config["appConfig"]
        node_config["config"]["outputParams"] = [
            {
                "desc": "文本输出",
                "isSystem": True,
                "key": "result",
                "type": "String",
            },
        ]

        if dependencies.strip() != no_dependency_label:
            context_list = []
            for dep in dependencies.split(","):
                dep = dep.strip()
                context_list.append(
                    f"${{{task_list_order_mapping[dep][2]}.result}}",
                )
            context = "\n".join(context_list)
            node_config["config"]["input_params"] = [
                {
                    "key": "content",
                    "type": "String",
                    "value": agent_sys_prompt_with_context.format(
                        task=msg.content,
                        context=context,
                        subtask=subtask,
                    ),
                    "valueFrom": "refer",
                },
            ]
        else:
            node_config["config"]["input_params"] = [
                {
                    "key": "content",
                    "type": "String",
                    "value": agent_sys_prompt_without_context.format(
                        task=msg.content,
                        subtask=subtask,
                    ),
                    "valueFrom": "input",
                },
            ]
        subgraph_config["nodes"].append(
            node_config,
        )

        if dependencies.strip() != no_dependency_label:
            for dep in dependencies.split(","):
                dep = dep.strip()
                G.add_edge(dep, step_number)
                dependencies_by_task[(step_number, agent)].append(
                    (dep, task_map[dep][2]),
                )

                source = task_list_order_mapping[dep][2]
                source_handle = source
                target = task_list_order_mapping[step_number][2]
                target_handle = target
                subgraph_config["edges"].append(
                    {
                        "id": f"xy-edges__{source}{source_handle}-{target}"
                        f"{target_handle}",
                        "source": source,
                        "sourceHandle": source_handle,
                        "target": target,
                        "targetHandle": target_handle,
                    },
                )
    for node in G.nodes:
        node_id = task_list_order_mapping[node][2]
        if G.in_degree(node) == 0:
            subgraph_config["edges"].append(
                {
                    "id": f"xy-edges__{subgraph_start_node_id}"
                    f"{subgraph_start_node_id}-{node_id}{node_id}",
                    "source": subgraph_start_node_id,
                    "sourceHandle": subgraph_start_node_id,
                    "target": node_id,
                    "targetHandle": node_id,
                },
            )

            # Mark the node to determine whether the node's result in agent
            # group progress or result
            for node_config in subgraph_config["nodes"]:
                if node_config["id"] == node_id:
                    node_config["ag_p_or_r"] = "p"
                    break
        if G.out_degree(node) == 0:
            subgraph_config["edges"].append(
                {
                    "id": f"xy-edges__{node_id}{node_id}-"
                    f"{subgraph_end_node_id}{subgraph_end_node_id}",
                    "source": node_id,
                    "sourceHandle": node_id,
                    "target": subgraph_end_node_id,
                    "targetHandle": subgraph_end_node_id,
                },
            )
            for node_config in subgraph_config["nodes"]:
                if node_config["id"] == node_id:
                    node_config["ag_p_or_r"] = "r"
                    break

    # Perform topological sort
    try:
        nx.topological_sort(G)
    except nx.NetworkXUnfeasible as exc:
        raise ValueError("Circular dependency detected") from exc

    return subgraph_config


def get_language_dependent_resources(
    language: str = "en",
) -> tuple[Pattern, str, str, str, str, str]:
    """
    Returns the appropriate regex expressions and string resources based on
        the specified language.

    Args:
        language (str): Language code used to select  resources, e.g.,
            "zh" for Chinese, "en" for English.

    Returns:
        Tuple[Pattern, str, str, str, str, str]:
            - Regex pattern object for matching step descriptions.
            - System prompt with context.
            - Scheduler system prompt.
            - System prompt without context.
            - Scheduling progress format.
            - Description text for generation results.
    """

    step_pattern = (
        re.compile(
            r"# Step-(\d+)：\n<子任务>：(.*?)\n<智能体>：(.*?)\n<依赖信息>：("
            r".*?)(?=\n# Step|\n$|$)",
            re.DOTALL,
        )
        if language == "zh"
        else re.compile(
            r"# Step-(\d+):\n<Subtask>: (.*?)\n<Agent>: (.*?)\n<Dependency>: ("
            r".*?)(?=\n# Step|\n$|$)",
            re.DOTALL,
        )
    )

    agent_sys_prompt_with_context = (
        agent_sys_prompt_with_context_zh
        if (language == "zh")
        else agent_sys_prompt_with_context_en
    )

    scheduler_sys_prompt = (
        scheduler_sys_prompt_zh
        if (language == "zh")
        else scheduler_sys_prompt_en
    )

    agent_sys_prompt_without_context = (
        agent_sys_prompt_without_context_zh
        if (language == "zh")
        else agent_sys_prompt_without_context_en
    )

    scheduling_progress_format = (
        scheduling_progress_format_zh
        if (language == "zh")
        else scheduling_progress_format_en
    )

    generation_result_desc = (
        "执行结果" if language == "zh" else ("Generation " "Results")
    )

    return (
        step_pattern,
        agent_sys_prompt_with_context,
        scheduler_sys_prompt,
        agent_sys_prompt_without_context,
        scheduling_progress_format,
        generation_result_desc,
    )
