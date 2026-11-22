# 只读源码问答与能力目录

在仓库根目录运行。使用项目 Python 环境和现有源码解析依赖（在
`src/agentscope/business` 执行 `pnpm install --frozen-lockfile`）。

```sh
python -m examples.source_qa.demo
python -m examples.source_qa.demo '主管能替同事撤回报销单吗'
python -m examples.source_qa.demo '这个租户的审批额度是多少'
```

演示使用本地报销源码样本，不启动报销服务，不需要模型或业务账户。
无参数时列出全部入口，有问题时按动作名称展示规则、出处和缺失信息。

## 接入聊天

```python
from agentscope.business.source_catalog import SourceCatalog

# checkout、文件清单和授权函数由可信宿主提供。使用应用自己的登录身份。
catalog = SourceCatalog("expenses", checkout, source_files, can_read_expenses)
answer = await catalog.ask(authenticated_user_id, question, approved_chat_model)
```

宿主应提供稳定的、只读的源码检出目录，并为每个授权范围绑定单独的清单。
一次授权覆盖清单内的所有文件。传给模型的内容包括此范围内的源码片段，
应选用企业批准读取这些源码的模型。每次提问创建独立的 Agent。模型可用工具
只有列目录和取证据。最终答案由当前源码证据渲染，模型生成的业务事实不直接展示。

`search` 返回能力 ID 和版本；`explain` 要求两者匹配。版本由清单内文件名
和原始字节哈希共同确定。每条规则附带相对路径、符号、起止行、片段和文件哈希。
宿主可用 `catalog.export(principal, destination)` 将完整快照原子写入
源码树外的受控存储。该导出包含源码，文件访问权由宿主管理。聊天读取当前清单，
不会从旧导出恢复结论。

## 当前支持的源码模式

- Vue 模板中的静态文字按钮、点击处理函数、按钮及父元素的可见性指令。
- Vue script setup 的相对路径具名导入，JS/TS 导出函数直接返回 fetch 调用，
  路径和 HTTP 方法为字面量。
- Python 顶层函数上的 router 方法装饰器，与客户端方法、路径唯一匹配。
  追踪函数开头的条件拒绝、直接本地策略调用（含相对导入）和字面量状态赋值。
- 动态请求、无法解析或有歧义的策略返回缺失项。未识别的页面入口可能缺席；
  目录是源码中已发现的能力，不代表完整部署。局部校验不足以判断最终授权。
  具体记录、部署状态及租户配置值均需运行时证据。
