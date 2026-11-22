# -*- coding: utf-8 -*-
"""Read direct route guards and local policy calls from a source snapshot."""
import ast
from pathlib import PurePosixPath


def _citation(file: str, symbol: str, node: ast.AST, source: str) -> dict:
    return {
        "file": file,
        "symbol": symbol,
        "start": node.lineno,
        "end": node.end_lineno,
        "text": ast.get_source_segment(source, node),
    }


class SourceRules:
    """Link HTTP route declarations to their source guards."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = sources
        self.routes: dict[tuple[str, str], list] = {}
        self.functions: dict[tuple[str, str], list] = {}
        self.imports: dict[tuple[str, str], list] = {}
        for file, source in sources.items():
            if not file.endswith(".py"):
                continue
            for node in ast.parse(source, file).body:
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    module = (node.module or "").replace(".", "/")
                    target = PurePosixPath(file).parent / (module + ".py")
                    for alias in node.names:
                        key = (file, alias.asname or alias.name)
                        self.imports.setdefault(key, []).append(
                            (str(target), alias.name),
                        )
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault((file, node.name), []).append(
                        node,
                    )
                    for dec in node.decorator_list:
                        if not isinstance(dec, ast.Call) or not dec.args:
                            continue
                        if (
                            isinstance(dec.func, ast.Attribute)
                            and ast.unparse(dec.func.value) == "router"
                            and isinstance(dec.args[0], ast.Constant)
                            and isinstance(dec.args[0].value, str)
                        ):
                            key = (dec.func.attr.upper(), dec.args[0].value)
                            self.routes.setdefault(key, []).append(
                                (file, node, dec),
                            )

    def describe(self, request: dict) -> list[dict]:
        """Return observed handler checks and explicit gaps in the trace."""
        matches = self.routes.get((request["method"], request["route"]), [])
        if len(matches) != 1:
            return [
                {
                    "kind": "missing",
                    "statement": "需要唯一匹配的服务端路由声明。",
                    "evidence": request["evidence"],
                }
            ]
        file, func, dec = matches[0]
        citation = _citation(file, func.name, dec, self.sources[file])
        rules = [
            {
                "kind": "route",
                "statement": f"匹配源码中的路由声明 {ast.unparse(dec)}。",
                "evidence": citation,
            }
        ]
        if len(func.decorator_list) != 1 or dec.keywords:
            rules.append(
                {
                    "kind": "missing",
                    "statement": "还需核对路由装饰器和依赖的策略。",
                    "evidence": citation,
                }
            )
        return rules + self._guards(file, func, set())

    def _guards(
        self,
        file: str,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        seen: set,
    ) -> list[dict]:
        rules = []
        seen = seen | {(file, func.name)}
        for node in func.body:
            citation = _citation(file, func.name, node, self.sources[file])
            if isinstance(node, ast.Expr) and isinstance(
                node.value,
                ast.Constant,
            ):
                continue
            if (
                isinstance(node, ast.If)
                and not node.orelse
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Raise)
            ):
                test = ast.unparse(node.test)
                reason = ast.unparse(node.body[0])
                rules.append(
                    {
                        "kind": "rejection",
                        "statement": f"在 {func.name} 中，若 {test}，执行 {reason}。",
                        "evidence": citation,
                    }
                )
                values = sorted(
                    {
                        ast.unparse(item)
                        for item in ast.walk(node.test)
                        if isinstance(item, (ast.Subscript, ast.Call))
                    }
                )
                if values:
                    rules.append(
                        {
                            "kind": "runtime",
                            "statement": "判断具体记录还缺运行时值：" + "、".join(values),
                            "evidence": citation,
                        }
                    )
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                callee = ast.unparse(node.value.func)
                targets = self.imports.get((file, callee), [(file, callee)])
                if len(targets) == 1:
                    target = targets[0]
                    defs = self.functions.get(target, [])
                    if (
                        target not in seen
                        and len(defs) == 1
                        and isinstance(defs[0], ast.FunctionDef)
                        and not defs[0].decorator_list
                    ):
                        invocation = ast.unparse(node.value)
                        rules.append(
                            {
                                "kind": "policy_call",
                                "statement": f"调用本地策略 {invocation}。",
                                "evidence": citation,
                            }
                        )
                        rules.extend(self._guards(target[0], defs[0], seen))
                        continue
                rules.append(
                    {
                        "kind": "missing",
                        "statement": f"缺少可静态追踪的策略实现或调用关系：{callee}。",
                        "evidence": citation,
                    }
                )
                break
            # A write or return ends the supported guard prefix. Do not
            # interpret nested, subsequent or unreachable checks as mandatory.
            if (
                isinstance(node, ast.Assign)
                and isinstance(
                    node.value,
                    ast.Constant,
                )
                and all(isinstance(t, ast.Subscript) for t in node.targets)
            ):
                rules.append(
                    {
                        "kind": "assignment",
                        "statement": f"源码随后赋值 {ast.unparse(node)}。",
                        "evidence": citation,
                    }
                )
                if node is func.body[-1]:
                    break
            if isinstance(node, ast.Return) and (
                node.value is None or isinstance(node.value, ast.Constant)
            ):
                break
            rules.append(
                {
                    "kind": "missing",
                    "statement": "需要核对以下语句的控制流或依赖才能继续解释。",
                    "evidence": citation,
                }
            )
            break
        return rules
