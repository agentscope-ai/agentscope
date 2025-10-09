# -*- coding: utf-8 -*-
"""An example parallel service execution."""

from typing import Sequence, Any, Callable
import os
import time
import argparse
from functools import partial
from loguru import logger

import agentscope
from agentscope.service import (
    google_search,
    bing_search,
    digest_webpage,
    ServiceToolkit,
)
from agentscope.service.service_response import (
    ServiceResponse,
    ServiceExecStatus,
)
from agentscope.agent import UserAgent, ReActAgent
from agentscope.manager import ModelManager
from agentscope.rpc.rpc_meta import RpcMeta, async_func


class RpcService(metaclass=RpcMeta):
    """The RPC service class."""

    def __init__(
        self,
        service_func: Callable[..., Any],
        **kwargs: Any,
    ) -> None:
        """
        Initialize the distributed service function.

        Args:
            service_func (`Callable[..., Any]`): The service function to be
                wrapped.
            **kwargs: Additional keyword arguments passed to the service.
        """
        if "model_config_name" in kwargs:
            model_config_name = kwargs.pop("model_config_name")
            model_manager = ModelManager.get_instance()
            model = model_manager.get_model_by_config_name(model_config_name)
            kwargs["model"] = model
        self.service_func = partial(service_func, **kwargs)

    @async_func
    def __call__(self, *args: tuple, **kwargs: dict) -> Any:
        """
        Execute the service function with the given arguments.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            `ServiceResponse`: The execution results of the services.
        """
        try:
            result = self.service_func(*args, **kwargs)
        except Exception as e:
            result = ServiceResponse(
                status=ServiceExecStatus.ERROR,
                content=str(e),
            )
        return result


def search_and_digest_webpage(
    query: str,
    search_engine_type: str = "google",
    num_results: int = 10,
    api_key: str = None,
    cse_id: str = None,
    model_config_name: str = None,
    html_selected_tags: Sequence[str] = ("h", "p", "li", "div", "a"),
    dist_search: bool = False,
) -> ServiceResponse:
    """
    Search question with search engine and digest the website in search result.

    Args:
        query (`str`):
            The search query string.
        search_engine_type (`str`, optional): the search engine to use.
            Defaults to "google".
        num_results (`int`, defaults to `10`):
            The number of search results to return.
        api_key (`str`, optional): api key for the search engine. Defaults
            to None.
        cse_id (`str`, optional): cse_id for the search engine. Defaults to
            None.
        model_config_name (`str`, optional): The name of model
            configuration for this tool. Defaults to None.
        html_selected_tags (Sequence[str]):
            the text in elements of `html_selected_tags` will
            be extracted and feed to the model.
        dist_search (`bool`, optional): whether to use distributed web digest.

    Returns:
        `ServiceResponse`: A dictionary with two variables: `status` and
        `content`. The `status` variable is from the ServiceExecStatus enum,
        and `content` is a list of search results or error information,
        which depends on the `status` variable.
        For each searching result, it is a dictionary with keys 'title',
        'link', 'snippet' and 'model_summary'.
    """
    if search_engine_type == "google":
        assert (api_key is not None) and (
            cse_id is not None
        ), "google search requires 'api_key' and 'cse_id'"
        search = partial(
            google_search,
            api_key=api_key,
            cse_id=cse_id,
        )
    elif search_engine_type == "bing":
        assert api_key is not None, "bing search requires 'api_key'"
        search = partial(bing_search, api_key=api_key)
    results = search(
        question=query,
        num_results=num_results,
    ).content

    digest = RpcService(
        digest_webpage,
        model_config_name=model_config_name,
        to_dist=dist_search,
    )
    cmds = [
        {
            "func": digest,
            "arguments": {
                "web_text_or_url": page["link"],
                "html_selected_tags": html_selected_tags,
            },
        }
        for page in results
    ]

    def execute_cmd(cmd: dict) -> str:
        service_func = cmd["func"]
        kwargs = cmd.get("arguments", {})

        # Execute the function
        func_res = service_func(**kwargs)
        return func_res

    # Execute the commands
    execute_results = [execute_cmd(cmd=cmd) for cmd in cmds]
    if dist_search:
        execute_results = [exe.result() for exe in execute_results]
    for result, exe_result in zip(results, execute_results):
        result["model_summary"] = exe_result.content
    return ServiceResponse(
        ServiceExecStatus.SUCCESS,
        results,
    )


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--logger-level",
        choices=["DEBUG", "INFO"],
        default="INFO",
    )
    parser.add_argument(
        "--studio-url",
        default=None,
        type=str,
    )
    parser.add_argument(
        "--use-dist",
        action="store_true",
    )
    parser.add_argument(
        "--api-key",
        type=str,
    )
    parser.add_argument(
        "--search-engine",
        type=str,
        choices=["google", "bing"],
        default="google",
    )
    parser.add_argument("--cse-id", type=str, default=None)
    return parser.parse_args()


def main() -> None:
    """Example for parallel service execution."""
    args = parse_args()

    # Prepare the model configuration
    YOUR_MODEL_CONFIGURATION_NAME = "dash"
    YOUR_MODEL_CONFIGURATION = [
        {
            "model_type": "dashscope_chat",
            "config_name": "dash",
            "model_name": "qwen-turbo",
            "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        },
    ]

    # Initialize the agentscope
    agentscope.init(
        model_configs=YOUR_MODEL_CONFIGURATION,
        use_monitor=False,
        logger_level=args.logger_level,
        studio_url=args.studio_url,
    )
    user_agent = UserAgent()
    service_toolkit = ServiceToolkit()

    service_toolkit.add(
        search_and_digest_webpage,
        search_engine_type=args.search_engine,
        num_results=10,
        api_key=args.api_key,
        cse_id=args.cse_id,
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        html_selected_tags=["p", "div", "h1", "li"],
        dist_search=args.use_dist,
    )
    agent = ReActAgent(
        name="assistant",
        model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
        verbose=True,
        service_toolkit=service_toolkit,
    )

    # User input and ReActAgent reply
    x = user_agent()
    start_time = time.time()
    agent(x)
    end_time = time.time()
    logger.info(f"Time taken: {end_time - start_time} seconds")


if __name__ == "__main__":
    main()
