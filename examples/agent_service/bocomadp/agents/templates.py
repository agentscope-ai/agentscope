"""Sub-agent templates — reusable blueprints passed to ``create_app``.

Adapted from agentscope's ``examples/agent_service/main.py``. Each template
defines a sub-agent *type* (e.g. ``"researcher"``, ``"coder"``) that the
leader agent can route to via the ``AgentCreate`` tool's ``subagent_type``
parameter.

Add your own templates here. They are wired into the app in ``main.py``
via ``create_app(custom_subagent_templates=load_subagent_templates())``.

## How to add a new template

1. Pick a unique ``type`` string (must be unique across all templates).
2. Write a ``system_prompt_template`` using the available placeholders:
   ``{member_name}``, ``{team_name}``, ``{leader_name}``,
   ``{team_description}``, ``{member_description}``.
3. Pick a :class:`PermissionContext` matching the trust level the sub-agent
   needs (EXPLORE = read-only, WRITE = read+write filesystem, ...).
4. Register it in :func:`load_subagent_templates`.
"""

from __future__ import annotations

from agentscope.app import SubAgentTemplate
from agentscope.permission import PermissionContext, PermissionMode


def _researcher_template() -> SubAgentTemplate:
    """Read-only explorer sub-agent."""
    return SubAgentTemplate(
        type="researcher",
        description=(
            "专门从事调研与信息收集的只读智能体。它们可以读取文件和"
            "收集信息，但不能修改、创建或删除任何内容。当需要调研代码库、"
            "理解其结构，或从文件中收集信息以支持规划——且不做任何改动时，"
            "请使用此智能体类型。"
        ),
        system_prompt_template=(
            "你是{member_name}，团队'{team_name}'中的研究员智能体，"
            "由{leader_name}领导。\n\n"
            "团队目标：{team_description}\n\n"
            "你的职责：{member_description}\n\n"
            "## 职责\n"
            "- 完成团队负责人分配的研究任务。\n"
            "- 你是只读的：可以查看文件和代码库，但绝不能修改、创建或删除任何内容。\n\n"
            "## 汇报\n"
            "- 无论任务成功还是失败，都必须使用TeamSay工具向{leader_name}汇报任务结果。\n"
            "- 私有推理保持私有；只分享负责人需要的结论和发现。\n\n"
            "注意：`TeamSay`是你与{leader_name}及其他团队成员沟通的**唯一**渠道。"
            "你产生的任何其他输出对他们是不可见的，因此任何想让他们看到的内容"
            "**必须**通过`TeamSay`发送。"
        ),
        permission_context=PermissionContext(mode=PermissionMode.EXPLORE),
    )


def _coder_template() -> SubAgentTemplate:
    """Read-write coder sub-agent (example — adjust permissions to taste)."""
    return SubAgentTemplate(
        type="coder",
        description=(
            "可以读写文件、运行shell命令并进行代码修改的智能体。"
            "当任务需要修改代码库、运行测试或执行脚本时，请使用此智能体类型。"
        ),
        system_prompt_template=(
            "你是{member_name}，团队'{team_name}'中的程序员智能体，"
            "由{leader_name}领导。\n\n"
            "团队目标：{team_description}\n\n"
            "你的职责：{member_description}\n\n"
            "## 职责\n"
            "- 完成团队负责人分配的编程任务。\n"
            "- 你可以在权限范围内读取、写入、删除文件，并运行shell命令。\n\n"
            "## 汇报\n"
            "- 无论任务成功还是失败，都必须使用TeamSay工具向{leader_name}汇报任务结果。\n"
            "- 如果命令执行失败，请在汇报中包含错误输出，以便负责人决定如何继续。\n\n"
            "注意：`TeamSay`是你与{leader_name}及其他团队成员沟通的**唯一**渠道。"
        ),
        # ACCEPT_EDITS allows file read/write + shell within the workspace sandbox.
        permission_context=PermissionContext(mode=PermissionMode.ACCEPT_EDITS),
    )


def load_subagent_templates() -> list[SubAgentTemplate]:
    """Return all sub-agent templates to register with ``create_app``.

    Add new templates to this list as you build them. Duplicate ``type``
    values will be rejected by ``create_app`` at startup.
    """
    return [
        _researcher_template(),
        _coder_template(),
    ]
