# Python 语法教程 - Java 开发者指南

本教程面向有 Java 背景的开发者，通过 AgentScope 源码讲解 Python 语法。

## 🚀 快速开始

### 1. 生成个性化学习路径

```bash
python3 learning_path_generator.py
```

输出示例：
```
📚 个性化学习路径 - 目标: full_stack
总时长: 3.9 小时 | 预计 2 天

1. Python 类与对象 (30分钟)
2. Python 异步编程 (45分钟)
3. AgentScope 项目概述 (10分钟)
4. 核心概念 (30分钟)
5. Agent 模块深度 (45分钟)
...
```

### 2. 自动校验教案

```bash
python3 doc_checker.py
```

自动检测：
- ✅ 文档与源码一致性
- ✅ 术语使用统一性
- ✅ 行号引用准确性

### 3. 从源码生成文档

```bash
python3 auto_teaching_builder.py
```

自动从源码提取类、方法、属性，生成带 Java 对照的文档。

## 📚 学习路径

| 章节 | 内容 | Java 对照 | 时长 |
|------|------|-----------|------|
| [01 - 类与对象](01_class_object.md) | 定义、构造器、属性、@property | class vs Java class | 30分钟 |
| [02 - 异步编程](02_async_await.md) | async/await、协程、EventLoop | CompletableFuture | 45分钟 |
| [03 - 装饰器](03_decorator.md) | @装饰器、闭包、@wraps | AOP/拦截器 | 30分钟 |
| [04 - 类型提示](04_type_hints.md) | 类型注解、泛型、Protocol | Java Generics | 35分钟 |
| [05 - 数据类](05_dataclass.md) | @dataclass、field、defaults | Lombok @Data | 25分钟 |
| [06 - 上下文管理器](06_context_manager.md) | with、__enter__、async with | try-with-resources | 30分钟 |
| [07 - 继承与多态](07_inheritance.md) | 继承、方法覆盖、super() | extends/implements | 35分钟 |
| [08 - 元类](08_metaclass.md) | type()、__new__、metaclass | 注解处理器 | 40分钟 |

## 🛠️ 工具

| 工具 | 文件 | 功能 |
|------|------|------|
| 学习路径生成 | `learning_path_generator.py` | 根据背景生成个性化路径 |
| 文档校验器 | `doc_checker.py` | 自动检测文档问题 |
| 源码文档生成 | `auto_teaching_builder.py` | 从源码自动生成文档 |

## 📖 AgentScope 源码文件对照

| Python 语法 | AgentScope 源码示例 | 章节 |
|-------------|---------------------|------|
| 类定义 | `_message_base.py:Msg` | 01, 07 |
| async/await | `_agent_base.py:observe()` | 02 |
| 装饰器 | `hooks/__init__.py` | 03 |
| 类型提示 | `_agent_meta.py` | 04 |
| @dataclass | `rag/_document.py` | 05 |
| 上下文管理器 | `pipeline/_msghub.py` | 06 |
| 继承与多态 | `_agent_base.py:AgentBase` | 07 |
| 元类 | `_agent_meta.py` | 08 |

## 🎯 学习建议

1. **先跑工具**：运行 `learning_path_generator.py` 生成你的专属路径
2. **对照学习**：每章都有 Java 对照，加深理解
3. **实践结合**：阅读 AgentScope 真实源码

## 🔄 自动化工作流

```
┌─────────────────┐
│ learning_path   │ → 个性化学习路径
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ doc_checker     │ → 自动发现问题
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ auto_teaching   │ → 从源码生成文档
└─────────────────┘
```

---

*教程基于 AgentScope v1.0.19 源码*
