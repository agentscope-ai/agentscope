# 第 24 章 造一个新 Memory

> 本章你将：继承 `MemoryBase`，创建自定义存储后端。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 24.1 MemoryBase 接口

```python
from agentscope.memory import MemoryBase

class MyMemory(MemoryBase):
    async def add(self, messages, rank=None, mark=None):
        ...

    async def get_memory(self, recent_n=None, mark=None):
        ...

    async def delete(self, index):
        ...

    async def delete_by_mark(self, mark):
        ...

    async def update_messages_mark(self, mark, new_mark=None, index=None):
        ...

    async def clear(self):
        ...
```

---

## 24.2 实现示例：文件存储

```python
import json
from pathlib import Path
from agentscope.memory import MemoryBase
from agentscope.message import Msg

class FileMemory(MemoryBase):
    def __init__(self, path: str = "memory.json"):
        self.path = Path(path)
        self._storage = []
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self._storage = [Msg.from_dict(d) for d in data]

    async def add(self, messages, rank=None, mark=None):
        if isinstance(messages, Msg):
            messages = [messages]
        self._storage.extend(messages)
        self._save()

    async def get_memory(self, recent_n=None, mark=None):
        result = self._storage
        if recent_n:
            result = result[-recent_n:]
        return result

    async def delete(self, index):
        if isinstance(index, int):
            self._storage.pop(index)
        self._save()

    async def clear(self):
        self._storage = []
        self._save()

    def _save(self):
        self.path.write_text(json.dumps([m.to_dict() for m in self._storage]))
```

---

## 24.3 试一试

1. 用 `FileMemory` 替换 `InMemoryMemory`
2. 重启程序后验证记忆是否保留
3. 添加 mark 过滤支持

---

## 24.4 检查点

你现在已经能：

- 继承 `MemoryBase` 创建自定义存储
- 实现所有必要的方法
- 让 Agent 无缝切换存储后端

---

## 下一章预告
