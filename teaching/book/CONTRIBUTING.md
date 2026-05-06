# 写作风格指南

> 本书借鉴《网络是怎样连接的》的写作风格，所有作者请务必遵循本指南。

---

## 一、核心写作原则

### 1. 追踪式学习优先

**每个章节都要回答一个问题**：数据在这一步是怎么流动的？

```
❌ 不要这样写                    ✓ 要这样写
"Msg是消息对象"          →  "Msg是Agent之间传递的消息，
包含name/content/role"          当用户发消息时，Msg是这样创建的..."
```

### 2. 图解优先于文字

每章至少包含1张追踪图，用ASCII或Mermaid格式：

```mermaid
sequenceDiagram
    用户->>Agent: 输入"你好"
    Agent->>Model: 调用模型
    Model-->>Agent: 返回回复
    Agent-->>用户: 显示回复
```

### 3. Java对照贯穿始终

每章至少包含1处Java对照，帮助Java开发者建立概念映射：

```markdown
**💡 Java开发者注意**
- Python的`self`就是Java的`this`
- 但Python必须显式声明self，Java的this是隐式的
```

---

## 二、章节结构模板

```markdown
## X-Y 章节标题

### 🎯 这一章的目标
学完之后，你能XXX

### 🚀 先跑起来
[完整可运行的代码，showLineNumbers]

### 🔍 追踪数据流动
[追踪图或时序图]

### 📖 深入理解
[核心概念解释]

### 💡 Java开发者注意
[Java对照和注意事项]

### 🎯 思考题
1. ...
2. ...
3. ...

<details>
<summary>思考题答案</summary>

1. ...
2. ...

</details>

★ **Insight** ─────────────────────────────────────
[关键洞察1]
[关键洞察2]
─────────────────────────────────────────────────
```

---

## 三、写作风格要求

### ✅ 要这样做

1. **用"你"而不是"读者"**
   - ❌ "读者需要理解..."
   - ✅ "你会理解..."

2. **先给结论，再解释原因**
   - ❌ "这是因为..."
   - ✅ "这是因为...（后面解释）"

3. **用"说人话"解释术语**
   - ❌ "ReAct是一种推理框架"
   - ✅ "ReAct = Reason + Act，就是'先想后做'"

4. **适当使用emoji增加亲和力**
   - 🎯 目标
   - 🔍 追踪
   - 💡 注意事项
   - 🚀 代码
   - ⚠️ 坑预警

5. **代码注释要友好**
   ```python
   # 这里是Java的xxx，Python这样写
   ```

### ❌ 不要这样做

1. 不要用"本节将讲解..."
2. 不要用"请注意..."（用"💡 Java开发者注意"代替）
3. 不要大段定义
4. 不要学术化的描述
5. 不要冷冰冰的"技术说明"

---

## 四、追踪图规范

### 消息追踪图

```
用户 → [Msg创建] → [Pipeline路由] → [Agent处理] → [回复Msg] → 用户
```

### 时序追踪图

```mermaid
sequenceDiagram
    actor User as 用户
    participant A as Agent
    participant M as Model
    participant T as Toolkit
    
    User->>A: "你好"
    A->>M: 调用模型
    M-->>A: 回复
    A->>T: 调用工具
    T-->>A: 工具结果
    A->>M: 再次调用
    M-->>A: 最终回复
    A-->>User: 显示回复
```

---

## 五、代码规范

### 1. 代码必须完整可运行

```python
# ❌ 错误 - 碎片代码
agent = agentscope.ReActAgent

# ✅ 正确 - 完整可运行
import agentscope

agentscope.init()
agent = agentscope.ReActAgent(
    name="my_agent",
    model=agentscope.OpenAIChatModel(api_key="..."),
    sys_prompt="你是一个有帮助的助手"
)
result = await agent("你好")
print(result)
```

### 2. 代码注释要标注行号

```python showLineNumbers
# 这是第1行
# 这是第2行
```

### 3. Java对照代码也要完整

```python
# Python
def greet(name: str) -> str:
    return f"Hello, {name}"

// Java
public String greet(String name) {
    return "Hello, " + name;
}
```

---

## 六、术语处理

### 首次出现用**粗体**

```markdown
**Msg** 是消息对象，**Agent** 是智能体...
```

### 术语"说人话"专栏

每章结尾用对话形式解释术语：

```markdown
### 📖 术语其实很简单

**Msg** = **M**essage
> "就是消息嘛！就像微信消息一样，有发送者、内容、接收者"

**Pipeline** = 管道
> "就像工厂的流水线，消息从一端进去，从另一端出来"
```

---

## 七、常见错误处理

### 用"坑预警"代替"请注意"

```markdown
⚠️ **坑预警**：Python的缩进很容易出错！
- 用4个空格，不要用Tab
- 混用空格和Tab会导致IndentationError
```

---

## 八、质量检查清单

提交章节前，请检查：

- [ ] 代码是否完整可运行？
- [ ] 是否有追踪图？
- [ ] 是否有Java对照？
- [ ] 术语是否首次出现用粗体？
- [ ] 是否有"说人话"的术语解释？
- [ ] 是否有思考题和答案？
- [ ] 是否遵循本指南的风格？
