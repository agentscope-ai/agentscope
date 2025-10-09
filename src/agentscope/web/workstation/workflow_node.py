# -*- coding: utf-8 -*-
"""Workflow node opt."""
from abc import ABC, abstractmethod
from enum import IntEnum
from functools import partial
from typing import List, Optional

from agentscope import msghub
from agentscope.agent import (
    DialogAgent,
    UserAgent,
    DictDialogAgent,
    ReActAgent,
)
from agentscope.manager import ModelManager
from agentscope.message import Msg
from agentscope.pipelines import SequentialPipeline
from agentscope.web.workstation._utils import (
    _ForLoopPipeline,
    _WhileLoopPipeline,
    _IfElsePipeline,
    _SwitchPipeline,
    _placeholder,
)
from agentscope.web.workstation.workflow_utils import (
    kwarg_converter,
    deps_converter,
    dict_converter,
)
from agentscope.service import (
    bing_search,
    google_search,
    read_text_file,
    write_text_file,
    execute_python_code,
    ServiceToolkit,
)

DEFAULT_FLOW_VAR = "flow"


class WorkflowNodeType(IntEnum):
    """Enum for workflow node."""

    MODEL = 0
    AGENT = 1
    PIPELINE = 2
    SERVICE = 3
    MESSAGE = 4
    COPY = 5


class WorkflowNode(ABC):
    """
    Abstract base class representing a generic node in a workflow.

    WorkflowNode is designed to be subclassed with specific logic implemented
    in the subclass methods. It provides an interface for initialization and
    execution of operations when the node is called.
    """

    node_type = None

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        """
        Initialize nodes. Implement specific initialization logic in
        subclasses.
        """
        self.node_id = node_id
        self.opt_kwargs = opt_kwargs
        self.source_kwargs = source_kwargs
        self.dep_opts = dep_opts
        self.dep_vars = [opt.var_name for opt in self.dep_opts]
        self.var_name = f"{self.node_type.name.lower()}_{self.node_id}"

    def __call__(self, x: dict = None):  # type: ignore[no-untyped-def]
        """
        Performs the operations of the node. Implement specific logic in
        subclasses.
        """

    @abstractmethod
    def compile(self) -> dict:
        """
        Compile Node to python executable code dict
        """
        return {
            "imports": "",
            "inits": "",
            "execs": "",
        }


class ModelNode(WorkflowNode):
    """
    A node that represents a model in a workflow.

    The ModelNode can be used to load and execute a model as part of the
    workflow node. It initializes model configurations and performs
    model-related operations when called.
    """

    node_type = WorkflowNodeType.MODEL

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        ModelManager.get_instance().load_model_configs([self.opt_kwargs])

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.manager import ModelManager",
            "inits": f"ModelManager.get_instance().load_model_configs("
            f"[{self.opt_kwargs}])",
            "execs": "",
        }


class MsgNode(WorkflowNode):
    """
    A node that manages messaging within a workflow.

    MsgNode is responsible for handling messages, creating message objects,
    and performing message-related operations when the node is invoked.
    """

    node_type = WorkflowNodeType.MESSAGE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.msg = Msg(**self.opt_kwargs)

    def __call__(self, x: dict = None) -> dict:
        return self.msg

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.message import Msg",
            "inits": f"{DEFAULT_FLOW_VAR} = Msg"
            f"({kwarg_converter(self.opt_kwargs)})",
            "execs": "",
        }


