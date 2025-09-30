# -*- coding: utf-8 -*-
# pylint: disable=E0611,R0912,R0915
"""
this file consist of the main workflow of the copilot,
the code is executed in steps:
* initializations
    * init model
    * init knowledge bank
    * init agent
* receive the request and enter the main pipeline function
* the query and past chat history are extracted from the request and passed
to the context manager
* the query is passed to the agent involved in the pipeline, and execute
in async mode

The environment variables used to initialize the components are:
    * AGENT_CONFIG_PATH [str]: the path of the agent config file
    * KNOWLEDGE_CONFIG_PATH [str]: the path of the knowledge config file
    * MODEL_CONFIG_PATH [str]: the path of the model config file

For defining the workflow and execution mode, you need the following:
    * RAG_AGENT_NAMES [str='']: the names of the RAG agent
    * MAX_REFIND [int=1] the max retry times for the planning
      (find best RAG agent)
    * RAG_RETURN_RAW [bool=True]: whether to return raw retrieved info from
      RAG agent
"""

import asyncio
import concurrent.futures
import copy
import json
import os
import threading
import time
from typing import List, Union, Generator, Dict, Tuple, Any

import nest_asyncio

from utils.fill_path_utils import (
    fill_paths_in_agent_configs,
    filling_paths_in_knowledge_config,
)
from utils.logging import logger
from utils.db_logging import create_logging_table
from utils.prepare_data_utils import prepare_docstring_txt
from routing.emb_routing import cluster_similarity_planning
from agents.context_manager import ContextManager
from agents.retrieval_agent import RetrievalAgent
from agents.summarizer import Summarizer
from app_knowledge.local_knowledge import ESKnowledge, LocalKnowledge
from app_knowledge.search_knowledge import BingKnowledge
from async_model.async_dashscope import AsyncDashScopeChatWrapper

from agentscope.agent import DialogAgent, AgentBase
from agentscope.message import Msg
from agentscope.rag import KnowledgeBank
from agentscope.manager import ModelManager

import agentscope

DEFAULT_FAIL_ANSWER = {
    "chinese": "抱歉，好像出问题了...",
    "english": "Oops, something went wrong...",
}

# global variables
base_dir = os.path.dirname(__file__)
knowledge_bank = None
rag_agents, guide_agent, summary_agent, backup_agent, context_manager = (
    [],
    None,
    None,
    None,
    None,
)


def init_model(
    model_config_path: str,
) -> None:
    """
    Initialize the model used by the agent, all settings are taken from the
    model config file.

    Args:
        model_config_path (`str`):
            The path of the model config file
    Returns:
        None
    """
    with open(model_config_path, "r", encoding="utf-8") as config_f:
        model_configs = json.load(config_f)
    # set the api keys by environment variables
    for config in model_configs:
        if "dashscope" in config.get("model_type", "") and (
            len(config.get("api_key", "")) == 0
        ):
            # for dashscope
            config["api_key"] = f"{os.environ.get('DASHSCOPE_API_KEY')}"
        else:
            # for other
            logger.warning("Other model may not be supported now.")
            continue
    model_manager = ModelManager.get_instance()
    model_manager.register_model_wrapper_class(AsyncDashScopeChatWrapper, True)
    agentscope.init(model_configs=model_configs)


def init_knowledge_bank(
    knowledge_config_path: str,
) -> KnowledgeBank:
    """
    Init knowledge bank. No input params are needed, all settings are taken
    from the knowledge config file.

    Args:
        knowledge_config_path (`str):
            The path of the knowledge config file
    Returns:
        `KnowledgeBank`: the container of knowledge that can be equipt with RAG
          agent
    """
    global knowledge_bank

    with open(
        os.path.join(base_dir, knowledge_config_path),
        "r",
        encoding="utf-8",
    ) as config_f:
        knowledge_configs = json.load(config_f)
        for config in knowledge_configs:
            filling_paths_in_knowledge_config(config, base_dir)

    knowledge_bank = KnowledgeBank(
        configs=knowledge_configs,
        new_knowledge_types=[
            BingKnowledge,
            LocalKnowledge,
            ESKnowledge,
        ],
    )
    for k_config in knowledge_configs:
        knowledge_bank.stored_knowledge[
            k_config["knowledge_id"]
        ].language = k_config["language"]
    return knowledge_bank


