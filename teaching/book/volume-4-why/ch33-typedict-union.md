# 第 33 章 TypedDict 与 Union

> 本章讨论：为什么 `ContentBlock` 用 `TypedDict` 而不是 `dataclass`，为什么用 Union 而不是继承。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 33.1 TypedDict vs dataclass

### TypedDict 的选择

```python
class TextBlock(TypedDict, total=False):
    type: Required[Literal["text"]]
    text: str
```

### 为什么不用 dataclass？

```python
@dataclass
class TextBlock:
    type: Literal["text"]
    text: str
```

原因：**ContentBlock 需要直接与 JSON 互转**。

- `TypedDict` 本质上是 `dict`，和 JSON 天然兼容，不需要 `to_dict()` / `from_dict()`
- `dataclass` 需要额外转换（`dataclasses.asdict()` 或自定义序列化）
- 模型 API 返回的是 JSON，ContentBlock 经常直接从 API 响应构建

这是**数据优先**（TypedDict）vs **行为优先**（dataclass）的选择。ContentBlock 是纯数据，没有方法，所以 TypedDict 更合适。

---

## 33.2 Union vs 继承

### Union 的选择

```python
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ...
```

### 为什么不用继承？

```python
class ContentBlockBase:
    type: str

class TextBlock(ContentBlockBase):
    text: str
```

原因：**TypedDict 不能继承非 TypedDict 类**。而且 Union 模式更接近 JSON 的本质——不同类型的数据通过 `type` 字段区分，而不是通过类型系统区分。

---

## 33.3 检查点

你现在已经理解了：

- **TypedDict**：数据优先，天然 JSON 兼容
- **Union**：通过 `type` 字段区分，而非类型继承
- **权衡**：数据传输的便利性 vs OOP 的行为封装

---

## 下一章预告