class DialogAgentNode(WorkflowNode):
    """
    A node representing a DialogAgent within a workflow.
    """

    node_type = WorkflowNodeType.AGENT

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.pipeline = DialogAgent(**self.opt_kwargs)

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.agent import DialogAgent",
            "inits": f"{self.var_name} = DialogAgent("
            f"{kwarg_converter(self.opt_kwargs)})",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class UserAgentNode(WorkflowNode):
    """
    A node representing a UserAgent within a workflow.
    """

    node_type = WorkflowNodeType.AGENT

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.pipeline = UserAgent(**self.opt_kwargs)

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.agent import UserAgent",
            "inits": f"{self.var_name} = UserAgent("
            f"{kwarg_converter(self.opt_kwargs)})",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class DictDialogAgentNode(WorkflowNode):
    """
    A node representing a DictDialogAgent within a workflow.
    """

    node_type = WorkflowNodeType.AGENT

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.pipeline = DictDialogAgent(**self.opt_kwargs)

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.agent import DictDialogAgent",
            "inits": f"{self.var_name} = DictDialogAgent("
            f"{kwarg_converter(self.opt_kwargs)})",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class ReActAgentNode(WorkflowNode):
    """
    A node representing a ReActAgent within a workflow.
    """

    node_type = WorkflowNodeType.AGENT

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        # Build tools
        self.service_toolkit = ServiceToolkit()
        for tool in dep_opts:
            if not hasattr(tool, "service_func"):
                raise TypeError(f"{tool} must be tool!")
            self.service_toolkit.add(tool.service_func)
        self.pipeline = ReActAgent(
            service_toolkit=self.service_toolkit,
            **self.opt_kwargs,
        )

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        tools = deps_converter(self.dep_vars)[1:-1].split(",")
        service_toolkit_code = ";".join(
            f"{self.var_name}_service_toolkit.add({tool.strip()})"
            for tool in tools
        )
        return {
            "imports": "from agentscope.agent import ReActAgent",
            "inits": f"{self.var_name}_service_toolkit = ServiceToolkit()\n"
            f"    {service_toolkit_code}\n"
            f"    {self.var_name} = ReActAgent"
            f"({kwarg_converter(self.opt_kwargs)}, service_toolkit"
            f"={self.var_name}_service_toolkit)",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class MsgHubNode(WorkflowNode):
    """
    A node that serves as a messaging hub within a workflow.

    MsgHubNode is responsible for broadcasting announcements to participants
    and managing the flow of messages within a workflow's node.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.announcement = Msg(
            name=self.opt_kwargs["announcement"].get("name", "Host"),
            content=self.opt_kwargs["announcement"].get("content", "Welcome!"),
            role="system",
        )
        assert len(self.dep_opts) == 1 and hasattr(
            self.dep_opts[0],
            "pipeline",
        ), (
            "MsgHub members must be a list of length 1, with the first "
            "element being an instance of PipelineBaseNode"
        )

        self.pipeline = self.dep_opts[0]
        self.participants = get_all_agents(self.pipeline)
        self.participants_var = get_all_agents(self.pipeline, return_var=True)

    def __call__(self, x: dict = None) -> dict:
        with msghub(self.participants, announcement=self.announcement):
            x = self.pipeline(x)
        return x

    def compile(self) -> dict:
        announcement = (
            f'Msg(name="'
            f'{self.opt_kwargs["announcement"].get("name", "Host")}", '
            f'content="'
            f'{self.opt_kwargs["announcement"].get("content", "Host")}"'
            f', role="system")'
        )
        execs = f"""with msghub({deps_converter(self.participants_var)},
        announcement={announcement}):
        {DEFAULT_FLOW_VAR} = {self.dep_vars[0]}({DEFAULT_FLOW_VAR})
        """
        return {
            "imports": "from agentscope.msghub import msghub\n"
            "from agentscope.message import Msg",
            "inits": "",
            "execs": execs,
        }


class PlaceHolderNode(WorkflowNode):
    """
    A placeholder node within a workflow.

    This node acts as a placeholder and can be used to pass through information
    or data without performing any significant operation.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.pipeline = _placeholder

    def __call__(self, x: Msg = None) -> Msg:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": (
                "from agentscope.web.workstation._utils import _placeholder"
            ),
            "inits": f"{self.var_name} = _placeholder",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class SequentialPipelineNode(WorkflowNode):
    """
    A node representing a sequential node within a workflow.

    SequentialPipelineNode executes a series of operators or nodes in a
    sequence, where the output of one node is the input to the next.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.pipeline = SequentialPipeline(operators=self.dep_opts)

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.pipelines import SequentialPipeline",
            "inits": f"{self.var_name} = SequentialPipeline("
            f"{deps_converter(self.dep_vars)})",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class ForLoopPipelineNode(WorkflowNode):
    """
    A node representing a for-loop structure in a workflow.

    ForLoopPipelineNode allows the execution of a pipeline node multiple times,
    iterating over a given set of inputs or a specified range.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        assert (
            len(self.dep_opts) == 1
        ), "ForLoopPipelineNode can only contain one PipelineNode."
        self.pipeline = _ForLoopPipeline(
            loop_body_operators=self.dep_opts[0],
            **self.opt_kwargs,
        )

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": (
                "from agentscope.web.workstation._utils import "
                "_ForLoopPipeline"
            ),
            "inits": f"{self.var_name} = _ForLoopPipeline("
            f"loop_body_operators="
            f"{deps_converter(self.dep_vars)},"
            f" {kwarg_converter(self.source_kwargs)})",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class WhileLoopPipelineNode(WorkflowNode):
    """
    A node representing a while-loop structure in a workflow.

    WhileLoopPipelineNode enables conditional repeated execution of a
    node based on a specified condition.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        assert (
            len(self.dep_opts) == 1
        ), "WhileLoopPipelineNode can only contain one PipelineNode."
        self.pipeline = _WhileLoopPipeline(
            loop_body_operators=self.dep_opts[0],
            **self.opt_kwargs,
        )

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": (
                "from agentscope.web.workstation._utils import "
                "_WhileLoopPipeline"
            ),
            "inits": f"{self.var_name} = _WhileLoopPipeline("
            f"loop_body_operators="
            f"{deps_converter(self.dep_vars)},"
            f" {kwarg_converter(self.source_kwargs)})",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.var_name}"
            f"({DEFAULT_FLOW_VAR})",
        }


class IfElsePipelineNode(WorkflowNode):
    """
    A node representing an if-else conditional structure in a workflow.

    IfElsePipelineNode directs the flow of execution to different node
    nodes based on a specified condition.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        assert (
            0 < len(self.dep_opts) <= 2
        ), "IfElsePipelineNode must contain one or two PipelineNode."
        if len(self.dep_opts) == 1:
            self.pipeline = _IfElsePipeline(
                if_body_operators=self.dep_opts[0],
                **self.opt_kwargs,
            )
        elif len(self.dep_opts) == 2:
            self.pipeline = _IfElsePipeline(
                if_body_operators=self.dep_opts[0],
                else_body_operators=self.dep_opts[1],
                **self.opt_kwargs,
            )

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        imports = (
            "from agentscope.web.workstation._utils import _IfElsePipeline"
        )
        execs = f"{DEFAULT_FLOW_VAR} = {self.var_name}({DEFAULT_FLOW_VAR})"
        if len(self.dep_vars) == 1:
            return {
                "imports": imports,
                "inits": f"{self.var_name} = _IfElsePipeline("
                f"if_body_operators={self.dep_vars[0]})",
                "execs": execs,
            }
        elif len(self.dep_vars) == 2:
            return {
                "imports": imports,
                "inits": f"{self.var_name} = _IfElsePipeline("
                f"if_body_operators={self.dep_vars[0]}, "
                f"else_body_operators={self.dep_vars[1]})",
                "execs": execs,
            }
        raise ValueError


