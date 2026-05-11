# 第 35 章 为什么 Formatter 独立于 Model

> 本章讨论：关注点分离 vs 简单性——为什么把格式化和模型调用分开。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 35.1 合并方案

```python
class OpenAIChatModel:
    async def __call__(self, msgs):
        # 格式化 + 调用 合在一起
        formatted = self._format(msgs)
        response = await self._call_api(formatted)
        return response
```

一个类做两件事：格式化 + API 调用。简单直接。

---

## 35.2 分离方案（AgentScope 的选择）

```python
# Formatter 负责格式化
formatted = await formatter.format(msgs)

# Model 负责调用
response = await model(formatted)
```

### 为什么分离？

1. **复用**：多个 Model 可能共享同一种格式（OpenAI 兼容 API 很普遍）
2. **独立测试**：格式化逻辑可以单独测试，不需要调用真实 API
3. **独立配置**：Token 截断策略可以独立于 Model 调整
4. **策略组合**：可以混合搭配不同的 Formatter 和 Model

### 代价

- **API 复杂**：用户需要创建两个对象而不是一个
- **概念多**：需要理解 Formatter 和 Model 的职责划分

### 权衡

AgentScope 选择分离，因为：
- 支持多种 API 格式是核心需求（OpenAI、Anthropic、Gemini）
- Token 截断是一个独立关注点（需要 TokenCounter 配合）
- 测试需要在不调用 API 的情况下验证格式化

如果只支持一种 API，合并方案更简单。但 AgentScope 需要支持多种，分离是必要的。

---

## 35.3 检查点

你现在已经理解了：

- **分离的好处**：复用、独立测试、独立配置、灵活组合
- **分离的代价**：API 复杂、概念多
- **为什么 AgentScope 选择分离**：多 API 支持是核心需求

---

## 下一章预告
