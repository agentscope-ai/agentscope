# -*- coding: utf-8 -*-
""""""
from ._local_workspace import LocalWorkspace
from ..tool import ToolGroup

workspace = LocalWorkspace(
    workdir="~/workdir",
    mcps=[],
    skills=[],
    tool_groups=[
        ToolGroup(
            name="browser_use",
            description="The browser use tools.",
            tools=[],
            skills=[],
            mcps=[],
        ),
        ToolGroup(
            name="pdf_related",
            description="The PDF related tools.",
            tools=[],
            skills=[],
            mcps=[],
        ),
    ],
    instructions="xxx",
)

workspace.list_skills() ==> 现在得到所有的skills
workspace.list_tools()  ==> 现在所有的skills
workspace.list_mcps()   ==> 现在所有的mcps

