# 第一部分小结：Python基础

本部分为Java开发者提供了学习AgentScope所需的Python核心知识。

---

## 📚 学到的内容

### 第1章：Python面向对象
- `self` vs `this`：Python显式传递self
- 访问控制：用命名约定（`_`、`__`）而非关键字
- dataclass：自动生成样板代码
- 抽象类：`@abstractmethod`装饰器

### 第2章：异步编程
- `async/await`：Python协程语法
- 事件循环：协程的调度器
- `await`让出控制权：非阻塞I/O的基础
- 并发 vs 并行：协程用于I/O密集型

### 第3章：高级语法
- 装饰器：修改函数行为的"包装器"
- 上下文管理器：`with`语句自动资源清理
- 元类：控制类的创建，用于框架注册

---

## 🔗 与后续内容的联系

这些Python知识是理解AgentScope的基础：

| Python概念 | AgentScope应用 |
|-----------|---------------|
| async/await | Agent.reply(), MsgHub.broadcast() |
| 上下文管理器 | `async with MsgHub(...)` |
| 装饰器 | `@abstractmethod`定义抽象方法 |
| dataclass | Msg类的简化定义 |

---

## 📝 练习建议

1. **编写一个异步Agent**：使用`async def`定义Agent方法
2. **实现上下文管理器**：创建一个管理资源的类
3. **编写装饰器**：为函数添加日志或计时功能

---

## ➡️ 下一站

准备好进入**[第二部分：Agent开发基础](./part_ii_getting_started/)**？

在接下来的三章中，我们将学习：
- 消息传递机制（Msg）
- 管道编排（Pipeline）
- 发布订阅（MsgHub）

这些是理解Agent如何通信的基础。
