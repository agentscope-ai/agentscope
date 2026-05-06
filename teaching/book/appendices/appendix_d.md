# 附录D：常见错误急救箱

## 安装问题

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `ModuleNotFoundError: No module named 'agentscope'` | 没安装 | `pip install agentscope` |
| `pip: command not found` | pip没加入PATH | 重装Python并勾选"Add to PATH" |
| `SSL error` | 网络问题 | 使用国内镜像：`pip install -i https://mirrors.aliyun.com/pypi/simple/` |

## 语法错误

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `IndentationError` | 缩进问题 | 使用4个空格，不混用Tab |
| `SyntaxError: invalid syntax` | 语法错误 | 检查是否少了冒号`:` |
| `SyntaxError: can't assign to literal` | 不能给字面量赋值 | 检查变量名是否用了关键字 |

## 运行错误

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `NameError: name 'self' is not defined` | self漏写 | 方法第一个参数要是self |
| `TypeError: object is not callable` | 对象不是可调用的 | 检查是否加了()` |
| `TypeError: 'NoneType' object is not callable` | 对象是None | 检查是否正确初始化 |

## 异步错误

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `asyncio.run() error` | 异步问题 | 确保用`await`调用异步方法 |
| `RuntimeError: Event loop is running` | 事件循环冲突 | 使用`asyncio.get_event_loop().run_until_complete()` |

## API错误

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `APIKeyError` | Key没配置 | 检查`OPENAI_API_KEY`环境变量 |
| `AuthenticationError` | Key无效 | 检查API Key是否正确 |
| `RateLimitError` | 请求过快 | 添加延时或升级账号 |
| `TimeoutError` | 网络超时 | 检查网络或重试 |

## AgentScope特有错误

| 错误信息 | 可能原因 | 解决方案 |
|----------|----------|----------|
| `AgentNotFoundError` | Agent名不存在 | 检查Agent名称 |
| `ToolNotFoundError` | 工具名不存在 | 检查Tool名称 |
| `MemoryConnectionError` | Memory连接失败 | 检查Redis是否启动 |
