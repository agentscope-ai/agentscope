# -*- coding: utf-8 -*-
""" Server of distributed agent"""
import os
import threading
import traceback
import json
from concurrent import futures
from multiprocessing.synchronize import Event as EventClass
from typing import Any
from loguru import logger
import requests

try:
    import cloudpickle as pickle
    import psutil
    import grpc
    from grpc import ServicerContext
    from google.protobuf.empty_pb2 import Empty
except ImportError as import_error:
    from agentscope.utils.common import ImportErrorReporter

    pickle = ImportErrorReporter(import_error, "distribute")
    psutil = ImportErrorReporter(import_error, "distribute")
    grpc = ImportErrorReporter(import_error, "distribute")
    ServicerContext = ImportErrorReporter(import_error, "distribute")
    Empty = ImportErrorReporter(  # type: ignore[misc]
        import_error,
        "distribute",
    )

from agentscope.rpc.rpc_object import RpcObject
from agentscope.rpc.rpc_meta import RpcMeta
import agentscope.rpc.rpc_agent_pb2 as agent_pb2
from agentscope.exception import StudioRegisterError
from agentscope.rpc import AsyncResult
from agentscope.rpc.rpc_agent_pb2_grpc import RpcAgentServicer
from agentscope.server.async_result_pool import get_pool
from agentscope.serialize import serialize


def _register_server_to_studio(
    studio_url: str,
    server_id: str,
    host: str,
    port: int,
) -> None:
    """Register a server to studio."""
    url = f"{studio_url}/api/servers/register"
    resp = requests.post(
        url,
        json={
            "server_id": server_id,
            "host": host,
            "port": port,
        },
        timeout=10,  # todo: configurable timeout
    )
    if resp.status_code != 200:
        logger.error(f"Failed to register server: {resp.text}")
        raise StudioRegisterError(f"Failed to register server: {resp.text}")


# todo: opt this
MAGIC_PREFIX = b"$$AS$$"


