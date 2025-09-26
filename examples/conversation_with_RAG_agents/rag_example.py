# -*- coding: utf-8 -*-
"""
An example for conversation between user and agents with RAG capability.
One agent is a tutorial assistant, the other is a code explainer.
"""
import json
import os

from groupchat_utils import filter_agents

import agentscope
from agentscope.agents import UserAgent
from agentscope.rag import KnowledgeBank


AGENT_CHOICE_PROMPT = """
There are following available agents. You need to choose the most appropriate
agent(s) to answer the user's question.

agent descriptions:{}

First, rephrase the user's question, which must contain the key information.
The you need to think step by step. If you believe some of the agents are
good candidates to answer the question (e.g., AGENT_1 and AGENT_2), then
you need to follow the following format to generate your output:

'
Because $YOUR_REASONING.
I believe @AGENT_1 and @AGENT_2 are the most appropriate agents to answer
your question.
'
"""


def prepare_docstring_html() -> None:
    """Prepare docstring in html for API assistant"""
    if not os.path.exists("../../docs/docstring_html/"):
        os.system(
            "sphinx-apidoc -f -o ../../docs/sphinx_doc/en/source "
            "../../src/agentscope -t template",
        )
        os.system(
            "sphinx-build -b html  ../../docs/sphinx_doc/en/source "
            "../../docs/docstring_html/ -W --keep-going",
        )


def main() -> None:
    """A RAG multi-agent demo"""
    # prepare html for api agent
    prepare_docstring_html()

    # prepare model
    model_configs = [
        {
            "model_type": "dashscope_text_embedding",
            "config_name": "qwen_emb_config",
            "model_name": "text-embedding-v2",
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
        },
        {
            "model_type": "dashscope_chat",
            "config_name": "qwen_config",
            "model_name": "qwen-max",
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
        },
    ]

    # load config of the agents
    with open("configs/agent_config.json", "r", encoding="utf-8") as f:
        agent_configs = json.load(f)

    agent_list = agentscope.init(
        model_configs=model_configs,
        agent_configs=agent_configs,
        project="Conversation with RAG agents",
    )
    rag_agent_list = agent_list[:4]
    guide_agent = agent_list[4]

    # the knowledge bank can be configured by loading config file
    knowledge_bank = KnowledgeBank(configs="configs/knowledge_config.json")

    # alternatively, we can easily input the configs to add data to RAG
    knowledge_bank.add_data_as_knowledge(
        knowledge_id="agentscope_tutorial_rag",
        knowledge_type="llamaindex_knowledge",
        emb_model_config_name="qwen_emb_config",
        data_dirs_and_types={
            "../../docs/tutorial/en/source/tutorial": [".md", ".py"],
        },
    )

    # let knowledge_bank to equip rag agent with a (set of) knowledge
    # corresponding to its knowledge_id_list
    for agent in rag_agent_list:
        knowledge_bank.equip(agent, agent.knowledge_id_list)

    # An alternative way is to provide knowledge list to agents
    # when initializing them one by one, e.g.
    #
    # ```
    # knowledge = knowledge_bank.get_knowledge(knowledge_id)
    # agent = LlamaIndexAgent(
    #   name="rag_worker",
    #   sys_prompt="{your_prompt}",
    #   model_config_name="{your_model}",
    #   knowledge_list=[knowledge], # provide knowledge object directly
    #   similarity_top_k=3,
    #   log_retrieval=False,
    #   recent_n_mem_for_retrieve=1,
    # )
    # ```

    rag_agent_names = [agent.name for agent in rag_agent_list]
    # update guide agent system prompt with the descriptions of rag agents
    rag_agent_descriptions = [
        "agent name: "
        + agent.name
        + "\n agent description："
        + agent.description
        + "\n"
        for agent in rag_agent_list
    ]

    guide_agent.sys_prompt = (
        guide_agent.sys_prompt
        + AGENT_CHOICE_PROMPT.format(
            "".join(rag_agent_descriptions),
        )
    )

    user_agent = UserAgent()
    while True:
        # The workflow is the following:
        # 1. user input a message,
        # 2. if it mentions (@) one of the agents, the agent will be called
        # 3. otherwise, the guide agent will decide which agent to call
        # 4. the called agent will respond to the user
        # 5. repeat
        x = user_agent()
        x.role = "user"  # to enforce dashscope requirement on roles
        if len(x.content) == 0 or str(x.content).startswith("exit"):
            break
        speak_list = filter_agents(x.content, rag_agent_list)
        if len(speak_list) == 0:
            guide_response = guide_agent(x)
            # Only one agent can be called in the current version,
            # we may support multi-agent conversation later
            speak_list = filter_agents(
                guide_response.content,
                rag_agent_list,
            )
        agent_name_list = [agent.name for agent in speak_list]
        for agent_name, agent in zip(agent_name_list, speak_list):
            if agent_name in rag_agent_names:
                agent(x)


if __name__ == "__main__":
    main()