def init_agents(
    agent_config_path: str,
) -> Tuple[
    List[RetrievalAgent],
    AgentBase,
    AgentBase,
    AgentBase,
    ContextManager,
]:
    """
    Init agent based on the given config file

    Args:
        agent_config_path (`str`):
            The path of the agent config file

    Returns:
        `Tuple`: A tuple of agent, RAG agent vary with the need of the user
    """
    global rag_agents, guide_agent, summary_agent
    global backup_agent, context_manager
    with open(
        os.path.join(base_dir, agent_config_path),
        "r",
        encoding="utf-8",
    ) as config_f:
        agent_configs = json.load(config_f)

    AVAILABLE_RAG_AGENTS = list(agent_configs.keys())

    rag_agent_names = []
    for agent_name in os.environ.get("RAG_AGENT_NAMES", "").split(","):
        rag_agent_names.append(agent_name)
        if agent_name not in AVAILABLE_RAG_AGENTS:
            continue

    # init RAG agent
    if len(rag_agent_names) > 0:
        _create_rag_agent(
            rag_agent_names=rag_agent_names,
            agent_configs=agent_configs,
        )
    else:
        rag_agents = []

    # init  guide agent
    guide_agent_config = agent_configs.get("guide_agent")["args"]
    guide_agent_config.pop("description")
    guide_agent = DialogAgent(**guide_agent_config)

    # init summary agent with parser
    summary_agent = Summarizer(**agent_configs.get("summary_agent")["args"])

    # init context manager
    context_manager = ContextManager(
        **agent_configs.get("context_manager")["args"],
    )

    # init backup_agent
    backup_agent = DialogAgent(**agent_configs.get("backup_assistant")["args"])

    logger.info(
        "does context_manager have async model? "
        f"{hasattr(context_manager.model, 'async_call')} "
        "does rag_agent have async model? "
        f"{hasattr(rag_agents[0].model, 'async_call')}\n",
    )
    return (
        rag_agents,
        guide_agent,
        summary_agent,
        backup_agent,
        context_manager,
    )


def _create_rag_agent(
    rag_agent_names: Union[str, List[str]],
    agent_configs: dict,
) -> List[RetrievalAgent]:
    """
    Create agent based on the given rag_agent_names.
    It has been checked that all names are included in the agent_configs.

    Args:
        rag_agent_names (`Union[str, List[str]]:
            The names of the RAG agent
        agent_configs (`dict`):
            The configurations of the agent
    Returns:
        A list of agent
    """
    global rag_agents
    if isinstance(rag_agent_names, str):
        rag_agent_names = [rag_agent_names]
    for rag_agent_name in rag_agent_names:
        agent_config = agent_configs.get(rag_agent_name, {})
        fill_paths_in_agent_configs(agent_config, base_dir)
        if agent_config:
            rag_agent = RetrievalAgent(**agent_config["args"])
            rag_agents.append(rag_agent)
        else:
            raise ValueError(
                f"RAG Agent create fail: The config of {rag_agent_name} "
                f"is not found in agent_config",
            )

    # load & equip knowledge bank
    assert isinstance(knowledge_bank, KnowledgeBank)
    for agent in rag_agents:
        agent.set_known_agents(rag_agents)
        knowledge_bank.equip(agent, agent.knowledge_id_list)

    return rag_agents


def str_to_bool(s: str) -> bool:
    """
    Convert string to bool
    Args:
        s (`str`):
            The string to convert
    """
    if not isinstance(s, str):
        return False
    if s.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif s.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        return False


