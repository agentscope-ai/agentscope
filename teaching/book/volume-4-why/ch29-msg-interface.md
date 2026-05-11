# 第 29 章 消息为什么是唯一接口

> **卷四：为什么要这样设计？** 从本章开始，讨论设计决策的前因后果。
> 本章讨论：为什么 Agent、Model、Tool 全部通过 `Msg` 通信。

---

## 29.1 问题

AgentScope 中所有模块都通过 `Msg` 传递数据：

- 用户 → Agent：`Msg`
- Agent → Memory：`Msg`
- Agent → Formatter：`list[Msg]`
- Formatter → Model：`list[dict]`（但从 `Msg` 转换来的）
- Model → Agent：`ChatResponse.message`（包含 `Msg`）
- Tool → Agent：`ToolResultBlock`（嵌入在 `Msg.content` 中）

为什么不直接用字符串？或者用多种不同的消息类型？

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 29.2 设计决策

### 统一接口的好处

1. **模块解耦**：Agent 不需要知道 Model 返回什么格式，只需要处理 `Msg`
2. **可组合**：不同模块可以自由组合，因为它们说"同一种语言"
3. **可测试**：只需要构造 `Msg` 就能测试任何模块

### `Msg` 足够通用

`Msg.content` 有两种形态（字符串或 ContentBlock 列表），覆盖了所有内容类型：

- 纯文本 → `str`
- 工具调用 → `ToolUseBlock`
- 工具结果 → `ToolResultBlock`
- 图片/音频/视频 → `ImageBlock`/`AudioBlock`/`VideoBlock`
- 思考过程 → `ThinkingBlock`

一个类型就够了。

### 代价

- **类型不安全**：`Msg.content` 是 `str | list[ContentBlock]`，调用方需要检查类型
- **隐式约定**：什么时候用字符串、什么时候用 ContentBlock 列表，靠约定而非强制

---

## 29.3 对比：如果用多种消息类型

```python
# 假设用不同类型
TextMessage(content: str)
ToolCallMessage(tool_name: str, args: dict)
ToolResultMessage(tool_id: str, result: str)
ImageMessage(image_url: str)
```

问题：Agent 的 `reply()` 返回值需要是 `TextMessage | ToolCallMessage | ...`，Memory 需要存储所有类型，Formatter 需要处理所有类型……每个模块都要处理类型分发。统一的 `Msg` 通过 `content` 字段内部的 `ContentBlock` 类型来区分，外部接口保持简单。

---

## 29.4 检查点

你现在已经理解了：

- **统一接口**：`Msg` 是所有模块的通信协议
- **好处**：解耦、可组合、可测试
- **代价**：类型不安全、隐式约定
- **为什么 `Msg` 够用**：`content` 的双形态 + 7 种 ContentBlock 覆盖所有情况

---

## 下一章预告