class AgentServerServicer(RpcAgentServicer):
    """A Servicer for RPC Agent Server (formerly RpcServerSideWrapper)"""

    def __init__(
        self,
        stop_event: EventClass,
        host: str = "localhost",
        port: int = None,
        server_id: str = None,
        studio_url: str = None,
        capacity: int = 32,
        pool_type: str = "local",
        redis_url: str = "redis://localhost:6379",
        max_pool_size: int = 8192,
        max_expire_time: int = 7200,
        max_timeout_seconds: int = 5,
    ):
        """Init the AgentServerServicer.

        Args:
            stop_event (`Event`): Event to stop the server.
            host (`str`, defaults to "localhost"):
                Hostname of the rpc agent server.
            port (`int`, defaults to `None`):
                Port of the rpc agent server.
            server_id (`str`, defaults to `None`):
                Server id of the rpc agent server.
            studio_url (`str`, defaults to `None`):
                URL of the AgentScope Studio.
            capacity (`int`, default to `32`):
                The number of concurrent agent in the servicer.
            max_pool_size (`int`, defaults to `8192`):
                The max number of async results that the server can
                accommodate. Note that the oldest result will be deleted
                after exceeding the pool size.
            max_expire_time (`int`, defaults to `7200`):
                Maximum time for async results to be cached in the server.
                Note that expired messages will be deleted.
            max_timeout_seconds (`int`, defaults to `5`):
                The maximum time (in seconds) that the server will wait for
                the result of an async call.
        """
        self.host = host
        self.port = port
        self.server_id = server_id
        self.studio_url = studio_url

        self.result_pool = get_pool(
            pool_type=pool_type,
            redis_url=redis_url,
            max_len=max_pool_size,
            max_expire=max_expire_time,
        )
        self.executor = futures.ThreadPoolExecutor(max_workers=capacity)
        self.task_id_lock = threading.Lock()
        self.agent_id_lock = threading.Lock()
        self.task_id_counter = 0
        self.agent_pool: dict[str, Any] = {}
        self.pid = os.getpid()
        self.stop_event = stop_event
        self.timeout = max_timeout_seconds

    def agent_exists(self, agent_id: str) -> bool:
        """Check whether the agent exists.

        Args:
            agent_id (`str`): the agent id.

        Returns:
            bool: whether the agent exists.
        """
        return agent_id in self.agent_pool

    def get_agent(self, agent_id: str) -> Any:
        """Get the object by agent id.

        Args:
            agent_id (`str`): the agent id.

        Returns:
            Any: the object.
        """
        with self.agent_id_lock:
            return self.agent_pool.get(agent_id, None)

    def is_alive(
        self,
        request: Empty,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Check whether the server is alive."""
        return agent_pb2.GeneralResponse(ok=True)

    def stop(
        self,
        request: Empty,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Stop the server."""
        self.stop_event.set()
        return agent_pb2.GeneralResponse(ok=True)

    def create_agent(
        self,
        request: agent_pb2.CreateAgentRequest,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Create a new agent on the server."""
        agent_id = request.agent_id
        agent_configs = pickle.loads(request.agent_init_args)
        cls_name = agent_configs["class_name"]
        try:
            cls = RpcMeta.get_class(cls_name)
        except ValueError as e:
            err_msg = (f"Class [{cls_name}] not found: {str(e)}",)
            logger.error(err_msg)
            return agent_pb2.GeneralResponse(ok=False, message=err_msg)
        try:
            instance = cls(
                *agent_configs["args"],
                **agent_configs["kwargs"],
            )
        except Exception as e:
            err_msg = f"Failed to create agent instance <{cls_name}>: {str(e)}"

            logger.error(err_msg)
            return agent_pb2.GeneralResponse(ok=False, message=err_msg)

        # Reset the __reduce_ex__ method of the instance
        # With this method, all objects stored in agent_pool will be serialized
        # into their Rpc version
        rpc_init_cfg = (
            cls,
            agent_id,
            self.host,
            self.port,
            True,
        )
        instance._dist_config = {  # pylint: disable=W0212
            "args": rpc_init_cfg,
        }

        def to_rpc(obj, _) -> tuple:  # type: ignore[no-untyped-def]
            return (
                RpcObject,
                obj._dist_config["args"],  # pylint: disable=W0212
            )

        instance.__reduce_ex__ = to_rpc.__get__(  # pylint: disable=E1120
            instance,
        )
        instance._oid = agent_id  # pylint: disable=W0212

        with self.agent_id_lock:
            if agent_id in self.agent_pool:
                return agent_pb2.GeneralResponse(
                    ok=False,
                    message=f"Agent with agent_id [{agent_id}] already exists",
                )
            self.agent_pool[agent_id] = instance
        logger.info(f"create agent instance <{cls_name}>[{agent_id}]")
        return agent_pb2.GeneralResponse(ok=True)

    def delete_agent(
        self,
        request: agent_pb2.StringMsg,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Delete agent from the server.

        Args:
            request (`StringMsg`): The `value` field is the agent_id of the
            agent to be deleted.
        """
        aid = request.value
        with self.agent_id_lock:
            if aid in self.agent_pool:
                agent = self.agent_pool.pop(aid)
                logger.info(
                    f"delete agent instance <{agent.__class__.__name__}>"
                    f"[{aid}]",
                )
                return agent_pb2.GeneralResponse(ok=True)
            else:
                logger.warning(
                    f"try to delete a non-existent agent [{aid}].",
                )
                return agent_pb2.GeneralResponse(
                    ok=False,
                    message=f"try to delete a non-existent agent [{aid}].",
                )

    def delete_all_agents(
        self,
        request: Empty,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        with self.agent_id_lock:
            self.agent_pool.clear()
            logger.info(
                "Deleting all agent instances on the server",
            )
        return agent_pb2.GeneralResponse(ok=True)

    def call_agent_func(  # pylint: disable=W0236
        self,
        request: agent_pb2.CallFuncRequest,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Call the specific servicer function."""
        agent_id = request.agent_id
        func_name = request.target_func
        raw_value = request.value
        agent = self.get_agent(request.agent_id)
        if agent is None:
            return context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Agent [{request.agent_id}] not exists.",
            )
        try:
            if (
                func_name
                in agent.__class__._info.async_func  # pylint: disable=W0212
            ):
                # async function
                task_id = self.result_pool.prepare()
                self.executor.submit(
                    self._process_task,
                    task_id,
                    agent_id,
                    func_name,
                    raw_value,
                )
                return agent_pb2.CallFuncResponse(
                    ok=True,
                    value=pickle.dumps(task_id),
                )
            elif (
                func_name
                in agent.__class__._info.sync_func  # pylint: disable=W0212
            ):
                # sync function
                args = pickle.loads(raw_value)
                res = getattr(agent, func_name)(
                    *args.get("args", ()),
                    **args.get("kwargs", {}),
                )
            else:
                res = getattr(agent, func_name)
            return agent_pb2.CallFuncResponse(
                ok=True,
                value=pickle.dumps(res),
            )
        except Exception:
            trace = traceback.format_exc()
            error_msg = f"Agent[{agent_id}] error: {trace}"
            logger.error(error_msg)
            return context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)

    def update_placeholder(
        self,
        request: agent_pb2.UpdatePlaceholderRequest,
        context: ServicerContext,
    ) -> agent_pb2.CallFuncResponse:
        """Update the value of a placeholder."""
        task_id = request.task_id
        try:
            result = self.result_pool.get(
                task_id,
                timeout=self.timeout,
            )
        except TimeoutError:
            context.abort(
                grpc.StatusCode.DEADLINE_EXCEEDED,
                "Timeout",
            )
        if result[:6] == MAGIC_PREFIX:
            return agent_pb2.CallFuncResponse(
                ok=False,
                message=result[6:].decode("utf-8"),
            )
        else:
            return agent_pb2.CallFuncResponse(
                ok=True,
                value=result,
            )

    def get_agent_list(
        self,
        request: Empty,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Get id of all agent on the server as a list."""
        from agentscope.agent import AgentBase

        with self.agent_id_lock:
            summaries = []
            for agent in self.agent_pool.values():
                if not isinstance(agent, AgentBase):
                    continue
                summaries.append(str(agent))
            return agent_pb2.GeneralResponse(
                ok=True,
                # TODO: unified into serialize function to avoid error.
                message=serialize(summaries),
            )

    def get_server_info(
        self,
        request: Empty,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Get the agent server resource usage information."""
        status = {}
        status["pid"] = self.pid
        status["id"] = self.server_id
        process = psutil.Process(self.pid)
        status["cpu"] = process.cpu_percent(interval=1)
        status["mem"] = process.memory_info().rss / (1024**2)
        status["size"] = len(self.agent_pool)
        return agent_pb2.GeneralResponse(ok=True, message=serialize(status))

    def set_model_configs(
        self,
        request: agent_pb2.StringMsg,
        context: ServicerContext,
    ) -> agent_pb2.GeneralResponse:
        """Set the model configs of the agent server."""
        from agentscope.manager import ModelManager

        model_configs = json.loads(request.value)
        try:
            ModelManager.get_instance().load_model_configs(model_configs)
        except Exception as e:
            return agent_pb2.GeneralResponse(
                ok=False,
                message=str(e),
            )
        return agent_pb2.GeneralResponse(ok=True)

    def get_agent_memory(
        self,
        request: agent_pb2.StringMsg,
        context: ServicerContext,
    ) -> agent_pb2.StringMsg:
        """Get the memory of a specific agent."""
        agent_id = request.value
        agent = self.get_agent(agent_id=agent_id)
        if agent is None:
            return agent_pb2.GeneralResponse(
                ok=False,
                message="Agent [{agent_id}] has not found",
            )
        if agent.memory is None:
            return agent_pb2.GeneralResponse(
                ok=False,
                message="Agent [{agent_id}] has no memory",
            )
        return agent_pb2.GeneralResponse(
            ok=True,
            message=serialize(agent.memory.get_memory()),
        )

    def download_file(
        self,
        request: agent_pb2.StringMsg,
        context: ServicerContext,
    ) -> Any:
        """Download file from local path."""
        filepath = request.value
        if not os.path.exists(filepath):
            context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"File {filepath} not found",
            )

        with open(filepath, "rb") as f:
            while True:
                piece = f.read(1024 * 1024)  # send 1MB each time
                if not piece:
                    break
                yield agent_pb2.ByteMsg(data=piece)

    def _process_task(
        self,
        task_id: int,
        agent_id: str,
        target_func: str,
        raw_args: bytes,
    ) -> None:
        """Processing the submitted task.

        Args:
            task_id (`int`): the id of the task.
            agent_id (`str`): the id of the agent that will be called.
            target_func (`str`): the name of the function that will be called.
            raw_args (`bytes`): the serialized input args.
        """
        if raw_args is not None:
            args = pickle.loads(raw_args)
        else:
            args = None
        agent = self.get_agent(agent_id)
        if isinstance(args, AsyncResult):
            args = args.result()  # pylint: disable=W0212
        try:
            if target_func == "reply":
                result = getattr(agent, target_func)(*args.get("args", ()))
            else:
                result = getattr(agent, target_func)(
                    *args.get("args", ()),
                    **args.get("kwargs", {}),
                )
            self.result_pool.set(task_id, pickle.dumps(result))
        except Exception:
            trace = traceback.format_exc()
            error_msg = f"Agent[{agent_id}] error: {trace}"
            logger.error(error_msg)
            self.result_pool.set(
                task_id,
                MAGIC_PREFIX + error_msg.encode("utf-8"),
            )
