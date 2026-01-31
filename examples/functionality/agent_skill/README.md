# Agent Skills in AgentScope

[Agent Skill](https://claude.com/blog/skills) is an approach proposed by
Anthropic to improve agent capabilities on specific tasks.

This directory contains two examples demonstrating different ways to use
Agent Skills in AgentScope:

## Examples

### 1. Manual Skill Registration (`main.py`)

Demonstrates the traditional approach of manually registering individual
skills using `toolkit.register_agent_skill()`. This example shows how to:
- Register a specific skill directory
- Use the skill in a ReAct agent
- Answer questions about AgentScope using the skill

### 2. Dynamic Skill Loading (`main_dynamic.py`)

Demonstrates the new dynamic skill loading feature using directory monitoring.
This example shows how to:
- Monitor a directory for skills using `toolkit.monitor_agent_skills()`
- Automatically discover all skills in subdirectories
- View discovered skills and their metadata
- Manually refresh skills with `toolkit.refresh_monitored_skills()`
- Remove monitored directories with `toolkit.remove_monitored_directory()`

The `skill` directory contains multiple demonstration skills:
- **Analyzing AgentScope Library**: Helps agents learn about AgentScope
- **Data Analysis**: Provides data analysis and visualization guidance
- **Web Scraping**: Covers web scraping techniques

## Quick Start

Install the latest version of AgentScope to run these examples:

```bash
pip install agentscope --upgrade
```

### Run Manual Registration Example

```bash
python main.py
```

### Run Dynamic Loading Example

```bash
python main_dynamic.py
```

> Note:
> - The example is built with DashScope chat model. If you want to change the model used in this example, don't
> forget to change the formatter at the same time! The corresponding relationship between built-in models and
> formatters are list in [our tutorial](https://doc.agentscope.io/tutorial/task_prompt.html#id1)
> - For local models, ensure the model service (like Ollama) is running before starting the agent.


## Key Differences

### Manual Registration

- Explicit control over which skills to load
- Skills must be registered individually
- Suitable for production with fixed skill sets

### Dynamic Loading

- Automatic discovery of all skills in a directory
- Skills can be added/removed without code changes
- Lazy loading with mtime-based caching for performance
- Suitable for development and dynamic environments
- Supports nested skill directories at any depth

## Creating Your Own Skills

To create a new skill:

1. Create a directory under `skill/`
2. Add a `SKILL.md` file with YAML frontmatter:

```markdown
---
name: Your Skill Name
description: Brief description of what this skill does
---

# Your Skill Name

## Overview

Detailed description...

## Quick Start

Instructions and examples...
```

3. The skill will be automatically discovered when using `monitor_agent_skills()`

## Dynamic Skill Features

The dynamic skill loading system provides several advanced features:

### Automatic Discovery

Skills are discovered recursively in monitored directories. Any subdirectory
containing a `SKILL.md` file with proper frontmatter will be registered.

### Lazy Loading

Skills are only scanned when `get_agent_skill_prompt()` is called, minimizing
filesystem operations during initialization.

### Change Detection

The system uses modification time (mtime) caching to efficiently detect:

- New skills added to monitored directories
- Modified skill definitions
- Deleted skills

### Manual Refresh

Force a refresh of all monitored skills:

```python
stats = toolkit.refresh_monitored_skills(force=True)
print(f"Added: {stats['added']}, Modified: {stats['modified']}, Removed: {stats['removed']}")
```

### Removing Monitored Directories

Remove a monitored directory and all its auto-discovered skills:

```python
# Remove all skills from a specific directory
toolkit.remove_monitored_directory("./skill")

# Manual skills (registered via register_agent_skill) are preserved
# Only auto-discovered skills from the specified directory are removed
```

**Key behaviors:**
- Removes the directory from the monitored list
- Removes all skills where `source="monitored"` from that directory
- Preserves manually registered skills
- Cleans up internal caches for removed skills
- Safe to call on non-monitored directories (logs warning)

**Use cases:**
- Switching between different skill sets
- Temporarily disabling a group of skills
- Managing multiple skill directories independently

**Example with multiple directories:**

```python
# Monitor multiple directories
toolkit.monitor_agent_skills("./core_skills")
toolkit.monitor_agent_skills("./experimental_skills")

# Later, remove only experimental skills
toolkit.remove_monitored_directory("./experimental_skills")
# Core skills remain available
```

### Conflict Resolution

If multiple skills have the same name, the first one discovered is kept,
and a warning is logged for the conflicting skill.
