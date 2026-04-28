#!/usr/bin/env python3
"""
AgentScope 智能教案生成器
从源码自动生成教案，带 Java 对照
"""

import os
import re
from pathlib import Path
from typing import Optional

class SourceDocGenerator:
    """从源码自动生成文档"""

    def __init__(self, source_root: str):
        self.source_root = Path(source_root)

    def parse_class(self, file_path: Path, class_name: str) -> Optional[dict]:
        """解析类定义"""
        content = file_path.read_text()
        if f"class {class_name}" not in content:
            return None

        # 提取 docstring
        docstring = self._extract_docstring(content, class_name)

        # 提取方法
        methods = self._extract_methods(content, class_name)

        # 提取属性
        attrs = self._extract_attributes(content, class_name)

        return {
            "name": class_name,
            "docstring": docstring,
            "methods": methods,
            "attributes": attrs,
            "source_file": str(file_path.relative_to(self.source_root))
        }

    def _extract_docstring(self, content: str, class_name: str) -> str:
        """提取类的文档字符串"""
        pattern = rf'class {class_name}.*?"""(.*?)"""'
        match = re.search(pattern, content, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _extract_methods(self, content: str, class_name: str) -> list[dict]:
        """提取类的方法"""
        methods = []
        # 简单实现：查找 def xxx(self
        for line in content.split('\n'):
            if 'def ' in line and 'self' in line:
                method_name = line.split('def ')[1].split('(')[0].strip()
                methods.append({"name": method_name})
        return methods

    def generate_markdown(self, class_info: dict) -> str:
        """生成 Markdown 文档"""
        md = f"""# {class_info['name']}

## 源码位置
`{class_info['source_file']}`

## 说明
{class_info['docstring'] or '无文档说明'}

## 方法列表
"""
        for method in class_info['methods']:
            md += f"- `{method['name']}()`\n"

        # Java 对照
        md += f"""
## Java 对照

```java
// 相当于 Java 的:
public class {class_info['name']} {{
"""
        for method in class_info['methods']:
            md += f"    public void {method['name']}() {{}}\n"
        md += "}\n```\n"

        return md

    def run(self):
        """运行生成器"""
        # 扫描关键模块
        modules = {
            "message": ["Msg", "TextBlock", "ToolUseBlock"],
            "agent": ["AgentBase", "ReActAgent", "UserAgent"],
            "pipeline": ["MsgHub", "SequentialPipeline", "FanoutPipeline"],
            "model": ["ModelBase", "ChatModel"],
        }

        for module, classes in modules.items():
            module_path = self.source_root / module
            if not module_path.exists():
                continue

            for class_name in classes:
                # 查找类文件
                for py_file in module_path.rglob("*.py"):
                    if class_name in py_file.stem or class_name in py_file.read_text():
                        class_info = self.parse_class(py_file, class_name)
                        if class_info:
                            print(f"生成: {class_name}")
                            # 可以输出到文件或打印
                            print(self.generate_markdown(class_info))
                        break


if __name__ == "__main__":
    generator = SourceDocGenerator("/Users/nadav/IdeaProjects/agentscope/src/agentscope")
    generator.run()
