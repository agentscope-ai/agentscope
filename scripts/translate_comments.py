#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻译 Python 文件中的英文注释和 docstring 为中文。
中文翻译插入到原文上方，用 # 注释形式。

用法:
    python scripts/translate_comments.py <file1.py> [file2.py ...]
    python scripts/translate_comments.py --dry-run <file.py>

环境变量:
    DEEPSEEK_API_KEY: DeepSeek API 密钥（必需）
"""
import os
import re
import sys
import ast
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("请先安装 openai: pip install openai")
    sys.exit(1)


def get_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("错误: 请设置 DEEPSEEK_API_KEY 环境变量")
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def extract_comments(lines: list[str]) -> list[dict]:
    """提取所有 # 注释块（连续行）"""
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # 跳过空行、shebang、编码声明
        if not stripped or stripped.startswith("#!") or re.match(r"^#.*coding[:=]", stripped):
            i += 1
            continue
        if stripped.startswith("#"):
            start = i
            comment_lines = []
            while i < len(lines) and lines[i].strip().startswith("#"):
                comment_lines.append(lines[i])
                i += 1
            text = "\n".join(comment_lines)
            # 跳过纯分隔线
            if all(l.strip() in ("#", "# ") for l in comment_lines):
                continue
            # 跳过已含大量中文
            chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
            total = len(text.replace(" ", "").replace("\n", ""))
            if total > 0 and chinese / total > 0.3:
                continue
            blocks.append({
                "type": "comment",
                "start": start,
                "end": i - 1,
                "text": text,
            })
            continue
        i += 1
    return blocks


def extract_docstrings(lines: list[str]) -> list[dict]:
    """用 AST 提取 docstring"""
    source = "\n".join(lines)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    blocks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    start = first.lineno - 1
                    end = (first.end_lineno - 1) if hasattr(first, 'end_lineno') and first.end_lineno else start
                    text = "\n".join(lines[start:end + 1])
                    # 跳过已含中文
                    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
                    total = len(text.replace(" ", "").replace("\n", ""))
                    if total > 0 and chinese / total > 0.3:
                        continue
                    blocks.append({
                        "type": "docstring",
                        "start": start,
                        "end": end,
                        "text": text,
                    })
    return blocks


def translate(client: OpenAI, blocks: list[dict]) -> list[str]:
    """批量翻译，返回翻译结果列表"""
    if not blocks:
        return []

    # 构造输入
    parts = []
    for i, b in enumerate(blocks):
        parts.append(f"=== BLOCK {i} ===\n{b['text']}")
    input_text = "\n\n".join(parts)

    prompt = f"""你是一个 Python 代码注释翻译助手。
下面的文本包含多个注释块，每个块用 === BLOCK N === 标记。
请将每个块翻译成中文，要求：
- 保持原有的注释格式（# 前缀、缩进）
- 准确传达技术含义
- 只输出翻译结果，不要额外解释
- 每个翻译块仍以 === BLOCK N === 标记开头

{input_text}"""

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=4096,
            )
            result = resp.choices[0].message.content
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  API 失败 ({e})，重试...")
            time.sleep(2 ** attempt)

    # 解析结果
    translations = []
    current = []
    for line in result.split("\n"):
        if re.match(r"^=== BLOCK \d+ ===", line):
            if current:
                translations.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        translations.append("\n".join(current).strip())

    # 补齐
    while len(translations) < len(blocks):
        translations.append("")
    return translations[:len(blocks)]


def replace_with_translations(lines: list[str], blocks: list[dict], translations: list[str]) -> list[str]:
    """直接替换原文为中文翻译"""
    result = list(lines)
    # 从后往前替换，避免行号偏移
    for block, trans in reversed(list(zip(blocks, translations))):
        if not trans.strip():
            continue

        if block["type"] == "comment":
            # 获取缩进
            indent = re.match(r"^(\s*)", result[block["start"]]).group(1)
            # 构造中文注释行
            zh_lines = []
            for tl in trans.split("\n"):
                tl = tl.strip()
                if not tl:
                    continue
                # 去掉翻译结果中可能带的 # 前缀，统一添加
                if tl.startswith("#"):
                    tl = tl[1:].strip()
                zh_lines.append(f"{indent}# {tl}")
            # 替换原文行
            result[block["start"]:block["end"]+1] = zh_lines

        elif block["type"] == "docstring":
            # docstring: 直接替换为中文
            indent = re.match(r"^(\s*)", result[block["start"]]).group(1)
            first_line = result[block["start"]]
            quote_match = re.search(r'("""|\'\'\')', first_line)
            if not quote_match:
                continue
            quote = quote_match.group(1)

            # 单行 docstring
            if block["start"] == block["end"]:
                trans_text = trans.strip().replace("\n", " ")
                # 去掉翻译中可能带的引号
                if trans_text.startswith('"""') and trans_text.endswith('"""'):
                    trans_text = trans_text[3:-3]
                elif trans_text.startswith("'''") and trans_text.endswith("'''"):
                    trans_text = trans_text[3:-3]
                # 直接替换
                result[block["start"]] = f'{indent}{quote}{trans_text}{quote}'

            else:
                # 多行 docstring: 直接替换
                trans_lines = [tl.strip() for tl in trans.split("\n") if tl.strip()]
                # 去掉翻译中可能带的引号
                if trans_lines and trans_lines[0].startswith('"""'):
                    trans_lines[0] = trans_lines[0][3:]
                if trans_lines and trans_lines[-1].endswith('"""'):
                    trans_lines[-1] = trans_lines[-1][:-3]
                # 构造中文 docstring
                zh_lines = [f'{indent}{quote}{trans_lines[0]}']
                for tl in trans_lines[1:]:
                    zh_lines.append(f'{indent}{tl}')
                zh_lines.append(f'{indent}{quote}')
                # 替换原文
                result[block["start"]:block["end"]+1] = zh_lines

    return result


def process_file(filepath: str, client: OpenAI, dry_run: bool = False):
    """处理单个文件"""
    print(f"\n处理: {filepath}")
    path = Path(filepath)
    if not path.exists():
        print(f"  文件不存在")
        return

    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # 提取注释和 docstring
    comment_blocks = extract_comments(lines)
    docstring_blocks = extract_docstrings(lines)
    all_blocks = sorted(comment_blocks + docstring_blocks, key=lambda b: b["start"])

    if not all_blocks:
        print("  未找到注释")
        return

    print(f"  找到 {len(all_blocks)} 个注释块")

    # 翻译
    translations = translate(client, all_blocks)

    if dry_run:
        print("  [DRY RUN] 预览:")
        for b, t in zip(all_blocks, translations):
            print(f"\n  --- 原文 (行 {b['start']+1}-{b['end']+1}) ---")
            print(f"  {b['text'][:150]}")
            print(f"  --- 翻译 ---")
            print(f"  {t[:150]}")
        return

    # 替换并写入
    new_lines = replace_with_translations(lines, all_blocks, translations)
    path.write_text("\n".join(new_lines), encoding="utf-8")
    print(f"  完成")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="翻译 Python 注释为中文")
    parser.add_argument("files", nargs="+", help="Python 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不修改文件")
    args = parser.parse_args()

    client = get_client()
    for f in args.files:
        try:
            process_file(f, client, dry_run=args.dry_run)
        except Exception as e:
            print(f"  失败: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
