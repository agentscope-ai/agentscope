# -*- coding: utf-8 -*-
"""Workflow running miscs."""
import os
import json
from numbers import Number
from typing import List, Any, Dict, Optional, Union, Tuple
import re
import importlib.util
import yaml
import json5
from app.db.init_db import get_session
from app.services.provider_service import ProviderService
from app.utils.crypto import decrypt_with_rsa
from loguru import logger

from agentscope.model import ModelWrapperBase

from ..constant import DO_NOT_INDENT_SIGN  # , DEV_MODE


def create_variable_mapping(placeholders: List[str]) -> Dict[str, str]:
    """
    Create a mapping of variable names from a list of placeholders.

    This function processes a list of placeholder strings that represent
    variable names. It creates a new mapping of these variable names with a
    simplified or transformed format.

    Args:
        placeholders (List[str]): A list of placeholder strings representing
            variable names.

    Returns:
        Dict[str, str]: A dictionary mapping original placeholders to their
            new variable names.
    """

    def can_convert_to_int(s: str) -> bool:
        try:
            int(s)
            return True
        except ValueError:
            return False

    # Dictionary to store the old to new variable name mapping
    mapping = {}
    # Dictionary to store the old to new variable name mapping
    full_var_mapping = {}
    # Dictionary to track the count of each base name
    count = {}

    for placeholder in placeholders:
        # Extract the base name after the last dot
        parts = placeholder.split(".")
        base_name = f"{parts[0]}.{parts[1]}"

        if base_name in full_var_mapping:
            new_base_name = full_var_mapping[base_name]
        else:
            if parts[1] in count:
                count[parts[1]] += 1
                full_var_mapping[base_name] = f"{parts[1]}_{count[parts[1]]}"
            else:
                count[parts[1]] = 1
                full_var_mapping[base_name] = parts[1]
            new_base_name = full_var_mapping[base_name]

        # Create new variable name
        new_name = f"{new_base_name}"
        for other_part in parts[2:]:
            if other_part.startswith("[") and other_part.endswith("]"):
                other_name = other_part[1:-1]
            else:
                other_name = other_part

            if can_convert_to_int(other_name):
                new_name += f"[{other_name}]"
            else:
                new_name += f"['{other_name}']"

        # Add to mapping
        mapping[placeholder] = new_name

    return mapping


