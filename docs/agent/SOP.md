# SOP：src/agentscope/agent 模块

## 一、功能定义（Scope）
- 层次：智能体编排层，位于业务之下、模型/工具之上。
- 作用：管理推理→工具调用→汇总的主循环，支持并行工具调用、实时打断、钩子、打印与消息广播；集成短期/长期记忆、RAG、计划工具。
- 非目标：不承载具体业务策略与领域规则；不内置复杂 Prompt 策略。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/agent/_agent_base.py
- 类：`AgentBase`
  - 成员要点：
    - `supported_hook_types`/`_class_*_hooks` 与实例级 `_instance_*_hooks`：钩子注册/移除/清理接口；顺序为 `OrderedDict`，后注册不覆盖前序除非同名。
    - `_reply_task`/`interrupt()`：通过取消当前任务触发自定义 `handle_interrupt()`。
    - `print(msg, last=True)`：处理 text/thinking/audio/tool_result 等块；音频流使用 `sounddevice` 输出；最后块清理缓存前缀。
    - 订阅广播：`_subscribers` 基于 MsgHub 名称的订阅列表，`_broadcast_to_subscribers` 将回复分发给其他 Agent 的 `observe()`。
    - 消息队列：`set_msg_queue_enabled()` 将打印中的消息块写入共享队列供流水线聚合。
  - 关键方法：
    - `reply()`/`observe()`/`handle_interrupt()` 为抽象入口；由子类实现。

### 文件：src/agentscope/agent/_react_agent.py
- 类：`ReActAgent`
  - 主循环（执行逻辑）：
    1) `await self.memory.add(msg)` 记录输入；
    2) `_retrieve_from_long_term_memory(msg)` 与 `_retrieve_from_knowledge(msg)` 注入检索提示（可打印提示）；
    3) 若要求结构化输出：`toolkit.set_extended_model(finish_function_name, structured_model)`；
    4) 循环至 `max_iters`：
       - `_reasoning()`：由 Formatter 组装对话，调用 Model 获取思考、文本与工具调用块；
       - `_acting(tool_call)`：顺序或并行执行工具，优先返回第一个产生回复的 `ToolResponse.metadata.response_msg`；
    5) 若无回复：`_summarizing()` 生成最终答复；
    6) 长期记忆静态模式：在结束后以 `record([...])` 记录；
    7) `await self.memory.add(reply_msg)` 并返回。
  - 结构化输出：
    - `generate_response(response: str, **kwargs)` 被注册为“完成函数”；当要求结构化输出时，使用 `structured_model.model_validate(kwargs)` 校验并写入 `Msg.metadata`。
  - 并行工具：`parallel_tool_calls=True` 时使用 `asyncio.gather` 收集多个工具调用；
  - 计划工具：当注入 `PlanNotebook` 时，创建 `plan_related` 工具组并注册其工具，允许通过 meta tool 动态启停。

### 文件：src/agentscope/agent/_react_agent_base.py
- 类：`ReActAgentBase`
  - 作用：提供 ReActAgent 共用的骨架与工具封装点（如 Hook 接入、通用状态注册等）。

### 文件：src/agentscope/agent/_user_agent.py
- 类：`UserAgent`
  - 作用：面向交互的用户代理；在 `agentscope.init(studio_url=...)` 后，输入来源可被 Studio 重载。

### 文件：src/agentscope/agent/_user_input.py
- 类/函数：`StudioUserInput`
  - 作用：将用户输入改为从 Studio 拉取（HTTP 轮询/重试），并通过 `UserAgent.override_class_input_method()` 注入。

### 文件：src/agentscope/agent/_agent_meta.py
- 类：`_AgentMeta`
  - 作用：在类创建阶段织入前/后置钩子包装，统一 `reply/print/observe` 的钩子调用序。

（若新增文件，请按“文件→类→函数→成员变量”补充条目，暂无逻辑可标注“暂无”。）

## 三、与其他组件的交互关系
- Formatter：格式化对话/工具调用参数；多模态与工具块需契合模型接口。
- Model：执行 LLM（含流/结构化/工具）；`trace_reply` 记录时序。
- Toolkit/MCP：注册本地或远端工具；支持后处理与分组启停。
- Memory（短/长）、RAG、Plan：在推理前注入提示、在结束后记录或推进计划。
- Pipeline：通过消息队列聚合 `print()` 的流式块进行 UI/日志输出。
- MsgHub：使用 `pipeline/_msghub.py` 在多 Agent 间自动广播各自的回复为他人 `observe()` 的输入。

## 四、Docs‑First 变更流程与验收
1) 先在本文件补齐改动意图与受影响条目；
2) 更新 `CLAUDE.md` 的调用链映射（Agent 主循环与关键私有方法）；
3) 在根目录新增/更新 `todo.md`：
   - 执行步骤：修改点与回滚策略；
   - 验收清单：示例用例/单测、并行工具/中断/结构化输出/消息队列打印/MsgHub 广播全覆盖；
4) 获批后实施，并在 PR 描述中链接本 SOP 条目与 `CLAUDE.md` 片段。

验收 Checklist（最小集）
- [ ] ReAct 主循环 6 步能按 SOP 重现（含并行工具/结构化输出分支）
- [ ] 中断触发与 `handle_interrupt()` 行为可测试重现
- [ ] `print()` 流式输出（text/thinking/audio/tool_result）与队列聚合可用
- [ ] MsgHub 自动广播与手动 `broadcast()` 场景通过
- [ ] ruff check src（仅检测）无待处理告警；mypy/pytest 通过
