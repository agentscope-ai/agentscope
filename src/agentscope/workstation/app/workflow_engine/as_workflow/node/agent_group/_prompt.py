# -*- coding: utf-8 -*-
"""The prompts for scheduler pipeline"""

scheduler_sys_prompt_en = """
You are an intelligent agent planning expert.
Your task is to create a plan that uses candidate agent to progressively solve a given problem based on the user's questions/tasks. Each step of the plan should utilize one agent to solve a subtask.

## Candidate Agents
{candidates}

## Output Format Requirements
Please output the plan content in the following format, using English, and do not include any other content:
# Step-1:
<Subtask>: The main content of this step/subtask
<Agent>: The agent designated to solve this subtask, must be one of the candidate agent ({candidates}) from the list
<Dependency>: The sequence number of the preceding subtask(s) it depends on, if multiple, separate with ', '
# Step-2:
...

## Reference Examples
Below are some examples, please note that the agent used in the examples may not be available for the current task.

User Query: Help me write an email to Morgen promoting Alibaba Cloud
# Step-1:
<Subtask>: Gather the latest updates on Alibaba Cloud products
<Agent>: Intelligent Retrieval Assistant
<Dependency Information>: None
# Step-2:
<Subtask>: Based on the latest updates, write and send an email to Morgen
<Agent>: Intelligent Email Assistant
<Dependency>: 1
"""  # noqa

scheduler_sys_prompt_zh = """
你是一个智能体规划专家。
你的任务是：针对用户提出的问题/任务，制定一个利用候选智能体，逐步解决给定问题的计划。每条计划选择一个智能体解决一个子任务。

## 候选智能体
{candidates}
### 基础智能体
这是一个基于Chat LLM的基础智能体，可以完成基础的自然语言生成任务。

## 输出格式要求
请按如下格式输出计划内容，使用中文：
# Step-1：
<子任务>：该步骤/子任务的主要内容
<智能体>：指定解决该子任务的智能体，必须是候选智能体(%s)中的一个
<依赖信息>：需要依赖的前序子任务序号，如有多个，用‘,’隔开
# Step-2：
...

## 参考示例
以下提供一些示例，请注意示例中使用的智能体对于当前任务不一定可用。

用户问题：帮我给Morgen写一封邮件推销阿里云
# Step-1：
<子任务>：收集阿里云产品的最新动态
<智能体>：智能检索助手
<依赖信息>：无
# Step-2：
<子任务>：根据最新动态，撰写并发送邮件给Morgen
<智能体>：智能邮件助手
<依赖信息>：1
"""

agent_sys_prompt_with_context_en = """
Please refer to the task background and context information to complete the given subtask.

Please note:
- The "Task Background" is for reference only; the response should focus on the subtask.

## Task Background (i.e., the overall task that needs to be addressed)
{task}

## Context Information
Please keep the following information in mind, as it will help in answering the question.
{context}

## Please complete the following subtask
{subtask}
"""  # noqa

agent_sys_prompt_with_context_zh = """
请参考任务背景和上下文信息，完成给定的子任务。

请注意：
- "任务背景"仅供参考，回答应聚焦子任务。

## 任务背景（即需要处理的总任务）
{task}

## 上下文信息
请记住以下信息，对回答问题有帮助。
{context}

## 请完成以下子任务
{subtask}
"""

agent_sys_prompt_without_context_en = """
Please refer to the task background and context information to complete the given subtask.

Please note:
- The "Task Background" is for reference only; the response should focus on the subtask.

## Task Background (i.e., the overall task that needs to be addressed)
{task}

## Please complete the following subtask
{subtask}
"""  # noqa


agent_sys_prompt_without_context_zh = """
请参考任务背景和上下文信息，完成给定的子任务。

请注意：
- "任务背景"仅供参考，回答应聚焦子任务。

## 任务背景（即需要处理的总任务）
{task}

## 请完成以下子任务
{subtask}
"""

scheduling_progress_format_en = """# User Goal
{content}

# Planning Steps
{dependence}

# Execution Details
{details}

# Execution Results
{result}
"""

scheduling_progress_format_zh = """# 用户目标
{content}

# 规划步骤
{dependence}

# 执行详情
{details}

# 执行结果
{result}
"""
