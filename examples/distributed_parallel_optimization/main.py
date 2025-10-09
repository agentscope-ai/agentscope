# -*- coding: utf-8 -*-
"""An example use multiple agent to search the Internet for answers"""
import time
import argparse
from loguru import logger
from searcher_agent import SearcherAgent
from answerer_agent import AnswererAgent

import agentscope
from agentscope.agent import UserAgent
from agentscope.message import Msg


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-workers", type=int, default=5)
    parser.add_argument("--use-dist", action="store_true")
    parser.add_argument(
        "--search-engine",
        type=str,
        choices=["google", "bing"],
        default="google",
    )
    parser.add_argument(
        "--api-key",
        type=str,
    )
    parser.add_argument("--cse-id", type=str, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    agentscope.init(
        model_configs="configs/model_configs.json",
        project="Parallel Optimization",
    )

    WORKER_NUM = 3
    searcher = SearcherAgent(
        name="Searcher",
        model_config_name="my_model",
        result_num=args.num_workers,
        search_engine_type=args.search_engine,
        api_key=args.api_key,
        cse_id=args.cse_id,
    )
    answerers = []
    for i in range(args.num_workers):
        answerer = AnswererAgent(
            name=f"Answerer-{i}",
            model_config_name="my_model",
        )
        if args.use_dist:
            answerer = answerer.to_dist()
        answerers.append(answerer)

    user_agent = UserAgent()

    msg = user_agent()
    while not msg.content == "exit":
        start_time = time.time()
        msg = searcher(msg)
        results = []
        for page, worker in zip(msg.content, answerers):
            results.append(worker(Msg(**page)))
        for result in results:
            logger.chat(result)
        end_time = time.time()
        logger.chat(
            Msg(
                name="system",
                role="system",
                content=f"Completed in [{end_time - start_time:.2f}]s",
            ),
        )
        msg = user_agent()
