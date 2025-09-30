# -*- coding: utf-8 -*-
# pylint: disable=too-many-statements
"""A manager for AgentScope."""
import os
from typing import Union, Any, Optional
from copy import deepcopy

import requests
import shortuuid
from loguru import logger

from ._monitor import MonitorManager
from ._file import FileManager
from ._model import ModelManager
from ..agent import AgentBase, UserAgent, StudioUserInput
from ..logging import LOG_LEVEL, setup_logger
from .._version import __version__
from ..message import Msg
from ..model import ModelWrapperBase
from ..utils.common import (
    _generate_random_code,
    _get_process_creation_time,
    _get_timestamp,
)
from ..constants import _RUNTIME_ID_FORMAT, _RUNTIME_TIMESTAMP_FORMAT


class ASManager:
    """A manager for AgentScope."""

    _instance = None

    __serialized_attrs = [
        "project",
        "name",
        "disable_saving",
        "run_id",
        "pid",
        "timestamp",
        "studio_url",
    ]

    def __new__(cls, *args: Any, **kwargs: Any) -> "ASManager":
        if cls._instance is None:
            cls._instance = super(ASManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "ASManager":
        """Get the instance of the singleton class."""
        if cls._instance is None:
            raise ValueError(
                "AgentScope hasn't been initialized. Please call "
                "`agentscope.init` function first.",
            )
        return cls._instance

    def __init__(self) -> None:
        """Initialize the manager. Note we initialize the managers by default
        arguments to avoid unnecessary errors when user doesn't call
        `agentscope.init` function"""
        self.project = ""
        self.name = ""
        self.run_id = ""
        self.pid = -1
        self.timestamp = ""
        self.disable_saving = True

        self.file = FileManager()
        self.model = ModelManager()
        self.monitor = MonitorManager()

        self.studio_url = None

        # TODO: unified with logger and studio
        self.logger_level: LOG_LEVEL = "INFO"

    def initialize(
        self,
        model_configs: Union[dict, str, list, None],
        project: Union[str, None],
        name: Union[str, None],
        disable_saving: bool,
        save_dir: str,
        save_log: bool,
        save_code: bool,
        save_api_invoke: bool,
        cache_dir: str,
        use_monitor: bool,
        logger_level: LOG_LEVEL,
        run_id: Union[str, None],
        studio_url: Union[str, None],
    ) -> None:
        """Initialize the package."""
        # =============== Init the runtime ===============
        self.project = project or _generate_random_code()
        self.name = name or _generate_random_code(uppercase=False)

        self.pid = os.getpid()
        timestamp = _get_process_creation_time()

        self.timestamp = timestamp.strftime(_RUNTIME_TIMESTAMP_FORMAT)

        self.run_id = run_id or _get_timestamp(
            _RUNTIME_ID_FORMAT,
            timestamp,
        ).format(self.name)

        self.disable_saving = disable_saving

        # =============== Init the file manager ===============
        if disable_saving:
            save_log = False
            save_code = False
            save_api_invoke = False
            use_monitor = False
            run_dir = None
        else:
            run_dir = os.path.abspath(os.path.join(save_dir, self.run_id))

        self.file.initialize(
            run_dir=run_dir,
            save_log=save_log,
            save_code=save_code,
            save_api_invoke=save_api_invoke,
            cache_dir=cache_dir,
        )
        # Save the python code here to avoid duplicated saving in the child
        # process (when calling deserialize function)
        if save_code:
            self.file.save_python_code()

        if not disable_saving:
            # Save the runtime information in .config file
            self.file.save_runtime_information(self.state_dict())

        # =============== Init the logger         ===============
        # TODO: unified with studio and gradio
        self.logger_level = logger_level
        # run_dir will be None if save_log is False
        setup_logger(self.file.run_dir, logger_level)

        # =============== Init the model manager  ===============
        self.model.initialize(model_configs)

        # =============== Init the monitor manager ===============
        self.monitor.initialize(use_monitor)

        # =============== Init the studio          ===============
        # TODO: unified with studio and gradio

        # Init studio client, which will push messages to web ui and fetch user
        # inputs from web ui
        if studio_url is not None:
            self.studio_url = studio_url
            # Register the run
            response = requests.post(
                url=f"{studio_url}/trpc/registerRun",
                json={
                    "id": self.run_id,
                    "project": self.project,
                    "name": self.name,
                    "timestamp": self.timestamp,
                    "run_dir": run_dir,
                    "pid": self.pid,
                    "status": "running",
                },
            )
            response.raise_for_status()

            self._register_studio_hooks(studio_url)

    def state_dict(self) -> dict:
        """Serialize the runtime information."""
        serialized_data = {
            k: getattr(self, k) for k in self.__serialized_attrs
        }

        serialized_data["agentscope_version"] = __version__

        serialized_data["file"] = self.file.state_dict()
        serialized_data["model"] = self.model.state_dict()
        serialized_data["logger"] = {
            "level": self.logger_level,
        }
        serialized_data["monitor"] = self.monitor.state_dict()

        return deepcopy(serialized_data)

    def load_dict(self, data: dict) -> None:
        """Load the runtime information from a dictionary"""
        for k in self.__serialized_attrs:
            assert k in data, f"Key {k} not found in data."
            setattr(self, k, data[k])

        self.file.load_dict(data["file"])
        # TODO: unified the logger with studio and gradio
        self.logger_level = data["logger"]["level"]
        setup_logger(self.file.run_dir, self.logger_level)
        self.model.load_dict(data["model"])
        self.monitor.load_dict(data["monitor"])

        if data.get("studio_url") is not None:
            # Register studio related hook
            self._register_studio_hooks(str(data.get("studio_url")))

    def _register_studio_hooks(self, studio_url: str) -> None:
        """Register studio related hooks within AgentScope."""

        # register agent hook
        def studio_pre_speak_hook(
            _obj: AgentBase,
            msg: Msg,
            _stream: bool,
            _last: bool,
        ) -> None:
            """Hook function for pre_speak."""
            message_data = msg.to_dict()
            message_data.pop("__module__")
            message_data.pop("__name__")

            if hasattr(_obj, "_reply_id"):
                reply_id = getattr(_obj, "_reply_id")
            else:
                reply_id = shortuuid.uuid()

            n_retry = 0
            while True:
                try:
                    res = requests.post(
                        f"{studio_url}/trpc/pushMessage",
                        json={
                            "runId": self.run_id,
                            "replyId": reply_id,
                            "name": reply_id,
                            "role": "assistant",
                            "msg": message_data,
                        },
                    )
                    res.raise_for_status()
                    break
                except Exception as e:
                    if n_retry < 3:
                        n_retry += 1
                        continue

                    raise e from None

        # Register the hook function to push messages to studio
        AgentBase.register_class_hook(
            "pre_speak",
            "studio_pre_speak_hook",
            studio_pre_speak_hook,
        )

        # Exchange the input method to the studio input method, so that
        # the user can input data from the web ui
        UserAgent.override_class_input_method(
            StudioUserInput(
                studio_url=studio_url,
                run_id=self.run_id,
                max_retries=3,
            ),
        )

        # Register the hook function to push model invocation to studio
        def studio_save_model_invocation_hook(
            obj: ModelWrapperBase,
            model_invocation_id: str,
            timestamp: str,
            arguments: dict,
            response: dict,
            usage: Optional[dict] = None,
        ) -> None:
            """The hook that pushes the model invocation to studio."""
            n_retry = 0
            while True:
                try:
                    res = requests.post(
                        url=f"{studio_url}/trpc/pushModelInvocation",
                        json={
                            "id": model_invocation_id,
                            "runId": self.run_id,
                            "timestamp": timestamp,
                            "modelName": obj.model_name,
                            "modelType": obj.model_type,
                            "configName": obj.config_name,
                            "arguments": arguments,
                            "response": response,
                            "usage": usage,
                        },
                    )
                    res.raise_for_status()
                    break
                except Exception as e:
                    if n_retry < 3:
                        n_retry += 1
                        continue

                    raise e from None

        ModelWrapperBase.register_save_model_invocation_hook(
            "studio_save_model_invocation_hook",
            studio_save_model_invocation_hook,
        )

    def flush(self) -> None:
        """Flush the runtime information."""
        self.project = ""
        self.name = ""
        self.run_id = ""
        self.pid = -1
        self.timestamp = ""
        self.disable_saving = True

        self.file.flush()
        self.model.flush()
        self.monitor.flush()
        logger.remove()

        self.logger_level = "INFO"
