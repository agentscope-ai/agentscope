# -*- coding: utf-8 -*-
"""The init function for the package."""
import json
from typing import Optional, Union, Sequence, Type

from loguru import logger

from agentscope import agent
from .agent import AgentBase
from .logging import LOG_LEVEL
from .constants import _DEFAULT_SAVE_DIR
from .constants import _DEFAULT_LOG_LEVEL
from .constants import _DEFAULT_CACHE_DIR
from .manager import ASManager, ModelManager
from .model import ModelWrapperBase

# init the singleton class by default settings to avoid reinit in subprocess
# especially in spawn mode, which will copy the object from the parent process
# to the child process rather than re-import the module (fork mode)
ASManager()


def init(
    model_configs: Optional[Union[dict, str, list]] = None,
    project: Optional[str] = None,
    name: Optional[str] = None,
    disable_saving: bool = False,
    save_dir: str = _DEFAULT_SAVE_DIR,
    save_log: bool = True,
    save_code: bool = True,
    save_api_invoke: bool = False,
    cache_dir: str = _DEFAULT_CACHE_DIR,
    use_monitor: bool = True,
    logger_level: LOG_LEVEL = _DEFAULT_LOG_LEVEL,
    runtime_id: Optional[str] = None,
    agent_configs: Optional[Union[str, list, dict]] = None,
    studio_url: Optional[str] = None,
) -> Sequence[AgentBase]:
    """A unified entry to initialize the package, including model configs,
    runtime names, saving directories and logging settings.

    Args:
        model_configs (`Optional[Union[dict, str, list]]`, defaults to `None`):
            A dict, a list of dicts, or a path to a json file containing
            model configs.
        project (`Optional[str]`, defaults to `None`):
            The project name, which is used to identify the project.
        name (`Optional[str]`, defaults to `None`):
            The name for runtime, which is used to identify this runtime.
        disable_saving (`bool`, defaults to `False`):
            Whether to disable saving files. If `True`, this will override
            the `save_log`, `save_code`, and `save_api_invoke` parameters.
        runtime_id (`Optional[str]`, defaults to `None`):
            The id for runtime, which is used to identify this runtime. Use
            `None` will generate a random id.
        save_dir (`str`, defaults to `./runs`):
            The directory to save logs, files, codes, and api invocations.
            If `dir` is `None`, when saving logs, files, codes, and api
            invocations, the default directory `./runs` will be created.
        save_log (`bool`, defaults to `False`):
            Whether to save logs locally.
        save_code (`bool`, defaults to `False`):
            Whether to save codes locally.
        save_api_invoke (`bool`, defaults to `False`):
            Whether to save api invocations locally, including model and web
            search invocation.
        cache_dir (`str`):
            The directory to cache files. In Linux/Mac, the dir defaults to
            `~/.cache/agentscope`. In Windows, the dir defaults to
            `C:\\users\\<username>\\.cache\\agentscope`.
        use_monitor (`bool`, defaults to `True`):
            Whether to activate the monitor.
        logger_level (`LOG_LEVEL`, defaults to `"INFO"`):
            The logging level of logger.
        agent_configs (`Optional[Union[str, list, dict]]`, defaults to `None`):
            The config dict(s) of agent or the path to the config file,
            which can be loaded by json.loads(). One agent config should
            cover the required arguments to initialize a specific agent
            object, otherwise the default values will be used.
        studio_url (`Optional[str]`, defaults to `None`):
            The url of the agentscope studio.
    """
    # Init the runtime
    ASManager.get_instance().initialize(
        model_configs=model_configs,
        project=project,
        name=name,
        disable_saving=disable_saving,
        save_dir=save_dir,
        save_log=save_log,
        save_code=save_code,
        save_api_invoke=save_api_invoke,
        cache_dir=cache_dir,
        use_monitor=use_monitor,
        logger_level=logger_level,
        run_id=runtime_id,
        studio_url=studio_url,
    )

    # Load config and init agent by configs
    if agent_configs is not None:
        if isinstance(agent_configs, str):
            with open(agent_configs, "r", encoding="utf-8") as file:
                configs = json.load(file)
        elif isinstance(agent_configs, dict):
            configs = [agent_configs]
        else:
            configs = agent_configs

        # setup agent
        agent_objs = []
        for config in configs:
            agent_cls = getattr(agent, config["class"])
            agent_args = config["args"]
            agent = agent_cls(**agent_args)
            agent_objs.append(agent)
        return agent_objs
    return []


def state_dict() -> dict:
    """Get the status of agentscope."""
    return ASManager.get_instance().state_dict()


def print_llm_usage() -> dict:
    """Print the usage of LLM."""
    return ASManager.get_instance().monitor.print_llm_usage()


def register_model_wrapper_class(
    model_wrapper_class: Type[ModelWrapperBase],
    exist_ok: bool = False,
) -> None:
    """Register the model wrapper in AgentScope so that you can use it with
    model configurations.

    Args:
        model_wrapper_class (`Type[ModelWrapperBase]`):
            The model wrapper class to be registered, which must inherit from
            `ModelWrapperBase`.
        exist_ok (`bool`, defaults to `False`):
            Whether to overwrite the existing model wrapper with the same name.
    """
    logger.info(
        f"Registering model wrapper class `{model_wrapper_class.__name__}`.",
    )
    ModelManager.get_instance().register_model_wrapper_class(
        model_wrapper_class,
        exist_ok,
    )
    logger.info("Done.")