def initializations() -> None:
    """
    Initialize the knowledge bases and agent
    """
    init_model(
        model_config_path=os.path.join(
            base_dir,
            os.environ.get("MODEL_CONFIG_PATH", "configs/model_config.json"),
        ),
    )
    prepare_docstring_txt(
        repo_path=os.getenv("PATH_TO_AGENTSCOPE_REPO"),
        text_dir=os.getenv("TEXT_DIR"),
    )
    global knowledge_bank
    knowledge_bank = init_knowledge_bank(
        knowledge_config_path=os.environ.get(
            "KNOWLEDGE_CONFIG_PATH",
            "configs/knowledge_config.json",
        ),
    )
    global rag_agents, guide_agent, summary_agent
    global backup_agent, context_manager
    init_agents(
        agent_config_path=os.environ.get(
            "AGENT_CONFIG_PATH",
            "configs/agent_config_dict.json",
        ),
    )


def create_dash_output(
    messages: Union[Msg, List[Msg]],
    **kwargs: Any,
) -> Dict:
    """
    Create the output for dashscope
    Args:
        messages (`Union[Msg, List[Msg]]`):
            The messages to be considered
    """
    logger.info(f"create dash output, kwargs: {kwargs}")

    # usage_tracker = kwargs.get('usage_tracker', None)
    def convert_to_dict(
        messages: Union[List[Msg], dict, Msg],
    ) -> Union[dict, List[dict], List[Msg]]:
        if isinstance(messages, Msg):
            messages = messages.to_dict()
        if isinstance(messages, dict):
            return {k: convert_to_dict(v) for k, v in messages.items()}
        elif isinstance(messages, list):
            return [convert_to_dict(item) for item in messages]
        else:
            return messages

    messages = convert_to_dict(messages)
    if isinstance(messages, dict):
        messages = [messages]
    for msg in messages:
        if msg["name"] != "thinking_step":
            msg["name"] = "assistant"
    output = {
        "output": {
            "choices": [
                {
                    "messages": messages,
                },
            ],
        },
        # TODO: the usage tracker can be added
        "usage": {},
    }
    return output


async def asyn_agent_reply(agent: AgentBase, query: Msg) -> Msg:
    """
    A wrapper function for the async agent reply
    """
    if hasattr(agent, "async_reply"):
        return await agent.async_reply(query)
    else:
        return agent(query)