def load_config(file_path: str) -> Dict:
    """
    Load a configuration file and return its content as a dictionary.

    This function reads a configuration file in either YAML or JSON format.
    It processes the JSON file to include specific keys for 'workflow' and
    'app'.

    Args:
        file_path (str): The path to the configuration file.

    Returns:
        Dict: The content of the configuration file as a
            dictionary. The format depends on the file extension
            (.yml/.yaml for YAML, .json for JSON).

    Raises:
        ValueError: If the file extension is not supported.
    """
    _, file_extension = os.path.splitext(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        if file_extension in [".yml", ".yaml"]:
            return yaml.safe_load(f)
        elif file_extension == ".json":
            res = json.load(f)
            res["workflow"] = {"graph": res["Data"]["config"]}
            res["app"] = {"mode": "agentflow"}
            return res
        else:
            raise ValueError("Unsupported file extension")


def add_indentation_to_code_lines(
    code_sections: List[str],
    indent_level: int,
    processed_lines: List[str],
) -> None:
    """
    Process a list of code sections by splitting lines and adding indentation,
    and append the results in-place to the provided processed_lines list.

    Args:
        code_sections (list of str): List containing code sections as strings.
        indent_level (int): The number of indentations to apply.
        processed_lines (list of str): List to append processed lines to.

    Returns:
        None: The function modifies the processed_lines list in-place.
    """
    for section in code_sections:
        lines = section.split("\n")
        for line in lines:
            processed_lines.append(f"{'    ' * indent_level}{line}")


def clear_script(script: str) -> str:
    """
    Process a script by removing specific indentation control markers and
    any trailing whitespace from each line where the marker is found.

    Args:
        script (str): The script content as a single string, potentially
            containing lines with a DO_NOT_INDENT_SIGN indicating parts to
            be joined without indentation.

    Returns:
        str: The modified script as a string with specified indentation
        markers removed.
    """
    lines = script.split("\n")
    clear_lines = []
    for line in lines:
        prefix = ""
        if DO_NOT_INDENT_SIGN in line:
            prefix, line = line.split(DO_NOT_INDENT_SIGN)
        clear_lines.append(prefix.rstrip() + line)
    return "\n".join(clear_lines)


def build_no_intend_code(code_str: str) -> str:
    """
    Prepend a specific marker to a code string to indicate that it should
    not be indented.

    Args:
        code_str (str): The code string to which the no-indent marker should
            be added.

    Returns:
        str: The code string prefixed with the no-indent marker.
    """
    return f"{DO_NOT_INDENT_SIGN}{code_str}"


def create_format_output(
    messages: List,
    filter_content: Optional[str] = None,
    **kwargs: Any,
) -> Dict:
    """
    Create a formatted output dictionary from a list of message dictionaries.

    Args:
        messages (List): A list of message dictionaries to be included in
            the output.
        filter_content (Optional[str]): An optional filter string. If provided,
            messages with this content will be excluded from the output.
        **kwargs: Additional keyword arguments that may include:
            - usage_tracker: An object with a 'usage' attribute to track
                usage data.
            - custom_field: A custom field to include in the output under
                the 'custom' key.

    Returns:
        Dict: A dictionary with formatted output, including usage
        and optionally filtered messages and custom fields.
    """
    usage_tracker = kwargs.get("usage_tracker", None)
    if filter_content:
        messages = [
            msg for msg in messages if filter_content != msg["content"]
        ]
    output: Dict[str, Any] = {
        "output": {
            "choices": [
                {
                    "messages": messages,
                },
            ],
        },
        "usage": usage_tracker.usage if usage_tracker else None,
    }

    custom_field = kwargs.get("custom_field", {})
    if custom_field:
        output["output"]["custom"] = custom_field

    return output


# pylint: disable=too-many-return-statements, too-many-branches
def identify_and_convert_expr(expr: Union[str, Number]) -> Tuple[str, Any]:
    """
    Identify the type of expression and convert it accordingly.

    This function attempts to determine the type of given expression and
    convert it to a corresponding Python data type. It checks for booleans,
    numbers, JSON objects, arrays, and defaults to strings. The function
    supports JSON-like string input and returns a tuple with the identified
    type and the converted value.

    Args:
        expr (Union[str, Number]): The expression to be evaluated and
            converted. It can be a string or a number.

    Returns:
        Tuple[str, Any]: A tuple where the first element is a string
            representing the identified type ("Boolean", "Number", "Object",
            "Array<String>", etc.), and the second element is the converted
            value in its appropriate Python type.

    Raises:
        ValueError: May be raised indirectly when attempting to convert
            strings to numbers or JSON if the conversion fails.
    """
    # TODO: might be inconsistent with official version
    if isinstance(expr, Number):
        return "Number", expr

    # Boolean
    if expr.lower() == "true":
        return "Boolean", True
    elif expr.lower() == "false":
        return "Boolean", False

    # Number
    try:
        if "." in expr:
            return "Number", float(expr)
        else:
            return "Number", int(expr)
    except ValueError:
        pass

    # JSON
    try:
        result = json5.loads(expr)

        if isinstance(result, dict):
            return "Object", result

        if isinstance(result, list):
            # Check for specific types within the list
            if all(isinstance(item, str) for item in result):
                return "Array<String>", result
            elif all(item is True or item is False for item in result):
                return "Array<Boolean>", result
            elif all(isinstance(item, (int, float)) for item in result):
                return "Array<Number>", result
            elif all(isinstance(item, dict) for item in result):
                return "Array<Object>", result
            else:
                # If it doesn't match any specific array type, return a
                # generic array
                return "Array<Mixed>", result

    except (ValueError, TypeError):
        pass

    return "String", expr


def get_model_instance(
    model_config: dict,
    stream: bool = False,
) -> ModelWrapperBase:
    """
    Get a model instance based on the provided model configuration.
    This function retrieves a model instance based on the provided model
    configuration. It uses the ModelManager to get the model instance
    based on the model ID and parameters.
    Args:
        model_config (dict): The model configuration dictionary.
    Returns:
        Any: The model instance.
    Raises:
        ValueError: If the model ID is not found in the ModelManager.
    """
    # if DEV_MODE:
    #     api_key = os.getenv("DASHSCOPE_API_KEY")
    # else:
    #     # TODO: fix api_key
    #     ...

    provider = model_config.get("provide", "Tongyi")
    try:
        for session in get_session():
            provider_service = ProviderService(
                session=session,
            )
            provider_info = provider_service.get_provider(
                provider=provider,
                workspace_id="1",
            )
            assert (
                provider_info.credential is not None
            ), "Provider credential cannot be None"
            model_credential = json.loads(provider_info.credential)
            api_key = decrypt_with_rsa(model_credential["api_key"])

    except Exception as e:
        logger.error(
            f"Failed to retrieve from knowledge base: {str(e)}",
        )
        # only for local test
        api_key = os.getenv("DASHSCOPE_API_KEY")

    if "model_id" in model_config and "qwen" in model_config["model_id"]:
        from agentscope.model import DashScopeChatWrapper

        params = {}
        for param in model_config["params"]:
            if param["enable"]:
                params[param["key"]] = param["value"]

        return DashScopeChatWrapper(
            config_name="_",
            model_name=model_config["model_id"],
            api_key=api_key,
            stream=stream,
            generate_args=params,
        )
    elif "app_id" in model_config:
        from agentscope.model import DashScopeApplicationWrapper

        return DashScopeApplicationWrapper(
            config_name="_",
            app_id=model_config["app_id"],
            api_key=api_key,
            stream=stream,
        )

    else:
        raise ValueError(f"Unsupported model configuration: {model_config}")


def get_module_source_content(module_name: str) -> Optional[str]:
    """
    Retrieve the source content of a specified Python module.

    Args:
        module_name (str): The name of the module to retrieve.

    Returns:
        Optional[str]: The content of the module's source file if available,
        or a string with an error message if an exception occurs.
    """
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            raise ImportError(
                f'No module named "{module_name.split(".")[-1]} found',
            )
        if not spec.has_location or spec.origin is None:
            raise FileNotFoundError(
                f'The module "{module_name.split(".")[-1]}" does not have a '
                f"physical location on filesystem",
            )
        file_path = spec.origin
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()
            content = re.sub(
                r"from\s+\.\s+import\s+(\w+)",
                r"import \1",
                content,
            )

            content = re.sub(
                r"from\s+(\.+)(\w+)(.*?)\s+import",
                r"from \2\3 import",
                content,
            )

    except Exception as e:
        content = f"raise Exception('{e}')"

    return content
