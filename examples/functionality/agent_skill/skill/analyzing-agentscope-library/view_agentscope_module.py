# -*- coding: utf-8 -*-
# pylint: skip-file
"""Get the signatures of functions and classes in the agentscope library."""
from typing import Literal, Callable

import agentscope
import inspect
from pydantic import BaseModel


def get_class_signature(cls: type) -> str:
    """Get the signature of a class.

    Args:
        cls (`type`):
            A class object.

    Returns:
        str: The signature of the class.
    """
    # Obtain class name and docstring
    class_name = cls.__name__
    class_docstring = cls.__doc__ or ""

    # Construct the class string
    class_str = f"class {class_name}:\n"
    if class_docstring:
        class_str += f'    """{class_docstring}"""\n'

    # Obtain the module of the class
    methods = []
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        # Skip methods that are not part of the class
        if method.__qualname__.split(".")[0] != class_name:
            continue

        if name.startswith("_") and name not in ["__init__", "__call__"]:
            continue

        # Obtain the method's signature
        sig = inspect.signature(method)

        # Construct the method string
        method_str = f"    def {name}{sig}:\n"

        # Add the method's docstring if it exists
        method_docstring = method.__doc__ or ""
        if method_docstring:
            method_str += f'        """{method_docstring}"""\n'

        methods.append(method_str)

    class_str += "\n".join(methods)
    return class_str


def get_function_signature(func: Callable) -> str:
    """Get the signature of a function."""
    sig = inspect.signature(func)
    method_str = f"def {func.__name__}{sig}:\n"

    method_docstring = func.__doc__ or ""
    if method_docstring:
        method_str += f'   """{method_docstring}"""\n'

    return method_str


class FuncOrCls(BaseModel):
    """The class records the module, signature, docstring, reference, and
    type"""

    module: str
    """The module of the function or class."""
    signature: str
    """The signature of the function or class."""
    docstring: str
    """The docstring of the function or class."""
    reference: str
    """The reference to the source code of the function or class"""
    type: Literal["function", "class"]
    """The type of the function or class, either 'function' or 'class'."""

    def __init__(
        self,
        module: str,
        signature: str,
        docstring: str,
        reference: str,
        # pylint: disable=redefined-builtin
        type: Literal["function", "class"],
    ) -> None:
        """Initialize the FuncOrCls instance."""
        super().__init__(
            module=module,
            signature=signature.strip(),
            docstring=docstring.strip(),
            reference=reference,
            type=type,
        )


def _truncate_docstring(docstring: str, max_length: int = 200) -> str:
    """Truncate the docstring to a maximum length.

    Args:
        docstring (`str`):
            The docstring to truncate.
        max_length (`int`, *optional*, defaults to 200):
            The maximum length of the docstring.

    Returns:
        `str`:
            The truncated docstring.
    """
    if len(docstring) > max_length:
        return docstring[:max_length] + "..."
    return docstring