class SwitchPipelineNode(WorkflowNode):
    """
    A node representing a switch-case structure within a workflow.

    SwitchPipelineNode routes the execution to different node nodes
    based on the evaluation of a specified key or condition.
    """

    node_type = WorkflowNodeType.PIPELINE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        assert 0 < len(self.dep_opts), (
            "SwitchPipelineNode must contain at least " "one PipelineNode."
        )
        case_operators = {}
        self.case_operators_var = {}

        if len(self.dep_opts) == len(self.opt_kwargs["cases"]):
            # No default_operators provided
            default_operators = _placeholder
            self.default_var_name = "_placeholder"
        elif len(self.dep_opts) == len(self.opt_kwargs["cases"]) + 1:
            # default_operators provided
            default_operators = self.dep_opts.pop(-1)
            self.default_var_name = self.dep_vars.pop(-1)
        else:
            raise ValueError(
                f"SwitchPipelineNode deps {self.dep_opts} not matches "
                f"cases {self.opt_kwargs['cases']}.",
            )

        for key, value, var in zip(
            self.opt_kwargs["cases"],
            self.dep_opts,
            self.dep_vars,
        ):
            case_operators[key] = value.pipeline
            self.case_operators_var[key] = var
        self.opt_kwargs.pop("cases")
        self.source_kwargs.pop("cases")
        self.pipeline = _SwitchPipeline(
            case_operators=case_operators,
            default_operators=default_operators,  # type: ignore[arg-type]
            **self.opt_kwargs,
        )

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        imports = (
            "from agentscope.web.workstation._utils import _SwitchPipeline\n"
            "from agentscope.web.workstation._utils import _placeholder"
        )
        execs = f"{DEFAULT_FLOW_VAR} = {self.var_name}({DEFAULT_FLOW_VAR})"
        return {
            "imports": imports,
            "inits": f"{self.var_name} = _SwitchPipeline(case_operators="
            f"{dict_converter(self.case_operators_var)}, "
            f"default_operators={self.default_var_name},"
            f" {kwarg_converter(self.source_kwargs)})",
            "execs": execs,
        }


class CopyNode(WorkflowNode):
    """
    A node that duplicates the output of another node in the workflow.

    CopyNode is used to replicate the results of a parent node and can be
    useful in workflows where the same output is needed for multiple
    subsequent operations.
    """

    node_type = WorkflowNodeType.COPY

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        assert len(self.dep_opts) == 1, "CopyNode can only have one parent!"
        self.pipeline = self.dep_opts[0]

    def __call__(self, x: dict = None) -> dict:
        return self.pipeline(x)

    def compile(self) -> dict:
        return {
            "imports": "",
            "inits": "",
            "execs": f"{DEFAULT_FLOW_VAR} = {self.dep_vars[0]}"
            f"({DEFAULT_FLOW_VAR})",
        }


