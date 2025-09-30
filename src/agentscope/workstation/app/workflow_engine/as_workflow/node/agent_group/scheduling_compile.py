# -*- coding: utf-8 -*-
"""scheduling function"""
import re
from collections import defaultdict
from typing import Sequence, Optional, Pattern

import networkx as nx
from _prompt import (
    agent_sys_prompt_without_context_en,
    agent_sys_prompt_without_context_zh,
    agent_sys_prompt_with_context_en,
    agent_sys_prompt_with_context_zh,
    scheduler_sys_prompt_en,
    scheduler_sys_prompt_zh,
    scheduling_progress_format_zh,
    scheduling_progress_format_en,
)
from agentscope.agent import Operator, DialogAgent
from agentscope.message import Msg


# TODO: combine this with scheduling.scheduling_pipeline
def scheduling_pipeline(
    model_config_name: str,
    operators: Sequence[Operator],
    desc_list: list[str],
    x: Optional[Msg],
    language: str = "en",
) -> dict:
    """scheduling procedure.

    Args:
        model_config_name (`str`): The model config name for
            Scheduler agent.
        operators (`Operators`): Operators executed as the body of
            the function.
        desc_list (`list[str]`): Descriptions corresponding to each operator.
        x (`Optional[Msg]`, defaults to `None`):
            The input Msg.
        language (`str`): The language

    Returns:
        `Msg`: the output message.
    """
    assert len(operators) == len(
        desc_list,
    ), "The number of operators and descriptions must be the same."

    (
        step_pattern,
        agent_sys_prompt_with_context,
        scheduler_sys_prompt,
        agent_sys_prompt_without_context,
        scheduling_progress_format,
        generation_result_desc,
    ) = get_language_dependent_resources(language)

    desc_dict = {}
    candidate_parts = []
    for i, operator in enumerate(operators):
        desc_dict[operator.name] = operator
        candidate_parts.append(
            f"""
### {operator.name}
{desc_list[i]}""",
        )

    candidates = "".join(candidate_parts).strip()

    candidates = candidates.strip()

    scheduler_agent = DialogAgent(
        name="scheduler",
        sys_prompt=scheduler_sys_prompt.format(candidates=candidates),
        model_config_name=model_config_name,
    )

    scheduler_result = scheduler_agent(x)
    matches = step_pattern.findall(scheduler_result.content)

    dependence = format_dependency(matches, language=language)

    matches, dependent_dict = topological_sort(matches, language=language)
    agent_names = [scheduler_agent.name]
    agent_results = [scheduler_result]

    context_dict = {}

    for idx, subtask, agent_name, _ in matches:
        dependencies = dependent_dict[(idx, agent_name)]
        context = "\n".join(
            "\n".join(context_dict[d])
            for d in dependencies
            if d in context_dict
        )
        if context:
            prompt = agent_sys_prompt_with_context.format(
                task=x.content,
                context=context,
                subtask=subtask,
            )
        else:
            prompt = agent_sys_prompt_without_context.format(
                task=x.content,
                subtask=subtask,
            )
        msg = Msg(role="assistant", name=agent_name, content=prompt)
        app_res = desc_dict[agent_name](msg)
        memory_size = desc_dict[agent_name].memory.size()
        desc_dict[agent_name].memory.delete(
            [memory_size - 2, memory_size - 1],
        )
        context_dict.setdefault(agent_name, []).append(app_res.content)
        agent_names.append(agent_name)
        agent_results.append(app_res)
    details = "\n".join(
        f"## {name}\n### {generation_result_desc}\n{result}\n"
        for name, result in zip(
            agent_names,
            [agent_result.content for agent_result in agent_results],
        )
    )

    scheduling_progress = scheduling_progress_format.format(
        content=x.content,
        dependence=dependence,
        details=details,
        result=agent_results[-1].content,
    )

    # TODO change the following to Msg
    return {
        "role": "assistant",
        "name": agent_results[-1].name,
        "content": agent_results[-1].content,
        "metadata": scheduling_progress,
    }


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


def topological_sort(
    task_list: list[tuple],
    language: str = "en",
) -> tuple[list[tuple], dict]:
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
        language (str): The language

    Returns:
        Tuple[List[Tuple], Dict[tuple, List[str]]]:
        - A list of tasks sorted in a feasible execution order based on
            the provided dependencies.
        - A dictionary where each key is a tuple consists of ("idx",
            "agent_name"), and the value is a list of agent names that the
            key depends on, sorted in the order of execution.

    Raises:
        ValueError: If there is a circular dependency that prevents
            topological sorting or if a task has undefined dependencies.

    Example:
        Input: [
            ("1", "info", "AgentA", "None"),
            ("2", "info", "AgentB", "1"),
            ("3", "info", "AgentC", "2,1")
        ]
        Output: (
            [("1", "info", "AgentA", "None"), ("2", "info", "AgentB",
            "1"), ("3", "info", "AgentC", "2,1")],
            {("1", "AgentA"): [], ("2", "AgentB"): ["AgentA"], ("3",
            "AgentC"): ["AgentA", "AgentB"]}
        )
    """
    no_dependency_label = "无" if language == "zh" else "None"

    G = nx.DiGraph()

    task_map = {task[0]: task for task in task_list}
    dependencies_by_task = defaultdict(list)

    # Populate the graph and map dependencies with unique identifiers
    for step_number, _, agent, dependencies in task_list:
        G.add_node(step_number)
        if dependencies.strip() != no_dependency_label:
            for dep in dependencies.split(","):
                dep = dep.strip()
                G.add_edge(dep, step_number)
                dependencies_by_task[(step_number, agent)].append(
                    (dep, task_map[dep][2]),
                )

    # Perform topological sort
    try:
        sorted_task_ids = list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible as exc:
        raise ValueError("Circular dependency detected") from exc

    # Create sorted tasks
    sorted_tasks = [task_map[task_id] for task_id in sorted_task_ids]

    # Create the dependency dictionary for each unique task
    dependency_dict = {}
    for task_id in sorted_task_ids:
        agent = task_map[task_id][2]
        key = (task_id, agent)
        if key in dependencies_by_task:
            # Prepare a list of agent names maintaining the task order
            sorted_dependencies = sorted(
                dependencies_by_task[key],
                key=lambda x: sorted_task_ids.index(x[0]),
            )
            dependency_dict[key] = [dep[1] for dep in sorted_dependencies]
        else:
            dependency_dict[key] = []

    return sorted_tasks, dependency_dict


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
        "执行结果" if language == "zh" else ("Generation Results")
    )

    return (
        step_pattern,
        agent_sys_prompt_with_context,
        scheduler_sys_prompt,
        agent_sys_prompt_without_context,
        scheduling_progress_format,
        generation_result_desc,
    )
