# 审稿报告

## 1. Runtime模块审稿

### 准确性问题

1. **第209行**：`sequential_pipeline` 函数的 return 语句在第44行，但文档说"第 42-43 行: 核心逻辑"，未包含 return 语句。

2. **第278-280行**：fanout_pipeline 的行号引用偏差
   - "第 96-98 行"创建任务：`tasks = [` 在第97行，`asyncio.create_task` 在第98行
   - "第 100-102 行"顺序模式：else 分支实际在第103-104行
   - "第 52 行: 消息通过 deepcopy 拷贝"：第52行是 `**kwargs: Any,`，deepcopy 实际在第98行

3. **第337-340行**：`stream_printing_messages` 源码分析的行号整体偏高约10行
   - 实际 queue 初始化在第159-163行（文档说160-163）
   - 实际 task 创建在第165-171行（文档说166-171）
   - 实际 while 循环在第173-187行（文档说174-187）
   - 实际异常检查在第189-192行（文档说190-192）

4. **第66行**：描述源码位置为"第 43-90 行"，但文件中 SequentialPipeline 到第40行结束，FanoutPipeline 从第43行开始到第90行结束，需明确说明是两个类的范围。

### 清晰度问题

1. 第58-59行说明"SequentialPipeline 和 FanoutPipeline 并不继承 AgentBase，而是组合持有 AgentBase 实例"，但继承体系图中用虚线表示可能引起误解，建议明确标注"组合关系"。

2. 第208-210行对 `fanout_pipeline` 的分析缺少对 `**kwargs` 参数作用的说明。

### 代码示例问题

1. **第436-441行**：`MathAgent` 使用 `eval(f"{msg.content} {self.operation}")` 存在安全隐患，建议添加输入验证或说明仅用于教学演示。

## 2. Dispatcher模块审稿

### 准确性问题

1. **第23行**（MsgHub docstring）：示例中写的是 `with MsgHub(participant=[agent1, agent2, agent3])`，但构造函数参数名是 `participants`（复数），不是 `participant`（单数）。这是一个错误。

2. **第238行**：文档说"第 130-138 行"是 broadcast 方法，broadcast 方法实际从第130行到第138行，但广播逻辑中调用 `await agent.observe(msg)` 在第138行，引用基本正确。

3. **第186-188行**：订阅者管理机制的说明较为简略，缺少对订阅者数据结构的具体说明。

### 清晰度问题

1. 第65-66行说明"MsgHub 不继承 AgentBase"，但没有明确说明 MsgHub 是独立类，不继承任何基类。

2. 缺少对订阅者字典数据结构的说明（key 是 MsgHub 名称，value 是订阅者列表）。

### 代码示例问题

1. **第23行**（docstring示例）：`participant` 应改为 `participants`。

2. **第427-439行**"等价手动实现对比"示例中：
   ```python
   x1 = await agent1()
   ```
   缺少 Msg 类型的输入参数，应补充为 `x1 = await agent1(Msg("user", "Hello", "user"))`。

3. **第359-360行**：`SimpleAgent` 的 `__call__` 方法缺少 `async` 关键字。

---

## 修改建议汇总

| 文件 | 位置 | 问题 | 建议 |
|------|------|------|------|
| module_runtime_deep.md | 第280行 | deepcopy 引用行号错误 | 改为"第98行" |
| module_runtime_deep.md | 第337-340行 | 行号整体偏高约10行 | 逐行校准 |
| module_runtime_deep.md | 第278-279行 | fanout_pipeline 行号偏差 | 校准为97-99行和103-104行 |
| module_dispatcher_deep.md | 第23行 | `participant` 应为 `participants` | 修正参数名 |
| module_dispatcher_deep.md | 第427-439行 | agent示例缺少输入参数 | 补充 Msg 参数 |
| module_dispatcher_deep.md | 第359行 | __call__缺少async | 添加 async 关键字 |
