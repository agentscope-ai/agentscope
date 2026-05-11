# 第 20 章 可观测性

> 本章你将理解：OpenTelemetry 追踪、日志系统、如何在 Agent 运行时观察内部状态。

---

## 20.1 为什么需要可观测性？

Agent 的执行是异步的、多步的、涉及外部 API 调用的。出了问题，你很难只靠 print 定位。可观测性提供三个工具：

| 工具 | 回答的问题 |
|------|-----------|
| 日志（Logging） | "发生了什么？" |
| 追踪（Tracing） | "每一步花了多长时间？" |
| 指标（Metrics） | "整体表现怎么样？" |

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 20.2 源码入口

| 文件 | 内容 |
|------|------|
| `src/agentscope/tracing/` | OpenTelemetry 追踪 |
| `src/agentscope/_logging.py` | 日志系统 |
| `src/agentscope/formatter/_truncated_formatter_base.py` | `@trace_format` 装饰器 |

---

## 20.3 日志系统

```python
# 开启 DEBUG 日志
agentscope.init(logging_level="DEBUG")

# 使用 logger
from agentscope import logger
logger.info("这条信息会被记录")
```

### 日志级别

```
DEBUG → INFO → WARNING → ERROR
```

- `DEBUG`：详细的内部状态，调试时使用
- `INFO`：关键操作（模型调用、工具执行），正常运行时使用
- `WARNING`：可恢复的问题
- `ERROR`：需要关注的错误

---

## 20.4 OpenTelemetry 追踪

AgentScope 集成了 OpenTelemetry，可以追踪每次操作的耗时和结果：

```python
agentscope.init(tracing_url="http://localhost:6006/v1/traces")
```

追踪会记录：

- 每次 `model()` 调用的耗时和 token 使用
- 每次工具执行的耗时和结果
- 每次 `format()` 的输入输出大小
- 整个 ReAct 循环的轮数和总耗时

### `@trace_format` 装饰器

在 Formatter 中，`format()` 方法用 `@trace_format` 装饰器自动记录追踪信息：

```python
@trace_format
async def format(self, msgs, **kwargs):
    ...
```

---

## 20.5 试一试

### 查看 DEBUG 日志

```python
import agentscope
agentscope.init(logging_level="DEBUG", logging_path="debug.log")

# 运行你的 Agent...
# 日志会同时输出到控制台和 debug.log 文件
```

---

## 20.6 检查点

你现在已经理解了：

- **可观测性三支柱**：日志、追踪、指标
- **日志系统**：`setup_logger()` 配置级别和输出
- **OpenTelemetry 追踪**：`tracing_url` 接入第三方平台
- **装饰器追踪**：`@trace_format` 自动记录操作信息

---

卷二结束。你已经拆开了所有齿轮。卷三将手把手教你造新的齿轮。
