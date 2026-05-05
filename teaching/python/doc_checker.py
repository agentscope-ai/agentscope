#!/usr/bin/env python3
"""
AgentScope 教案自动校验脚本
检查文档与源码的一致性、交叉引用、代码示例语法
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def _resolve_project_root() -> Path:
    """从脚本位置推断项目根目录"""
    script = Path(__file__).resolve()
    # teaching/python/doc_checker.py -> 项目根
    return script.parent.parent.parent


PROJECT_ROOT = _resolve_project_root()
SOURCE_DIR = PROJECT_ROOT / "src" / "agentscope"
DOCS_DIR = PROJECT_ROOT / "teaching"

# 模块目录到文件名的映射（支持检查路径引用）
MODULE_DIRS = [
    "agent", "model", "tool", "memory", "pipeline", "formatter",
    "rag", "session", "tracing", "embedding", "token", "evaluate",
    "realtime", "tts", "tuner", "mcp", "a2a", "message", "hooks",
]


def check_source_references(content: str, filename: str) -> list[str]:
    """检查文档中引用的源码文件是否真实存在"""
    issues = []

    # 匹配类似 src/agentscope/agent/_react_agent.py 的路径引用
    pattern = r'(?:src/)?agentscope/([\w/]+\.py)'
    for match in re.finditer(pattern, content):
        ref_path = match.group(1)
        full_path = SOURCE_DIR / ref_path
        if not full_path.exists():
            issues.append(f"[{filename}] 引用的源码文件不存在: agentscope/{ref_path}")

    # 匹配单独的 .py 文件引用（如 `_agent_base.py`）
    pattern2 = r'`(_\w+\.py)`'
    for match in re.finditer(pattern2, content):
        py_file = match.group(1)
        # 搜索所有模块目录
        found = False
        for mod_dir in MODULE_DIRS:
            if (SOURCE_DIR / mod_dir / py_file).exists():
                found = True
                break
        if not found and (SOURCE_DIR / py_file).exists():
            found = True
        if not found:
            # 仅在明显是源码文件名时报告（以下划线开头）
            if py_file.startswith("_"):
                issues.append(
                    f"[{filename}] 引用的源码文件可能不存在: {py_file}")

    return issues


def check_class_names(content: str, filename: str) -> list[str]:
    """检查文档中引用的关键类名在源码中是否存在"""
    issues = []

    # 关键类 -> 所在模块目录的映射
    key_classes = {
        "AgentBase": "agent",
        "ReActAgent": "agent",
        "ReActAgentBase": "agent",
        "UserAgent": "agent",
        "MsgHub": "pipeline",
        "ChatModelBase": "model",
        "OpenAIChatModel": "model",
        "AnthropicChatModel": "model",
        "Toolkit": "tool",
        "MemoryBase": "memory",
        "FormatterBase": "formatter",
        "Msg": "message",
    }

    for class_name, module_dir in key_classes.items():
        if class_name not in content:
            continue
        mod_path = SOURCE_DIR / module_dir
        if not mod_path.exists():
            continue
        found = False
        for py_file in mod_path.rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="ignore")
                if f"class {class_name}" in text:
                    found = True
                    break
            except Exception:
                continue
        if not found:
            issues.append(
                f"[{filename}] 引用的类 {class_name} 在 {module_dir}/ 中未找到")

    return issues


def check_broken_links(content: str, filename: str) -> list[str]:
    """检查 Markdown 内部链接是否指向存在的文件"""
    issues = []

    # 匹配 [text](path.md) 格式
    pattern = r'\[([^\]]+)\]\(([^)]+\.md)\)'
    for match in re.finditer(pattern, content):
        link_text = match.group(1)
        link_path = match.group(2)

        # 跳过 URL 链接
        if link_path.startswith("http"):
            continue

        # 解析相对路径
        doc_file = DOCS_DIR / filename
        target = (doc_file.parent / link_path).resolve()

        if not target.exists():
            issues.append(
                f"[{filename}] 链接指向不存在的文件: [{link_text}]({link_path})")

    return issues


def check_python_code_blocks(content: str, filename: str) -> list[str]:
    """检查 Python 代码块的语法是否正确"""
    issues = []

    # 提取 python 代码块
    pattern = r'```python\n(.*?)```'
    for i, match in enumerate(re.finditer(pattern, content, re.DOTALL), 1):
        code = match.group(1)
        # 跳过伪代码和包含省略号的代码
        if "..." in code or "伪代码" in code:
            continue
        try:
            ast.parse(code)
        except SyntaxError as e:
            # 只报告严重语法错误，忽略缩进问题（Markdown 代码块常见）
            if "unexpected EOF" not in str(e) and "expected an indented block" not in str(e):
                issues.append(
                    f"[{filename}] 代码块 #{i} 语法错误: {e.msg} (行 {e.lineno})")

    return issues


def check_line_number_ranges(content: str, filename: str) -> list[str]:
    """检查行号引用范围是否合理"""
    issues = []

    # 匹配 "第XX行" 或 ":XX-" 格式的行号引用
    pattern = r'第\s*(\d+)\s*[-~]\s*(\d+)\s*行'
    for match in re.finditer(pattern, content):
        start, end = int(match.group(1)), int(match.group(2))
        span = end - start
        if span > 300:
            issues.append(
                f"[{filename}] 行号引用范围过大 ({span}行): 第{start}-{end}行")

    return issues


def main() -> int:
    print("=" * 60)
    print("AgentScope 教案校验工具")
    print(f"源码目录: {SOURCE_DIR}")
    print(f"文档目录: {DOCS_DIR}")
    print("=" * 60)

    if not SOURCE_DIR.exists():
        print(f"错误: 源码目录不存在: {SOURCE_DIR}")
        return 1
    if not DOCS_DIR.exists():
        print(f"错误: 文档目录不存在: {DOCS_DIR}")
        return 1

    all_issues: list[str] = []
    doc_files = list(DOCS_DIR.glob("**/*.md"))

    checks = [
        ("源码文件引用", check_source_references),
        ("类名存在性", check_class_names),
        ("内部链接", check_broken_links),
        ("Python 语法", check_python_code_blocks),
        ("行号范围", check_line_number_ranges),
    ]

    for check_name, check_fn in checks:
        print(f"\n[检查] {check_name}...")
        count = 0
        for doc_file in doc_files:
            try:
                content = doc_file.read_text(encoding="utf-8")
                rel_name = str(doc_file.relative_to(DOCS_DIR))
                issues = check_fn(content, rel_name)
                all_issues.extend(issues)
                count += len(issues)
            except Exception as e:
                all_issues.append(f"[{doc_file.name}] 读取失败: {e}")
                count += 1
        status = f"发现 {count} 个问题" if count else "通过"
        print(f"  -> {status}")

    print("\n" + "=" * 60)
    print("校验结果")
    print("=" * 60)

    if all_issues:
        print(f"\n共发现 {len(all_issues)} 个问题:\n")
        for i, issue in enumerate(all_issues, 1):
            print(f"  {i}. {issue}")
        return 1
    else:
        print("\n所有检查通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