def start_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Start loop event for async"""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def run_query(
    query: Msg = None,
    rag_return_raw: bool = True,
    **kwargs: Any,
) -> Generator:
    """
    Run the query and return the result
    Args:
        query (`Msg`):
            A query from user
        rag_return_raw (`bool`):
            whether to let the retrieval agent return raw content

    Returns:
        `Generator`: stream output of the result
    """
    assert isinstance(context_manager, ContextManager)
    request_id = kwargs.get("request_id", "run_query.default_request_id")

    # handle the "exit" situation
    if len(query.content) == 0 or str(query.content).startswith("exit"):
        yield Msg(name="agent", role="assistant", content="")

    query_language = "Chinese"

    if query.metadata is None:
        query.metadata = {}
    query.metadata["language"] = query_language

    logger.query_info(
        request_id=request_id,
        location="copilot_app_run.run_query:input_query",
        context={
            "original_query": query,
            "query_language": query_language,
        },
    )

    # hardcode the rag_return_raw flog to be True for efficiency
    for agent in rag_agents:
        agent.return_raw = rag_return_raw

    # STAGE 1: Rewritten query to add context
    # by default, we only use one rewrite mechanism,
    # the query if rewritten by the context manager according to chat history
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=start_loop, args=(loop,))
    thread.start()
    rewrite_futures = [
        asyncio.run_coroutine_threadsafe(
            context_manager.async_rewrite_query(query),
            loop,
        ),
    ]

    # STAGE 2: Routing
    time_planning_start = time.perf_counter()

    cur_query = copy.deepcopy(query)
    try:
        speak_list = cluster_similarity_planning(
            query=cur_query,
            rag_agents=rag_agents,
            backup_agent=backup_agent,
        )
    except Exception as e:
        speak_list = []
        logger.query_error(
            request_id=request_id,
            location="copilot_app_run.run_query:find_speakers",
            context={"planning_method_fail": str(e)},
        )

    # if no RAG agent is found, use backup agent
    if len(speak_list) == 0:
        speak_list = [backup_agent]
    # if more than one RAG agent is called, involve the context
    # manager if we have one
    elif len(speak_list) > 0:
        if context_manager:
            speak_list = [context_manager] + speak_list

    logger.query_info(
        request_id=request_id,
        location="copilot_app_run.run_query:find_speakers",
        context={
            "time_cost": time.perf_counter() - time_planning_start,
            "speak_list": ("，".join(agent.name for agent in speak_list)),
        },
    )

    # STAGE 3: RAG agent (or backup agent) generate answers/raw material
    agent_response_msgs = {}
    time_rewrite_start = time.perf_counter()
    # get context related rewritten query
    for future in rewrite_futures:
        try:
            rewritten_query = future.result()
        except Exception as e:
            rewritten_query = Msg(role="user", name="", content=None)
            logger.query_error(
                request_id=request_id,
                location="copilot_app_run.run_query:multithread_answers",
                context={"rewritten_query_async_fail": str(e)},
            )
        logger.query_info(
            request_id=request_id,
            location="copilot_app_run.run_query:multithread_answers2",
            context={
                "time_cost": time.perf_counter() - time_rewrite_start,
                "rewritten_query_async": rewritten_query,
            },
        )

    if len(rewritten_query.content or "") > 0:
        query = rewritten_query
        query.metadata["request_id"] = request_id

    # run rag agent for results
    reply_futures = [
        asyncio.run_coroutine_threadsafe(
            asyn_agent_reply(agent, query),
            loop,
        )
        for i, agent in enumerate(speak_list)
    ]
    for future in concurrent.futures.as_completed(reply_futures):
        try:
            response_msg = future.result()
            agent_response_msgs[response_msg.name] = response_msg
        except Exception as e:
            logger.query_error(
                request_id=request_id,
                location="copilot_app_run.run_query:async_agent_reply",
                context={"error_text": str(e)},
            )
            response_msg = Msg
        logger.query_info(
            request_id=request_id,
            location="copilot_app_run.run_query:async_agent_reply",
            context={"agent_response_msg": response_msg},
        )
    loop.call_soon_threadsafe(loop.stop)
    thread.join()

    # STAGE 4: summarization and generate final answer
    # if RAG agent(s) and context manager are called, use the summary agent
    if len(agent_response_msgs) > 1:
        query_with_retrieved_result = Msg(
            name=query.name,
            role=query.role,
            content=agent_response_msgs,
            metadata={
                "query": query.content,
                "language": query_language,
                "request_id": request_id,
            },
        )

        # output in stream mode
        query_with_retrieved_result.metadata["rag_return_raw"] = rag_return_raw
        assert isinstance(summary_agent, Summarizer)
        answer = summary_agent(query_with_retrieved_result)
        yield from answer

    # if only backup agent is called, use its response directly
    elif len(agent_response_msgs) == 1:
        # no need to activate summarizer when there is only one RAG agent
        answer = ""
        for _, ans in agent_response_msgs.items():
            answer = ans
        answer_meta = answer.metadata if answer.metadata is not None else {}
        if "sources" in answer_meta and len(answer_meta["sources"]) > 0:
            answer.content = (
                answer.content
                + "\n\n###参考链接/reference：\n\n"
                + "\n\n".join(
                    answer_meta.get("sources", "[]"),
                )
            )
        yield answer
    # if no answer is provided, return the default fail answer.
    else:
        answer = Msg(
            name="agent",
            role="assistant",
            content=DEFAULT_FAIL_ANSWER.get(
                query_language.lower(),
                DEFAULT_FAIL_ANSWER["english"],
            ),
        )
        yield answer


def copilot_bot_pipeline(
    request_payload: dict,
    **kwargs: Any,
) -> Generator:
    """
    this pipeline defines the prime workflow of the copilot.
    Args:
        request_payload (`dict`):
            the payload of the request in standard Dashscope request format
        **kwargs (`dict`):
            consist of other useful parameters, e.g. request_id

    Returns:
        `Generator`: stream output
    """

    # if no request_id is provided, use the current timestamp
    request_id = kwargs.get("request_id", "default_request_id")
    # start the time tic
    time_start = time.perf_counter()

    # log the exact request input
    logger.query_info(
        request_id=request_id,
        location="copilot_app_run.copilot_bot_pipeline:input",
        context={"request_payload": request_payload},
    )

    # extract history messages and query message from the payload
    payload_copy = copy.deepcopy(request_payload)
    # clear the memory of context manager to save new history
    assert isinstance(context_manager, AgentBase)
    context_manager.memory.clear()
    if (
        len(payload_copy["input"]["messages"]) == 0
        or len(
            payload_copy["input"]["messages"][-1].get("content", ""),
        )
        == 0
    ):
        # add a default query (simply greeting)
        query = Msg(
            role="user",
            name="user",
            content="Hello.",
            metadata={"request_id": request_id},
        )
    else:
        history_msg_list = payload_copy["input"]["messages"][:-1]
        # convert the msg from dict to Msg, request id is also added
        query = Msg(
            role=payload_copy["input"]["messages"][-1].get("role", "user"),
            name=payload_copy["input"]["messages"][-1].get("name", "user"),
            content=payload_copy["input"]["messages"][-1].get("content", ""),
            metadata={"request_id": request_id},
        )
        # insert each of the msg dict into the memory of context manager
        # the context_manager's memory does not have the latest query
        # the latest query will be inserted inside the call of context_manager
        for msg in history_msg_list:
            history_msg = Msg(
                role=msg.get("role", "assistant"),
                name=msg.get("name", "assistant"),
                content=msg.get("content", "None"),
            )
            context_manager.observe(history_msg)

    response = run_query(
        query=query,
        rag_return_raw=str_to_bool(os.environ.get("RAG_RETURN_RAW", "True")),
        **kwargs,
    )
    last_pkg = None
    time_first_pkg = time_start
    for pkg in response:
        if not last_pkg:  # log the first pkg time
            time_first_pkg = time.perf_counter()
        last_pkg = pkg
        yield create_dash_output(pkg, **kwargs)
    logger.query_info(
        request_id=request_id,
        location="copilot_app_run.copilot_bot_pipeline:output",
        context={
            "time_first_pkg": time_first_pkg - time_start,
            "time_last_pkg": time.perf_counter() - time_start,
            "last_pkg": last_pkg,
        },
    )


def run_demo(request_template: dict) -> None:
    """
    This method is used to run a demo with a single query
    Args:
        request_template: the request template used for the pipeline
    """
    print("\n===== start of the demo =====\n")

    for pack_idx, package in enumerate(
        copilot_bot_pipeline(
            request_payload=request_template["payload"],
            request_id=request_template["header"]["request_id"],
        ),
    ):
        print(f"------Package {pack_idx}------")
        print(json.dumps(package, ensure_ascii=False, indent=2))

    print("\n===== end of the demo =====\n")


if __name__ == "__main__":
    nest_asyncio.apply()
    from datetime import datetime

    DB_NAME = os.getenv(
        "DB_PATH",
        f'logs/runs-{datetime.now().strftime("%Y-%m-%d-%H-%M")}.db',
    )
    # create table for logging
    create_logging_table(DB_NAME)
    initializations()
    with open(
        "configs/dash_request_template.json",
        "r",
        encoding="utf-8",
    ) as f:
        request = json.load(f)

    run_demo(request_template=request)
