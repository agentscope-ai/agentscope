#!/usr/bin/env python3
"""
AgentScope 智能教案生成器
使用 AST 解析源码，生成带 Java 对照的文档
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Optional


def _resolve_project_root() -> Path:
    """从脚本位置推断项目根目录"""
    script = Path(__file__).resolve()
    return script.parent.parent.parent


PROJECT_ROOT = _resolve_project_root()


class ClassInfo:
    """解析后的类信息"""

    def __init__(self, name: str, file_path: str) -> None:
        self.name = name
        self.file_path = file_path
        self.bases: list[str] = []
        self.docstring: str = ""
        self.methods: list[dict] = []
        self.attributes: list[dict] = []


def parse_file(file_path: Path) -> list[ClassInfo]:
    """用 AST 解析 Python 文件，提取所有类定义"""
    try:
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    classes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        info = ClassInfo(node.name, str(file_path))
        info.bases = [
            _ast_name(base) for base in node.bases
        ]

        # docstring
        doc = ast.get_docstring(node)
        if doc:
            info.docstring = textwrap.dedent(doc).strip()

        # 方法和属性
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = _parse_method(item)
                info.methods.append(method)
            elif isinstance(item, ast.AnnAssign) and item.target:
                attr_name = (
                    item.target.id if isinstance(item.target, ast.Name) else "?"
                )
                attr_type = (
                    ast.unparse(item.annotation) if item.annotation else "Any"
                )
                info.attributes.append({"name": attr_name, "type": attr_type})

        classes.append(info)

    return classes


def _ast_name(node: ast.expr) -> str:
    """提取 AST 名称节点的字符串"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_ast_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return ast.unparse(node)
    return ast.unparse(node)


def _parse_method(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    """解析方法定义"""
    args = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        type_str = ast.unparse(arg.annotation) if arg.annotation else "Any"
        args.append({"name": arg.arg, "type": type_str})

    returns = ast.unparse(node.returns) if node.returns else "None"
    doc = ast.get_docstring(node) or ""
    is_async = isinstance(node, ast.AsyncFunctionDef)

    return {
        "name": node.name,
        "args": args,
        "returns": returns,
        "docstring": doc.strip(),
        "is_async": is_async,
    }


def _java_type(py_type: str) -> str:
    """将 Python 类型映射为 Java 类型"""
    mapping = {
        "str": "String",
        "int": "int",
        "float": "double",
        "bool": "boolean",
        "list": "List",
        "dict": "Map<String, Object>",
        "None": "void",
        "Any": "Object",
        "Msg": "Msg",
    }
    return mapping.get(py_type, py_type)


def generate_class_doc(info: ClassInfo) -> str:
    """生成单个类的 Markdown 文档"""
    lines = [f"## {info.name}", ""]

    # 继承关系
    if info.bases:
        lines.append(f"**继承**: `{' -> '.join(info.bases)}`")
        lines.append("")

    # 源码位置
    rel = Path(info.file_path)
    try:
        rel = rel.relative_to(PROJECT_ROOT)
    except ValueError:
        pass
    lines.append(f"**源码**: `{rel}`")
    lines.append("")

    # 文档字符串
    if info.docstring:
        lines.append("### 概述")
        lines.append("")
        lines.append(info.docstring[:500])
        lines.append("")

    # 属性
    if info.attributes:
        lines.append("### 属性")
        lines.append("")
        lines.append("| 属性 | 类型 |")
        lines.append("|------|------|")
        for attr in info.attributes[:20]:
            lines.append(f"| `{attr['name']}` | `{attr['type']}` |")
        lines.append("")

    # 方法
    if info.methods:
        lines.append("### 方法")
        lines.append("")
        for method in info.methods:
            prefix = "async " if method["is_async"] else ""
            args_str = ", ".join(
                f"{a['name']}: {a['type']}" for a in method["args"]
            )
            lines.append(
                f"- `{prefix}{method['name']}({args_str}) -> {method['returns']}`"
            )
            if method["docstring"]:
                first_line = method["docstring"].split("\n")[0]
                lines.append(f"  - {first_line}")
        lines.append("")

    # Java 对照
    lines.append("### Java 对照")
    lines.append("")
    lines.append("```java")
    base = info.bases[0] if info.bases else "Object"
    lines.append(f"public class {info.name} extends {base} {{")
    for method in info.methods:
        if method["name"].startswith("_"):
            continue
        ret = _java_type(method["returns"])
        params = ", ".join(
            f"{_java_type(a['type'])} {a['name']}" for a in method["args"]
        )
        prefix = "async " if method["is_async"] else ""
        lines.append(
            f"    // {prefix}{method['name']}\n"
            f"    public {ret} {method['name']}({params}) {{ /* ... */ }}"
        )
    lines.append("}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def run(
    source_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> None:
    """运行生成器"""
    if source_root is None:
        source_root = PROJECT_ROOT / "src" / "agentscope"
    if output_dir is None:
        output_dir = PROJECT_ROOT / "teaching" / "python" / "_generated"

    if not source_root.exists():
        print(f"错误: 源码目录不存在: {source_root}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # 要解析的模块
    target_modules = {
        "agent": ["_agent_base.py", "_react_agent.py"],
        "model": ["_model_base.py"],
        "pipeline": ["_msghub.py"],
        "message": ["_message_base.py"],
    }

    total_classes = 0
    for module_dir, files in target_modules.items():
        module_path = source_root / module_dir
        if not module_path.exists():
            continue

        for filename in files:
            file_path = module_path / filename
            if not file_path.exists():
                print(f"  跳过（不存在）: {module_dir}/{filename}")
                continue

            classes = parse_file(file_path)
            if not classes:
                continue

            print(f"解析 {module_dir}/{filename}: {len(classes)} 个类")
            total_classes += len(classes)

            # 为每个类生成文档
            for info in classes:
                doc = generate_class_doc(info)
                out_file = output_dir / f"{info.name}.md"
                out_file.write_text(doc, encoding="utf-8")
                print(f"  -> 生成: {info.name}.md")

    print(f"\n完成: 共解析 {total_classes} 个类，输出到 {output_dir}")


if __name__ == "__main__":
    run()
