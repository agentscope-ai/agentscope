# SOP：src/agentscope/pipeline 模块

## 一、功能定义（Scope）
- 层次：编排层；将多个 Agent 通过顺序或扇出方式组合执行；集中处理流式打印。
- 作用：
  - 顺序：上一个 Agent 的输出作为下一个的输入；
  - 扇出：同一输入发给多个 Agent，并可选择并发收集；
  - 聚合：将多个 Agent 的 `print()` 流式块统一导出给上层 UI/日志。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/pipeline/_class.py
- 类：`SequentialPipeline`
  - 方法：`__call__(msg)` → 调用 `sequential_pipeline`。
- 类：`FanoutPipeline`
  - 成员：`enable_gather: bool` 控制并发；
  - 方法：`__call__(msg, **kwargs)` → 调用 `fanout_pipeline`。

### 文件：src/agentscope/pipeline/_functional.py
- 函数：`async sequential_pipeline(agents, msg)`
  - 逻辑：依次 `await agent(msg)`；返回最终 `Msg|list[Msg]|None`。
  - 取消/异常：若中途抛错即上抛；由调用方决定重试或中断。
- 函数：`async fanout_pipeline(agents, msg, enable_gather=True, **kwargs)`
  - 逻辑：
    - `enable_gather=True`：以 `asyncio.create_task` 并发执行，`gather` 收集；
    - 否则：顺序 `await`；
    - 为避免共享引用，均对输入 `msg` 做 `deepcopy`。
  - 取消/异常：并发模式下，单个任务异常会传播；必要时由调用方包装容错。
- 函数：`async stream_printing_messages(agents, coroutine_task, end_signal="[END]")`
  - 逻辑：
    1) 为每个 Agent 开启共享 `asyncio.Queue` 作为打印收集通道；
    2) `create_task(coroutine_task)`，在完成时向队列写入 `end_signal`；
    3) 循环 `queue.get()`，遇到 `end_signal` 结束；否则 `yield (msg, is_last_chunk)`；
  - 交互：依赖 Agent 调用 `set_msg_queue_enabled(True, queue)` 将 `print()` 的块写入队列。

## 三、与其他组件的交互关系
- Agent：作为可调用单元接入；`print()` 的块经消息队列被本模块聚合导出。
- 上层 UI/日志：可消费 `stream_printing_messages` 的生成器进行实时呈现。
 - MsgHub：可配合 `pipeline/_msghub.py` 在上下文中自动广播各 Agent 回复为其他 Agent 的 `observe()` 输入。

## 四、Docs‑First 变更流程与验收
1) 在本 SOP 增补变更点与执行逻辑；
2) 更新 `CLAUDE.md` 中的“Pipeline”调用链；
3) 在根 `todo.md` 写入：顺序/扇出/聚合三条路径的最小用例与异常/取消校验项；
4) 获批后修改与合入。

验收 Checklist（最小集）
- [ ] 顺序管线：多 Agent 串行数据传递正确，异常能上抛
- [ ] 扇出管线：并发与串行模式结果一致；异常传播可预期
- [ ] 打印聚合：能持续产出 `(Msg, is_last_chunk)`，遇到 `end_signal` 正常结束
- [ ] 可与 MsgHub 协同，自动广播不干扰管线语义
- [ ] `ruff check src`（仅检测）无待处理告警；`mypy/pytest` 通过
