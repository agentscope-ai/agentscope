# 第 28 章 集成实战

> 本章你将：完成一个端到端的 PR 演练，从发现需求到提交代码。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 28.1 PR 流程

```
1. Fork 仓库 → 2. 创建分支 → 3. 编写代码 → 4. 运行测试 → 5. 提交 PR
```

### Step 1: Fork 和分支

```bash
# Fork 后 clone 你的仓库
git clone https://github.com/你的用户名/agentscope.git
cd agentscope

# 创建功能分支
git checkout -b feature/my-new-tool
```

### Step 2: 编写代码

- 工具函数放在 `src/agentscope/tool/` 下
- 或者作为独立函数让用户注册
- 添加类型注解和文档字符串

### Step 3: 编写测试

```python
# tests/test_my_tool.py
import pytest
from agentscope.tool import Toolkit

def test_my_tool_registration():
    toolkit = Toolkit()
    toolkit.register_tool_function(my_tool)
    assert "my_tool" in toolkit.tools

@pytest.mark.asyncio
async def test_my_tool_execution():
    toolkit = Toolkit()
    toolkit.register_tool_function(my_tool)
    result = await toolkit.call_tool_function("my_tool", {"arg1": "value"})
    assert result is not None
```

### Step 4: 运行检查

```bash
# 运行测试
pytest tests/test_my_tool.py -v

# 运行代码质量检查
pre-commit run --all-files

# 类型检查
mypy src/agentscope/tool/_my_tool.py
```

### Step 5: 提交 PR

```bash
git add src/agentscope/tool/_my_tool.py tests/test_my_tool.py
git commit -m "feat: add my_new_tool for ..."
git push origin feature/my-new-tool
```

然后在 GitHub 上创建 Pull Request。

---

## 28.2 PR 最佳实践

1. **小而聚焦**：一个 PR 做一件事
2. **写好描述**：说明做了什么、为什么这么做、怎么测试
3. **添加测试**：新功能必须有测试
4. **通过 CI**：所有检查必须通过
5. **代码审查**：认真对待 review 意见

---

## 28.3 检查点

你现在掌握了完整的贡献流程：

- Fork → 分支 → 编码 → 测试 → 检查 → PR
- 代码质量标准（pre-commit、mypy）
- PR 最佳实践

---

卷三结束。你已经能造新齿轮了。卷四将讨论为什么要这样设计。