def get_agentscope_module_signatures() -> list[FuncOrCls]:
    """Get the signatures of functions and classes in the agentscope library.

    Returns:
        `list[FuncOrCls]`:
            A list of FuncOrCls instances representing the functions and
            classes in the agentscope library.
    """
    signatures = []
    for module in agentscope.__all__:
        as_module = getattr(agentscope, module)
        path_module = ".".join(["agentscope", module])

        # Functions
        if inspect.isfunction(as_module):
            file = inspect.getfile(as_module)
            source_lines, start_line = inspect.getsourcelines(as_module)
            signatures.append(
                FuncOrCls(
                    module=path_module,
                    signature=get_function_signature(as_module),
                    docstring=_truncate_docstring(as_module.__doc__ or ""),
                    reference=f"{file}: {start_line}-"
                    f"{start_line + len(source_lines)}",
                    type="function",
                ),
            )

        else:
            if not hasattr(as_module, "__all__"):
                continue

            # Modules with __all__ attribute
            for name in as_module.__all__:
                func_or_cls = getattr(as_module, name)
                path_func_or_cls = ".".join([path_module, name])

                if inspect.isclass(func_or_cls):
                    file = inspect.getfile(func_or_cls)
                    source_lines, start_line = inspect.getsourcelines(
                        func_or_cls,
                    )
                    signatures.append(
                        FuncOrCls(
                            module=path_func_or_cls,
                            signature=get_class_signature(func_or_cls),
                            docstring=_truncate_docstring(
                                func_or_cls.__doc__ or "",
                            ),
                            reference=(
                                f"{file}: {start_line}-"
                                f"{start_line + len(source_lines)}"
                            ),
                            type="class",
                        ),
                    )

                elif inspect.isfunction(func_or_cls):
                    file = inspect.getfile(func_or_cls)
                    source_lines, start_line = inspect.getsourcelines(
                        func_or_cls,
                    )
                    signatures.append(
                        FuncOrCls(
                            module=path_func_or_cls,
                            signature=get_function_signature(func_or_cls),
                            docstring=_truncate_docstring(
                                func_or_cls.__doc__ or "",
                            ),
                            reference=(
                                f"{file}: {start_line}-"
                                f"{start_line + len(source_lines)}"
                            ),
                            type="function",
                        ),
                    )

    return signatures


def view_agentscope_library(
    module: str,
) -> str:
    """View AgentScope's Python library by given a module name
    (e.g. agentscope), and return the module's submodules, classes, and
    functions. Given a class name, return the class's documentation, methods,
    and their signatures. Given a function name, return the function's
    documentation and signature. If you don't have any information about
    AgentScope library, try to use "agentscope" to view the available top
    modules.

    Note this function only provide the module's brief information.
    For more information, you should view the source code.

    Args:
        module (`str`):
            The module name to view, which should be a module path separated
            by dots (e.g. "agentscope.models"). It can refer to a module,
            a class, or a function.
    """
    if not module.startswith("agentscope"):
        return (
            f"Module '{module}' is invalid. The input module should be "
            f"'agentscope' or submodule of 'agentscope.xxx.xxx' "
            f"(separated by dots)."
        )

    agentscope_top_modules = {}
    for as_module in agentscope.__all__:
        if as_module in ["__version__", "logger"]:
            continue
        agentscope_top_modules[as_module] = getattr(
            agentscope,
            as_module,
        ).__doc__

    # top modules
    if module == "agentscope":
        top_modules_description = (
            [
                "The top-level modules in AgentScope library:",
            ]
            + [
                f"- agentscope.{k}: {v}"
                for k, v in agentscope_top_modules.items()
            ]
            + [
                "You can further view the classes/function within above "
                "modules by calling this function with the above module name.",
            ]
        )
        return "\n".join(top_modules_description)

    # class, functions
    modules = get_agentscope_module_signatures()
    for as_module in modules:
        if as_module.module == module:
            return f"""- The signature of '{module}':
```python
{as_module.signature}
```

- Source code reference: {as_module.reference}"""

    # two-level modules
    collected_modules = []
    for as_module in modules:
        if as_module.module.startswith(module):
            collected_modules.append(as_module)

    if len(collected_modules) > 0:
        collected_modules_content = (
            [
                f"The classes/functions and their truncated docstring in "
                f"'{module}' module:",
            ]
            + [f"- {_.module}: {repr(_.docstring)}" for _ in collected_modules]
            + [
                "The docstring is truncated for limited context. For detailed "
                "signature and methods, call this function with the above "
                "module name",
            ]
        )

        return "\n".join(collected_modules_content)

    return (
        f"Module '{module}' not found. Use 'agentscope' to view the "
        f"top-level modules to ensure the given module is valid."
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--module",
        type=str,
        default="agentscope",
        help="The module name to view, e.g. 'agentscope'",
    )
    args = parser.parse_args()

    res = view_agentscope_library(module=args.module)
    print(res)