class BingSearchServiceNode(WorkflowNode):
    """
    Bing Search Node
    """

    node_type = WorkflowNodeType.SERVICE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.service_func = partial(bing_search, **self.opt_kwargs)

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.service import ServiceToolkit\n"
            "from functools import partial\n"
            "from agentscope.service import bing_search",
            "inits": f"{self.var_name} = partial(bing_search,"
            f" {kwarg_converter(self.opt_kwargs)})",
            "execs": "",
        }


class GoogleSearchServiceNode(WorkflowNode):
    """
    Google Search Node
    """

    node_type = WorkflowNodeType.SERVICE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.service_func = partial(google_search, **self.opt_kwargs)

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.service import ServiceToolkit\n"
            "from functools import partial\n"
            "from agentscope.service import google_search",
            "inits": f"{self.var_name} = partial(google_search,"
            f" {kwarg_converter(self.opt_kwargs)})",
            "execs": "",
        }


class PythonServiceNode(WorkflowNode):
    """
    Execute python Node
    """

    node_type = WorkflowNodeType.SERVICE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.service_func = execute_python_code

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.service import ServiceToolkit\n"
            "from agentscope.service import execute_python_code",
            "inits": f"{self.var_name} = execute_python_code",
            "execs": "",
        }


class ReadTextServiceNode(WorkflowNode):
    """
    Read Text Service Node
    """

    node_type = WorkflowNodeType.SERVICE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.service_func = read_text_file

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.service import ServiceToolkit\n"
            "from agentscope.service import read_text_file",
            "inits": f"{self.var_name} = read_text_file",
            "execs": "",
        }


class WriteTextServiceNode(WorkflowNode):
    """
    Write Text Service Node
    """

    node_type = WorkflowNodeType.SERVICE

    def __init__(
        self,
        node_id: str,
        opt_kwargs: dict,
        source_kwargs: dict,
        dep_opts: list,
    ) -> None:
        super().__init__(node_id, opt_kwargs, source_kwargs, dep_opts)
        self.service_func = write_text_file

    def compile(self) -> dict:
        return {
            "imports": "from agentscope.service import ServiceToolkit\n"
            "from agentscope.service import write_text_file",
            "inits": f"{self.var_name} = write_text_file",
            "execs": "",
        }


NODE_NAME_MAPPING = {
    "dashscope_chat": ModelNode,
    "openai_chat": ModelNode,
    "post_api_chat": ModelNode,
    "post_api_dall_e": ModelNode,
    "Message": MsgNode,
    "DialogAgent": DialogAgentNode,
    "UserAgent": UserAgentNode,
    "DictDialogAgent": DictDialogAgentNode,
    "ReActAgent": ReActAgentNode,
    "Placeholder": PlaceHolderNode,
    "MsgHub": MsgHubNode,
    "SequentialPipeline": SequentialPipelineNode,
    "ForLoopPipeline": ForLoopPipelineNode,
    "WhileLoopPipeline": WhileLoopPipelineNode,
    "IfElsePipeline": IfElsePipelineNode,
    "SwitchPipeline": SwitchPipelineNode,
    "CopyNode": CopyNode,
    "BingSearchService": BingSearchServiceNode,
    "GoogleSearchService": GoogleSearchServiceNode,
    "PythonService": PythonServiceNode,
    "ReadTextService": ReadTextServiceNode,
    "WriteTextService": WriteTextServiceNode,
}


def get_all_agents(
    node: WorkflowNode,
    seen_agents: Optional[set] = None,
    return_var: bool = False,
) -> List:
    """
    Retrieve all unique agent objects from a pipeline.

    Recursively traverses the pipeline to collect all distinct agent-based
    participants. Prevents duplication by tracking already seen agent.

    Args:
        node (WorkflowNode): The WorkflowNode from which to extract agent.
        seen_agents (set, optional): A set of agent that have already been
            seen to avoid duplication. Defaults to None.

    Returns:
        list: A list of unique agent objects found in the pipeline.
    """
    if seen_agents is None:
        seen_agents = set()

    all_agents = []

    for participant in node.pipeline.participants:
        if participant.node_type == WorkflowNodeType.AGENT:
            if participant not in seen_agents:
                if return_var:
                    all_agents.append(participant.var_name)
                else:
                    all_agents.append(participant.pipeline)
                seen_agents.add(participant.pipeline)
        elif participant.node_type == WorkflowNodeType.PIPELINE:
            nested_agents = get_all_agents(
                participant,
                seen_agents,
                return_var=return_var,
            )
            all_agents.extend(nested_agents)
        else:
            raise TypeError(type(participant))

    return all_agents
