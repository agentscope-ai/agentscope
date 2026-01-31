# -*- coding: utf-8 -*-
"""Example demonstrating dynamic skill loading with directory monitoring."""
import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    execute_python_code,
    view_text_file,
)


async def main() -> None:
    """Demonstrate dynamic skill loading with directory monitoring."""
    toolkit = Toolkit()

    # Register basic tools
    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)

    # Monitor a directory for skills - skills will be discovered automatically
    # Any subdirectory containing SKILL.md will be registered as a skill
    print("\033[1;33m=== Monitoring Skills Directory ===\033[0m")
    toolkit.monitor_agent_skills("./skill")

    # Show discovered skills
    print(f"\033[1;32mDiscovered {len(toolkit.skills)} skills:\033[0m")
    for skill_name, skill_info in toolkit.skills.items():
        print(f"  - {skill_name}: {skill_info['description']}")
        print(f"    Source: {skill_info['source']}, Dir: {skill_info['dir']}")
    print()

    # Create agent with monitored skills
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You are a helpful assistant named Friday.\n\n"
            "# IMPORTANT\n"
            "- Don't make any assumptions. All your knowledge must "
            "come from your equipped skills.\n"
            "- When asked about your skills, list them clearly.\n"
        ),
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
            enable_thinking=False,
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    # Show agent's system prompt with skills
    print("\033[1;32m=== Agent System Prompt ===\033[0m")
    print(agent.sys_prompt)
    print()

    # Test 1: Ask about available skills
    print("\033[1;33m=== Test 1: What skills do you have? ===\033[0m")
    await agent(Msg("user", "What skills do you have?", "user"))
    print()

    # Test 2: Use a specific skill
    print(
        "\033[1;33m=== Test 2: How to create tool functions in "
        "AgentScope? ===\033[0m",
    )
    await agent(
        Msg(
            "user",
            (
                "How to create custom tool functions for agents in "
                "AgentScope?"
            ),
            "user",
        ),
    )
    print()

    # Demonstrate manual refresh
    print("\033[1;33m=== Refreshing Skills (Manual) ===\033[0m")
    stats = toolkit.refresh_monitored_skills(force=False)
    print(
        f"Refresh stats: Added={stats['added']}, "
        f"Modified={stats['modified']}, Removed={stats['removed']}",
    )
    print()

    # Demonstrate removing monitored directory
    print("\033[1;33m=== Removing Monitored Directory ===\033[0m")
    print("Note: This removes all auto-discovered skills from the directory")
    print("Manual skills (registered via register_agent_skill) are preserved")
    print()

    # Show skills before removal
    print(f"Skills before removal: {len(toolkit.skills)}")
    monitored_skills = [
        name
        for name, info in toolkit.skills.items()
        if info.get("source") == "monitored"
    ]
    manual_skills = [
        name
        for name, info in toolkit.skills.items()
        if info.get("source") == "manual"
    ]
    print(f"  - Monitored skills: {len(monitored_skills)}")
    print(f"  - Manual skills: {len(manual_skills)}")
    print()

    # Uncomment to actually remove the monitored directory:
    # toolkit.remove_monitored_directory("./skill")
    # print(f"Skills after removal: {len(toolkit.skills)}")
    # print(f"  - All monitored skills from './skill' have been removed")
    # print(f"  - Manual skills are preserved: {len(manual_skills)}")
    # print()

    # Example: Monitor multiple directories
    print("\033[1;33m=== Multiple Directory Monitoring ===\033[0m")
    print("You can monitor multiple directories simultaneously:")
    print("  toolkit.monitor_agent_skills('./skill')")
    print("  toolkit.monitor_agent_skills('./more_skills')")
    print()
    print("To remove a specific directory:")
    print("  toolkit.remove_monitored_directory('./skill')")
    print("  # Skills from './more_skills' remain available")
    print()


if __name__ == "__main__":
    asyncio.run(main())
