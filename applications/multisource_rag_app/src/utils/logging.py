# -*- coding: utf-8 -*-
"""
logging module for rag application
"""
import json
import os
from datetime import datetime
from typing import Dict
from typing import Any

from .db_logging import record_log

logger_type = os.environ.get("LOGGER_TYPE", "loguru")
if logger_type == "dash":
    from dashscope_serving.logger.dashscope_logger import (
        dashscope_logger as dash_logger,
    )
else:
    from loguru import logger as loguru_logger

DB_NAME = os.getenv(
    "DB_PATH",
    f'logs/runs-{datetime.now().strftime("%Y-%m-%d-%H-%M")}.db',
)


def to_dict(obj: Any) -> Dict:
    """convert to dictionary"""
    try:
        if isinstance(obj, dict):
            return obj
        elif hasattr(obj, "to_dict"):
            return obj.to_dict()
        else:
            return {"obj": str(obj)}
    except TypeError:
        return {"obj": str(obj)}


class logger:
    """logger for rag application"""

    @staticmethod
    def info(*arg: Any, **kwargs: Any) -> Any:
        """logging info"""
        if len(kwargs) > 0:
            try:  # try to record the info if all fields are present
                request_id = kwargs.get("request_id", "")
                location = kwargs.get("location", "")
                context = kwargs.get("context", {})
                if request_id and location and context:
                    context = json.dumps(
                        context,
                        ensure_ascii=False,
                        indent=2,
                        default=to_dict,
                    )  # or indent=2?
                    record_log(
                        db_name=DB_NAME,
                        request_id=request_id,
                        location=location,
                        context=context,
                    )
            except Exception as e:
                print(f"record_log error: {e}")
        if logger_type == "dash":
            if arg and isinstance(arg[0], str):
                # only one str is given, e.g., logger.info("hello world")
                msg_text = arg[0]
            else:
                # other cases, e.g. typical dash logger usage
                msg_text = ""  # default value for "message" field
            return dash_logger.query_info(
                request_id=kwargs.get("request_id", "default_request_id"),
                message=kwargs.get("location", msg_text),
                context=kwargs.get("context", ""),
            )
        if arg:
            return loguru_logger.info(f"{arg[0]}")
        else:
            return loguru_logger.info(
                "\n"
                + json.dumps(
                    kwargs,
                    ensure_ascii=False,
                    indent=2,
                    default=to_dict,
                ),
            )

    @staticmethod
    def query_info(*arg: Any, **kwargs: Any) -> Any:
        """logging query info"""
        return logger.info(*arg, **kwargs)

    @staticmethod
    def error(*arg: Any, **kwargs: Any) -> Any:
        """logging error"""
        if logger_type == "dash":
            return dash_logger.query_error(
                request_id=kwargs.get("request_id", "default_request_id"),
                message=kwargs.get("location", str(arg[0])),
                context=kwargs.get("context", ""),
            )
        if arg:
            if kwargs:
                return loguru_logger.error(
                    f"{arg[0]}, "
                    f"{json.dumps(kwargs, ensure_ascii=False, default=to_dict)}",
                )
            else:
                return loguru_logger.error(f"{arg[0]}")
        else:
            return loguru_logger.error(
                json.dumps(kwargs, ensure_ascii=False, default=to_dict),
                exc_info=True,
            )

    @staticmethod
    def query_error(*arg: Any, **kwargs: Any) -> Any:
        """logging query error"""
        return logger.error(*arg, **kwargs)

    @staticmethod
    def warning(*arg: Any, **kwargs: Any) -> Any:
        """logging warning"""
        return logger.warning(*arg, **kwargs)
