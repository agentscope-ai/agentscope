#!/usr/bin/env bash
# verify_references.sh — 验证书中的源码引用是否存在
# 用法: ./verify_references.sh [markdown_file_or_directory]
# 从 Markdown 文件中提取源码引用并验证文件路径和类名/方法名

set -euo pipefail

SRC_ROOT="src/agentscope"
PASS=0
FAIL=0
WARN=0
ERRORS=()

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_pass() { PASS=$((PASS + 1)); }
log_fail() { FAIL=$((FAIL + 1)); ERRORS+=("$1"); }
log_warn() { WARN=$((WARN + 1)); }

# 收集所有 markdown 文件
if [ $# -eq 0 ]; then
    FILES=$(find teaching/book -name "*.md" -not -path "*/scripts/*" | sort)
else
    FILES="$*"
fi

if [ -z "$FILES" ]; then
    echo "没有找到 Markdown 文件"
    exit 1
fi

echo "=== 源码引用验证 ==="
echo "源码根目录: $SRC_ROOT"
echo ""

for file in $FILES; do
    [ ! -f "$file" ] && continue
    echo "检查: $file"

    # 1. 验证文件路径引用 (src/agentscope/...py)
    while IFS= read -r ref_path; do
        if [ -f "$ref_path" ]; then
            log_pass
        else
            log_fail "文件不存在: $ref_path (引用自 $file)"
            echo -e "  ${RED}FAIL${NC} 文件不存在: $ref_path"
        fi
    done < <(grep -oP 'src/agentscope/[a-zA-Z0-9_/.]+\.py' "$file" 2>/dev/null | sort -u)

    # 2. 验证 ClassName.method_name 引用
    while IFS= read -r ref; do
        class_name="${ref%%.*}"
        # 在源码中搜索类名或方法名
        if grep -rq "class ${class_name}" "$SRC_ROOT" 2>/dev/null; then
            log_pass
        else
            log_fail "类不存在: ${class_name} (引用自 $file)"
            echo -e "  ${RED}FAIL${NC} 类不存在: ${class_name}"
        fi
    done < <(grep -oP '`[A-Z][a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)+`' "$file" 2>/dev/null | tr -d '`' | sort -u)

    # 3. 检查硬编码行号（应不存在）
    line_refs=$(grep -nP '第 \d+ 行|line \d+|:L\d+' "$file" 2>/dev/null || true)
    if [ -n "$line_refs" ]; then
        log_warn
        echo -e "  ${YELLOW}WARN${NC} 发现硬编码行号引用:"
        echo "$line_refs" | head -3
    fi
done

echo ""
echo "=== 验证结果 ==="
echo -e "通过: ${GREEN}${PASS}${NC}"
echo -e "失败: ${RED}${FAIL}${NC}"
echo -e "警告: ${YELLOW}${WARN}${NC}"

if [ ${#ERRORS[@]} -gt 0 ]; then
    echo ""
    echo "失败详情:"
    for err in "${ERRORS[@]}"; do
        echo -e "  ${RED}- ${err}${NC}"
    done
    exit 1
fi

exit 0
